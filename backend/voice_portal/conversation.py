"""Grounded turns and devotionals: retrieval → prompt → streamed deltas with citations attached at the end."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
import time
from collections.abc import Callable

from .config import Config
from .db import now
from .reasoning import ReasoningError, stream_chat
from .references import explicit_references, resolve_reference
from .retrieval import cite_id, document_by_source, focus_terms, resolve_citation, search
from .sources.scripture import BOOK_NAMES
from .sources.theographic import passage_entities

SYSTEM_PROMPT = """You are the voice of a personal Scripture and prayer portal for one user, Rawley.
Answer briefly in spoken-style sentences (two to five sentences), suitable for text-to-speech.
Use ONLY the numbered evidence passages below. Each passage is labelled with its kind:
SCRIPTURE (the World English Bible text), TEACHING (a pastor's published text; it is that teacher's interpretation),
or PRAYER (Rawley's own recorded prayer; a personal statement, not Scripture).
Cite evidence inline as [n]. Keep Scripture, a teacher's interpretation, and personal context clearly distinguished.
The evidence is a small set of retrieved excerpts, not the whole corpus. If it does not contain what was asked, say
"the retrieved excerpts do not include that" and stop; never say that something is absent from Rawley's prayers,
from Scripture or from the teaching, because you have not seen the whole corpus. Never invent a quotation, a date,
or a source."""


def _evidence_block(hits: list[dict]) -> str:
    lines = []
    for n, hit in enumerate(hits, 1):
        kind = {"scripture": "SCRIPTURE", "teaching": "TEACHING", "prayer": "PRAYER"}.get(hit["source_family"], hit["source_family"].upper())
        head = hit["title"] or hit["source_doc_id"]
        refs = ", ".join(hit.get("verse_refs") or [])
        if refs:
            head += f" ({refs})"
        when = hit.get("recorded_at") or ""
        lines.append(f"[{n}] {kind} — {head} {('recorded ' + str(when)[:10]) if when else ''}\n{hit['text'].strip()}")
    return "\n\n".join(lines)


def gather_evidence(conn: sqlite3.Connection, query: str, *, families: list[str] | None, passage_cite_ids: list[str] | None, per_family: int = 2) -> tuple[list[dict], dict]:
    """Balanced evidence: the passage actually heard first (its exact revision, even if a later import
    made it historical), then the best chunks of each source family. Returns (hits, scope)."""
    hits: list[dict] = []
    seen: set[str] = set()
    supplied: list[str] = []
    failed: list[str] = []
    for cite in passage_cite_ids or []:
        try:
            detail = resolve_citation(conn, cite)
        except ValueError:
            detail = {"available": False}
        if detail.get("available") and cite not in seen:
            seen.add(cite)
            supplied.append(cite)
            title = detail["title"] or detail["source_doc_id"]
            if not detail.get("current"):
                title += " (the revision you heard; a newer revision of this source exists)"
            hits.append({"cite_id": cite, "source_family": detail["source_family"], "source_doc_id": detail["source_doc_id"], "revision_hash": detail["revision_hash"], "title": title, "text": detail["text"], "attribution": detail["attribution"], "recorded_at": detail.get("recorded_at"), "verse_refs": [v["osis"] for v in detail.get("verse_locators") or []], "strategy": "heard_passage", "current": bool(detail.get("current"))})
        else:
            failed.append(cite)
    # Explicit references in the question ("Psalm 23", "1 Peter 5:7") are grounded in the requested
    # retained chapter before any topic retrieval; a reference that is not retained is reported, not invented.
    references: list[dict] = []
    for ref in explicit_references(query):
        ref_hits, report = resolve_reference(conn, ref)
        references.append(report)
        for hit in ref_hits[:4]:
            if hit["cite_id"] not in seen:
                seen.add(hit["cite_id"])
                hits.append(hit)
    strategies: dict[str, str | None] = {}
    for family in families or ["scripture", "teaching", "prayer"]:
        result = search(conn, query, families=[family], limit=per_family)
        strategies[family] = result.get("strategy")
        for hit in result["hits"]:
            if hit["cite_id"] not in seen:
                seen.add(hit["cite_id"])
                hits.append(hit)
    scope = {"passage_supplied": supplied, "passage_failed": failed, "explicit_references": references, "focus_terms": focus_terms(query), "strategies": strategies}
    return hits, scope


def run_turn(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    turn_id: str,
    session_id: str | None,
    transcript: str,
    context: dict | None,
    emit: Callable[[dict], None],
    cancel: threading.Event,
    families: list[str] | None = None,
) -> dict:
    """Execute one reasoning turn; emits assistant_text_delta / assistant_done / assistant_cancelled / error."""
    created = now()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO turns(turn_id, session_id, created_at, transcript, status, model) VALUES (?,?,?,?,?,?)",
            (turn_id, session_id, created, transcript, "running", cfg.inference_model),
        )
    query = transcript
    context = context or {}
    if context.get("passage_title"):
        query = f"{transcript} {context['passage_title']}"
    hits, scope = gather_evidence(conn, query, families=families, passage_cite_ids=context.get("passage_cite_ids"))
    passage_note = ""
    if scope["passage_supplied"]:
        passage_note = f"\nThe user has just been listening to: {context.get('passage_title')}. That passage is included in the evidence."
    elif context.get("passage_cite_ids"):
        passage_note = f"\nThe user was listening to {context.get('passage_title')}, but that passage could not be supplied as evidence; do not quote it."
    for ref in scope.get("explicit_references") or []:
        if ref["supplied"] and ref.get("reason") == "verses_not_in_retained_chapter":
            passage_note += f"\nThe question names {ref['matched']}; the retained chapter is included but those verse numbers are not in it."
        elif not ref["supplied"]:
            passage_note += f"\nThe question names {ref['matched']}, which is not in the retained Scripture edition; say so and do not quote it."
        else:
            passage_note += f"\nThe question names {ref['matched']}; that passage is included in the evidence."
    if scope["focus_terms"]:
        passage_note += f"\nThe retrieval looked for excerpts about: {', '.join(scope['focus_terms'])}."
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + passage_note},
        {"role": "user", "content": f"Evidence:\n\n{_evidence_block(hits) if hits else '(no evidence passages matched)'}\n\nQuestion: {transcript}"},
    ]
    started = time.monotonic()
    first_ms = None
    answer: list[str] = []
    seq = 0
    usage = None
    status = "done"
    error = None
    try:
        for item in stream_chat(cfg.inference_base_url, cfg.inference_model, messages, cancel=cancel, timeout=cfg.inference_timeout_s, max_tokens=cfg.inference_max_tokens, extra_body=cfg.inference_extra_body):
            if "delta" in item:
                if first_ms is None:
                    first_ms = int((time.monotonic() - started) * 1000)
                answer.append(item["delta"])
                seq += 1
                emit({"type": "assistant_text_delta", "turn_id": turn_id, "seq": seq, "text": item["delta"]})
            else:
                usage = item.get("usage")
                if item.get("reasoning_chars"):
                    usage = {**(usage or {}), "reasoning_chars_streamed": item["reasoning_chars"]}
                if item.get("cancelled"):
                    status = "cancelled"
    except ReasoningError as exc:
        status = "error"
        error = f"{exc} {exc.body or ''}".strip()
    done_ms = int((time.monotonic() - started) * 1000)
    citations = [
        {"n": n, "cite_id": h["cite_id"], "source_family": h["source_family"], "title": h["title"], "attribution": h["attribution"], "verse_refs": h.get("verse_refs") or [], "revision_hash": h["revision_hash"]}
        for n, h in enumerate(hits, 1)
    ]
    with conn:
        conn.execute(
            "UPDATE turns SET status = ?, answer = ?, citations_json = ?, first_delta_ms = ?, done_ms = ?, usage_json = ?, error = ? WHERE turn_id = ?",
            (status, "".join(answer), json.dumps(citations), first_ms, done_ms, json.dumps(usage) if usage else None, error, turn_id),
        )
    if status == "cancelled":
        emit({"type": "assistant_cancelled", "turn_id": turn_id, "partial": "".join(answer)})
    elif status == "error":
        emit({"type": "error", "turn_id": turn_id, "message": error, "citations": citations})
    else:
        emit({"type": "assistant_done", "turn_id": turn_id, "text": "".join(answer), "citations": citations, "first_delta_ms": first_ms, "done_ms": done_ms, "usage": usage, "model": cfg.inference_model, "evidence_count": len(hits), "scope": scope})
    return {"turn_id": turn_id, "status": status, "answer": "".join(answer), "citations": citations, "first_delta_ms": first_ms, "done_ms": done_ms, "usage": usage, "error": error, "scope": scope}


def _passage_for_date(cfg: Config, date: dt.date | None) -> str:
    date = date or dt.date.today()
    return cfg.devotional_passages[date.toordinal() % len(cfg.devotional_passages)]


def build_devotional(conn: sqlite3.Connection, cfg: Config, *, passage: str | None = None, date: str | None = None, verse_limit: int = 12) -> dict:
    """Verse segments from the retained edition plus one teaching excerpt, all with citations."""
    day = dt.date.fromisoformat(date) if date else None
    ref = passage or _passage_for_date(cfg, day)
    book, _, chapter = ref.partition(".")
    edition_doc = conn.execute(
        "SELECT * FROM documents WHERE source_family = 'scripture' AND current = 1 AND source_doc_id LIKE ? LIMIT 1",
        (f"%/{book}.{chapter}",),
    ).fetchone()
    if edition_doc is None:
        return {"available": False, "reason": "passage_not_imported", "passage": ref}
    locators = json.loads(edition_doc["locators_json"])
    chunks = conn.execute("SELECT chunk_index, start, end FROM chunks WHERE doc_id = ? ORDER BY chunk_index", (edition_doc["doc_id"],)).fetchall()

    def chunk_for(offset: int) -> int:
        for c in chunks:
            if c["start"] <= offset < c["end"]:
                return c["chunk_index"]
        return chunks[-1]["chunk_index"] if chunks else 0

    body = edition_doc["body"]
    segments = []
    verse_locs = [loc for loc in locators if loc.get("kind") == "verse"][:verse_limit]
    for loc in verse_locs:
        segments.append(
            {
                "kind": "scripture",
                "osis": loc["osis"],
                "verse": loc["verse"],
                "text": body[loc["start"]:loc["end"]],
                "cite_id": cite_id(edition_doc["doc_id"], edition_doc["revision_hash"], chunk_for(loc["start"])),
            }
        )
    meta = json.loads(edition_doc["metadata_json"])
    title = edition_doc["title"]
    teaching = search(conn, f"{BOOK_NAMES.get(book, book)} {chapter} " + " ".join(s["text"] for s in segments[:3]), families=["teaching"], limit=1)
    teaching_segment = None
    if teaching["hits"]:
        hit = teaching["hits"][0]
        teaching_segment = {"kind": "teaching", "text": hit["text"], "cite_id": hit["cite_id"], "title": hit["title"], "attribution": hit["attribution"], "recorded_at": hit["recorded_at"]}
    entities = passage_entities(conn, [s["osis"] for s in segments]) if conn.execute("SELECT COUNT(*) FROM theographic_verses").fetchone()[0] else None
    return {
        "available": True,
        "passage": ref,
        "title": title,
        "edition": meta.get("edition"),
        "revision_hash": edition_doc["revision_hash"],
        "source_doc_id": edition_doc["source_doc_id"],
        "verse_count_total": meta.get("verse_count"),
        "segments": segments + ([teaching_segment] if teaching_segment else []),
        "teaching_found": teaching_segment is not None,
        "theographic": entities,
    }


def passage_lookup(conn: sqlite3.Connection, book: str, chapter: int) -> dict | None:
    row = document_by_source(conn, "scripture", f"%/{book}.{chapter}")
    return dict(row) if row else None
