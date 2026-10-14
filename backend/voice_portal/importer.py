"""Idempotent, versioned import of selected source records into the consumer projection.

Rules (from the plan):
- reimport of identical source data changes nothing;
- a changed body becomes current and the previous revision remains inspectable (current=0);
- a record missing from a family's full selection loses membership (current=0, removed_at) and
  cannot remain an FTS hit;
- chunk boundaries are deterministic for a given body;
- title and metadata are descriptive: an unchanged body refreshes them in place without a new revision;
- a family fed by several adapters (teaching: the YouTube/publisher catalog and the scripture_reference
  library) scopes membership removal with `membership_prefix`, so one adapter's complete selection
  never retires another adapter's records.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from .db import now
from .sources import SourceRecord, SourceUnavailable

CHUNK_TARGET = 700  # code points; paragraphs are merged up to this size, long paragraphs are split


def chunk_boundaries(body: str, target: int = CHUNK_TARGET) -> list[tuple[int, int]]:
    """Deterministic (start, end) code-point spans covering the non-empty text of `body`."""
    spans: list[tuple[int, int]] = []
    pos = 0
    length = len(body)
    paragraphs: list[tuple[int, int]] = []
    while pos < length:
        nxt = body.find("\n\n", pos)
        end = length if nxt == -1 else nxt
        start = pos
        while start < end and body[start].isspace():
            start += 1
        stop = end
        while stop > start and body[stop - 1].isspace():
            stop -= 1
        if stop > start:
            paragraphs.append((start, stop))
        pos = end + 2 if nxt != -1 else length
    for start, stop in paragraphs:
        if stop - start <= target:
            if spans and (stop - spans[-1][0]) <= target:
                spans[-1] = (spans[-1][0], stop)
            else:
                spans.append((start, stop))
            continue
        cursor = start
        while cursor < stop:
            limit = min(cursor + target, stop)
            if limit < stop:
                cut = max(body.rfind(". ", cursor, limit), body.rfind("? ", cursor, limit), body.rfind("! ", cursor, limit), body.rfind("\n", cursor, limit))
                if cut > cursor + target // 3:
                    limit = cut + 1
            while limit > cursor and body[limit - 1].isspace():
                limit -= 1
            if limit <= cursor:
                limit = min(cursor + target, stop)
            spans.append((cursor, limit))
            cursor = limit
            while cursor < stop and body[cursor].isspace():
                cursor += 1
    return spans


def _insert_chunks(conn: sqlite3.Connection, doc_id: int, family: str, body: str) -> int:
    count = 0
    for index, (start, end) in enumerate(chunk_boundaries(body)):
        text = body[start:end]
        cur = conn.execute(
            "INSERT INTO chunks(doc_id, chunk_index, start, end, text) VALUES (?,?,?,?,?)",
            (doc_id, index, start, end, text),
        )
        conn.execute(
            "INSERT INTO chunks_fts(text, chunk_id, doc_id, source_family) VALUES (?,?,?,?)",
            (text, cur.lastrowid, doc_id, family),
        )
        count += 1
    return count


def _retire(conn: sqlite3.Connection, doc_id: int, removed: bool) -> None:
    conn.execute(
        "UPDATE documents SET current = 0, removed_at = COALESCE(removed_at, ?) WHERE doc_id = ?",
        (now() if removed else None, doc_id),
    )
    conn.execute("DELETE FROM chunks_fts WHERE doc_id = ?", (doc_id,))


def _activate(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    conn.execute("UPDATE documents SET current = 1, removed_at = NULL WHERE doc_id = ?", (row["doc_id"],))
    conn.execute("DELETE FROM chunks_fts WHERE doc_id = ?", (row["doc_id"],))
    for chunk in conn.execute("SELECT chunk_id, text FROM chunks WHERE doc_id = ? ORDER BY chunk_index", (row["doc_id"],)):
        conn.execute(
            "INSERT INTO chunks_fts(text, chunk_id, doc_id, source_family) VALUES (?,?,?,?)",
            (chunk["text"], chunk["chunk_id"], row["doc_id"], row["source_family"]),
        )


def import_family(
    conn: sqlite3.Connection,
    family: str,
    records: Iterable[SourceRecord],
    *,
    selection: dict,
    source_path: str,
    complete_selection: bool = True,
    membership_prefix: str | None = None,
    receipt_family: str | None = None,
) -> dict:
    """Import one family's selection. Returns counts.

    `complete_selection` enables membership removal; with `membership_prefix` only current records whose
    `source_doc_id` starts with that prefix can lose membership (the adapter's own scope).
    `receipt_family` names the receipt row (default: the family) so several adapters of one family
    keep separately visible receipts.
    """
    started = now()
    counts = {"seen": 0, "inserted": 0, "unchanged": 0, "revised": 0, "reinstated": 0, "removed": 0, "chunks": 0}
    seen_ids: set[str] = set()
    if membership_prefix is not None:
        selection = {**selection, "membership_prefix": membership_prefix}
    receipt = conn.execute(
        "INSERT INTO import_receipts(source_family, started_at, source_path, selection_json) VALUES (?,?,?,?)",
        (receipt_family or family, started, source_path, json.dumps(selection, default=str)),
    ).lastrowid
    try:
        with conn:
            for rec in records:
                counts["seen"] += 1
                seen_ids.add(rec.source_doc_id)
                revision = rec.revision
                existing = conn.execute(
                    "SELECT doc_id, source_family, current FROM documents WHERE source_family = ? AND source_doc_id = ? AND revision_hash = ?",
                    (family, rec.source_doc_id, revision),
                ).fetchone()
                if existing is not None:
                    if existing["current"]:
                        counts["unchanged"] += 1
                        metadata_json = json.dumps(rec.metadata, default=str)
                        conn.execute(
                            "UPDATE documents SET title = ?, metadata_json = ? WHERE doc_id = ? AND (title IS NOT ? OR metadata_json IS NOT ?)",
                            (rec.title, metadata_json, existing["doc_id"], rec.title, metadata_json),
                        )
                    else:
                        for other in conn.execute(
                            "SELECT doc_id FROM documents WHERE source_family = ? AND source_doc_id = ? AND current = 1",
                            (family, rec.source_doc_id),
                        ).fetchall():
                            _retire(conn, other["doc_id"], removed=False)
                        _activate(conn, existing)
                        counts["reinstated"] += 1
                    continue
                previous = conn.execute(
                    "SELECT doc_id FROM documents WHERE source_family = ? AND source_doc_id = ? AND current = 1",
                    (family, rec.source_doc_id),
                ).fetchall()
                for other in previous:
                    _retire(conn, other["doc_id"], removed=False)
                cur = conn.execute(
                    "INSERT INTO documents(source_family, source_doc_id, revision_hash, title, body, body_sha256, locators_json, "
                    "source_locator, observed_at, recorded_at, recorded_at_basis, metadata_json, imported_at, current) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (
                        family, rec.source_doc_id, revision, rec.title, rec.body, rec.body_sha256,
                        json.dumps(rec.locators, separators=(",", ":")), rec.source_locator, rec.observed_at,
                        rec.recorded_at, rec.recorded_at_basis, json.dumps(rec.metadata, default=str), now(),
                    ),
                )
                counts["chunks"] += _insert_chunks(conn, cur.lastrowid, family, rec.body)
                counts["revised" if previous else "inserted"] += 1
            if complete_selection:
                scope_sql = "" if membership_prefix is None else " AND substr(source_doc_id, 1, ?) = ?"
                scope_params: tuple = () if membership_prefix is None else (len(membership_prefix), membership_prefix)
                for row in conn.execute(
                    "SELECT doc_id, source_doc_id FROM documents WHERE source_family = ? AND current = 1" + scope_sql, (family, *scope_params)
                ).fetchall():
                    if row["source_doc_id"] not in seen_ids:
                        _retire(conn, row["doc_id"], removed=True)
                        counts["removed"] += 1
            conn.execute(
                "UPDATE import_receipts SET finished_at = ?, counts_json = ? WHERE receipt_id = ?",
                (now(), json.dumps(counts), receipt),
            )
    except SourceUnavailable as exc:
        with conn:
            conn.execute("UPDATE import_receipts SET finished_at = ?, error = ? WHERE receipt_id = ?", (now(), str(exc), receipt))
        raise
    counts["receipt_id"] = receipt
    return counts


def family_counts(conn: sqlite3.Connection) -> dict:
    out: dict = {}
    for row in conn.execute(
        "SELECT source_family, SUM(current) AS current, COUNT(*) AS revisions FROM documents GROUP BY source_family"
    ):
        out[row["source_family"]] = {"current": row["current"], "revisions": row["revisions"]}
    for name in ("theographic_people", "theographic_places", "theographic_verses"):
        out[name] = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    return out


def latest_receipts(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT r.* FROM import_receipts r JOIN (SELECT source_family, MAX(receipt_id) AS receipt_id FROM import_receipts GROUP BY source_family) m "
        "ON m.receipt_id = r.receipt_id ORDER BY r.source_family"
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["selection"] = json.loads(item.pop("selection_json") or "{}")
        item["counts"] = json.loads(item.pop("counts_json") or "{}")
        out.append(item)
    return out
