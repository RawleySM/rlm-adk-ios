"""send_to_intake: Rawley's own last words (never the model's paraphrase) are filed with Intake through
the skill service's /tools/work, the skill service's acknowledgement is spoken and nothing more, a
request the desktop cannot take is spooled, and every node that answers requests offers the tool."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipecat.flows import NO_RESPONSE  # noqa: E402
from pipecat.frames.frames import TTSSpeakFrame  # noqa: E402

from talk import bug_report, flows, tools  # noqa: E402
from talk.config import TalkConfig  # noqa: E402
from talk.floor import FloorProcessor  # noqa: E402

CONTEXT = [
    {"role": "system", "content": "role"},
    {"role": "user", "content": "Read Psalm 23."},
    {"role": "assistant", "content": "Psalm 23."},
    {"role": "user", "content": "(I opened Romans 12 in the scripture pane.)"},
    {"role": "user", "content": "Tell Intake I want a map of the parts of my prayers,"},
    {"role": "user", "content": [{"type": "text", "text": "one line per part."}]},
    {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "function": {"name": "send_to_intake", "arguments": "{\"kind\": \"project_proposal\"}"}}]},
]


class FakeContext:
    def __init__(self, messages):
        self.messages = messages

    def get_messages(self):
        return list(self.messages)


class FakeTask:
    def __init__(self):
        self.frames = []

    async def queue_frames(self, frames):
        self.frames.extend(frames)


class FakeFlow:
    def __init__(self, floor):
        self.state = {"reading": floor.reading_state, "floor": floor}
        self.task = FakeTask()
        self.current_node = "conversation"


class IntakeWords(unittest.TestCase):
    def test_the_users_messages_since_the_assistant_last_spoke(self):
        self.assertEqual(tools.intake_words(CONTEXT), "Tell Intake I want a map of the parts of my prayers, one line per part.")

    def test_tool_results_and_bridge_free_assistant_turns_are_passed_over(self):
        messages = CONTEXT + [{"role": "tool", "content": "{}", "tool_call_id": "c1"}]
        self.assertEqual(tools.intake_words(messages), "Tell Intake I want a map of the parts of my prayers, one line per part.")

    def test_fallback_and_limits(self):
        self.assertEqual(tools.intake_words([{"role": "assistant", "content": "Psalm 23."}], fallback="remember that"), "remember that")
        self.assertEqual(tools.intake_words([]), "")
        many = [{"role": "user", "content": f"part {n}"} for n in range(10)]
        self.assertEqual(tools.intake_words(many, max_messages=3), "part 7 part 8 part 9")
        self.assertEqual(len(tools.intake_words([{"role": "user", "content": "x" * 9000}])), 4000)


class SendToIntake(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg = TalkConfig(state_root=tempfile.mkdtemp())
        tools.configure(self.cfg)
        self.floor = FloorProcessor(self.cfg, tools.new_reading_state(), session_id="webrtc-test")
        self.floor.context = FakeContext(CONTEXT)
        self.flow = FakeFlow(self.floor)
        self.filed = []
        self.reply = {"filed": True, "ticket_id": "bug-20261017-01", "status": "recorded", "spoken": "Sent to Intake."}
        self._saved = bug_report.file_report

        async def file_report(cfg, payload, route="/issues"):
            self.filed.append((route, payload))
            return self.reply

        bug_report.file_report = file_report

    async def asyncTearDown(self):
        bug_report.file_report = self._saved

    def spoken(self):
        return [f.text for f in self.flow.task.frames if isinstance(f, TTSSpeakFrame)]

    async def test_files_his_words_and_says_only_the_acknowledgement(self):
        result, next_node = await tools.send_to_intake({"kind": "project_proposal"}, self.flow)
        self.assertIs(next_node, NO_RESPONSE)
        route, payload = self.filed[0]
        self.assertEqual(route, "/tools/work")
        self.assertEqual(payload["transcript"]["segments"][0]["text"], "Tell Intake I want a map of the parts of my prayers, one line per part.")
        self.assertEqual((payload["kind"], payload["origin"], payload["source"]), ("project_proposal", "conversation", {"session_id": "webrtc-test"}))
        self.assertTrue(payload["report_id"])
        self.assertIn("recent_turns", payload["context"])
        self.assertEqual(self.spoken(), ["Sent to Intake."])
        self.assertEqual((result["ticket_id"], result["text"]), ("bug-20261017-01", "Sent to Intake."))

    async def test_the_skill_services_words_are_spoken_verbatim(self):
        self.reply = {**self.reply, "spoken": "Saved for Intake. Mail to Intake is on hold, so it has not been sent yet."}
        await tools.send_to_intake({"kind": "message"}, self.flow)
        self.assertEqual(self.spoken(), ["Saved for Intake. Mail to Intake is on hold, so it has not been sent yet."])

    async def test_a_spooled_request_says_so(self):
        self.reply = {"filed": False, "spooled": "/x.json", "error": "HTTP 503"}
        await tools.send_to_intake({"kind": "teaching"}, self.flow)
        self.assertEqual(self.spoken(), [tools.SPOOLED_SPOKEN])

    async def test_an_unknown_kind_is_no_hint(self):
        await tools.send_to_intake({"kind": "research"}, self.flow)
        self.assertIsNone(self.filed[0][1]["kind"])

    async def test_nothing_to_send_asks_instead_of_filing(self):
        self.floor.context = FakeContext([{"role": "assistant", "content": "Psalm 23."}])
        result, next_node = await tools.send_to_intake({"kind": "message"}, self.flow)
        self.assertIsNone(next_node)
        self.assertIn("instruction", result)
        self.assertEqual(self.filed, [])
        self.assertEqual(self.spoken(), [])

    async def test_spoken_requests_fall_back_to_the_last_forwarded_utterance(self):
        self.floor.context = None
        self.floor.reading_state["last_user_text"] = "Remember that I read the Psalms at night."
        await tools.send_to_intake({"kind": "teaching"}, self.flow)
        self.assertEqual(self.filed[0][1]["transcript"]["segments"][0]["text"], "Remember that I read the Psalms at night.")


class Offered(unittest.TestCase):
    def test_every_answering_node_offers_send_to_intake(self):
        for name in ("available", "conversation", "devotional", "project"):
            names = [f.name for f in flows.node(name)["functions"]]
            self.assertIn("send_to_intake", names, name)
            self.assertNotIn("request_work", names, name)
        for name in ("prayer", "quiet"):
            self.assertEqual(flows.node(name)["functions"], [], name)
        schema = tools.SCHEMAS["send_to_intake"]
        self.assertEqual(schema.required, ["kind"])
        self.assertEqual(schema.properties["kind"]["enum"], list(tools.INTAKE_KINDS))


if __name__ == "__main__":
    unittest.main()
