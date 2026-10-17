"""Bug mode on the floor: output silenced, every transcript kept for the report and never a turn,
no phrase acts while reporting, filing returns to the mode before and resumes an interrupted reading,
and a report the skill service cannot take is spooled and retried."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipecat.frames.frames import (  # noqa: E402
    InterimTranscriptionFrame,
    LLMMessagesAppendFrame,
    OutputTransportMessageUrgentFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

from talk import bug_report, passage  # noqa: E402
from talk.config import TalkConfig  # noqa: E402
from talk.floor import FloorProcessor, OutputGate  # noqa: E402
from talk.scripture_tts import ScriptureSpeakFrame  # noqa: E402
from talk.tools import new_reading_state  # noqa: E402

ROMANS_12 = {
    "available": True,
    "reference": "Romans 12",
    "title": "Romans 12 (World English Bible)",
    "references": [{"book": "ROM", "chapter": 12}],
    "verses": [{"verse": n, "text": f"Verse {n} text.", "osis": f"Rom.12.{n}"} for n in range(1, 22)],
}


class FakeContext:
    def get_messages(self):
        return [
            {"role": "system", "content": "role"},
            {"role": "user", "content": "Read Romans 12."},
            {"role": "assistant", "content": [{"type": "text", "text": "Romans 12."}]},
            {"role": "tool", "content": "{}"},
        ]


class BugFloor(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = TalkConfig(state_root=str(self.tmp))
        self.state = new_reading_state()
        passage.apply(self.state, passage.from_tool_result(ROMANS_12, "Romans 12"), source="client")
        self.floor = FloorProcessor(self.cfg, self.state, session_id="test-1")
        self.floor.context = FakeContext()
        self.pushed = []
        self.interruptions = 0
        self.filed = []

        async def push_frame(frame, direction=FrameDirection.DOWNSTREAM):
            self.pushed.append(frame)

        async def interrupt():
            self.interruptions += 1
            self.state["reading"] = False
            self.floor.interruption_seen.set()

        async def file_report(cfg, payload):
            self.filed.append(payload)
            return {"filed": True, "ticket_id": "bug-20261015-01", "title": "Shading lags", "created": True}

        async def wait_for_triage(cfg, ticket_id, **kw):
            return {"ticket_id": ticket_id, "triage": {"state": "done", "title": "Verse shading lags the voice", "kind": "bug", "component": "scripture_pane", "severity": "major", "priority": "P2", "disposition": "ready"}}

        self.floor.push_frame = push_frame
        self.floor.broadcast_interruption = interrupt
        self._saved = (bug_report.file_report, bug_report.wait_for_triage)
        bug_report.file_report = file_report
        bug_report.wait_for_triage = wait_for_triage

    async def asyncTearDown(self):
        bug_report.file_report, bug_report.wait_for_triage = self._saved

    def client_messages(self, kind=None):
        out = [f.message for f in self.pushed if isinstance(f, OutputTransportMessageUrgentFrame)]
        return [m for m in out if kind is None or m.get("type") == kind]

    async def say(self, text, final=True):
        cls = TranscriptionFrame if final else InterimTranscriptionFrame
        await self.floor.process_frame(cls(text, "u", "2026-10-15T00:00:00Z"), FrameDirection.DOWNSTREAM)

    async def test_start_silences_and_announces(self):
        self.state["reading"] = True
        report = await self.floor.start_bug(source={"app_build": "17", "nested": {"x": 1}}, reason="client")
        self.assertEqual(self.floor.mode, "bug")
        self.assertEqual(self.interruptions, 1)
        self.assertTrue(report.resume_reading)
        self.assertEqual(report.source, {"session_id": "test-1", "app_build": "17"})
        self.assertEqual(report.context["passage"]["reference"], "Romans 12")
        self.assertEqual(report.context["recent_turns"], [{"role": "user", "text": "Read Romans 12."}, {"role": "assistant", "text": "Romans 12."}])
        self.assertEqual(self.client_messages("mode")[-1]["mode"], "bug")
        self.assertEqual(self.client_messages("bug_report")[-1]["state"], "started")
        again = await self.floor.start_bug()
        self.assertIs(again, report)

    async def test_transcripts_become_the_report_never_a_turn(self):
        await self.floor.start_bug()
        self.pushed.clear()
        await self.say("The shading is behind the voice", final=False)
        await self.say("The shading is behind the voice.")
        await self.say("Lord, quiet please, wake up, read it.")
        self.assertEqual(self.floor.mode, "bug", "no phrase acts while reporting")
        self.assertEqual([s["text"] for s in self.floor.bug.segments], ["The shading is behind the voice.", "Lord, quiet please, wake up, read it."])
        finals = [f for f in self.pushed if isinstance(f, TranscriptionFrame)]
        self.assertTrue(finals and all(f.text == "" for f in finals), "only empty finalized transcripts reach the aggregator")
        texts = self.client_messages("bug_text")
        self.assertEqual([(t["final"], t["source"]) for t in texts], [(False, "voice"), (True, "voice"), (True, "voice")])
        self.assertFalse(any(isinstance(f, ScriptureSpeakFrame) for f in self.pushed))

    async def test_typed_text_is_part_of_the_report(self):
        await self.floor.start_bug()
        await self.floor.bug_typed("It happens at verse five.")
        self.assertEqual(self.floor.bug.segments[-1]["source"], "typed")

    async def test_mode_requests_while_reporting_set_the_return_mode(self):
        await self.floor.start_bug()
        await self.floor.set_mode("quiet", "client:sync")
        self.assertEqual(self.floor.mode, "bug")
        await self.floor.stop_bug()
        self.assertEqual(self.floor.mode, "quiet")

    async def test_stop_files_returns_and_resumes_the_reading(self):
        self.state["reading"] = True
        self.state["spoken_count"] = 4
        await self.floor.start_bug()
        await self.say("The shading is two verses behind by verse five.")
        message = await self.floor.stop_bug(device_segments=["the shading is two verses"], source={"audio_route": "bluetoothHFP"})
        await asyncio.sleep(0)
        self.assertEqual(message["state"], "filed")
        self.assertEqual(message["ticket_id"], "bug-20261015-01")
        self.assertEqual(self.floor.mode, "available")
        payload = self.filed[0]
        self.assertEqual(payload["transcript"]["segments"][0]["text"], "The shading is two verses behind by verse five.")
        self.assertEqual(payload["transcript"]["device_segments"], [{"text": "the shading is two verses"}])
        self.assertEqual(payload["source"]["audio_route"], "bluetoothHFP")
        self.assertEqual(payload["context"]["passage"], {"reference": "Romans 12", "reading": True, "spoken_count": 4, "verse_count": 21})
        self.assertEqual(payload["ended_by"], "client")
        notes = [f for f in self.pushed if isinstance(f, LLMMessagesAppendFrame)]
        self.assertIn("bug-20261015-01", notes[-1].messages[0]["content"])
        spoken = [f.text for f in self.pushed if isinstance(f, ScriptureSpeakFrame)]
        self.assertEqual(spoken[0], "Verse 5 text.", "the reading resumes after the verses heard")
        states = [m["state"] for m in self.client_messages("bug_report")]
        self.assertEqual(states[:4], ["started", "finishing", "filing", "filed"])
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertEqual(self.client_messages("bug_report")[-1]["state"], "triaged")
        self.assertEqual(self.client_messages("bug_report")[-1]["priority"], "P2")
        self.assertIsNone(await self.floor.stop_bug(), "nothing open to stop")

    async def test_chip_kind_hint_travels_with_the_report(self):
        await self.floor.client_bug({"type": "client_bug", "action": "start", "kind": "teaching", "app_build": "28"})
        await self.say("Remember that I read the Psalms at night.")
        message = await self.floor.stop_bug()
        self.assertEqual(message["state"], "filed")
        self.assertEqual((self.filed[0]["kind"], self.filed[0]["origin"]), ("teaching", "bug_chip"))
        await self.floor.start_bug()
        await self.floor.stop_bug()
        self.assertIsNone(self.filed[1]["kind"], "no hint: triage decides")

    async def test_failed_filing_is_reported_as_spooled(self):
        async def spooled(cfg, payload):
            return {"filed": False, "spooled": "/x.json", "error": "HTTP 503"}

        bug_report.file_report = spooled
        await self.floor.start_bug()
        message = await self.floor.stop_bug()
        self.assertEqual(message["state"], "spooled")
        self.assertEqual(self.floor.mode, "available")
        self.assertFalse([f for f in self.pushed if isinstance(f, LLMMessagesAppendFrame)])

    async def test_cancel_files_nothing(self):
        await self.floor.start_bug()
        await self.say("never mind")
        await self.floor.cancel_bug()
        self.assertEqual(self.filed, [])
        self.assertEqual(self.floor.mode, "available")
        self.assertEqual(self.client_messages("bug_report")[-1]["state"], "cancelled")

    async def test_reading_is_blocked_while_reporting(self):
        await self.floor.start_bug()
        self.assertFalse(await self.floor.read_active(reason="test"))
        self.assertEqual(self.client_messages("reading")[-1]["state"], "blocked")

    async def test_session_end_files_what_was_heard(self):
        await self.floor.start_bug()
        await self.say("The app froze.")
        result = await self.floor.abandon_bug()
        self.assertTrue(result["filed"])
        self.assertEqual(self.filed[0]["ended_by"], "disconnect")
        self.assertIsNone(self.floor.bug)

    async def test_output_gate_drops_speech_while_reporting(self):
        gate = OutputGate(self.floor)
        passed = []

        async def push_frame(frame, direction=FrameDirection.DOWNSTREAM):
            passed.append(frame)

        gate.push_frame = push_frame
        await self.floor.start_bug()
        await gate.process_frame(TTSAudioRawFrame(b"\0\0" * 240, 24000, 1), FrameDirection.DOWNSTREAM)
        self.assertEqual(passed, [])


class Spool(unittest.IsolatedAsyncioTestCase):
    async def test_failed_post_is_spooled_then_flushed(self):
        cfg = TalkConfig(state_root=tempfile.mkdtemp())
        saved = bug_report.post_report
        calls = []

        async def down(cfg, payload):
            calls.append(payload["report_id"])
            raise RuntimeError("skill service HTTP 503")

        async def up(cfg, payload):
            calls.append(payload["report_id"])
            return {"ticket_id": "bug-20261015-07", "created": True}

        try:
            bug_report.post_report = down
            report = bug_report.BugReport()
            report.add("The pane is blank.", "voice")
            result = await bug_report.file_report(cfg, report.payload(ended_by="client", asr="cohere"))
            self.assertFalse(result["filed"])
            spooled = Path(result["spooled"])
            self.assertEqual(json.loads(spooled.read_text())["transcript"]["segments"][0]["text"], "The pane is blank.")
            bug_report.post_report = up
            self.assertEqual(await bug_report.flush_spool(cfg), ["bug-20261015-07"])
            self.assertFalse(spooled.exists())
        finally:
            bug_report.post_report = saved
            if bug_report._retry_task is not None:
                bug_report._retry_task.cancel()


if __name__ == "__main__":
    unittest.main()
