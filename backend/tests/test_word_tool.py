"""The word-from-God tool: which prayer is answered, one strict-JSON theme call (thinking disabled,
cached per prayer revision, keyword fallback when the owner fails), pairings-ledger passages first,
live echoes and theme retrieval after, verbatim verses with citations, and one-sentence frames.
The prayer store is only ever read."""

import datetime as dt
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from tests.fakes import FakeInference
from tests.helpers import import_chapters, make_config, numbered, sha, write_prayer_store
from voice_portal.db import connect, now
from voice_portal.importer import import_family
from voice_portal.sources.prayers import read_prayers
from voice_portal.tools import word

MATTHEW_11 = numbered("Matthew 11", 30, {
    28: "Come to me, all you who labor and are heavily burdened, and I will give you rest.",
    29: "Take my yoke upon you and learn from me, for I am gentle and lowly in heart; and you will find rest for your souls.",
    30: "For my yoke is easy, and my burden is light.",
})
MATTHEW_6 = numbered("Matthew 6", 34, {
    9: "Pray like this: ‘Our Father in heaven, may your name be kept holy.",
    10: "Let your Kingdom come. Let your will be done on earth as it is in heaven.",
    11: "Give us today our daily bread.",
    12: "Forgive us our debts, as we also forgive our debtors.",
    13: "Bring us not into temptation, but deliver us from the evil one. For yours is the Kingdom, the power, and the glory forever. Amen.’",
})
ISAIAH_40 = numbered("Isaiah 40", 31, {
    29: "He gives power to the weak. He increases the strength of him who has no might.",
    31: "But those who wait for Yahweh will renew their strength. They will mount up with wings like eagles. They will run, and not be weary. They will walk, and not faint.",
})
PSALM_127 = numbered("Psalm 127", 5, {3: "Behold, children are a heritage of Yahweh. The fruit of the womb is his reward."})
PSALM_23 = numbered("Psalm 23", 6, {1: "Yahweh is my shepherd; I shall lack nothing."})

NEW_PRAYER = ("Lord, I am weary and tired tonight; give me rest for my soul and strength to wait for you. "
              "Forgive us our debts, as we also forgive our debtors. "
              "Thank you for my son and for the children you have given us as a heritage.")
OLD_PRAYER = "Father, you are my shepherd and I lack nothing. Thank you for green pastures."
THEMES = {"themes": [
    {"label": "rest for a weary soul", "petition": "You asked God for rest and strength.", "terms": ["rest", "weary", "strength"]},
    {"label": "forgiveness", "petition": "You asked God to forgive as you forgive.", "terms": ["forgive", "debts", "debtors"]},
    {"label": "thanks for your children", "petition": "You thanked God for your son.", "terms": ["children", "heritage", "son"]},
]}


def publish_ledger(ledger: Path, rows: list[dict]) -> None:
    """Write the ledger and a manifest.json beside it, as the Prayer & Word publisher does."""
    ledger.parent.mkdir(parents=True, exist_ok=True)
    raw = ("\n".join(json.dumps(r) for r in rows) + "\n").encode("utf-8")
    ledger.write_bytes(raw)
    manifest = {
        "schema_version": 1,
        "product_id": "scripture_pairings_frozen_test",
        "status": "frozen",
        "coverage_ends": "2026-10-08",
        "published_at": "2026-10-16T17:47:37Z",
        "files": {ledger.name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "rows": len(rows), "format": "jsonl"}},
        "row_count": len(rows),
    }
    (ledger.parent / "manifest.json").write_text(json.dumps(manifest))


def local_ts(hour: int, day: int = 13) -> float:
    return dt.datetime(2026, 10, day, hour, 0).astimezone().timestamp()


class WordToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = make_config(root)
        self.cfg.inference_status_url = ""
        self.inference = FakeInference(reply=json.dumps(THEMES), delay=0, reasoning=None)
        self.cfg.inference_base_url = self.inference.base_url
        self.cfg.tools_inference_timeout_s = 20
        self.conn = connect(self.cfg.db_path)
        import_chapters(self.conn, {("MAT", 11): MATTHEW_11, ("MAT", 6): MATTHEW_6, ("ISA", 40): ISAIAH_40, ("PSA", 127): PSALM_127, ("PSA", 23): PSALM_23})
        write_prayer_store(Path(self.cfg.prayer_store), [("p-old", OLD_PRAYER, local_ts(20, 11)), ("p-new", NEW_PRAYER, local_ts(7, 13))])
        records, selection = read_prayers(self.cfg.prayer_store)
        import_family(self.conn, "prayer", records, selection=selection, source_path=self.cfg.prayer_store)
        self.ledger = root / "published" / "scripture_pairings_frozen_test" / "pairings.jsonl"
        rows = [
            {"annotation_id": "a-chapter", "basis": "cited", "prayer_id": "p-new", "scripture_refs": ["Psalm 23"], "text_sha256": sha(NEW_PRAYER), "usable": 1, "phrase_in_current_text": "exact", "record_key": "k0"},
            {"annotation_id": "a-echo", "basis": "echoed", "prayer_id": "p-new", "scripture_refs": ["Matthew 11:28-29 (rest for your souls)"], "text_sha256": sha(NEW_PRAYER), "usable": 1, "phrase_in_current_text": "exact", "record_key": "k1"},
            {"annotation_id": "a-other", "basis": "cited", "prayer_id": "p-old", "scripture_refs": ["Psalm 23:1"], "text_sha256": sha(OLD_PRAYER), "usable": 1, "phrase_in_current_text": "exact", "record_key": "k2"},
        ]
        publish_ledger(self.ledger, rows)
        self.cfg.scripture_pairings_ledger = str(self.ledger)
        self.store_path = Path(self.cfg.prayer_store)
        self.store_hash = hashlib.sha256(self.store_path.read_bytes()).hexdigest()

    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.store_path.read_bytes()).hexdigest(), self.store_hash)  # never written
        self.conn.close()
        self.inference.stop()
        self.tmp.cleanup()

    def test_word_for_latest_prayer(self):
        out = word.word_tool(self.conn, self.cfg, "latest", today=dt.date(2026, 10, 13))
        self.assertTrue(out["available"])
        self.assertEqual(out["prayer"]["id"], "p-new")
        self.assertEqual(out["prayer"]["basis"], "corpus_newest")
        self.assertEqual(out["themes_source"], "reasoning")
        self.assertEqual([t["label"] for t in out["themes"]], [t["label"] for t in THEMES["themes"]])
        passages = out["passages"]
        self.assertTrue(2 <= len(passages) <= 4, passages)
        self.assertEqual(passages[0]["source"], "pairings")
        self.assertEqual(passages[0]["reference"], "Matthew 11:28-29")
        self.assertEqual(passages[0]["annotation_id"], "a-echo")  # the whole-chapter ledger ref is skipped
        references = [p["reference"] for p in passages]
        self.assertTrue(any(r.startswith("Matthew 6:12") for r in references), references)  # the prayer's own words echo it
        lead = out["spoken"].split(". ")[0]
        self.assertIn("October 13", lead)
        position = 0
        for passage in passages:
            self.assertTrue(passage["cite_ids"])
            self.assertTrue(passage["verses"])
            frame = passage["frame"]
            self.assertEqual(len(re.findall(r"[.!?](?:\s|$)", frame)), 1, frame)  # exactly one sentence
            self.assertTrue(frame.endswith("."))
            reading = " ".join(v["text"] for v in passage["verses"])
            found = out["spoken"].index(f"{frame} {reading}", position)
            self.assertGreaterEqual(found, position)  # frames and verses in reading order
            position = found
        self.assertNotRegex(out["spoken"], r"\[\d|[*_`#]")
        request = self.inference.requests[0]
        self.assertEqual(request["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(request["response_format"]["type"], "json_schema")
        self.assertIn(NEW_PRAYER, request["messages"][1]["content"])

    def test_ledger_rows_need_a_matching_manifest(self):
        out = word.word_tool(self.conn, self.cfg, "latest", today=dt.date(2026, 10, 13))
        report = out["candidates"]["ledger_manifest"]
        self.assertEqual((report["state"], report["product_id"], report["rows"]), ("verified", "scripture_pairings_frozen_test", 3))
        self.assertEqual(out["candidates"]["ledger_rows_total"], 3)
        manifest_path = self.ledger.parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"]["pairings.jsonl"]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest, indent=1))
        tampered = word.word_tool(self.conn, self.cfg, "latest", today=dt.date(2026, 10, 13))
        self.assertEqual(tampered["candidates"]["ledger_manifest"]["state"], "mismatch")
        self.assertEqual(tampered["candidates"]["ledger_rows_total"], 0)
        self.assertNotIn("pairings", [p["source"] for p in tampered["passages"]])
        manifest_path.unlink()
        missing = word.word_tool(self.conn, self.cfg, "latest", today=dt.date(2026, 10, 13))
        self.assertEqual(missing["candidates"]["ledger_manifest"]["state"], "no_manifest")
        self.assertEqual(missing["candidates"]["ledger_rows_total"], 0)

    def test_themes_are_cached_per_prayer_revision(self):
        word.word_tool(self.conn, self.cfg)
        second = word.word_tool(self.conn, self.cfg)
        self.assertEqual(len(self.inference.requests), 1)
        self.assertTrue(second["themes_detail"]["cached"])
        word.word_tool(self.conn, self.cfg, refresh=True)
        self.assertEqual(len(self.inference.requests), 2)

    def test_published_capture_wins_a_tie_and_morning_selects_by_local_hour(self):
        with self.conn:
            self.conn.execute("INSERT INTO prayer_captures(client_capture_id, created_at, updated_at, status, recording_id, prayer_id) VALUES (?,?,?,?,?,?)", ("cap-1", now(), now(), "published", "rec-1", "p-new"))
        out = word.word_tool(self.conn, self.cfg)
        self.assertEqual(out["prayer"]["basis"], "published_capture")
        self.assertEqual(out["prayer"]["client_capture_id"], "cap-1")
        write_prayer_store(Path(self.cfg.prayer_store), [("p-old", OLD_PRAYER, local_ts(9, 11)), ("p-new", NEW_PRAYER, local_ts(20, 13))])
        self.store_hash = hashlib.sha256(self.store_path.read_bytes()).hexdigest()
        with self.conn:
            self.conn.execute("DELETE FROM prayer_captures")
        morning = word.word_tool(self.conn, self.cfg, "morning")
        self.assertEqual(morning["prayer"]["id"], "p-old")
        self.assertEqual(morning["passages"][0]["source"], "pairings")
        self.assertEqual(morning["passages"][0]["reference"], "Psalm 23:1")

    def test_owner_failure_falls_back_to_keyword_themes(self):
        self.inference.status = 503
        out = word.word_tool(self.conn, self.cfg)
        self.assertEqual(out["themes_source"], "fallback_keywords")
        self.assertEqual(len(self.inference.requests), 1)  # a 503 is not retried
        self.assertTrue(out["themes_detail"]["errors"])
        self.assertTrue(2 <= len(out["passages"]) <= 4)
        self.assertTrue(all(len(re.findall(r"[.!?](?:\s|$)", p["frame"])) == 1 for p in out["passages"]))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM tool_cache").fetchone())  # fallbacks are not cached

    def test_rejected_structured_output_is_retried_plainly(self):
        self.inference.status = 400
        out = word.word_tool(self.conn, self.cfg)
        self.assertEqual(len(self.inference.requests), 2)
        self.assertIn("response_format", self.inference.requests[0])
        self.assertNotIn("response_format", self.inference.requests[1])
        self.assertEqual(out["themes_source"], "fallback_keywords")

    def test_unparseable_reply_and_no_prayer(self):
        self.inference.reply = "Here are the themes: rest, forgiveness."
        out = word.word_tool(self.conn, self.cfg)
        self.assertEqual(out["themes_source"], "fallback_keywords")
        self.cfg.prayer_store = str(Path(self.tmp.name) / "absent.sqlite3")
        with self.conn:
            self.conn.execute("UPDATE documents SET current = 0 WHERE source_family = 'prayer'")
        empty = word.word_tool(self.conn, self.cfg)
        self.assertFalse(empty["available"])
        self.assertIn("could not find", empty["spoken"])

    def test_devotional_prefers_todays_chapter(self):
        self.cfg.devotional_passages = ["ISA.40"]
        out = word.word_tool(self.conn, self.cfg, "devotional", today=dt.date(2026, 10, 13))
        sources = [p["source"] for p in out["passages"]]
        self.assertIn("devotional", sources)
        devotional = out["passages"][sources.index("devotional")]
        self.assertTrue(devotional["reference"].startswith("Isaiah 40:"))
        self.assertTrue(devotional["frame"].startswith("From today's devotional passage"))

    def test_fallback_themes_and_parsing(self):
        themes = word.fallback_themes(NEW_PRAYER)
        self.assertTrue(themes)
        self.assertTrue(all(t["quote"] and t["terms"] and "'" not in "".join(t["terms"]) for t in themes))
        parsed = word._parse_themes("```json\n" + json.dumps({"themes": [{"label": "Rest!", "petition": "You asked for **rest** [1].", "terms": ["Rest", "the", "weary heart"]}]}) + "\n```")
        self.assertEqual(parsed, [{"label": "Rest", "petition": "You asked for rest.", "terms": ["rest", "weary", "heart"]}])
        with self.assertRaises(ValueError):
            word._parse_themes('{"themes": []}')


if __name__ == "__main__":
    unittest.main()
