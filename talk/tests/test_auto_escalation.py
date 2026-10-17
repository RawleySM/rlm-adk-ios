"""The escalation safety net (talk/escalation.py), independent of the prompt: a reply that says the
request cannot be met files Rawley's verbatim words with Intake (origin auto_escalation, the reply and
the moment's context on the report), speaks one line once, and never fires in prayer/quiet/bug, in a
tool turn, after an interruption, twice for one user turn, or with the flag off."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipecat.frames.frames import (  # noqa: E402
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

from talk import bug_report, escalation, tools  # noqa: E402
from talk.config import TalkConfig  # noqa: E402
from talk.escalation import ESCALATED_SPOKEN, EscalationWatch, cannot_meet, is_question, kind_hint_for  # noqa: E402
from talk.floor import FloorProcessor  # noqa: E402

CANNOT = [
    "I can't set a timer from here.",
    "I cannot send text messages.",
    "I'm unable to open your calendar.",
    "I am not able to change the voice speed.",
    "I don't have access to your email.",
    "I don’t have a way to play music.",
    "That isn't something I can do yet.",
    "Searching the web is beyond what I can do here.",
    "I'm not set up to book flights.",
    "I have no way to check the weather.",
]
CAN = [
    "Psalm 23 is open in the pane; shall I read it?",
    "You can't go wrong with Romans 8.",
    "Paul writes, \"I can't do the good I want.\" That is Romans 7.",
    "Here is what Piper says about rest.",
    "He said, “I cannot come down,” and kept building the wall.",
    "",
]


class Patterns(unittest.TestCase):
    def test_cannot_phrases_match(self):
        for text in CANNOT:
            self.assertIsNotNone(cannot_meet(text), text)

    def test_answers_and_quoted_scripture_do_not_match(self):
        for text in CAN:
            self.assertIsNone(cannot_meet(text), text)

    def test_question_hint(self):
        self.assertTrue(is_question("What is the weather in Jacksonville?"))
        self.assertTrue(is_question("how many prayers did I record last week"))
        self.assertFalse(is_question("Can you add a dark mode."))
        self.assertFalse(is_question("Set a timer for ten minutes."))
        self.assertEqual(kind_hint_for("Why is the sky blue?"), "message")
        self.assertEqual(kind_hint_for("Play worship music."), "feature_request")


class FakeContext:
    def __init__(self, messages):
        self.messages = messages

    def get_messages(self):
        return list(self.messages)


class Watch(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg = TalkConfig(state_root=tempfile.mkdtemp())
        tools.configure(self.cfg)
        self.floor = FloorProcessor(self.cfg, tools.new_reading_state(), session_id="webrtc-esc")
        self.floor.context = FakeContext([{"role": "system", "content": "role"}, {"role": "user", "content": "Set a timer for ten minutes."}])
        self.floor.turn_serial = 1
        self.watch = EscalationWatch(self.cfg, self.floor)
        self.pushed = []

        async def push_frame(frame, direction=FrameDirection.DOWNSTREAM):
            self.pushed.append((frame, direction))

        self.watch.push_frame = push_frame
        self.filed = []
        self.reply = {"filed": True, "ticket_id": "bug-20261017-07", "status": "recorded", "spoken": "Sent to Intake."}
        self._saved = bug_report.file_report

        async def file_report(cfg, payload, route="/issues"):
            self.filed.append((route, payload))
            return self.reply

        bug_report.file_report = file_report

    async def asyncTearDown(self):
        bug_report.file_report = self._saved

    async def frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        await self.watch.process_frame(frame, direction)

    async def reply_turn(self, text):
        await self.frame(LLMFullResponseStartFrame())
        for piece in text.split(" "):
            await self.frame(LLMTextFrame(piece + " "))
        await self.frame(LLMFullResponseEndFrame())
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    def spoken(self):
        return [f.text for f, d in self.pushed if isinstance(f, TTSSpeakFrame)]

    def notes(self):
        return [m["content"] for f, d in self.pushed if isinstance(f, LLMMessagesAppendFrame) for m in f.messages]

    async def test_a_cannot_reply_files_his_words_and_speaks_once(self):
        await self.reply_turn("I can't set timers from here.")
        self.assertEqual(self.spoken(), [ESCALATED_SPOKEN])
        route, payload = self.filed[0]
        self.assertEqual(route, "/tools/work")
        self.assertEqual(payload["origin"], "auto_escalation")
        self.assertEqual(payload["kind"], "feature_request")
        self.assertEqual(payload["ended_by"], "auto_escalation")
        self.assertEqual(payload["transcript"]["segments"][0]["text"], "Set a timer for ten minutes.")
        self.assertEqual(payload["assistant_reply"], "I can't set timers from here.")
        self.assertEqual(payload["source"], {"session_id": "webrtc-esc"})
        self.assertEqual(payload["context"]["mode_before"], "available")
        self.assertIn("node", payload["context"])
        self.assertEqual(self.notes(), ["(The host passed that request to Intake as ticket bug-20261017-07; Intake will reply on the phone.)"])
        self.assertEqual(self.watch.escalations[0]["ticket_id"], "bug-20261017-07")
        # The end frame went downstream before the spoken line, so the TTS flushes the reply first.
        kinds = [type(f).__name__ for f, d in self.pushed]
        self.assertLess(kinds.index("LLMFullResponseEndFrame"), kinds.index("TTSSpeakFrame"))

    async def test_a_question_is_hinted_as_a_message(self):
        self.floor.context = FakeContext([{"role": "user", "content": "What is the weather in Jacksonville?"}])
        await self.reply_turn("I don't have access to weather information.")
        self.assertEqual(self.filed[0][1]["kind"], "message")

    async def test_an_answer_is_not_escalated(self):
        await self.reply_turn("Psalm 23 says the Lord is my shepherd.")
        self.assertEqual((self.filed, self.spoken()), ([], []))

    async def test_prayer_quiet_and_bug_never_escalate(self):
        for mode in ("prayer", "quiet", "bug"):
            self.floor.mode = mode
            await self.reply_turn("I can't do that.")
        self.floor.mode = "available"
        self.floor.held = True
        await self.reply_turn("I can't do that.")
        self.assertEqual((self.filed, self.spoken()), ([], []))

    async def test_a_tool_turn_is_not_escalated(self):
        await self.frame(LLMFullResponseStartFrame())
        await self.frame(FunctionCallInProgressFrame(function_name="read_scripture", tool_call_id="c1", arguments={"reference": "Hezekiah 3"}))
        await self.frame(LLMFullResponseEndFrame())
        await self.frame(FunctionCallResultFrame(function_name="read_scripture", tool_call_id="c1", arguments={}, result={"error": "not found"}))
        await self.reply_turn("I can't find a book called Hezekiah; did you mean Isaiah?")
        self.assertEqual((self.filed, self.spoken()), ([], []))
        # The next user turn is clean again.
        self.floor.turn_serial = 2
        await self.reply_turn("I can't send texts.")
        self.assertEqual(len(self.filed), 1)

    async def test_the_models_own_send_to_intake_is_not_doubled(self):
        await self.frame(FunctionCallInProgressFrame(function_name="send_to_intake", tool_call_id="c2", arguments={"kind": "feature_request"}))
        await self.reply_turn("I can't do that myself.")
        self.assertEqual(self.filed, [])

    async def test_once_per_user_turn(self):
        await self.reply_turn("I can't do that.")
        await self.reply_turn("As I said, I cannot do that.")
        self.assertEqual(len(self.filed), 1)
        self.assertEqual(self.spoken(), [ESCALATED_SPOKEN])
        self.floor.turn_serial = 2
        await self.reply_turn("I can't do that either.")
        self.assertEqual(len(self.filed), 2)

    async def test_an_interrupted_reply_is_not_escalated(self):
        await self.frame(LLMFullResponseStartFrame())
        await self.frame(LLMTextFrame("I can't "))
        # What an InterruptionFrame does to the watch (the base processor's own interruption
        # handling needs a running pipeline, so the frame itself is not pushed here).
        self.watch.note_interruption()
        await self.frame(LLMTextFrame("do that."))
        await self.frame(LLMFullResponseEndFrame())
        await asyncio.sleep(0)
        self.assertEqual(self.filed, [])

    async def test_flag_off(self):
        self.cfg.auto_escalate = False
        await self.reply_turn("I can't do that.")
        self.assertEqual((self.filed, self.spoken()), ([], []))

    async def test_a_spooled_filing_still_speaks_and_notes(self):
        self.reply = {"filed": False, "spooled": "/x.json", "error": "HTTP 503"}
        await self.reply_turn("I can't do that.")
        self.assertEqual(self.spoken(), [ESCALATED_SPOKEN])
        self.assertIn("desktop is not answering", self.notes()[0])
        self.assertIsNone(self.watch.escalations[0]["ticket_id"])

    async def test_falls_back_to_the_last_forwarded_utterance(self):
        self.floor.context = None
        self.floor.reading_state["last_user_text"] = "Turn on the lights."
        await self.reply_turn("I'm not able to control lights.")
        self.assertEqual(self.filed[0][1]["transcript"]["segments"][0]["text"], "Turn on the lights.")

    async def test_every_frame_passes_through(self):
        frames = [LLMFullResponseStartFrame(), LLMTextFrame("I can't do that."), LLMFullResponseEndFrame()]
        for f in frames:
            await self.frame(f)
        passed = [f for f, d in self.pushed if not isinstance(f, (TTSSpeakFrame, LLMMessagesAppendFrame))]
        self.assertEqual(passed, frames)


class Prompt(unittest.TestCase):
    def test_the_prompt_and_the_tool_tell_the_model_to_escalate(self):
        from talk import bot

        self.assertIn("without being asked, whenever a request needs something your tools cannot do", bot.SYSTEM_PROMPT)
        self.assertIn("Never\nanswer that you cannot; send it instead", bot.SYSTEM_PROMPT)
        self.assertIn("Do not send to Intake\nwhat your tools already handle", bot.SYSTEM_PROMPT)
        self.assertIn("Never tell him you cannot; send it instead.", tools.SCHEMAS["send_to_intake"].description)

    def test_report_shape(self):
        report = tools.intake_report("Play music.", "feature_request", None, origin="auto_escalation", assistant_reply="I can't play music.")
        self.assertEqual((report["origin"], report["ended_by"], report["assistant_reply"]), ("auto_escalation", "auto_escalation", "I can't play music."))
        plain = tools.intake_report("Tell Intake hello.", "message", None)
        self.assertEqual((plain["origin"], plain["ended_by"]), ("conversation", "send_to_intake"))
        self.assertNotIn("assistant_reply", plain)

    def test_config_flag_defaults_on(self):
        self.assertTrue(TalkConfig().auto_escalate)
        self.assertIsNotNone(escalation.CANNOT_PATTERNS)


if __name__ == "__main__":
    unittest.main()
