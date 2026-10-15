"""Scripture pane: passage state, read queue, read phrases, pane note and the reading voice choice."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from talk import passage  # noqa: E402
from talk.flows import role_message  # noqa: E402
from talk.tools import new_reading_state  # noqa: E402

ROMANS_12 = {
    "available": True,
    "reference": "Romans 12",
    "title": "Romans 12 (World English Bible)",
    "edition": {"name": "World English Bible"},
    "references": [{"book": "ROM", "chapter": 12}],
    "verses": [{"verse": n, "text": f"Verse {n} text.", "osis": f"Rom.12.{n}"} for n in range(1, 22)],
}


class PassageState(unittest.TestCase):
    def setUp(self):
        self.state = new_reading_state()
        passage.apply(self.state, passage.from_tool_result(ROMANS_12, "romans twelve"), source="client")

    def test_apply_and_message(self):
        message = passage.message(self.state)
        self.assertEqual(message["type"], "passage")
        self.assertEqual(message["reference"], "Romans 12")
        self.assertEqual(message["book"], "ROM")
        self.assertEqual(message["chapter"], 12)
        self.assertEqual(len(message["verses"]), 21)
        self.assertEqual(message["spoken_count"], 0)

    def test_unavailable_result_is_none(self):
        self.assertIsNone(passage.from_tool_result({"available": False, "verses": []}, "Hezekiah 3"))

    def test_fresh_passage_reads_from_the_reference(self):
        units = passage.read_queue(self.state, restart=False)
        self.assertEqual(units[0], {"index": None, "verse": None, "text": "Romans 12."})
        self.assertEqual([u["verse"] for u in units[1:]], list(range(1, 22)))

    def test_resume_after_verse_seven(self):
        self.state["spoken_count"] = 7
        units = passage.read_queue(self.state, restart=False)
        self.assertEqual(units[0]["verse"], 8)
        self.assertNotIn(None, [u["index"] for u in units])

    def test_restart_and_finished_read_from_the_start(self):
        self.state["spoken_count"] = 7
        self.assertEqual(passage.read_queue(self.state, restart=True)[1]["verse"], 1)
        self.state["spoken_count"] = 21
        self.assertEqual(passage.read_queue(self.state, restart=False)[1]["verse"], 1)

    def test_new_passage_resets_position(self):
        self.state["spoken_count"] = 9
        passage.apply(self.state, passage.from_tool_result(ROMANS_12, "Romans 12"), source="client")
        self.assertEqual(self.state["spoken_count"], 0)
        self.assertEqual(self.state["queue"], [])


class ReadPhrases(unittest.TestCase):
    def test_reading_the_open_passage(self):
        for text in ("Read it.", "Please read this to me.", "Can you read the passage?", "Read it again.", "Start reading", "Read the chapter aloud, please."):
            self.assertIsNotNone(passage.read_request(passage.norm(text)), text)

    def test_restart_words(self):
        self.assertEqual(passage.read_request(passage.norm("Read it again.")), "restart")
        self.assertEqual(passage.read_request(passage.norm("Read it from the beginning")), "restart")
        self.assertEqual(passage.read_request(passage.norm("Read it.")), "resume")

    def test_new_references_and_questions_are_not_read_requests(self):
        for text in ("Read me Psalm 23.", "Read Romans 8.", "What does this chapter say about love?", "I read it yesterday", "Did you read it?"):
            self.assertIsNone(passage.read_request(passage.norm(text)), text)


class PaneNote(unittest.TestCase):
    def setUp(self):
        self.state = new_reading_state()

    def test_no_passage_no_note(self):
        self.assertEqual(passage.pane_note(self.state, 9000), "")
        self.assertNotIn("Scripture pane", role_message("conversation", self.state, "ROLE"))

    def test_note_carries_reference_status_and_verses(self):
        passage.apply(self.state, passage.from_tool_result(ROMANS_12, "Romans 12"), source="client")
        self.state["spoken_count"] = 7
        note = passage.pane_note(self.state, 9000)
        self.assertIn("Romans 12 (World English Bible)", note)
        self.assertIn("stopped after verse 7", note)
        self.assertIn("21 Verse 21 text.", note)
        instruction = role_message("devotional", self.state, "ROLE")
        self.assertTrue(instruction.startswith("ROLE"))
        self.assertIn("Scripture pane", instruction)
        self.assertNotIn("Scripture pane", role_message("prayer", self.state, "ROLE"))

    def test_long_chapters_are_cut_with_a_notice(self):
        passage.apply(self.state, passage.from_tool_result(ROMANS_12, "Romans 12"), source="client")
        note = passage.pane_note(self.state, 60)
        self.assertIn("later verses omitted", note)


if __name__ == "__main__":
    unittest.main()
