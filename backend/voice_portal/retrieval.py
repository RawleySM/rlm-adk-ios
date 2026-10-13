"""FTS5 retrieval over current chunks and version-qualified citation resolution."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3

STOPWORDS = {
    "the", "and", "a", "an", "of", "to", "in", "is", "it", "that", "this", "for", "on", "with", "as", "was",
    "are", "be", "by", "at", "or", "from", "what", "does", "do", "did", "how", "why", "who", "me", "my", "i",
    "you", "your", "we", "our", "he", "his", "she", "her", "they", "them", "about", "tell", "can", "please",
    "have", "has", "had", "not", "but", "so", "if", "then", "there", "here", "which", "when", "where", "into",
    "say", "says", "said", "mean", "means", "lord", "god",
}
TOKEN = re.compile(r"[0-9A-Za-z']+")

# Words that describe the *request* ("which prayer of mine speaks about…", "what Scripture fits…")
# rather than its subject. They are common across the whole corpus, so an OR query lets them
# outrank the one word that actually matters ("son"). They are dropped from the focus query and only
# return in the broad fallback when nothing else matches.
FRAME_WORDS = {
    "prayer", "prayers", "prayed", "pray", "praying", "scripture", "scriptures", "verse", "verses", "passage",
    "passages", "bible", "biblical", "teaching", "teachings", "sermon", "sermons", "devotional", "psalm",
    "speak", "speaks", "spoke", "spoken", "say", "says", "said", "mention", "mentions", "mentioned", "talk",
    "talks", "talked", "about", "which", "what", "fits", "fit", "suits", "suit", "mine", "my", "me", "find",
    "show", "give", "tell", "recorded", "recording", "ever", "any", "one", "something", "anything", "relate",
    "relates", "related", "connect", "connects", "regarding", "concerning", "again", "please", "could", "would",
}


def _tokens(text: str) -> list[str]:
    seen: list[str] = []
    for raw in TOKEN.findall(text):
        term = raw.lower().strip("'")
        if len(term) >= 2 and term not in seen:
            seen.append(term)
    return seen


def content_terms(text: str) -> list[str]:
    tokens = _tokens(text)
    terms = [t for t in tokens if t not in STOPWORDS]
    return terms or tokens


def focus_terms(text: str) -> list[str]:
    """Subject words of a natural request: content terms minus request-frame vocabulary."""
    return [t for t in content_terms(text) if t not in FRAME_WORDS]


def fts_query(text: str, *, operator: str = "OR") -> str:
    terms = content_terms(text)
    return f" {operator} ".join(f'"{term}"' for term in terms[:16])


def query_plan(text: str) -> list[tuple[str, str]]:
    """Ordered (strategy, match) pairs: all focus terms, any focus term, then the broad request."""
    focus = focus_terms(text)[:16]
    broad = fts_query(text)
    plan: list[tuple[str, str]] = []
    if focus:
        if len(focus) > 1:
            plan.append(("focus_all", " AND ".join(f'"{t}"' for t in focus)))
        plan.append(("focus_any", " OR ".join(f'"{t}"' for t in focus)))
    if broad and broad not in {m for _, m in plan}:
        plan.append(("broad", broad))
    return plan


def cite_id(doc_id: int, revision_hash: str, chunk_index: int) -> str:
    return f"{doc_id}:{revision_hash[:12]}:{chunk_index}"


def parse_cite_id(value: str) -> tuple[int, str, int]:
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError(f"malformed citation id: {value!r}")
    return int(parts[0]), parts[1], int(parts[2])


def search(conn: sqlite3.Connection, query: str, *, families: list[str] | None = None, limit: int = 8) -> dict:
    """Focus-first FTS: chunks matching every subject word rank first, then any subject word, then
    the broad request words. Each stage only fills what the previous stages left open."""
    plan = query_plan(query)
    if not plan:
        return {"query": query, "match": "", "strategy": None, "hits": []}
    hits: list[dict] = []
    seen: set[str] = set()
    strategies: list[str] = []
    for strategy, match in plan:
        remaining = limit - len(hits)
        if remaining <= 0:
            break
        stage = _search_match(conn, match, families=families, limit=remaining + len(hits))
        added = 0
        for hit in stage:
            if hit["cite_id"] not in seen and len(hits) < limit:
                hit["strategy"] = strategy
                seen.add(hit["cite_id"])
                hits.append(hit)
                added += 1
        if added:
            strategies.append(strategy)
    return {"query": query, "match": plan[0][1], "plan": plan, "strategy": "+".join(strategies) or None, "focus_terms": focus_terms(query), "hits": hits}


def _search_match(conn: sqlite3.Connection, match: str, *, families: list[str] | None, limit: int) -> list[dict]:
    sql = (
        "SELECT f.chunk_id, f.doc_id, f.source_family, bm25(chunks_fts) AS rank, snippet(chunks_fts, 0, '[', ']', ' … ', 24) AS snip, "
        "c.chunk_index, c.start, c.end, c.text, d.source_doc_id, d.revision_hash, d.title, d.recorded_at, d.recorded_at_basis, d.observed_at, d.metadata_json "
        "FROM chunks_fts f JOIN chunks c ON c.chunk_id = f.chunk_id JOIN documents d ON d.doc_id = f.doc_id "
        "WHERE chunks_fts MATCH ? AND d.current = 1"
    )
    params: list = [match]
    if families:
        sql += " AND d.source_family IN (%s)" % ",".join("?" for _ in families)
        params.extend(families)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    hits: list[dict] = []
    for row in conn.execute(sql, params):
        meta = json.loads(row["metadata_json"] or "{}")
        hits.append(
            {
                "cite_id": cite_id(row["doc_id"], row["revision_hash"], row["chunk_index"]),
                "source_family": row["source_family"],
                "source_doc_id": row["source_doc_id"],
                "revision_hash": row["revision_hash"],
                "title": row["title"],
                "chunk_index": row["chunk_index"],
                "start": row["start"],
                "end": row["end"],
                "rank": row["rank"],
                "snippet": row["snip"],
                "text": row["text"],
                "recorded_at": row["recorded_at"],
                "recorded_at_basis": row["recorded_at_basis"],
                "observed_at": row["observed_at"],
                "attribution": attribution(row["source_family"], meta),
                "verse_refs": verse_refs_for_span(json.loads(conn.execute("SELECT locators_json FROM documents WHERE doc_id = ?", (row["doc_id"],)).fetchone()[0]), row["start"], row["end"]),
            }
        )
    return hits


def attribution(family: str, meta: dict) -> str:
    if family == "scripture":
        ed = meta.get("edition") or {}
        return f"Scripture, {ed.get('name') or ed.get('source_id')} {ed.get('version') or ''}".strip()
    if family == "teaching":
        return f"Teaching, {meta.get('publisher') or meta.get('provider')} ({meta.get('date') or 'undated'})"
    if family == "prayer":
        return f"Personal prayer (recorded, {meta.get('asr_model_id') or 'transcribed'})"
    return family


def verse_refs_for_span(locators: list[dict], start: int, end: int) -> list[str]:
    return [loc["osis"] for loc in locators if loc.get("kind") == "verse" and loc["start"] < end and loc["end"] > start]


def resolve_citation(conn: sqlite3.Connection, value: str) -> dict:
    """Return the exact cited text for a version-qualified citation, or an explicit unavailable payload."""
    doc_id, rev12, chunk_index = parse_cite_id(value)
    doc = conn.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
    if doc is None:
        return {"cite_id": value, "available": False, "reason": "document_not_found"}
    if doc["revision_hash"][:12] != rev12:
        return {"cite_id": value, "available": False, "reason": "revision_mismatch", "stored_revision": doc["revision_hash"]}
    chunk = conn.execute("SELECT * FROM chunks WHERE doc_id = ? AND chunk_index = ?", (doc_id, chunk_index)).fetchone()
    if chunk is None:
        return {"cite_id": value, "available": False, "reason": "chunk_not_found"}
    body = doc["body"]
    verified = hashlib.sha256(body.encode("utf-8")).hexdigest() == doc["body_sha256"] and body[chunk["start"]:chunk["end"]] == chunk["text"]
    locators = json.loads(doc["locators_json"] or "[]")
    meta = json.loads(doc["metadata_json"] or "{}")
    return {
        "cite_id": value,
        "available": True,
        "verified": verified,
        "current": bool(doc["current"]),
        "removed_at": doc["removed_at"],
        "source_family": doc["source_family"],
        "source_doc_id": doc["source_doc_id"],
        "revision_hash": doc["revision_hash"],
        "body_sha256": doc["body_sha256"],
        "title": doc["title"],
        "attribution": attribution(doc["source_family"], meta),
        "source_locator": doc["source_locator"],
        "observed_at": doc["observed_at"],
        "recorded_at": doc["recorded_at"],
        "recorded_at_basis": doc["recorded_at_basis"],
        "chunk_index": chunk_index,
        "start": chunk["start"],
        "end": chunk["end"],
        "text": chunk["text"],
        "verse_locators": [loc for loc in locators if loc.get("kind") == "verse" and loc["start"] < chunk["end"] and loc["end"] > chunk["start"]],
        "metadata": meta,
    }


def document_by_source(conn: sqlite3.Connection, family: str, source_doc_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM documents WHERE source_family = ? AND source_doc_id = ? AND current = 1", (family, source_doc_id)
    ).fetchone()


def document_revisions(conn: sqlite3.Connection, family: str, source_doc_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT doc_id, revision_hash, body_sha256, length(body) AS chars, imported_at, current, removed_at FROM documents "
        "WHERE source_family = ? AND source_doc_id = ? ORDER BY imported_at", (family, source_doc_id)
    ).fetchall()
    return [dict(r) for r in rows]
