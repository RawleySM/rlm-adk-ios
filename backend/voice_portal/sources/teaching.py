"""Teaching records from the YouTube/publisher catalog (`source_document_current_v1`).

Selection: the newest usable full-text publisher records per provider, chosen by the record's own
`date` metadata (publisher-supplied), then `observed_at`. Transcript rows are not duplicated into the
projection; the transcript `text_hash` for the same video is retained as metadata for inspection.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..db import connect_source
from . import SourceRecord, SourceUnavailable

FAMILY = "teaching"
PROVIDERS = {"desiringgod": "Desiring God", "eleven22": "The Church of Eleven22"}


def read_teaching(store_path: str | Path, *, limit_per_provider: int, min_chars: int) -> tuple[list[SourceRecord], dict]:
    try:
        conn = connect_source(store_path)
    except FileNotFoundError as exc:
        raise SourceUnavailable(str(exc)) from exc
    records: list[SourceRecord] = []
    selection: dict = {
        "view": "source_document_current_v1",
        "rule": f"newest_{limit_per_provider}_per_provider_by_metadata_date_min_{min_chars}_chars",
        "providers": {},
    }
    try:
        for provider, publisher in PROVIDERS.items():
            rows = conn.execute(
                "SELECT url, provider, observation_id, observed_at, body_sha256, body_chars, body_text, "
                "metadata_json, has_transcript, "
                "json_extract(metadata_json, '$.date') AS meta_date, json_extract(metadata_json, '$.title') AS meta_title "
                "FROM source_document_current_v1 WHERE provider = ? AND body_chars >= ? "
                "ORDER BY meta_date DESC, observed_at DESC LIMIT ?",
                (provider, min_chars, limit_per_provider),
            ).fetchall()
            seen_hashes: set[str] = set()
            kept = 0
            for row in rows:
                if row["body_sha256"] in seen_hashes:
                    continue  # same body served under two URLs; keep the first (newest) one
                seen_hashes.add(row["body_sha256"])
                meta = json.loads(row["metadata_json"] or "{}")
                meta.pop("publisher_body_html", None)
                meta.pop("wordpress", None)
                video_id = meta.get("youtube_id")
                transcript_hashes = []
                if video_id:
                    transcript_hashes = [
                        r[0] for r in conn.execute("SELECT text_hash FROM transcripts WHERE video_id = ?", (video_id,))
                    ]
                records.append(
                    SourceRecord(
                        source_family=FAMILY,
                        source_doc_id=row["url"],
                        body=row["body_text"],
                        title=row["meta_title"] or row["url"],
                        revision_hash=row["body_sha256"],
                        source_locator=f"source_document_observations:{row['observation_id']}",
                        observed_at=row["observed_at"],
                        recorded_at=row["meta_date"],
                        recorded_at_basis="publisher_metadata_date",
                        metadata={
                            "provider": provider,
                            "publisher": publisher,
                            "observation_id": row["observation_id"],
                            "body_chars": row["body_chars"],
                            "has_transcript": row["has_transcript"],
                            "date": row["meta_date"],
                            "description": (meta.get("description") or "")[:500],
                            "youtube_id": video_id,
                            "related_transcript_text_hashes": transcript_hashes,
                            "selection_rule": "latest_ok_nonempty_v1",
                        },
                    )
                )
                kept += 1
            selection["providers"][provider] = {"candidates": len(rows), "kept": kept}
    except SourceUnavailable:
        raise
    except Exception as exc:
        raise SourceUnavailable(f"youtube catalog unreadable at {store_path}: {exc}") from exc
    finally:
        conn.close()
    selection["count"] = len(records)
    return records, selection
