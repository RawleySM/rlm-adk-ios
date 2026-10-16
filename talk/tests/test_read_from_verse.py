"""Starting a reading at a verse: tapped verse numbers, "start at verse 16" phrases, one-verse references."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipecat.frames.frames import InputTransportMessageFrame  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

from talk import passage  # noqa: E402
from talk.client_messages import ClientMessages  # noqa: E402
from talk.config import TalkConfig  # noqa: E402
from talk.floor import FloorProcessor  # noqa: E402
from talk.scripture_tts import ScriptureSpeakFrame  # noqa: E402
from talk.tools import new_reading_state  # noqa: E402

ROMANS_8 = {
    "available": True,
    "reference": "Romans 8",
    "title": "Romans 8 (World English Bible)",
    "edition": {"name": "World English Bible"},
    "references": [{"book": "ROM", "chapter": 8}],
    "verses": [{"verse": n, "text": f"Verse {n} text.", "osis": f"Rom.8.{n}"} for n in range(1, 40)],
}


class VersePhrases(unittest.TestCase):
    def test_requests_name_the_verse(self):
        cases = {
            "Start at verse 16.": 16,
            "Read from verse sixteen": 16,
            "Go back to verse 5": 5,
            "Continue from verse twelve, please": 12,
            "Can you start reading at verse twenty two": 22,
            "Back up to verse one hundred nineteen": 119,
            "Jump to verse 9": 9,
        }
        for text, verse in cases.items():
            self.assertEqual(passage.verse_request(passage.norm(text)), verse, text)

    def test_questions_and_other_passages_are_not(self):
        for text in ("What does verse 16 mean?", "Read Romans 8 verse 16", "Read chapter 3 verse 2", "Continue", "Read it", "Verse 16 is beautiful"):
            self.assertIsNone(passage.verse_request(passage.norm(text)), text)

    def test_spoken_numbers(self):
        self.assertEqual(passage.spoken_number(["sixteen"]), 16)
        self.assertEqual(passage.spoken_number(["twenty", "two", "please"]), 22)
        self.assertEqual(passage.spoken_number(["one", "hundred", "and", "nineteen"]), 119)
        self.assertEqual(passage.spoken_number(["28"]), 28)
        self.assertIsNone(passage.spoken_number(["please"]))

    def test_index_of_verse(self):
        state = new_reading_state()
        passage.apply(state, passage.from_tool_result(ROMANS_8, "Romans 8"), source="client")
        self.assertEqual(passage.index_of_verse(state, 16), 15)
        self.assertIsNone(passage.index_of_verse(state, 40))


class ReadFromVerse(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = new_reading_state()
        passage.apply(self.state, passage.from_tool_result(ROMANS_8, "Romans 8"), source="client")
        self.floor = FloorProcessor(TalkConfig(state_root=tempfile.mkdtemp()), self.state, session_id="verse-1")
        self.pushed = []

        async def push_frame(frame, direction=FrameDirection.DOWNSTREAM):
            self.pushed.append(frame)

        async def interrupt():
            self.state["reading"] = False
            self.floor.interruption_seen.set()

        self.floor.push_frame = push_frame
        self.floor.broadcast_interruption = interrupt

    async def send(self, message: dict) -> None:
        await ClientMessages(self.floor).process_frame(InputTransportMessageFrame(message=message), FrameDirection.DOWNSTREAM)

    def spoken(self) -> list[str]:
        return [f.text for f in self.pushed if isinstance(f, ScriptureSpeakFrame)]

    def started(self) -> dict:
        return next(f.message for f in self.pushed if getattr(f, "message", {}).get("state") == "started")

    async def test_from_index_announces_the_verse_and_reads_on(self):
        self.assertTrue(await self.floor.read_active(reason="test", from_index=15))
        self.assertEqual(self.spoken()[0], "Romans 8, verse 16.")
        self.assertEqual(self.spoken()[1], "Verse 16 text.")
        self.assertEqual(self.spoken()[-1], "Verse 39 text.")
        self.assertEqual(len(self.spoken()), 1 + 24)
        started = self.started()
        self.assertEqual((started["from_index"], started["from_verse"], started["spoken_count"]), (15, 16, 15))
        self.assertEqual(self.state["spoken_through"], 15)

    async def test_from_index_overrides_a_kept_position(self):
        self.state["spoken_count"] = 30
        await self.floor.read_active(restart=True, reason="test", from_index=4)
        self.assertEqual(self.spoken()[:2], ["Romans 8, verse 5.", "Verse 5 text."])

    async def test_tapped_verse_number_from_the_client(self):
        # Over WebRTC every field is a string, inside the RTVI client-message envelope.
        await self.send({"label": "rtvi-ai", "type": "client-message", "data": {"t": "client_read", "d": {"action": "play", "index": "15"}}})
        self.assertEqual(self.spoken()[:2], ["Romans 8, verse 16.", "Verse 16 text."])

    async def test_play_without_index_still_resumes(self):
        self.state["spoken_count"] = 7
        await self.send({"type": "client_read", "action": "play"})
        self.assertEqual(self.spoken()[0], "Verse 8 text.")

    async def test_typed_verse_request(self):
        await self.send({"type": "client_text", "text": "start at verse 20"})
        self.assertEqual(self.spoken()[:2], ["Romans 8, verse 20.", "Verse 20 text."])


if __name__ == "__main__":
    unittest.main()
