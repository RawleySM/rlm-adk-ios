"""Published prayers from the prayer-ingest mirror (`prayer_corpus_v1`, contract v1.1)."""

from __future__ import annotations

from pathlib import Path

from ..db import connect_source
from . import SourceRecord, SourceUnavailable

FAMILY = "prayer"


def read_prayers(store_path: str | Path) -> tuple[list[SourceRecord], dict]:
    """All currently published rows. Keyed by prayer_id, versioned by text_sha256."""
    try:
        conn = connect_source(store_path)
    except FileNotFoundError as exc:
        raise SourceUnavailable(str(exc)) from exc
    try:
        rows = conn.execute(
            "SELECT prayer_id, kind, recorded_at, text, text_sha256, char_count, duration_s, "
            "original_filename, asr_model_id, recording_id, source_kind, listed_at, recorded_at_basis, "
            "gateway_event_id, gateway_sequence FROM prayer_corpus_v1 ORDER BY listed_at, prayer_id"
        ).fetchall()
    except Exception as exc:  # view missing or store locked
        raise SourceUnavailable(f"prayer_corpus_v1 unreadable at {store_path}: {exc}") from exc
    finally:
        conn.close()
    records = []
    for row in rows:
        records.append(
            SourceRecord(
                source_family=FAMILY,
                source_doc_id=row["prayer_id"],
                body=row["text"] or "",
                title=f"Prayer {str(row['recorded_at'])[:10]}",
                revision_hash=row["text_sha256"],
                source_locator=f"prayer_corpus_v1:{row['prayer_id']}",
                observed_at=str(row["listed_at"]),
                recorded_at=str(row["recorded_at"]),
                recorded_at_basis=row["recorded_at_basis"],
                metadata={
                    "kind": row["kind"],
                    "duration_s": row["duration_s"],
                    "asr_model_id": row["asr_model_id"],
                    "recording_id": row["recording_id"],
                    "source_kind": row["source_kind"],
                    "gateway_sequence": row["gateway_sequence"],
                    "char_count": row["char_count"],
                },
            )
        )
    selection = {"view": "prayer_corpus_v1", "rule": "all_published_rows", "count": len(records)}
    return records, selection
