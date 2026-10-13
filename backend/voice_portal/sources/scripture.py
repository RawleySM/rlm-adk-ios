"""One retained WEB edition through `scripture_reference_readers`, projected as chapter documents.

Each chapter document keeps verse locators (code-point offsets into the chapter body) with the native
OSIS id, the reader's own locator offsets into the asset text revision, the asset entry id and the
body SHA-256 so a citation resolves to the exact retained bytes.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from . import SourceRecord, SourceUnavailable

FAMILY = "scripture"

BOOK_NAMES = {
    "GEN": "Genesis", "EXO": "Exodus", "LEV": "Leviticus", "NUM": "Numbers", "DEU": "Deuteronomy",
    "JOS": "Joshua", "JDG": "Judges", "RUT": "Ruth", "1SA": "1 Samuel", "2SA": "2 Samuel",
    "1KI": "1 Kings", "2KI": "2 Kings", "1CH": "1 Chronicles", "2CH": "2 Chronicles", "EZR": "Ezra",
    "NEH": "Nehemiah", "EST": "Esther", "JOB": "Job", "PSA": "Psalms", "PRO": "Proverbs",
    "ECC": "Ecclesiastes", "SNG": "Song of Songs", "ISA": "Isaiah", "JER": "Jeremiah",
    "LAM": "Lamentations", "EZK": "Ezekiel", "DAN": "Daniel", "HOS": "Hosea", "JOL": "Joel",
    "AMO": "Amos", "OBA": "Obadiah", "JON": "Jonah", "MIC": "Micah", "NAM": "Nahum",
    "HAB": "Habakkuk", "ZEP": "Zephaniah", "HAG": "Haggai", "ZEC": "Zechariah", "MAL": "Malachi",
    "MAT": "Matthew", "MRK": "Mark", "LUK": "Luke", "JHN": "John", "ACT": "Acts", "ROM": "Romans",
    "1CO": "1 Corinthians", "2CO": "2 Corinthians", "GAL": "Galatians", "EPH": "Ephesians",
    "PHP": "Philippians", "COL": "Colossians", "1TH": "1 Thessalonians", "2TH": "2 Thessalonians",
    "1TI": "1 Timothy", "2TI": "2 Timothy", "TIT": "Titus", "PHM": "Philemon", "HEB": "Hebrews",
    "JAS": "James", "1PE": "1 Peter", "2PE": "2 Peter", "1JN": "1 John", "2JN": "2 John",
    "3JN": "3 John", "JUD": "Jude", "REV": "Revelation",
}


@dataclass(frozen=True)
class Verse:
    """Reader-independent verse atom (lets tests inject verses without a full store)."""

    book: str
    chapter: int
    verse: int
    text: str
    osis: str
    entry_id: str
    asset_body_sha256: str
    asset_path: str
    locator_start: int
    locator_end: int
    source_marker: str


def load_reader(reader_path: str | None):
    if reader_path:
        path = str(Path(reader_path).expanduser())
        if path not in sys.path:
            sys.path.insert(0, path)
    try:
        import scripture_reference_readers as readers  # type: ignore
    except ImportError as exc:
        raise SourceUnavailable(
            "scripture_reference_readers is not importable; set reader_path to a pinned copy "
            f"(see runbook): {exc}"
        ) from exc
    return readers


def iter_edition_verses(store_root: str, source_id: str, representation: str, reader_path: str | None) -> tuple[Iterator[Verse], dict]:
    """Yield verses of one edition representation; return (iterator, edition metadata)."""
    readers = load_reader(reader_path)
    try:
        store = readers.open_store(store_root)
    except Exception as exc:
        raise SourceUnavailable(f"scripture store unreadable at {store_root}: {exc}") from exc
    editions = [
        e for e in readers.edition_assets(store) if e.source_id == source_id and e.representation == representation
    ]
    if not editions:
        raise SourceUnavailable(f"edition {source_id} ({representation}) not found in catalog {store.catalog_version}")
    edition = editions[0]
    meta = {
        "store_manifest_id": store.manifest_id,
        "store_run_id": store.run_id,
        "catalog_version": store.catalog_version,
        "source_id": edition.source_id,
        "representation": edition.representation,
        "edition_id": edition.edition_id,
        "name": edition.name,
        "version": edition.version,
        "license": edition.license,
        "language": edition.language,
        "assets": len(edition.assets),
        "books": list(edition.books),
    }

    def _iter() -> Iterator[Verse]:
        for asset in edition.assets:
            for atom in readers.iter_verses(store, asset):
                yield Verse(
                    book=atom.book,
                    chapter=atom.chapter,
                    verse=atom.verse,
                    text=atom.text,
                    osis=atom.locator.meta.get("osis", f"{atom.book}.{atom.chapter}.{atom.verse}"),
                    entry_id=asset.entry_id,
                    asset_body_sha256=asset.body_sha256,
                    asset_path=asset.source_path,
                    locator_start=atom.locator.start,
                    locator_end=atom.locator.end,
                    source_marker=atom.source_marker,
                )

    return _iter(), meta


def chapter_records(verses: Iterable[Verse], edition_meta: dict) -> Iterator[SourceRecord]:
    """Group verse atoms into one SourceRecord per chapter with exact verse offsets."""
    current_key: tuple[str, int] | None = None
    buffer: list[Verse] = []

    def flush() -> SourceRecord | None:
        if not buffer:
            return None
        book, chapter = buffer[0].book, buffer[0].chapter
        parts: list[str] = []
        locators: list[dict] = []
        offset = 0
        for v in buffer:
            if parts:
                parts.append(" ")
                offset += 1
            start = offset
            parts.append(v.text)
            offset += len(v.text)
            locators.append(
                {
                    "kind": "verse",
                    "osis": v.osis,
                    "verse": v.verse,
                    "start": start,
                    "end": offset,
                    "entry_id": v.entry_id,
                    "asset_body_sha256": v.asset_body_sha256,
                    "asset_path": v.asset_path,
                    "asset_text_start": v.locator_start,
                    "asset_text_end": v.locator_end,
                    "source_marker": v.source_marker,
                }
            )
        body = "".join(parts)
        asset_hashes = sorted({v.asset_body_sha256 for v in buffer})
        revision = hashlib.sha256((":".join(asset_hashes) + "|" + body).encode("utf-8")).hexdigest()
        edition_id = edition_meta.get("edition_id") or edition_meta.get("source_id")
        return SourceRecord(
            source_family=FAMILY,
            source_doc_id=f"{edition_id}/{book}.{chapter}",
            body=body,
            title=f"{BOOK_NAMES.get(book, book)} {chapter} ({edition_meta.get('name') or edition_id})",
            revision_hash=revision,
            source_locator=f"{buffer[0].entry_id}:{buffer[0].asset_path}",
            observed_at=None,
            metadata={
                "edition": {k: edition_meta.get(k) for k in ("source_id", "representation", "edition_id", "name", "version", "license", "language", "catalog_version", "store_manifest_id")},
                "book": book,
                "chapter": chapter,
                "verse_count": len(buffer),
                "asset_body_sha256s": asset_hashes,
            },
            locators=locators,
        )

    for verse in verses:
        key = (verse.book, verse.chapter)
        if current_key is not None and key != current_key:
            rec = flush()
            if rec:
                yield rec
            buffer = []
        current_key = key
        buffer.append(verse)
    rec = flush()
    if rec:
        yield rec


def read_scripture(store_root: str, source_id: str, representation: str, reader_path: str | None) -> tuple[Iterator[SourceRecord], dict]:
    verses, meta = iter_edition_verses(store_root, source_id, representation, reader_path)
    selection = {"edition": meta, "rule": "one_complete_retained_edition_chapter_documents"}
    return chapter_records(verses, meta), selection


def theographic_csv_bodies(store_root: str, source_id: str, reader_path: str | None, names: tuple[str, ...]) -> tuple[dict[str, str], dict]:
    """Return verified CSV bodies (decoded) for the named Theographic files, plus their catalog identity."""
    readers = load_reader(reader_path)
    try:
        store = readers.open_store(store_root)
    except Exception as exc:
        raise SourceUnavailable(f"scripture store unreadable at {store_root}: {exc}") from exc
    bodies: dict[str, str] = {}
    identity: dict = {"source_id": source_id, "catalog_version": store.catalog_version, "files": {}}
    for asset in readers.iter_assets(store):
        if asset.source_id != source_id:
            continue
        name = asset.source_path.rsplit("/", 1)[-1]
        if name in names:
            raw = readers.read_body(store, asset)  # raises IntegrityError on hash mismatch
            bodies[name] = raw.decode("utf-8-sig")
            identity["files"][name] = {
                "entry_id": asset.entry_id,
                "revision": asset.revision,
                "body_sha256": asset.body_sha256,
                "body_bytes": asset.body_bytes,
            }
    missing = [n for n in names if n not in bodies]
    if missing:
        raise SourceUnavailable(f"Theographic files missing from catalog: {missing}")
    return bodies, identity


def dump_locators(locators: list[dict]) -> str:
    return json.dumps(locators, separators=(",", ":"))
