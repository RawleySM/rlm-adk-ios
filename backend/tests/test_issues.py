"""Intake issues log: a report is recorded at once with its verbatim words, triaged by one strict-JSON
reasoning call (keyword rules when the owner fails) into one of five kinds (the first spoken word,
then the caller's hint, then the model decide), folded from an append-only log, idempotent per
report id, and served over HTTP with the phone's feed."""

import json
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from tests.fakes import FakeInference
from tests.helpers import make_config
from voice_portal import issues
from voice_portal.server import make_server

REPORT = {
    "report_id": "r-1",
    "surface": "voice_portal",
    "started_at": 1000.0,
    "ended_at": 1030.0,
    "ended_by": "client",
    "transcript": {
        "segments": [
            {"t": 1003.0, "text": "When Romans 12 is read aloud the shading falls behind the voice.", "source": "voice"},
            {"t": 1010.0, "text": "By verse five it is two verses behind, every time.", "source": "voice"},
            {"t": 1015.0, "text": "I expected the highlighted verse to be the one I hear.", "source": "typed"},
        ],
        "device_segments": [{"t": 1004.0, "text": "when romans twelve is read"}],
        "asr": "cohere-transcribe",
    },
    "source": {"session_id": "ws-abc", "app_build": "17", "transport": "websocket", "audio_route": "bluetoothHFP"},
    "context": {"mode_before": "available", "passage": {"reference": "Romans 12", "reading": True, "spoken_count": 4}, "recent_turns": [{"role": "user", "text": "Read Romans 12."}]},
}

MODEL_TRIAGE = {
    "kind": "bug",
    "title": "Verse shading lags the reading voice in Romans 12",
    "summary": "While Romans 12 is read aloud the pane's shading falls behind the voice, two verses behind by verse five.",
    "observed": "Shading two verses behind the voice by verse five.",
    "expected": "The shaded verse is the one being heard.",
    "steps": ["Open Romans 12", "Ask for it to be read", "Watch the shading at verse five"],
    "component": "scripture_pane",
    "severity": "major",
    "flags": ["reproducible", "not_a_flag"],
    "needs_detail": False,
    "detail_questions": [],
}


def _wait(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class IssuesLog(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = make_config(self.tmp)
        self.inference = FakeInference(reply=json.dumps(MODEL_TRIAGE), delay=0, reasoning="")
        self.cfg.inference_base_url = self.inference.base_url

    def tearDown(self):
        self.inference.stop()

    def test_default_log_lives_under_the_state_root(self):
        self.assertEqual(issues.log_path(self.cfg), self.tmp / "state" / "intake" / "issues.jsonl")
        self.cfg.issues_log = str(self.tmp / "stores" / "intake" / "issues.jsonl")
        self.assertEqual(issues.log_path(self.cfg), self.tmp / "stores" / "intake" / "issues.jsonl")

    def test_report_is_recorded_verbatim_before_any_triage(self):
        ticket, created = issues.record_report(self.cfg, REPORT)
        self.assertTrue(created)
        self.assertRegex(ticket["ticket_id"], r"^bug-\d{8}-01$")
        self.assertEqual(ticket["status"], "open")
        self.assertTrue(ticket["text"].startswith("When Romans 12 is read aloud"))
        self.assertIn("I expected the highlighted verse", ticket["text"])
        self.assertEqual(ticket["triage"]["state"], "pending")
        self.assertEqual(ticket["triage"]["source"], "provisional_keywords")
        self.assertEqual(ticket["triage"]["component"], "scripture_pane")
        self.assertEqual(ticket["context"]["passage"]["reference"], "Romans 12")
        lines = issues.log_path(self.cfg).read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["event"], "reported")
        self.assertEqual(self.inference.requests, [], "recording never waits for the model")

    def test_repeat_report_id_writes_nothing(self):
        first, _ = issues.record_report(self.cfg, REPORT)
        again, created = issues.record_report(self.cfg, REPORT)
        self.assertFalse(created)
        self.assertEqual(again["ticket_id"], first["ticket_id"])
        self.assertEqual(len(issues.log_path(self.cfg).read_text().splitlines()), 1)
        second, _ = issues.record_report(self.cfg, {**REPORT, "report_id": "r-2"})
        self.assertTrue(second["ticket_id"].endswith("-02"))

    def test_model_triage_flags_sorts_and_dispositions(self):
        ticket, _ = issues.record_report(self.cfg, REPORT)
        triage = issues.triage_ticket(self.cfg, ticket["ticket_id"])
        self.assertEqual(triage["source"], "reasoning")
        self.assertEqual(triage["title"], "Verse shading lags the reading voice in Romans 12")
        self.assertEqual(triage["component"], "scripture_pane")
        self.assertEqual(triage["flags"], ["reproducible"], "off-list flags are dropped")
        self.assertEqual(triage["disposition"], "ready")
        self.assertEqual(triage["priority"], "P2")
        self.assertEqual(triage["route"], "intake")
        request = self.inference.requests[0]
        self.assertEqual(request["response_format"]["json_schema"]["name"], "issue_triage")
        self.assertIn("Romans 12 (being read aloud)", request["messages"][1]["content"])
        folded = issues.read_tickets(issues.log_path(self.cfg))[ticket["ticket_id"]]
        self.assertEqual(folded["triage"]["state"], "done")
        self.assertEqual(folded["events"], 2)
        self.assertIsNone(issues.triage_ticket(self.cfg, ticket["ticket_id"]), "a triaged ticket is not triaged twice")

    def test_owner_failure_falls_back_to_keywords(self):
        self.inference.status = 503
        ticket, _ = issues.record_report(self.cfg, REPORT)
        triage = issues.triage_ticket(self.cfg, ticket["ticket_id"])
        self.assertEqual(triage["source"], "fallback_keywords")
        self.assertTrue(triage["errors"])
        self.assertEqual(triage["component"], "scripture_pane")
        self.assertIn("reproducible", triage["flags"])
        self.assertEqual(triage["state"], "done")

    def test_nothing_said_is_kept_as_no_report(self):
        ticket, _ = issues.record_report(self.cfg, {"report_id": "empty", "transcript": {"segments": [{"t": 1, "text": "Um."}]}, "context": {"passage": {"reference": "John 1"}}})
        triage = issues.triage_ticket(self.cfg, ticket["ticket_id"])
        self.assertEqual(triage["disposition"], "no_report")
        self.assertEqual(triage["priority"], "P4")
        self.assertEqual(self.inference.requests, [], "no model call for an empty report")

    def test_phone_transcript_is_used_when_the_server_heard_nothing(self):
        ticket, _ = issues.record_report(self.cfg, {"report_id": "device", "transcript": {"segments": [], "device_segments": [{"text": "the pray button is hidden behind the keyboard"}]}})
        self.assertEqual(ticket["text"], "the pray button is hidden behind the keyboard")

    def test_dispositions_and_priorities(self):
        words = "the reading voice stops in the middle of every chapter now"
        self.assertEqual(issues.disposition_of({"kind": "bug", "severity": "minor", "flags": []}, words), ("ready", "P3"))
        self.assertEqual(issues.disposition_of({"kind": "bug", "severity": "minor", "flags": ["blocks_use"]}, words), ("ready", "P1"))
        self.assertEqual(issues.disposition_of({"kind": "feature_request", "severity": "minor", "flags": []}, words), ("request", "P3"))
        self.assertEqual(issues.disposition_of({"kind": "bug", "severity": "major", "needs_detail": True, "flags": []}, words), ("needs_detail", "P2"))
        for kind in ("feature_request", "message", "teaching", "project_proposal"):
            self.assertEqual(issues.disposition_of({"kind": kind, "severity": "minor", "needs_detail": True, "flags": []}, words)[0], "request", f"{kind}: no needs_detail gate")

    def test_first_spoken_word_names_the_kind(self):
        self.assertEqual(issues.spoken_kind("Teach: when I say amen twice, the prayer is over."), "teaching")
        self.assertEqual(issues.spoken_kind("Um, remember that I read the Psalms at night."), "teaching")
        self.assertEqual(issues.spoken_kind("Project. A map of the parts of my prayers."), "project_proposal")
        self.assertEqual(issues.spoken_kind("Proposal: a weekly testimony digest."), "project_proposal")
        self.assertEqual(issues.spoken_kind("Bug, the shading lags."), "bug")
        self.assertIsNone(issues.spoken_kind("The project page is blank."))
        self.assertIsNone(issues.spoken_kind(""))

    def test_kind_order_first_word_then_hint_then_model(self):
        self.inference.reply = json.dumps({**MODEL_TRIAGE, "kind": "message"})
        cases = [
            ("Remember that verse numbers are blue on purpose.", None, "teaching"),
            ("Remember that verse numbers are blue on purpose.", "project_proposal", "teaching"),
            ("Could the pane show two chapters side by side?", "feature_request", "feature_request"),
            ("Could the pane show two chapters side by side?", "not_a_kind", "message"),
            ("Could the pane show two chapters side by side?", None, "message"),
        ]
        for n, (text, hint, kind) in enumerate(cases):
            ticket, _ = issues.record_report(self.cfg, {"report_id": f"k-{n}", "kind": hint, "origin": "conversation", "transcript": {"segments": [{"t": 1, "text": text}]}})
            self.assertEqual(ticket["kind_hint"], hint if hint in issues.KINDS else None)
            self.assertEqual(ticket["origin"], "conversation")
            triage = issues.triage_ticket(self.cfg, ticket["ticket_id"])
            self.assertEqual(triage["kind"], kind, (text, hint))
            self.assertEqual(triage["disposition"], "request")
        self.assertIn("He sent it as a feature request.", self.inference.requests[2]["messages"][1]["content"])
        self.assertEqual(self.inference.requests[0]["response_format"]["json_schema"]["schema"]["properties"]["kind"]["enum"], issues.KINDS)

    def test_auto_escalation_records_origin_and_the_assistants_reply(self):
        report = {
            "report_id": "r-auto",
            "origin": "auto_escalation",
            "kind": "feature_request",
            "ended_by": "auto_escalation",
            "assistant_reply": "I can't set a timer from here.",
            "transcript": {"segments": [{"t": 1, "text": "Set a timer for ten minutes.", "source": "conversation"}]},
            "context": {"mode_before": "available", "node": "conversation", "recent_turns": [{"role": "user", "text": "Set a timer for ten minutes."}]},
        }
        ticket, created = issues.record_report(self.cfg, report)
        self.assertTrue(created)
        self.assertEqual((ticket["origin"], ticket["kind_hint"], ticket["assistant_reply"]), ("auto_escalation", "feature_request", "I can't set a timer from here."))
        self.assertEqual(ticket["context"]["node"], "conversation")
        self.assertEqual(issues.list_tickets(self.cfg)[0]["origin"], "auto_escalation")
        issues.triage_ticket(self.cfg, ticket["ticket_id"])
        prompt = self.inference.requests[0]["messages"][1]["content"]
        self.assertIn("could not meet this request; its reply was: I can't set a timer from here.", prompt)
        self.assertIn("He sent it as a feature request.", prompt)
        plain, _ = issues.record_report(self.cfg, {**report, "report_id": "r-plain", "origin": "conversation", "assistant_reply": None})
        self.assertIsNone(plain["assistant_reply"])
        folded = issues.read_tickets(issues.log_path(self.cfg))[ticket["ticket_id"]]
        self.assertEqual(folded["assistant_reply"], "I can't set a timer from here.")

    def test_keyword_rules_know_the_new_kinds(self):
        self.assertEqual(issues.keyword_triage("Please remember that I pray at six.")["kind"], "teaching")
        self.assertEqual(issues.keyword_triage("I want to start a new project about my prayer parts.")["kind"], "project_proposal")
        self.assertEqual(issues.keyword_triage("The reading voice stops mid chapter.", "message")["kind"], "message")
        self.assertEqual(issues.keyword_triage("Bug: the reading voice stops.", "message")["kind"], "bug")
        self.assertEqual(issues.keyword_triage("I'd like a larger font in the pane.")["kind"], "feature_request")

    def test_legacy_kinds_still_fold_and_list(self):
        ticket, _ = issues.record_report(self.cfg, REPORT)
        with issues._LogLock(issues.log_path(self.cfg)):
            issues._append(issues.log_path(self.cfg), {"schema": issues.SCHEMA, "event": "triaged", "ticket_id": ticket["ticket_id"], "at": 2.0, "triage": {**ticket["triage"], "kind": "feedback", "state": "done"}})
        self.assertEqual(issues.list_tickets(self.cfg)[0]["kind"], "feedback")
        self.assertEqual(issues.list_tickets(self.cfg)[0]["origin"], "bug_chip")

    def test_pending_tickets_are_triaged_again_at_start(self):
        ticket, _ = issues.record_report(self.cfg, REPORT)
        pending = issues.resume_pending(self.cfg)
        self.assertEqual(pending, [ticket["ticket_id"]])
        self.assertTrue(_wait(lambda: issues.read_tickets(issues.log_path(self.cfg))[ticket["ticket_id"]]["triage"]["state"] == "done"))
        self.assertEqual(issues.resume_pending(self.cfg), [])

    def test_torn_line_does_not_break_the_fold(self):
        issues.record_report(self.cfg, REPORT)
        with open(issues.log_path(self.cfg), "a") as fh:
            fh.write('{"schema": "intake-issue.v1", "event": "triaged", "ticket_id"\n')
        self.assertEqual(len(issues.read_tickets(issues.log_path(self.cfg))), 1)


class IssuesHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = make_config(self.tmp)
        self.inference = FakeInference(reply=json.dumps(MODEL_TRIAGE), delay=0, reasoning="")
        self.cfg.inference_base_url = self.inference.base_url
        self.cfg.inference_status_url = self.inference.base_url.replace("/v1", "/status")
        self.server = make_server(self.cfg)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.inference.stop()

    def _call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_post_list_get(self):
        status, body = self._call("POST", "/issues", REPORT)
        self.assertEqual(status, 201)
        self.assertTrue(body["created"])
        self.assertEqual(body["triage_state"], "pending")
        tid = body["ticket_id"]
        status, body = self._call("POST", "/issues", REPORT)
        self.assertEqual((status, body["created"], body["ticket_id"]), (200, False, tid))
        self.assertTrue(_wait(lambda: self._call("GET", f"/issues/{tid}")[1]["triage"]["state"] == "done"))
        status, ticket = self._call("GET", f"/issues/{tid}")
        self.assertEqual(ticket["triage"]["title"], MODEL_TRIAGE["title"])
        status, listing = self._call("GET", "/issues?status=open")
        self.assertEqual(listing["issues"][0]["ticket_id"], tid)
        self.assertEqual(listing["issues"][0]["priority"], "P2")
        self.assertEqual(self._call("GET", "/issues/bug-19990101-01")[0], 404)
        self.assertEqual(self._call("POST", "/issues", {"transcript": {}})[0], 400)

    def test_tools_work_returns_before_triage_and_lists_the_origin(self):
        report = {"report_id": "r-work-auto", "origin": "auto_escalation", "kind": "feature_request", "assistant_reply": "I cannot play music.", "transcript": {"segments": [{"t": 1, "text": "Play some worship music.", "source": "conversation"}]}, "context": {"node": "conversation", "mode_before": "available"}}
        self.inference.delay = 0.1  # per streamed word: a triage of a few seconds must not hold the acknowledgement
        started = time.monotonic()
        status, body = self._call("POST", "/tools/work", report)
        elapsed = time.monotonic() - started
        self.assertEqual((status, body["status"], body["kind_hint"]), (201, "recorded", "feature_request"))
        self.assertLess(elapsed, 0.9, f"/tools/work took {elapsed:.3f}s: it must return before the triage")
        self.assertEqual(self._call("GET", "/issues")[1]["issues"][0]["origin"], "auto_escalation")
        status, ticket = self._call("GET", f"/issues/{body['ticket_id']}")
        self.assertEqual(ticket["assistant_reply"], "I cannot play music.")
        # The slow triage finishes before the test ends, so no reader thread outlives it.
        self.assertTrue(_wait(lambda: self._call("GET", f"/issues/{body['ticket_id']}")[1]["triage"]["state"] == "done", timeout=30))

    def test_kind_hint_over_http_and_the_feed_route(self):
        status, body = self._call("POST", "/issues", {**REPORT, "report_id": "r-hint", "kind": "teaching"})
        self.assertEqual(status, 201)
        self.assertEqual(self._call("GET", f"/issues/{body['ticket_id']}")[1]["kind_hint"], "teaching")
        status, feed = self._call("GET", "/intake/feed")
        self.assertEqual((status, feed["items"]), (200, []))
        self.assertIsInstance(feed["now"], float)
        status, feed = self._call("GET", "/intake/feed?since=0&limit=5")
        self.assertEqual((status, feed["items"]), (200, []))


if __name__ == "__main__":
    unittest.main()
