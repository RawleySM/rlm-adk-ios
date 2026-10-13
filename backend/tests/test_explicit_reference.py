"""Explicit Scripture references in a question must be grounded in the requested retained chapter
(verified, revision-qualified) before topic retrieval; unavailable references invent nothing."""

import tempfile
import unittest
from pathlib import Path

from tests.helpers import EDITION_META, fake_verses, make_config, write_prayer_store
from voice_portal import conversation, retrieval
from voice_portal.db import connect
from voice_portal.importer import import_family
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


class ExplicitReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_config(Path(self.tmp.name))
        self.conn = connect(self.cfg.db_path)
        verses = fake_verses({("PSA", 23): PSALM_23, ("1PE", 5): FIRST_PETER_5, ("HEB", 4): HEBREWS_4, ("JHN", 3): JOHN_3, ("1JN", 3): FIRST_JOHN_3})
        import_family(self.conn, "scripture", chapter_records(verses, EDITION_META), selection={"edition": EDITION_META}, source_path="fake")
        write_prayer_store(Path(self.cfg.prayer_store), [("p1", "Lord, give me rest tonight; I am weary and I cast my worries on you.", 1.0)])
        records, sel = read_prayers(self.cfg.prayer_store)
        import_family(self.conn, "prayer", records, selection=sel, source_path=self.cfg.prayer_store)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _evidence(self, question, **kw):
        return conversation.gather_evidence(self.conn, question, families=None, passage_cite_ids=None, **kw)

    def test_psalm_23_question_is_grounded_in_psalm_23_first(self):
        """Live smoke 2026-10-13: this question retrieved 1 Peter 5 and Hebrews 4 and omitted Psalm 23."""
        hits, scope = self._evidence("What does Psalm 23 say about rest? Explain briefly using the source.")
        self.assertTrue(hits, "no evidence")
        self.assertEqual(hits[0]["source_doc_id"], "github:BibleAquifer/WorldEnglishBible#eng/PSA.23")
        self.assertEqual(hits[0]["strategy"], "explicit_reference")
        self.assertIn("green pastures", hits[0]["text"])
        cite = retrieval.resolve_citation(self.conn, hits[0]["cite_id"])
        self.assertTrue(cite["verified"])
        self.assertEqual(scope["explicit_references"][0]["ref"], "PSA.23")
        self.assertEqual(scope["explicit_references"][0]["supplied"], True)
        # topic retrieval still follows (rest -> Hebrews 4 / 1 Peter 5 / the prayer) and the prayer stays labelled as a prayer
        self.assertIn("prayer", {h["source_family"] for h in hits})

    def test_psalms_spelling_and_lowercase(self):
        for q in ("psalms 23 about rest", "What does PSALM 23 say", "Read Ps 23"):
            hits, scope = self._evidence(q)
            self.assertEqual(hits[0]["source_doc_id"], "github:BibleAquifer/WorldEnglishBible#eng/PSA.23", q)

    def test_numbered_book_is_not_confused_with_its_unnumbered_namesake(self):
        hits, scope = self._evidence("What does 1 John 3 say about being children of God?")
        self.assertEqual(hits[0]["source_doc_id"], "github:BibleAquifer/WorldEnglishBible#eng/1JN.3")
        self.assertEqual([r["ref"] for r in scope["explicit_references"]], ["1JN.3"])
        hits, scope = self._evidence("Explain John 3 to me")
        self.assertEqual(hits[0]["source_doc_id"], "github:BibleAquifer/WorldEnglishBible#eng/JHN.3")
        hits, scope = self._evidence("What does 1 Peter 5 say about rest?")
        self.assertEqual(hits[0]["source_doc_id"], "github:BibleAquifer/WorldEnglishBible#eng/1PE.5")

    def test_verse_reference_narrows_to_the_chunk_holding_those_verses(self):
        hits, scope = self._evidence("What does 1 Peter 5:7 mean?")
        self.assertEqual(hits[0]["strategy"], "explicit_reference")
        self.assertIn("1Pe.5.4", hits[0]["verse_refs"])  # fixture chapter has 4 verses; verse 7 is beyond it
        self.assertEqual(scope["explicit_references"][0]["ref"], "1PE.5:7")
        self.assertEqual(scope["explicit_references"][0]["verses_found"], [])
        hits, scope = self._evidence("Read Psalm 23:4-6 to me")
        self.assertEqual(scope["explicit_references"][0]["verses_found"], ["Ps.23.4", "Ps.23.5", "Ps.23.6"])
        self.assertIn("valley of the shadow", hits[0]["text"])

    def test_unavailable_reference_invents_nothing(self):
        hits, scope = self._evidence("What does Genesis 51 say?")
        self.assertEqual(scope["explicit_references"][0]["ref"], "GEN.51")
        self.assertFalse(scope["explicit_references"][0]["supplied"])
        self.assertFalse(any(h.get("strategy") == "explicit_reference" for h in hits))
        hits, scope = self._evidence("What does Obadiah 1 say about pride?")  # book not imported in this fixture
        self.assertFalse(scope["explicit_references"][0]["supplied"])

    def test_no_reference_means_no_explicit_block(self):
        hits, scope = self._evidence("What have I prayed about rest?")
        self.assertEqual(scope["explicit_references"], [])
        self.assertFalse(any(h.get("strategy") == "explicit_reference" for h in hits))

    def test_heard_passage_still_comes_first_and_keeps_its_revision(self):
        heard = retrieval.search(self.conn, "green pastures", families=["scripture"])["hits"][0]["cite_id"]
        hits, scope = conversation.gather_evidence(self.conn, "What does 1 Peter 5 say about this?", families=None, passage_cite_ids=[heard])
        self.assertEqual(hits[0]["strategy"], "heard_passage")
        self.assertEqual(hits[0]["cite_id"], heard)
        self.assertEqual(hits[1]["strategy"], "explicit_reference")
        self.assertEqual(hits[1]["source_doc_id"], "github:BibleAquifer/WorldEnglishBible#eng/1PE.5")
        self.assertEqual(scope["passage_supplied"], [heard])


if __name__ == "__main__":
    unittest.main()
