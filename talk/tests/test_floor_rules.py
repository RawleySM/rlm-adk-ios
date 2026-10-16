"""Pure rules of the floor: address detection, amen detection, normalisation."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from talk.config import TalkConfig  # noqa: E402
from talk.floor import FloorProcessor, _norm  # noqa: E402


class FloorRules(unittest.TestCase):
    def setUp(self):
        self.floor = FloorProcessor(TalkConfig())

    def test_address_phrases_enter_prayer(self):
        for text in ("Lord, thank you for this evening.", "Heavenly Father, we come to you.", "Let's pray. Jesus, you know the day I had.", "Dear God, I am tired."):
            self.assertIsNotNone(self.floor._starts_with_address(text), text)

    def test_ordinary_questions_do_not(self):
        for text in ("What does Psalm 23 say about rest?", "Read me the Lord's prayer.", "Is the Lord mentioned in Romans 8?"):
            self.assertIsNone(self.floor._starts_with_address(text), text)

    def test_amen_only_at_the_end(self):
        self.assertTrue(self.floor._ends_with_amen("Hold my family tonight. Amen."))
        self.assertTrue(self.floor._ends_with_amen("In Jesus' name, amen"))
        self.assertFalse(self.floor._ends_with_amen("Some would say amen to that, but I am not finished."))

    def test_assistant_name_prefers_the_display_name(self):
        self.assertEqual(self.floor.assistant_name, TalkConfig().llm_model)
        named = FloorProcessor(TalkConfig(llm_model="qwen-coding-b", llm_display_name="Qwen3.8"))
        self.assertEqual(named.assistant_name, "Qwen3.8")

    def test_norm(self):
        self.assertEqual(_norm("Lord,  thank You!"), "lord thank you")


if __name__ == "__main__":
    unittest.main()
