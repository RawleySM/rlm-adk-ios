"""Explicit Scripture references in the user's own words are grounded in the requested retained chapter
(verified, revision-qualified) before topic retrieval; coverage is reported exactly; invalid or
unavailable references invent nothing; the heard passage keeps its exact revision."""

import json
import tempfile
import threading
import unittest
from pathlib import Path

from tests.fakes import FakeInference
from tests.helpers import EDITION_META, fake_verses, make_config, write_prayer_store
from voice_portal import conversation, retrieval
from voice_portal.db import connect
from voice_portal.importer import import_family
from voice_portal.references import explicit_references
from voice_portal.sources.prayers import read_prayers
from voice_portal.sources.scripture import chapter_records

PSALM_23 = [
    "Yahweh is my shepherd: I shall lack nothing.",
    "He makes me lie down in green pastures. He leads me beside still waters.",
    "He restores my soul. He guides me in the paths of righteousness for his name's sake.",
    "Even though I walk through the valley of the shadow of death, I will fear no evil, for you are with me.",
    "You prepare a table before me in the presence of my enemies. You anoint my head with oil. My cup runs over.",
    "Surely goodness and loving kindness shall follow me all the days of my life, and I will dwell in Yahweh's house forever.",
]
FIRST_PETER_5 = [
    "Therefore I exhort the elders among you, as a fellow elder and a witness of the sufferings of Christ.",
    "Shepherd the flock of God which is among you, exercising the oversight, not under compulsion, but voluntarily.",
    "Humble yourselves therefore under the mighty hand of God, that he may exalt you in due time.",
    "Casting all your worries on him, because he cares for you, and gives rest to the humble.",
]
HEBREWS_4 = ["Let us fear therefore, lest perhaps anyone of you should seem to have come short of a promise of entering into his rest.", "For we who have believed do enter into that rest."]
JOHN_3 = ["Now there was a man of the Pharisees named Nicodemus, a ruler of the Jews.", "For God so loved the world, that he gave his one and only Son."]
FIRST_JOHN_3 = ["See how great a love the Father has given to us, that we should be called children of God!", "Beloved, now we are children of God."]
# A long chapter: 40 verses of ~95 chars -> several 700-char chunks (more than the explicit chunk cap).
PSALM_119 = [f"Verse {n} of the long psalm: blessed are those whose way is blameless, who walk in the law of Yahweh, verse {n}." for n in range(1, 71)]


def chunk_ids(conn, book, chapter):
    doc = conn.execute("SELECT doc_id, revision_hash FROM documents WHERE source_doc_id LIKE ? AND current = 1", (f"%/{book}.{chapter}",)).fetchone()
    return [retrieval.cite_id(doc["doc_id"], doc["revision_hash"], r[0]) for r in conn.execute("SELECT chunk_index FROM chunks WHERE doc_id = ? ORDER BY chunk_index", (doc["doc_id"],))]


class ExplicitReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_config(Path(self.tmp.name))
        self.conn = connect(self.cfg.db_path)
        verses = fake_verses({("PSA", 23): PSALM_23, ("1PE", 5): FIRST_PETER_5, ("HEB", 4): HEBREWS_4, ("JHN", 3): JOHN_3, ("1JN", 3): FIRST_JOHN_3, ("PSA", 119): PSALM_119})
        import_family(self.conn, "scripture", chapter_records(verses, EDITION_META), selection={"edition": EDITION_META}, source_path="fake")
        write_prayer_store(Path(self.cfg.prayer_store), [("p1", "Lord, give me rest tonight; I am weary and I cast my worries on you.", 1.0)])
        records, sel = read_prayers(self.cfg.prayer_store)
        import_family(self.conn, "prayer", records, selection=sel, source_path=self.cfg.prayer_store)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _evidence(self, question, **kw):
        return conversation.gather_evidence(self.conn, question, families=None, passage_cite_ids=None, **kw)

    # ----- parsing -----

    def test_parses_canonical_numbered_and_spoken_ordinal_forms(self):
        cases = {
            "First Peter 5:7": "1PE.5:7", "Second Timothy 3:16": "2TI.3:16", "Third John 1": "3JN.1", "II Kings 2": "2KI.2",
            "1 Corinthians 13:4-7": "1CO.13:4-7", "psalms 23": "PSA.23", "Ps 23:4": "PSA.23:4", "Song of Solomon 2:1": "SNG.2:1",
            "What does 1 John 3 say": "1JN.3", "Explain John 3 to me": "JHN.3", "Genesis 1.1": "GEN.1:1",
        }
        for text, expected in cases.items():
            refs = explicit_references(text)
            self.assertEqual([r["ref"] for r in refs], [expected], text)
            self.assertIsNone(refs[0]["invalid"], text)

    def test_unknown_or_bare_numbered_base_is_not_a_reference(self):
        for text in ("Peter 5 was great", "First Genesis 2", "Kings 2", "call me at 23", "no reference here", "Psalm"):
            self.assertEqual(explicit_references(text), [], text)

    def test_invalid_verse_numbers_are_flagged_not_widened(self):
        self.assertEqual(explicit_references("Psalm 23:0")[0]["invalid"], "invalid_verse")
        self.assertEqual(explicit_references("Psalm 23:6-4")[0]["invalid"], "reversed_range")
        self.assertEqual(explicit_references("Psalm 0")[0]["invalid"], "invalid_chapter")

    # ----- resolution and coverage -----

    def test_psalm_23_question_is_grounded_in_psalm_23_first(self):
        """Live smoke 2026-10-13: this question retrieved 1 Peter 5 and Hebrews 4 and omitted Psalm 23."""
        hits, scope = self._evidence("What does Psalm 23 say about rest? Explain briefly using the source.")
        self.assertEqual(hits[0]["source_doc_id"], "github:BibleAquifer/WorldEnglishBible#eng/PSA.23")
        self.assertEqual(hits[0]["strategy"], "explicit_reference")
        self.assertIn("green pastures", hits[0]["text"])
        self.assertTrue(retrieval.resolve_citation(self.conn, hits[0]["cite_id"])["verified"])
        rep = scope["explicit_references"][0]
        self.assertEqual((rep["ref"], rep["supplied"], rep["coverage"], rep["chunks_supplied"], rep["chunks_total"]), ("PSA.23", True, "full", 1, 1))
        self.assertIn("prayer", {h["source_family"] for h in hits})

    def test_numbered_book_is_not_confused_with_its_unnumbered_namesake(self):
        hits, scope = self._evidence("What does 1 John 3 say about being children of God?")
        self.assertEqual(hits[0]["source_doc_id"], "github:BibleAquifer/WorldEnglishBible#eng/1JN.3")
        hits, _ = self._evidence("Explain John 3 to me")
        self.assertEqual(hits[0]["source_doc_id"], "github:BibleAquifer/WorldEnglishBible#eng/JHN.3")
        hits, _ = self._evidence("What does First Peter 5 say about rest?")
        self.assertEqual(hits[0]["source_doc_id"], "github:BibleAquifer/WorldEnglishBible#eng/1PE.5")

    def test_valid_verse_narrows_to_the_chunk_holding_it_in_a_multi_chunk_chapter(self):
        ids = chunk_ids(self.conn, "PSA", 119)
        self.assertGreater(len(ids), 8, "fixture chapter must span more chunks than the cap")
        hits, scope = self._evidence("What does Psalm 119:25 mean?")
        ex = [h for h in hits if h["strategy"] == "explicit_reference"]
        self.assertEqual(len(ex), 1)
        self.assertIn("Ps.119.25", ex[0]["verse_refs"])
        self.assertIn("verse 25", ex[0]["text"])
        rep = scope["explicit_references"][0]
        self.assertEqual((rep["coverage"], rep["verses_found"], rep["verses_missing"], rep["chunks_supplied"]), ("full", ["Ps.119.25"], [], 1))
        hits, scope = self._evidence("Read Psalm 23:4-6 to me")
        rep = scope["explicit_references"][0]
        self.assertEqual((rep["coverage"], rep["verses_found"]), ("full", ["Ps.23.4", "Ps.23.5", "Ps.23.6"]))
        self.assertIn("valley of the shadow", hits[0]["text"])

    def test_partial_range_reports_exactly_which_verses_are_missing(self):
        hits, scope = self._evidence("Read Psalm 23:5-99")
        rep = scope["explicit_references"][0]
        self.assertTrue(rep["supplied"])
        self.assertEqual(rep["coverage"], "partial")
        self.assertEqual(rep["verses_found"], ["Ps.23.5", "Ps.23.6"])
        self.assertEqual(rep["verses_missing"][:2], ["PSA.23:7", "PSA.23:8"])
        self.assertEqual(len(rep["verses_missing"]), 93)
        self.assertEqual(rep["reason"], "some_verses_not_in_retained_chapter")
        from voice_portal.references import coverage_note
        note = coverage_note(rep)
        self.assertIn("only Ps.23.5, Ps.23.6", note)
        self.assertIn("verses 7-99 are not in it", note)
        self.assertNotIn("that passage is included", note)

    def test_invalid_verse_zero_and_reversed_range_supply_nothing(self):
        for q, reason in (("Read Psalm 23:0", "invalid_verse"), ("Read Psalm 23:6-4", "reversed_range")):
            hits, scope = self._evidence(q)
            rep = scope["explicit_references"][0]
            self.assertFalse(rep["supplied"], q)
            self.assertEqual((rep["coverage"], rep["reason"]), ("none", reason), q)
            self.assertFalse(any(h.get("strategy") == "explicit_reference" for h in hits), q)
        hits, scope = self._evidence("What does 1 Peter 5:7 say?")  # fixture chapter has four verses
        rep = scope["explicit_references"][0]
        self.assertEqual((rep["supplied"], rep["coverage"], rep["reason"], rep["verses_missing"]), (False, "none", "verses_not_in_retained_chapter", ["1PE.5:7"]))

    def test_long_chapter_reports_partial_coverage_tied_to_supplied_chunks(self):
        ids = chunk_ids(self.conn, "PSA", 119)
        hits, scope = self._evidence("Read Psalm 119")
        ex = [h for h in hits if h["strategy"] == "explicit_reference"]
        rep = scope["explicit_references"][0]
        self.assertEqual(len(ex), 8)
        self.assertEqual([h["cite_id"] for h in ex], ids[:8])
        self.assertEqual((rep["coverage"], rep["chunks_supplied"], rep["chunks_total"], rep["reason"]), ("partial", 8, len(ids), "chapter_longer_than_supplied_chunks"))
        from voice_portal.references import coverage_note
        self.assertIn(f"first 8 of {len(ids)} parts", coverage_note(rep))
        # a small chapter is supplied whole
        hits, scope = self._evidence("Read Psalm 23")
        self.assertEqual(scope["explicit_references"][0]["coverage"], "full")

    def test_unavailable_chapter_invents_nothing(self):
        hits, scope = self._evidence("What does Genesis 51 say?")
        rep = scope["explicit_references"][0]
        self.assertEqual((rep["ref"], rep["supplied"], rep["coverage"], rep["reason"]), ("GEN.51", False, "none", "chapter_not_in_retained_edition"))
        self.assertFalse(any(h.get("strategy") == "explicit_reference" for h in hits))

    def test_no_reference_means_no_explicit_block(self):
        hits, scope = self._evidence("What have I prayed about rest?")
        self.assertEqual(scope["explicit_references"], [])

    def test_heard_passage_still_comes_first_and_keeps_its_revision(self):
        heard = retrieval.search(self.conn, "green pastures", families=["scripture"])["hits"][0]["cite_id"]
        hits, scope = conversation.gather_evidence(self.conn, "What does 1 Peter 5 say about this?", families=None, passage_cite_ids=[heard])
        self.assertEqual((hits[0]["strategy"], hits[0]["cite_id"]), ("heard_passage", heard))
        self.assertEqual((hits[1]["strategy"], hits[1]["source_doc_id"]), ("explicit_reference", "github:BibleAquifer/WorldEnglishBible#eng/1PE.5"))

    def test_references_come_from_the_transcript_not_the_augmented_query(self):
        hits, scope = conversation.gather_evidence(self.conn, "Why? Psalms 23 (World English Bible)", families=None, passage_cite_ids=None, reference_text="Why?")
        self.assertEqual(scope["explicit_references"], [])


class RunTurnReferenceTests(unittest.TestCase):
    """run_turn-level: the heard title must not be parsed as the user's reference, and a historical heard
    revision must not be shadowed by the current chapter."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_config(Path(self.tmp.name))
        self.inference = FakeInference(reply="Because he restores your soul [1].", delay=0.01)
        self.cfg.inference_base_url = self.inference.base_url
        self.cfg.inference_status_url = f"http://127.0.0.1:{self.inference.port}/status"
        self.conn = connect(self.cfg.db_path)
        verses = fake_verses({("PSA", 23): PSALM_23})
        import_family(self.conn, "scripture", chapter_records(verses, EDITION_META), selection={"edition": EDITION_META}, source_path="fake")
        self.heard = retrieval.search(self.conn, "green pastures", families=["scripture"])["hits"][0]["cite_id"]
        # A later import revises the chapter text: the heard citation becomes historical.
        revised = fake_verses({("PSA", 23): PSALM_23[:-1] + ["Surely goodness and loving kindness shall follow me all the days of my life."]})
        import_family(self.conn, "scripture", chapter_records(revised, EDITION_META), selection={"edition": EDITION_META}, source_path="fake")
        self.assertFalse(retrieval.resolve_citation(self.conn, self.heard)["current"])

    def tearDown(self):
        self.inference.stop()
        self.conn.close()
        self.tmp.cleanup()

    def test_why_with_heard_title_uses_only_the_heard_revision(self):
        events = []
        result = conversation.run_turn(self.conn, self.cfg, turn_id="t-why", session_id=None, transcript="Why?", context={"passage_title": "Psalms 23 (World English Bible)", "passage_cite_ids": [self.heard]}, emit=events.append, cancel=threading.Event())
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["scope"]["explicit_references"], [], "the heard title is not the user's reference")
        self.assertEqual(result["scope"]["passage_supplied"], [self.heard])
        cites = result["citations"]
        self.assertEqual(cites[0]["cite_id"], self.heard)
        heard_doc = retrieval.resolve_citation(self.conn, self.heard)
        current_doc = retrieval.document_by_source(self.conn, "scripture", heard_doc["source_doc_id"])
        current_prefix = retrieval.cite_id(current_doc["doc_id"], current_doc["revision_hash"], 0)
        self.assertFalse(any(c["cite_id"] == current_prefix for c in cites), "the current revision must not be injected beside the heard one")
        system_prompt = self.inference.requests[0]["messages"][0]["content"]
        self.assertNotIn("The question names", system_prompt)
        self.assertIn("revision you heard", self.inference.requests[0]["messages"][1]["content"])

    def test_explicit_reference_in_transcript_still_resolves_at_run_turn_level(self):
        events = []
        result = conversation.run_turn(self.conn, self.cfg, turn_id="t-ref", session_id=None, transcript="What does Psalm 23:4 say?", context=None, emit=events.append, cancel=threading.Event())
        rep = result["scope"]["explicit_references"][0]
        self.assertEqual((rep["ref"], rep["coverage"], rep["verses_found"]), ("PSA.23:4", "full", ["Ps.23.4"]))
        self.assertIn("that passage is included", self.inference.requests[0]["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
