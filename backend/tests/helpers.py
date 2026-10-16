"""Fixtures that mirror the real source contracts' columns (not their full schemas)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from voice_portal.config import Config


def make_config(tmp: Path) -> Config:
    cfg = Config(state_root=str(tmp / "state"))
    cfg.prayer_store = str(tmp / "prayer_ingest.sqlite3")
    cfg.youtube_store = str(tmp / "catalog.sqlite3")
    cfg.allocatorctl = str(tmp / "allocatorctl")
    cfg.allocatorctl_timeout_s = 3
    # Never the operator's published ledger; tests that need one publish it under tmp.
    cfg.scripture_pairings_ledger = str(tmp / "published" / "pairings.jsonl")
    cfg.ensure_dirs()
    return cfg


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_prayer_store(path: Path, prayers: list[tuple[str, str, float]]) -> None:
    """prayers: (prayer_id, text, recorded_at). Creates a table with prayer_corpus_v1's columns."""
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE prayer_corpus_v1 (prayer_id TEXT, kind TEXT, recorded_at REAL, text TEXT, text_sha256 TEXT, char_count INTEGER, duration_s REAL, "
        "original_filename TEXT, asr_model_id TEXT, recording_id TEXT, source_kind TEXT, origin TEXT, origin_prayer_id TEXT, listed_at REAL, recorded_at_basis TEXT, gateway_event_id TEXT, gateway_sequence INTEGER)"
    )
    for i, (pid, text, recorded) in enumerate(prayers):
        conn.execute(
            "INSERT INTO prayer_corpus_v1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, "publication", recorded, text, sha(text), len(text), 60.0, f"VoiceMemos:{pid}", "cohere-transcribe-03-2026", f"rec-{pid}", "apple_voice_memos", None, None, recorded + 100, "device_capture", f"ev-{pid}", i),
        )
    conn.commit()
    conn.close()


def write_youtube_store(path: Path, docs: list[tuple]) -> None:
    """docs: (url, provider, date, title, body[, authors]); `authors` lands in metadata like the catalog's."""
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE source_document_current_v1 (url TEXT, provider TEXT, observation_id INTEGER, observed_at TEXT, body_sha256 TEXT, body_chars INTEGER, body_text TEXT, metadata_json TEXT, has_transcript INTEGER, legacy_body_sha256 TEXT, selection_rule TEXT)"
    )
    conn.execute("CREATE TABLE transcripts (id INTEGER PRIMARY KEY, video_id TEXT, text TEXT, text_hash TEXT)")
    for i, doc in enumerate(docs):
        url, provider, date, title, body = doc[:5]
        meta = {"date": date, "title": title, "description": "d", "youtube_id": f"vid{i}"}
        if len(doc) > 5 and doc[5] is not None:
            meta["authors"] = list(doc[5])
        conn.execute(
            "INSERT INTO source_document_current_v1 VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (url, provider, 1000 + i, f"2026-10-0{1 + i % 9}T00:00:00+00:00", sha(body), len(body), body, json.dumps(meta), 1, None, "latest_ok_nonempty_v1"),
        )
        conn.execute("INSERT INTO transcripts(video_id, text, text_hash) VALUES (?,?,?)", (f"vid{i}", body, sha(body)))
    conn.commit()
    conn.close()


def fake_verses(chapters: dict[tuple[str, int], list[str]]):
    from voice_portal.sources.scripture import Verse

    osis_books = {"PSA": "Ps", "JHN": "John", "GEN": "Gen"}
    for (book, chapter), texts in chapters.items():
        for n, text in enumerate(texts, 1):
            yield Verse(book=book, chapter=chapter, verse=n, text=text, osis=f"{osis_books.get(book, book.title())}.{chapter}.{n}", entry_id=f"entry-{book}", asset_body_sha256=sha(book + str(chapter)), asset_path=f"eng/json/{book}.content.json", locator_start=n * 10, locator_end=n * 10 + len(text), source_marker=str(n))


EDITION_META = {"source_id": "github:BibleAquifer/WorldEnglishBible", "representation": "aquifer_json", "edition_id": "github:BibleAquifer/WorldEnglishBible#eng", "name": "World English Bible", "version": "1.0.2", "license": "Public Domain CC0", "language": "eng", "catalog_version": "v3-test", "store_manifest_id": "test"}

THEO_BODIES = {
    "People.csv": "personLookup,status,personID,displayTitle,name\nDavid,accepted,p1,David,David\nAbraham,accepted,p2,Abraham,Abraham\n",
    "Places.csv": "placeLookup,status,displayTitle,placeID\nBethlehem,accepted,Bethlehem,pl1\n",
    "Verses.csv": "osisRef,status,verseID,book,chapter,verseNum,verseText,people,places,event,yearNum\nPs.23.1,accepted,v1,Psalms,23,1,The LORD is my shepherd,David,,,\nPs.23.2,accepted,v2,Psalms,23,2,He makes me lie down,\"David, Unknown Person\",Bethlehem,,\n",
}


def import_chapters(conn, chapters: dict[tuple[str, int], list[str]]) -> None:
    """Project fake WEB chapters (verse lists) into the scripture family."""
    from voice_portal.importer import import_family
    from voice_portal.sources.scripture import chapter_records

    import_family(conn, "scripture", chapter_records(fake_verses(chapters), EDITION_META), selection={"edition": EDITION_META}, source_path="fake")


def numbered(prefix: str, count: int, overrides: dict[int, str] | None = None) -> list[str]:
    """`count` filler verses ("<prefix> verse n.") with real text substituted at the given numbers."""
    overrides = overrides or {}
    return [overrides.get(n, f"{prefix} filler verse {n} about ordinary matters.") for n in range(1, count + 1)]
