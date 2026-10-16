"""Skill-service tools: spoken reference parsing, verbatim Scripture, the conversation policy, project
check-in over the real register records (held-out descriptions), the work acknowledgement, and the
HTTP routes the Pipecat talk tier calls."""

import json
import os
import re
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from tests.helpers import import_chapters, make_config, numbered
from tests.test_outbox import STUB
from voice_portal.db import connect
from voice_portal.server import make_server
from voice_portal.tools import policy as policy_tool
from voice_portal.tools.project import load_records, project_tool, rank
from voice_portal.tools.scripture import scripture_tool
from voice_portal.tools.spoken import clean_spoken, normalize_spoken_reference, spoken_reference, words_to_numbers
from voice_portal.tools.work import work_tool

FIXTURE_RECORDS = Path(__file__).resolve().parent / "fixtures" / "project_work"

PSALM_23 = [
    "A Psalm by David. Yahweh is my shepherd; I shall lack nothing.",
    "He makes me lie down in green pastures. He leads me beside still waters.",
    "He restores my soul. He guides me in the paths of righteousness for his name’s sake.",
    "Even though I walk through the valley of the shadow of death, I will fear no evil, for you are with me. Your rod and your staff, they comfort me.",
    "You prepare a table before me in the presence of my enemies. You anoint my head with oil. My cup runs over.",
    "Surely goodness and loving kindness shall follow me all the days of my life, and I will dwell in Yahweh’s house forever.",
]
ROMANS_8 = numbered("Romans 8", 39, {
    28: "We know that all things work together for good for those who love God, for those who are called according to his purpose.",
    29: "For whom he foreknew, he also predestined to be conformed to the image of his Son, that he might be the firstborn among many brothers.",
    30: "Whom he predestined, those he also called. Whom he called, those he also justified. Whom he justified, those he also glorified.",
})
JOHN_3 = numbered("John 3", 36, {16: "For God so loved the world, that he gave his only born Son, that whoever believes in him should not perish, but have eternal life."})

# Held-out project descriptions, written from the register v2 records before the scorer was run.
HELD_OUT = [
    ("the scripture enrichment foundation with the reference grammar and the concordance", "scripture-enrichment-c15"),
    ("check on the steward thing that tracks idle sparks and cost", "steward-reference"),
    ("the prayer life project from cycle twelve with the pairing ledger", "prayer-atlas-c8"),
    ("the inference supervisors that run qwen on the two sparks", "spark-inference"),
    ("the voice portal on my iPhone", "rlm-adk-ios"),
]


def no_markup(test: unittest.TestCase, spoken: str) -> None:
    test.assertNotRegex(spoken, r"\[\d")
    test.assertNotRegex(spoken, r"[*_`#]")


class SpokenTests(unittest.TestCase):
    def test_number_words(self):
        self.assertEqual(words_to_numbers("Psalm one hundred nineteen"), "Psalm 119")
        self.assertEqual(words_to_numbers("John three sixteen"), "John 3 16")
        self.assertEqual(words_to_numbers("twenty-three and a hundred and fifty"), "23 and 150")
        self.assertEqual(words_to_numbers("verses one-three"), "verses 1-3")
        self.assertEqual(words_to_numbers("someone said nothing"), "someone said nothing")

    def test_spoken_reference_forms(self):
        cases = {
            "John chapter three verse sixteen": "John 3:16",
            "John three sixteen": "John 3:16",
            "Romans eight, verses twenty eight through thirty": "Romans 8:28-30",
            "Psalm twenty-three": "Psalm 23",
            "first Peter five seven": "first Peter 5:7",
            "Romans 8:28-30": "Romans 8:28-30",
            "Genesis chapter one verses one to five": "Genesis 1:1-5",
        }
        for spoken, written in cases.items():
            self.assertEqual(normalize_spoken_reference(spoken), written, spoken)

    def test_outbound_forms(self):
        self.assertEqual(spoken_reference("Psalms", 23), "Psalm 23")
        self.assertEqual(spoken_reference("Romans", 8, 28, 30), "Romans 8, verses 28 to 30")
        self.assertEqual(spoken_reference("John", 3, 16, 16), "John 3, verse 16")
        self.assertEqual(clean_spoken("**Rest** [1] is a gift [2, 3]. plan_organize_prayers"), "Rest is a gift. plan organize prayers")


class ScriptureToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_config(Path(self.tmp.name))
        self.conn = connect(self.cfg.db_path)
        import_chapters(self.conn, {("PSA", 23): PSALM_23, ("ROM", 8): ROMANS_8, ("JHN", 3): JOHN_3})

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_whole_chapter_is_verbatim(self):
        out = scripture_tool(self.conn, "Psalm 23")
        self.assertTrue(out["available"])
        self.assertEqual(out["coverage"], "full")
        self.assertEqual(out["spoken"], " ".join(PSALM_23))
        self.assertEqual([v["verse"] for v in out["verses"]], [1, 2, 3, 4, 5, 6])
        self.assertTrue(out["cite_ids"])
        cited = " ".join(r["text"] for r in (self.conn.execute("SELECT text FROM chunks WHERE doc_id = ?", (int(c.split(":")[0]),)).fetchone() for c in out["cite_ids"]))
        for verse in out["verses"]:
            self.assertIn(verse["text"], cited)  # every verse lies inside a cited chunk

    def test_verse_range_and_spoken_form(self):
        out = scripture_tool(self.conn, "Romans 8:28-30")
        self.assertEqual(out["spoken"], " ".join(ROMANS_8[27:30]))
        self.assertEqual([v["osis"] for v in out["verses"]], ["Rom.8.28", "Rom.8.29", "Rom.8.30"])
        spoken = scripture_tool(self.conn, "John chapter three verse sixteen")
        self.assertEqual(spoken["normalized"], "John 3:16")
        self.assertEqual(spoken["spoken"], JOHN_3[15])
        self.assertEqual(spoken["reference"], "John 3:16")

    def test_partial_unknown_unretained_invalid(self):
        partial = scripture_tool(self.conn, "Psalm 23:5-9")
        self.assertTrue(partial["available"])
        self.assertEqual(partial["coverage"], "partial")
        self.assertEqual(partial["spoken"], " ".join(PSALM_23[4:6]))
        self.assertIn("7, 8, 9", partial["spoken_notice"])
        unknown = scripture_tool(self.conn, "Hezekiah 3")
        self.assertFalse(unknown["available"])
        self.assertEqual(unknown["reason"], "no_reference_recognised")
        self.assertTrue(unknown["spoken"])
        missing = scripture_tool(self.conn, "Genesis 50")
        self.assertFalse(missing["available"])
        self.assertEqual(missing["reason"], "chapter_not_in_retained_edition")
        self.assertIn("Genesis 50", missing["spoken"])
        self.assertEqual(missing["verses"], [])
        invalid = scripture_tool(self.conn, "Romans 8:50")
        self.assertFalse(invalid["available"])
        self.assertIn("no such verse", invalid["spoken"])


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_config(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults(self):
        policy = policy_tool.load_policy(self.cfg)
        self.assertEqual(policy["amen_silence_s"], 1.2)
        self.assertIn("quiet please", policy["quiet_phrases"])
        self.assertIn("wake up", policy["wake_phrases"])
        self.assertIn("mm-hmm", policy["backchannel_words"])
        self.assertIn("Lord", policy["address_phrases"])
        self.assertEqual(policy["thresholds"]["project_confidence_ratio"], 1.5)
        self.assertFalse(policy["source"]["present"])
        self.assertEqual(len(policy["version"]), 16)

    def test_overrides_merge_and_bad_types_are_rejected(self):
        (self.cfg.root / "policy.json").write_text(json.dumps({"amen_silence_s": 1.5, "quiet_phrases": "quiet", "thresholds": {"bridge_after_ms": 400, "nope": 1}, "extra": True}))
        policy = policy_tool.load_policy(self.cfg)
        self.assertEqual(policy["amen_silence_s"], 1.5)
        self.assertEqual(policy["thresholds"]["bridge_after_ms"], 400)
        self.assertEqual(policy["thresholds"]["barge_in_cut_ms"], 120)  # untouched default
        self.assertIsInstance(policy["quiet_phrases"], list)
        rejected = {r["key"] for r in policy["source"]["rejected"]}
        self.assertEqual(rejected, {"quiet_phrases", "thresholds.nope", "extra"})
        self.assertNotEqual(policy["version"], policy_tool.load_policy(make_config(Path(self.tmp.name) / "other"))["version"])

    def test_unreadable_file_serves_defaults(self):
        (self.cfg.root / "policy.json").write_text("{not json")
        policy = policy_tool.load_policy(self.cfg)
        self.assertEqual(policy["amen_silence_s"], 1.2)
        self.assertTrue(policy["source"]["error"])


class ProjectToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_config(Path(self.tmp.name))
        self.cfg.project_register_records = str(FIXTURE_RECORDS)

    def tearDown(self):
        self.tmp.cleanup()

    def test_held_out_descriptions_top1_at_least_four_of_five(self):
        records = load_records(FIXTURE_RECORDS)
        self.assertEqual(len(records), 16)
        tops = [(rank(records, text) or [{"project_id": None}])[0]["project_id"] for text, _ in HELD_OUT]
        correct = sum(top == want for top, (_, want) in zip(tops, HELD_OUT))
        self.assertGreaterEqual(correct, 4, list(zip(tops, [w for _, w in HELD_OUT])))

    def test_only_v2_records_are_read(self):
        directory = Path(self.tmp.name) / "mixed"
        directory.mkdir()
        (directory / "old.json").write_text(json.dumps({"schema": "rlm-adk.project-work-record.v1", "project_id": "old", "title": "Old"}))
        (directory / "new.json").write_text((FIXTURE_RECORDS / "rlm-adk-ios.json").read_text())
        self.assertEqual([r["project_id"] for r in load_records(directory)], ["rlm-adk-ios"])

    def test_confident_report_names_the_five_parts_in_order(self):
        out = project_tool(self.cfg, HELD_OUT[0][0])
        self.assertTrue(out["confident"])
        self.assertEqual(out["candidates"][0]["project_id"], "scripture-enrichment-c15")
        self.assertLessEqual(len(out["candidates"]), 3)
        spoken = out["spoken"]
        positions = [spoken.index(word) for word in ("Disposition:", "Need:", "Products:", "Consumers:", "Evidence:")]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Disposition: consolidate, into prayer and word.", spoken)
        self.assertIn("Consumers: none recorded.", spoken)
        self.assertIn("Evidence: tested.", spoken)
        self.assertEqual(set(out["report"]["axes"]), {"disposition", "need", "products", "consumers", "evidence"})
        self.assertEqual(out["report"]["axes"]["disposition"]["successors"], ["prayer-and-word"])
        no_markup(self, spoken)

    def test_archived_repository_found_by_an_old_pointer_name(self):
        out = project_tool(self.cfg, "", confirm="prayer-atlas-c8")
        report = out["report"]
        self.assertIn("prayer-life-c12", report["host_pointer_ids"])
        self.assertTrue(report["spoken"].startswith("prayer-atlas-c8: prayer lineage c8 to c12"))
        self.assertIn("Disposition: archived, into prayer and word.", report["spoken"])
        self.assertIn("Consumers: 1 confirmed, rlm adk ios voice portal tools word; 1 hypothetical", report["spoken"])
        self.assertIn("Products: produces scripture pairing ledger and prayer enrichment; reads prayer corpus.", report["spoken"])

    def test_unclear_match_asks_to_confirm_then_confirm_reports(self):
        out = project_tool(self.cfg, "the scripture project")
        self.assertFalse(out["confident"])
        self.assertNotIn("report", out)
        self.assertTrue(out["spoken"].startswith("Do you mean"))
        self.assertTrue(out["spoken"].rstrip().endswith(".") and "?" in out["spoken"])
        best = out["confirm_project_id"]
        self.assertIn(re.sub(r"\s*\([^)]*\)", "", out["candidates"][0]["title"]).strip(), out["spoken"])
        confirmed = project_tool(self.cfg, "the scripture project", confirm=best)
        self.assertTrue(confirmed["confirmed"])
        self.assertEqual(confirmed["report"]["project_id"], best)
        self.assertIn("Disposition:", confirmed["spoken"])
        self.assertIn("do not have a project", project_tool(self.cfg, "x", confirm="no-such-project")["spoken"])

    def test_ratio_comes_from_policy_and_missing_register(self):
        (self.cfg.root / "policy.json").write_text(json.dumps({"thresholds": {"project_confidence_ratio": 100.0}}))
        self.assertFalse(project_tool(self.cfg, HELD_OUT[0][0])["confident"])
        self.cfg.project_register_records = str(Path(self.tmp.name) / "absent")
        out = project_tool(self.cfg, "anything")
        self.assertFalse(out["available"])
        self.assertTrue(out["spoken"])


class WorkToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.cfg = make_config(tmp)
        stub = Path(self.cfg.allocatorctl)
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        self.inbox = tmp / "stub_inbox.jsonl"
        os.environ["STUB_INBOX"] = str(self.inbox)
        self.conn = connect(self.cfg.db_path)

    def tearDown(self):
        os.environ.pop("STUB_MODE", None)
        os.environ.pop("STUB_INBOX", None)
        self.conn.close()
        self.tmp.cleanup()

    def test_posted_is_accepted_not_done(self):
        os.environ["STUB_MODE"] = "ok"
        out = work_tool(self.conn, self.cfg, "Find what Edwards says about affections", "research")
        self.assertEqual(out["status"], "directive_posted")
        self.assertTrue(out["message_id"].startswith("m"))
        self.assertFalse(out["completed"])
        self.assertEqual(out["spoken"], f"Accepted as operation {out['operation_id'][:8]}. It is not done; I will say when there is a result.")
        self.assertIn(out["operation_id"], self.inbox.read_text())
        again = work_tool(self.conn, self.cfg, "Find what Edwards says about affections", operation_id=out["operation_id"])
        self.assertEqual(len(self.inbox.read_text().splitlines()), 1)  # a repeat never posts twice
        self.assertIn(out["operation_id"][:8], again["spoken"])

    def test_refusal_is_spoken_verbatim(self):
        os.environ["STUB_MODE"] = "refuse"
        out = work_tool(self.conn, self.cfg, "Summarise this week's prayers")
        self.assertEqual(out["status"], "backend_unavailable")
        self.assertIn("ServiceControlRefused: the allocator service has no live run; start it first", out["spoken"])
        self.assertIn(out["short_id"], out["spoken"])
        self.assertIn("It is not done", out["spoken"])
        self.assertFalse(out["completed"])
        for word in ("completed", "finished", "done."):
            self.assertNotIn(word, out["spoken"].replace("It is not done.", ""))

    def test_empty_request_is_rejected(self):
        out = work_tool(self.conn, self.cfg, "  ")
        self.assertEqual(out["status"], "rejected")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM work_operations").fetchone()[0], 0)


class ToolRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.cfg = make_config(tmp)
        self.cfg.listen_host, self.cfg.listen_port = "127.0.0.1", 0
        self.cfg.project_register_records = str(FIXTURE_RECORDS)
        self.cfg.inference_base_url = "http://127.0.0.1:9/v1"
        self.cfg.inference_status_url = ""
        stub = Path(self.cfg.allocatorctl)
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        os.environ["STUB_INBOX"] = str(tmp / "stub_inbox.jsonl")
        os.environ["STUB_MODE"] = "refuse"
        conn = connect(self.cfg.db_path)
        import_chapters(conn, {("PSA", 23): PSALM_23, ("ROM", 8): ROMANS_8})
        conn.close()
        self.server = make_server(self.cfg)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        os.environ.pop("STUB_MODE", None)
        os.environ.pop("STUB_INBOX", None)
        self.tmp.cleanup()

    def _get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_routes(self):
        status, policy = self._get("/policy")
        self.assertEqual((status, policy["amen_silence_s"]), (200, 1.2))
        status, scripture = self._get("/tools/scripture?reference=Psalm%2023")
        self.assertEqual(status, 200)
        self.assertEqual(scripture["spoken"], " ".join(PSALM_23))
        status, spoken_form = self._get("/tools/scripture?reference=Romans%20eight%20verses%20twenty%20eight%20to%20thirty")
        self.assertEqual(spoken_form["reference"], "Romans 8:28-30")
        status, missing = self._get("/tools/scripture")
        self.assertEqual(status, 400)
        self.assertTrue(missing["spoken"])
        status, project = self._get("/tools/project?description=" + urllib.parse.quote(HELD_OUT[0][0]))
        self.assertEqual((status, project["candidates"][0]["project_id"]), (200, "scripture-enrichment-c15"))
        status, interp = self._get("/tools/interpretations")
        self.assertEqual(status, 400)
        status, interp = self._get("/tools/interpretations?reference=Romans%208%3A28&topic=suffering")
        self.assertEqual(status, 200)
        self.assertEqual(interp["scripture"]["reference"], "Romans 8:28")
        self.assertIn("did not find a teacher", interp["spoken"])  # no teaching projected in this fixture
        status, word_reply = self._get("/tools/word?which=latest")
        self.assertEqual((status, word_reply["available"]), (200, False))
        self.assertTrue(word_reply["spoken"])
        status, unknown = self._get("/tools/nothing")
        self.assertEqual(status, 404)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/tools/work", data=json.dumps({"request_text": "Gather sermons on rest", "kind": "research"}).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as response:
            work = json.loads(response.read())
            self.assertEqual(response.status, 201)
        self.assertEqual(work["status"], "backend_unavailable")
        self.assertFalse(work["completed"])
        for body in (policy, scripture, project, work):
            no_markup(self, body.get("spoken") or "")


if __name__ == "__main__":
    unittest.main()
