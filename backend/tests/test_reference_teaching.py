"""The scripture_reference library adapter (Edwards ThML, BibleProject transcripts), its scoped
membership inside the shared teaching family, metadata refresh on unchanged revisions, and the
interpretations tool built on the teaching family."""

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from tests.helpers import import_chapters, make_config, numbered, sha, write_prayer_store, write_youtube_store
from voice_portal import retrieval
from voice_portal.db import connect
from voice_portal.importer import import_family, latest_receipts
from voice_portal.sources import reference_teaching as rt
from voice_portal.sources.prayers import read_prayers
from voice_portal.sources.teaching import DOC_PREFIX as TEACHING_PREFIX
from voice_portal.sources.teaching import read_teaching
from voice_portal.tools.interpretations import NOT_RECORDED, interpretations_tool, voice_identity


@dataclass(frozen=True)
class Loc:
    kind: str
    ref: str
    start: int
    end: int
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Rev:
    text: str
    locators: tuple
    extractor: str
    entry_id: str = "e"
    body_sha256: str = "b" * 64
    extractor_version: str = "1"
    text_sha256: str = "t" * 64
    warnings: tuple = ()


@dataclass(frozen=True)
class Asset:
    entry_id: str
    source_id: str
    source_path: str
    url: str
    body_sha256: str
    kind: str | None = None


def paragraph(topic: str, count: int = 6) -> str:
    return " ".join(f"This is sentence {n} of the section on {topic}, written plainly for the test." for n in range(count))


class ThmlBuilder:
    """Concatenates headed sections and records ThML-like section locators (nested by level)."""

    def __init__(self):
        self.text = ""
        self.locators: list[Loc] = []
        self.open: list[tuple[int, str, str, int]] = []

    def start(self, div_id: str, title: str, level: int) -> None:
        self.open.append((len(self.text), div_id, title, level))
        self.text += title + "\n"

    def body(self, text: str) -> None:
        self.text += text + "\n"

    def end(self) -> None:
        start, div_id, title, level = self.open.pop()
        self.locators.append(Loc("section", f"div:{div_id}", start, len(self.text), {"element": f"div{level}", "level": str(level), "id": div_id, "title": title}))

    def revision(self) -> Rev:
        return Rev(text=self.text, locators=tuple(self.locators), extractor="thml", text_sha256=sha(self.text))


def head(title: str, creator: str = "Jonathan Edwards") -> bytes:
    return f'<ThML><ThML.head><DC><DC.Title>{title}</DC.Title><DC.Creator sub="Author" scheme="short-form">{creator}</DC.Creator></DC></ThML.head><ThML.body>'.encode()


class FakeReaders:
    """The slice of `scripture_reference_readers` the adapter uses, over in-memory assets."""

    def __init__(self, root: Path, items: list[tuple[Asset, Rev, bytes]]):
        self.items = items
        self.root = root
        for asset, _, raw in items:
            (root / asset.entry_id).write_bytes(raw)

    def open_store(self, store_root):
        return SimpleNamespace(manifest_id="manifest-1", run_id="run-1", catalog_version="v-test")

    def iter_assets(self, store, source_id=None):
        return [asset for asset, _, _ in self.items if source_id is None or asset.source_id == source_id]

    def extract_text(self, store, asset):
        return next(rev for a, rev, _ in self.items if a == asset)

    def body_path(self, store, asset):
        return self.root / asset.entry_id


def edwards_items() -> list[tuple[Asset, Rev, bytes]]:
    single = ThmlBuilder()
    single.start("i", "Title Page", 1)
    single.body("A TREATISE CONCERNING RELIGIOUS AFFECTIONS. BY JONATHAN EDWARDS")
    single.end()
    single.start("iii", "Part I. Concerning the nature of the affections", 1)
    single.start("iii.i", "I. The affections are the spring of action", 2)
    affections_text = paragraph("the affections as springs of action") + " True religion lies much in the affections, and the comfort of the saints is that God works all things together for good to them that love him; this is the comfort that holds the heart in trouble."
    single.body(affections_text)
    single.end()
    single.start("iii.ii", "II. Holy affections have their origin in the Spirit", 2)
    single.body(paragraph("the Spirit as the origin of holy affections"))
    single.end()
    single.end()
    single.start("ix", "Indexes", 1)
    single.start("ix.i", "Index of Scripture References", 2)
    single.body("Romans 8:28 " * 60)
    single.end()
    single.end()
    works = ThmlBuilder()
    works.start("vii", "VIII. A TREATISE CONCERNING RELIGIOUS AFFECTIONS, IN THREE PARTS.", 1)
    works.start("vii.i", "SECTION I. THE AFFECTIONS ARE THE SPRING OF ACTION", 2)
    works.body(affections_text)  # the collected volume reprints the single work
    works.end()
    works.start("vii.ii", "SECTION II. OF GRACIOUS AFFECTIONS AND LOVE", 2)
    works.body(paragraph("gracious affections and the love of God in the believer"))
    works.end()
    works.end()
    return [
        (Asset("e-aff", "ccel:edwards", "affections.xml", "https://www.ccel.org/ccel/e/edwards/affections.xml", "a" * 64, "thml"), single.revision(), head("Religious Affections")),
        (Asset("e-w1", "ccel:edwards", "works1.xml", "https://www.ccel.org/ccel/e/edwards/works1.xml", "c" * 64, "thml"), works.revision(), head("The Works of Jonathan Edwards, Volume One")),
    ]


TRANSCRIPT = (
    "The 10 Commandments\nas Wisdom\nVideo Transcript\n\nChapter 1: Commands Are for Life        0:00-0:58\n\n"
    "Jon: These are the most well-known rules in the whole Bible, maybe the most famous\n\n    rules in all of human history.\n\n"
    "Tim: Every command in the Bible is an invitation to find life.\n\n\n\n                                          1\n\x0c"
    "Tim: So this first command sets the tone for all of the commands to come.\n"
)


def transcript_item() -> tuple[Asset, Rev, bytes]:
    return (Asset("e-bp", "bibleproject:transcript", "media/10commandments/scripts/10c-intro.pdf", "https://cdn.example/10c-intro.pdf", "d" * 64, "pdf"), Rev(text=TRANSCRIPT, locators=(), extractor="pdf", text_sha256=sha(TRANSCRIPT), warnings=("pdftotext:24.02.0",)), b"%PDF-1.7")


class ReferenceTeachingAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, items):
        return rt.read_reference_teaching("store", ["ccel:edwards", "bibleproject:transcript"], None, readers=FakeReaders(self.root, items))

    def test_leaf_divisions_reprints_and_indexes(self):
        records, selection = self.read(edwards_items())
        edwards = selection["sources"]["ccel:edwards"]
        self.assertEqual(edwards["documents"], 3)
        self.assertEqual(edwards["skipped_duplicate"], 1)  # works1 reprint of the single work
        self.assertGreaterEqual(edwards["skipped_index"], 2)  # title page and index
        by_id = {r.source_doc_id: r for r in records}
        leaf = by_id["scripture_reference/ccel:edwards/affections.xml#iii.i"]
        self.assertEqual(leaf.metadata["teacher"], "Jonathan Edwards")
        self.assertEqual(leaf.metadata["teacher_basis"], "thml_dc_creator")
        self.assertEqual(leaf.metadata["work"], "Religious Affections")
        self.assertEqual(leaf.title, "Religious Affections: Part I. Concerning the nature of the affections / I. The affections are the spring of action")
        collected = by_id["scripture_reference/ccel:edwards/works1.xml#vii.ii"]
        self.assertEqual(collected.metadata["work"], "A Treatise Concerning Religious Affections, in Three Parts")
        self.assertEqual(collected.metadata["section"], "Section II. Of Gracious Affections and Love")
        self.assertEqual(collected.metadata["store_manifest_id"], "manifest-1")
        rev = edwards_items()[0][1]
        span = leaf.metadata["span"]
        self.assertEqual(rev.text[span["start"]:span["end"]], leaf.body)  # verbatim span of the reader text

    def test_transcript_body_and_title(self):
        records, selection = self.read([transcript_item()])
        self.assertEqual(selection["sources"]["bibleproject:transcript"]["documents"], 1)
        record = records[0]
        self.assertEqual(record.title, "The 10 Commandments as Wisdom (transcript)")
        self.assertEqual(record.metadata["teacher"], "BibleProject")
        self.assertIn("Jon: These are the most well-known rules in the whole Bible, maybe the most famous rules in all of human history.", record.body)
        self.assertNotIn("\x0c", record.body)
        self.assertNotRegex(record.body, r"(?m)^\s*1\s*$")
        self.assertEqual(record.body.count("\n\nTim:"), 2)

    def test_scoped_membership_in_the_shared_teaching_family(self):
        cfg = make_config(self.root)
        conn = connect(cfg.db_path)
        docs = [("https://www.desiringgod.org/a", "desiringgod", "2026-09-01", "A", "Desiring God text one about joy in God and Romans 8:28. " * 40, ["John Piper"]), ("https://www.desiringgod.org/b", "desiringgod", "2026-09-02", "B", "Desiring God text two about prayer and patience. " * 40, None)]
        write_youtube_store(Path(cfg.youtube_store), docs)
        records, selection = read_teaching(cfg.youtube_store, limit_per_provider=10, min_chars=100)
        import_family(conn, "teaching", records, selection=selection, source_path=cfg.youtube_store, membership_prefix=TEACHING_PREFIX)
        items = edwards_items() + [transcript_item()]
        records, selection = self.read(items)
        counts = import_family(conn, "teaching", records, selection=selection, source_path="store", membership_prefix=rt.DOC_PREFIX, receipt_family="teaching:scripture_reference")
        self.assertEqual((counts["inserted"], counts["removed"]), (4, 0))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents WHERE source_family='teaching' AND current=1").fetchone()[0], 6)
        # a smaller publisher selection removes only publisher records ...
        write_youtube_store(Path(cfg.youtube_store), docs[:1])
        records, selection = read_teaching(cfg.youtube_store, limit_per_provider=10, min_chars=100)
        counts = import_family(conn, "teaching", records, selection=selection, source_path=cfg.youtube_store, membership_prefix=TEACHING_PREFIX)
        self.assertEqual(counts["removed"], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents WHERE source_family='teaching' AND current=1 AND source_doc_id LIKE 'scripture_reference/%'").fetchone()[0], 4)
        # ... and a smaller library selection removes only library records
        records, selection = self.read(items[:1])
        counts = import_family(conn, "teaching", records, selection=selection, source_path="store", membership_prefix=rt.DOC_PREFIX, receipt_family="teaching:scripture_reference")
        self.assertEqual(counts["removed"], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents WHERE source_family='teaching' AND current=1 AND source_doc_id LIKE 'http%'").fetchone()[0], 1)
        families = {r["source_family"] for r in latest_receipts(conn)}
        self.assertTrue({"teaching", "teaching:scripture_reference"} <= families)
        hit = retrieval.search(conn, "affections spring action", families=["teaching"])["hits"][0]
        self.assertEqual(hit["attribution"], "Teaching, Jonathan Edwards, Religious Affections (undated)")
        conn.close()

    def test_unchanged_revision_refreshes_metadata(self):
        cfg = make_config(self.root)
        conn = connect(cfg.db_path)
        body = "Desiring God text about the joy of God in all things. " * 40
        write_youtube_store(Path(cfg.youtube_store), [("https://www.desiringgod.org/a", "desiringgod", "2026-09-01", "A", body, None)])
        records, selection = read_teaching(cfg.youtube_store, limit_per_provider=10, min_chars=100)
        import_family(conn, "teaching", records, selection=selection, source_path=cfg.youtube_store, membership_prefix=TEACHING_PREFIX)
        write_youtube_store(Path(cfg.youtube_store), [("https://www.desiringgod.org/a", "desiringgod", "2026-09-01", "A", body, ["John Piper"])])
        records, selection = read_teaching(cfg.youtube_store, limit_per_provider=10, min_chars=100)
        counts = import_family(conn, "teaching", records, selection=selection, source_path=cfg.youtube_store, membership_prefix=TEACHING_PREFIX)
        self.assertEqual((counts["unchanged"], counts["inserted"], counts["revised"]), (1, 0, 0))
        row = conn.execute("SELECT metadata_json, COUNT(*) FROM documents WHERE source_doc_id = 'https://www.desiringgod.org/a'").fetchone()
        self.assertIn('"authors": ["John Piper"]', row[0])
        self.assertEqual(row[1], 1)
        conn.close()


ROMANS_8 = numbered("Romans 8", 39, {
    27: "He who searches the hearts knows what is on the Spirit’s mind, because he makes intercession for the saints according to God.",
    28: "We know that all things work together for good for those who love God, for those who are called according to his purpose.",
    29: "For whom he foreknew, he also predestined to be conformed to the image of his Son, that he might be the firstborn among many brothers.",
})


class InterpretationsToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = make_config(self.root)
        self.conn = connect(self.cfg.db_path)
        import_chapters(self.conn, {("ROM", 8): ROMANS_8})
        docs = [
            ("https://www.desiringgod.org/murdered-friend", "desiringgod", "2026-09-01", "Sovereignty and My Murdered Friend", "Some background about grief that runs on for a while. " * 6 + "Take hold of Romans 8:28 and, with all its mysteries, believe it. Take your stand on it as a solid foundation while everything is shaking. " + "More reflection on suffering follows here. " * 6, ["John Piper"]),
            ("https://www.desiringgod.org/keller", "desiringgod", "2026-09-02", "Grace in Hard Places", "A guest writer reflects at length on hardship. " * 6 + "Romans 8:28 does not say all things are good; it says God works them for good. " + "Further thoughts on hope. " * 6, ["Tim Keller"]),
            ("https://www.eleven22.com/help-my-unbelief", "eleven22", "2026-09-03", "I Believe. Help My Unbelief", "Church sermon notes about doubt and faith. " * 6 + "I mean, Romans 8:28 says God is at work in all things for the good of those that love him. And sometimes God himself hands out pain. " + "Closing prayer notes. " * 6, None),
        ]
        write_youtube_store(Path(self.cfg.youtube_store), docs)
        records, selection = read_teaching(self.cfg.youtube_store, limit_per_provider=10, min_chars=100)
        import_family(self.conn, "teaching", records, selection=selection, source_path=self.cfg.youtube_store, membership_prefix=TEACHING_PREFIX)
        records, selection = rt.read_reference_teaching("store", ["ccel:edwards"], None, readers=FakeReaders(self.root, edwards_items()))
        import_family(self.conn, "teaching", records, selection=selection, source_path="store", membership_prefix=rt.DOC_PREFIX)
        write_prayer_store(Path(self.cfg.prayer_store), [("p1", "Lord, I keep coming back to Romans 8:28 this week; help me believe you work this for good.", 1790000000.0)])
        records, selection = read_prayers(self.cfg.prayer_store)
        import_family(self.conn, "prayer", records, selection=selection, source_path=self.cfg.prayer_store)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_traditions_come_only_from_the_explicit_map(self):
        self.assertEqual(voice_identity({"publisher": "Desiring God", "authors": ["John Piper"]}, "t")["tradition"], "Reformed Baptist (Christian Hedonism)")
        self.assertEqual(voice_identity({"publisher": "Desiring God", "authors": ["Tim Keller"]}, "t")["tradition"], NOT_RECORDED)
        self.assertEqual(voice_identity({"publisher": "Desiring God"}, "t")["tradition_basis"], "publisher")
        self.assertEqual(voice_identity({"publisher": "The Church of Eleven22"}, "t")["tradition"], "Evangelical, non-denominational")
        self.assertEqual(voice_identity({"publisher": "CCEL", "teacher": "Jonathan Edwards", "work": "Religious Affections"}, "t")["tradition"], "Reformed/Puritan")
        self.assertEqual(voice_identity({"publisher": "BibleProject", "teacher": "BibleProject"}, "t")["tradition"], "Evangelical, literary-theological")
        self.assertEqual(voice_identity({"publisher": "Someone Else"}, "t")["tradition"], NOT_RECORDED)

    def test_reference_voices_are_distinct_attributed_and_verbatim(self):
        out = interpretations_tool(self.conn, "Romans 8:28", None, limit=4)
        self.assertTrue(out["available"])
        teachers = [v["teacher"] for v in out["voices"]]
        self.assertEqual(len(teachers), len(set(teachers)))
        self.assertGreaterEqual(len(teachers), 2)
        self.assertIn("John Piper", teachers)
        for voice in out["voices"]:
            chunk = retrieval.resolve_citation(self.conn, voice["cite_id"])
            self.assertTrue(chunk["available"])
            self.assertIn(voice["excerpt"], " ".join(chunk["text"].split()))  # verbatim, whitespace collapsed
            self.assertIn(voice["teacher"], out["spoken"])
            if voice["teacher"] == "Tim Keller":
                self.assertEqual(voice["tradition"], NOT_RECORDED)
                self.assertIn("Tim Keller, whose tradition is not recorded", out["spoken"])
        self.assertEqual(out["scripture"]["reference"], "Romans 8:28")
        self.assertIn(ROMANS_8[27], out["spoken"])
        self.assertEqual(set(out["distinctions"]), {"scripture", "teacher_reading", "personal_context"})
        self.assertIsNotNone(out["personal_context"])
        self.assertIn("personal context", out["spoken"])
        self.assertIn("not Scripture", out["spoken"])
        self.assertNotRegex(out["spoken"], r"\[\d|[*_`#]")

    def test_topic_only_and_nothing_requested(self):
        out = interpretations_tool(self.conn, None, "affections", limit=3)
        self.assertTrue(out["available"])
        self.assertEqual(out["voices"][0]["teacher"], "Jonathan Edwards")
        self.assertIsNone(out["scripture"])
        empty = interpretations_tool(self.conn, None, None)
        self.assertFalse(empty["available"])
        self.assertTrue(empty["spoken"].endswith("?"))


if __name__ == "__main__":
    unittest.main()
