"""Explicit Scripture references in a question ("Psalm 23", "1 Peter 5:7", "Psalm 23:4-6").

This is deliberately small: book names come from the same table the Scripture projection uses
(`BOOK_NAMES`), plus a few spoken aliases; a reference is BOOK + chapter with an optional verse or
verse range. It resolves only against chapters that are actually in the retained projection; an
unknown book, missing chapter or out-of-range verse is reported, never invented.
"""

from __future__ import annotations

import json
import re
import sqlite3

from .retrieval import cite_id
from .sources.scripture import BOOK_NAMES

ALIASES = {
    "psalm": "PSA", "psalms": "PSA", "ps": "PSA",
    "song of solomon": "SNG", "songs": "SNG", "canticles": "SNG",
    "revelations": "REV", "rev": "REV",
    "gen": "GEN", "ex": "EXO", "deut": "DEU", "josh": "JOS", "prov": "PRO", "eccl": "ECC", "isa": "ISA",
    "jer": "JER", "ezek": "EZK", "dan": "DAN", "matt": "MAT", "mk": "MRK", "lk": "LUK", "jn": "JHN",
    "rom": "ROM", "cor": "COR", "gal": "GAL", "eph": "EPH", "phil": "PHP", "col": "COL", "thess": "TH",
    "tim": "TI", "heb": "HEB", "jas": "JAS", "pet": "PE",
}
# "1 Peter" / "First Peter" / "I Peter" → ordinal prefix
ORDINALS = {"1": "1", "2": "2", "3": "3", "first": "1", "second": "2", "third": "3", "i": "1", "ii": "2", "iii": "3"}

_BOOK_BY_NAME = {name.lower(): code for code, name in BOOK_NAMES.items()}  # "1 peter" -> 1PE, "psalms" -> PSA
_NAME_PATTERN = "|".join(sorted((re.escape(n) for n in list(_BOOK_BY_NAME) + list(ALIASES)), key=len, reverse=True))
_REF = re.compile(
    r"(?<![A-Za-z0-9])(?:(?P<ord>1|2|3|first|second|third|i|ii|iii)\s+)?(?P<book>" + _NAME_PATTERN + r")\.?\s+(?P<chapter>\d{1,3})(?:\s*[:.]\s*(?P<v1>\d{1,3})(?:\s*[-–]\s*(?P<v2>\d{1,3}))?)?(?![0-9])",
    re.IGNORECASE,
)


def _book_code(ordinal: str | None, name: str) -> str | None:
    lower = name.lower()
    ordinal = ORDINALS.get((ordinal or "").lower(), "")
    full = f"{ordinal} {lower}".strip()
    if full in _BOOK_BY_NAME:
        return _BOOK_BY_NAME[full]
    if lower in _BOOK_BY_NAME and not ordinal:
        return _BOOK_BY_NAME[lower]
    alias = ALIASES.get(lower)
    if alias is None:
        return None
    if alias in BOOK_NAMES and not ordinal:
        return alias
    candidate = f"{ordinal}{alias}"  # e.g. "1" + "PE" -> 1PE, "2" + "TI" -> 2TI
    return candidate if candidate in BOOK_NAMES else None


def explicit_references(text: str) -> list[dict]:
    """Parse references in reading order; unknown books are skipped (e.g. 'Peter 5' alone)."""
    out: list[dict] = []
    seen: set[str] = set()
    for m in _REF.finditer(text):
        code = _book_code(m.group("ord"), m.group("book"))
        if code is None:
            continue
        chapter = int(m.group("chapter"))
        v1 = int(m.group("v1")) if m.group("v1") else None
        v2 = int(m.group("v2")) if m.group("v2") else v1
        ref = f"{code}.{chapter}" + (f":{v1}" + (f"-{v2}" if v2 and v2 != v1 else "") if v1 else "")
        if ref in seen:
            continue
        seen.add(ref)
        out.append({"ref": ref, "book": code, "chapter": chapter, "verse_from": v1, "verse_to": v2, "matched": m.group(0).strip()})
    return out


def resolve_reference(conn: sqlite3.Connection, ref: dict) -> tuple[list[dict], dict]:
    """Return (hits, report) for one parsed reference against the current retained edition.

    Hits are the chapter's chunks (all of them, or only those holding the requested verses), each
    with a revision-qualified cite id. The report says exactly what was and was not found.
    """
    report = {**ref, "supplied": False, "verses_found": [], "reason": None}
    doc = conn.execute(
        "SELECT * FROM documents WHERE source_family = 'scripture' AND current = 1 AND source_doc_id LIKE ? ORDER BY doc_id LIMIT 1",
        (f"%/{ref['book']}.{ref['chapter']}",),
    ).fetchone()
    if doc is None:
        report["reason"] = "chapter_not_in_retained_edition"
        return [], report
    locators = [loc for loc in json.loads(doc["locators_json"] or "[]") if loc.get("kind") == "verse"]
    chunks = conn.execute("SELECT chunk_index, start, end, text FROM chunks WHERE doc_id = ? ORDER BY chunk_index", (doc["doc_id"],)).fetchall()
    wanted = chunks
    if ref["verse_from"]:
        verse_locs = [loc for loc in locators if ref["verse_from"] <= int(loc["verse"]) <= (ref["verse_to"] or ref["verse_from"])]
        report["verses_found"] = [loc["osis"] for loc in verse_locs]
        if verse_locs:
            lo = min(loc["start"] for loc in verse_locs)
            hi = max(loc["end"] for loc in verse_locs)
            wanted = [c for c in chunks if c["start"] < hi and c["end"] > lo]
        else:
            report["reason"] = "verses_not_in_retained_chapter"  # the chapter is still supplied, the verses are not claimed
    meta = json.loads(doc["metadata_json"] or "{}")
    edition = meta.get("edition") or {}
    hits = []
    for c in wanted:
        hits.append(
            {
                "cite_id": cite_id(doc["doc_id"], doc["revision_hash"], c["chunk_index"]),
                "source_family": "scripture",
                "source_doc_id": doc["source_doc_id"],
                "revision_hash": doc["revision_hash"],
                "title": doc["title"],
                "text": c["text"],
                "attribution": f"Scripture, {edition.get('name') or edition.get('source_id')} {edition.get('version') or ''}".strip(),
                "recorded_at": None,
                "verse_refs": [loc["osis"] for loc in locators if loc["start"] < c["end"] and loc["end"] > c["start"]],
                "strategy": "explicit_reference",
                "current": True,
            }
        )
    report["supplied"] = bool(hits)
    return hits, report
