import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import EDITION_META, THEO_BODIES, fake_verses, make_config, write_prayer_store, write_youtube_store
from voice_portal import conversation, retrieval
from voice_portal.db import connect
from voice_portal.importer import chunk_boundaries, family_counts, import_family
from voice_portal.sources import SourceUnavailable
from voice_portal.sources.prayers import read_prayers
from voice_portal.sources.scripture import chapter_records
from voice_portal.sources.teaching import read_teaching
from voice_portal.sources.theographic import load_theographic, passage_entities


class ChunkingTests(unittest.TestCase):
    def test_boundaries_cover_text_and_are_deterministic(self):
        body = ("Lord, teach me to pray. " * 60) + "\n\n" + ("Second paragraph here. " * 50)
        a = chunk_boundaries(body)
        b = chunk_boundaries(body)
        self.assertEqual(a, b)
        self.assertTrue(all(end > start for start, end in a))
        self.assertTrue(all(len(body[s:e]) <= 700 for s, e in a))
        self.assertEqual(a[0][0], 0)
        joined = " ".join(body[s:e] for s, e in a)
        self.assertIn("Second paragraph here.", joined)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.cfg = make_config(tmp)
        self.conn = connect(self.cfg.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _import_prayers(self, prayers):
        write_prayer_store(Path(self.cfg.prayer_store), prayers)
        records, selection = read_prayers(self.cfg.prayer_store)
        return import_family(self.conn, "prayer", records, selection=selection, source_path=self.cfg.prayer_store)

    def test_repeat_import_is_idempotent(self):
        prayers = [("p1", "Lord God, use my spirit. Turn my face to you.", 1.0), ("p2", "Father, thank you for my son and for grace.", 2.0)]
        first = self._import_prayers(prayers)
        second = self._import_prayers(prayers)
        self.assertEqual(first["inserted"], 2)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["unchanged"], 2)
        self.assertEqual(family_counts(self.conn)["prayer"], {"current": 2, "revisions": 2})

    def test_shorter_corrected_body_becomes_current_and_old_stays_inspectable(self):
        self._import_prayers([("p1", "Lord God, use my spirit. Turn my face to you. extra words that were wrong", 1.0)])
        old_hits = retrieval.search(self.conn, "wrong words")["hits"]
        self.assertEqual(len(old_hits), 1)
        old_cite = old_hits[0]["cite_id"]
        counts = self._import_prayers([("p1", "Lord God, use my spirit. Turn my face to you.", 1.0)])
        self.assertEqual(counts["revised"], 1)
        self.assertEqual(retrieval.search(self.conn, "wrong words")["hits"], [])
        revisions = retrieval.document_revisions(self.conn, "prayer", "p1")
        self.assertEqual([r["current"] for r in revisions], [0, 1])
        old = retrieval.resolve_citation(self.conn, old_cite)
        self.assertTrue(old["available"])
        self.assertFalse(old["current"])
        self.assertIn("wrong", old["text"])
        new_hit = retrieval.search(self.conn, "spirit face")["hits"][0]
        self.assertNotEqual(new_hit["revision_hash"], old["revision_hash"])

    def test_membership_removal_cannot_remain_a_hit(self):
        self._import_prayers([("p1", "Lord, bless Rawley today.", 1.0), ("p2", "Father, keep the zebra safe.", 2.0)])
        self.assertEqual(len(retrieval.search(self.conn, "zebra")["hits"]), 1)
        counts = self._import_prayers([("p1", "Lord, bless Rawley today.", 1.0)])
        self.assertEqual(counts["removed"], 1)
        self.assertEqual(retrieval.search(self.conn, "zebra")["hits"], [])
        row = self.conn.execute("SELECT current, removed_at FROM documents WHERE source_doc_id = 'p2'").fetchone()
        self.assertEqual(row["current"], 0)
        self.assertIsNotNone(row["removed_at"])
        # reappearing membership reinstates the same revision without duplicating rows
        counts = self._import_prayers([("p1", "Lord, bless Rawley today.", 1.0), ("p2", "Father, keep the zebra safe.", 2.0)])
        self.assertEqual(counts["reinstated"], 1)
        self.assertEqual(len(retrieval.search(self.conn, "zebra")["hits"]), 1)
        self.assertEqual(family_counts(self.conn)["prayer"]["revisions"], 2)

    def test_version_qualified_citation_rejects_wrong_revision(self):
        self._import_prayers([("p1", "Lord, bless Rawley today.", 1.0)])
        hit = retrieval.search(self.conn, "bless")["hits"][0]
        ok = retrieval.resolve_citation(self.conn, hit["cite_id"])
        self.assertTrue(ok["available"] and ok["verified"])
        self.assertEqual(ok["text"], "Lord, bless Rawley today.")
        doc_id, _, idx = hit["cite_id"].split(":")
        bad = retrieval.resolve_citation(self.conn, f"{doc_id}:000000000000:{idx}")
        self.assertFalse(bad["available"])
        self.assertEqual(bad["reason"], "revision_mismatch")
        with self.assertRaises(ValueError):
            retrieval.resolve_citation(self.conn, "garbage")

    def test_unavailable_source_is_explicit(self):
        with self.assertRaises(SourceUnavailable):
            read_prayers(Path(self.tmp.name) / "missing.sqlite3")
        with self.assertRaises(SourceUnavailable):
            read_teaching(Path(self.tmp.name) / "missing.sqlite3", limit_per_provider=5, min_chars=1)

    def test_teaching_selection_limits_and_dedups(self):
        docs = [(f"https://www.desiringgod.org/a{i}", "desiringgod", f"2026-01-{i + 1:02d}", f"T{i}", f"Teaching body {i} about prayer and joy in God. " * 3) for i in range(6)]
        docs.append(("https://www.desiringgod.org/dup", "desiringgod", "2026-02-01", "Dup", docs[5][4]))  # identical body under another URL
        docs.append(("https://coe22.com/s1", "eleven22", "2026-03-01", "S1", "Joby Martin on grace and the arrest of Jesus. " * 3))
        write_youtube_store(Path(self.cfg.youtube_store), docs)
        records, selection = read_teaching(self.cfg.youtube_store, limit_per_provider=4, min_chars=10)
        dg = [r for r in records if r.metadata["provider"] == "desiringgod"]
        self.assertEqual(len(dg), 3)  # 4 newest candidates, one body duplicated -> 3 kept
        self.assertEqual(dg[0].title, "Dup")
        self.assertEqual(selection["providers"]["eleven22"]["kept"], 1)
        counts = import_family(self.conn, "teaching", records, selection=selection, source_path=self.cfg.youtube_store)
        self.assertEqual(counts["inserted"], 4)
        hit = retrieval.search(self.conn, "arrest of Jesus", families=["teaching"])["hits"][0]
        self.assertIn("Eleven22", hit["attribution"])

    def test_scripture_chapters_and_verse_citations(self):
        verses = fake_verses({("PSA", 23): ["Yahweh is my shepherd: I shall lack nothing.", "He makes me lie down in green pastures.", "He restores my soul."], ("JHN", 15): ["I am the true vine, and my Father is the farmer."]})
        records = list(chapter_records(verses, EDITION_META))
        self.assertEqual([r.source_doc_id for r in records], ["github:BibleAquifer/WorldEnglishBible#eng/PSA.23", "github:BibleAquifer/WorldEnglishBible#eng/JHN.15"])
        counts = import_family(self.conn, "scripture", records, selection={"edition": EDITION_META}, source_path="fake")
        self.assertEqual(counts["inserted"], 2)
        hit = retrieval.search(self.conn, "green pastures")["hits"][0]
        self.assertEqual(hit["verse_refs"], ["Ps.23.1", "Ps.23.2", "Ps.23.3"])
        cite = retrieval.resolve_citation(self.conn, hit["cite_id"])
        self.assertTrue(cite["verified"])
        self.assertEqual(cite["verse_locators"][1]["osis"], "Ps.23.2")
        body = self.conn.execute("SELECT body FROM documents WHERE doc_id = ?", (int(hit["cite_id"].split(":")[0]),)).fetchone()[0]
        loc = cite["verse_locators"][1]
        self.assertEqual(body[loc["start"]:loc["end"]], "He makes me lie down in green pastures.")
        self.assertIn("World English Bible 1.0.2", cite["attribution"])
        # devotional uses the retained verses and reports theographic joins when loaded
        load_theographic(self.conn, THEO_BODIES, {"source_id": "theo-test"})
        dev = conversation.build_devotional(self.conn, self.cfg, passage="PSA.23")
        self.assertTrue(dev["available"])
        self.assertEqual([s["osis"] for s in dev["segments"] if s["kind"] == "scripture"], ["Ps.23.1", "Ps.23.2", "Ps.23.3"])
        self.assertFalse(dev["teaching_found"])
        theo = dev["theographic"]
        self.assertEqual(theo["missing_verses"], ["Ps.23.3"])
        self.assertEqual([p["person_id"] for p in theo["people"]], ["p1"])
        self.assertEqual(theo["unresolved_people_keys"], ["Unknown Person"])
        self.assertEqual([p["place_id"] for p in theo["places"]], ["pl1"])
        missing = conversation.build_devotional(self.conn, self.cfg, passage="GEN.1")
        self.assertFalse(missing["available"])

    def test_theographic_passage_lookup_direct(self):
        load_theographic(self.conn, THEO_BODIES, {"source_id": "theo-test"})
        out = passage_entities(self.conn, ["Ps.23.1", "Nope.1.1"])
        self.assertEqual(out["matched_verses"], ["Ps.23.1"])
        self.assertEqual(out["missing_verses"], ["Nope.1.1"])


if __name__ == "__main__":
    unittest.main()
