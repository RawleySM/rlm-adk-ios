"""Explicit Scripture references in the user's own words ("Psalm 23", "1 Peter 5:7", "Psalm 23:4-6",
"First Peter 5", "II Kings 2").

Deliberately small: canonical book names from the Scripture projection's own `BOOK_NAMES`, their
numbered forms with written or spoken ordinals, and a few spoken aliases (Psalm/Ps). A reference is
book + chapter with an optional verse or verse range. Resolution reports exactly what the retained
edition supplies: whole chapter, only the chunks holding the named verses, a partial verse range, a
or nothing (unknown book, chapter not retained, invalid verse numbers). Whole retained chapters are
always supplied in full: the largest in the real store (Psalm 119) is about 12.8k characters.
Nothing is invented.
"""

from __future__ import annotations

import json
import re
import sqlite3

from .retrieval import cite_id
from .sources.scripture import BOOK_NAMES

ORDINALS = {"1": "1", "2": "2", "3": "3", "first": "1", "second": "2", "third": "3", "i": "1", "ii": "2", "iii": "3"}
ALIASES = {"psalm": "PSA", "psalms": "PSA", "ps": "PSA", "song of solomon": "SNG", "revelations": "REV"}

# unnumbered canonical names ("genesis", "john", "jude") and numbered bases ("peter" -> {1: 1PE, 2: 2PE})
_PLAIN: dict[str, str] = {}
_NUMBERED: dict[str, dict[str, str]] = {}
for _code, _name in BOOK_NAMES.items():
    lower = _name.lower()
    if lower[0].isdigit():
        _NUMBERED.setdefault(lower[2:], {})[lower[0]] = _code
    else:
        _PLAIN[lower] = _code
_NAMES = sorted(set(_PLAIN) | set(_NUMBERED) | set(ALIASES), key=len, reverse=True)
# Book names and aliases as matched in text (longest first), for callers that pre-normalise speech.
BOOK_NAME_PATTERNS: tuple[str, ...] = tuple(_NAMES)
_REF = re.compile(
    r"(?<![A-Za-z0-9])(?:(?P<ord>1|2|3|first|second|third|iii|ii|i)\s+)?(?P<book>" + "|".join(re.escape(n) for n in _NAMES) + r")\.?\s+(?P<chapter>\d{1,3})(?:\s*[:.]\s*(?P<v1>\d{1,3})(?:\s*[-–]\s*(?P<v2>\d{1,3}))?)?(?![0-9])",
    re.IGNORECASE,
)


def _book_code(ordinal: str | None, name: str) -> str | None:
    lower = name.lower()
    number = ORDINALS.get((ordinal or "").lower())
    if number:
        return _NUMBERED.get(lower, {}).get(number)  # "First Peter" -> 1PE; "First Genesis" -> None
    if lower in _PLAIN:
        return _PLAIN[lower]  # "John" -> JHN (never 1JN without an ordinal)
    return ALIASES.get(lower)  # "Psalm" -> PSA; a bare numbered base ("Peter") is not a book


def explicit_references(text: str) -> list[dict]:
    """Parse references in reading order; unknown books are skipped. Invalid verse numbers are kept
    and flagged so resolution can report them instead of silently widening to the chapter."""
    out: list[dict] = []
    seen: set[str] = set()
    for m in _REF.finditer(text):
        code = _book_code(m.group("ord"), m.group("book"))
        if code is None:
            continue
        chapter = int(m.group("chapter"))
        v1 = int(m.group("v1")) if m.group("v1") else None
        v2 = int(m.group("v2")) if m.group("v2") else v1
        invalid = None
        if chapter < 1:
            invalid = "invalid_chapter"
        elif v1 is not None and (v1 < 1 or (v2 is not None and v2 < 1)):
            invalid = "invalid_verse"
        elif v1 is not None and v2 is not None and v2 < v1:
            invalid = "reversed_range"
        ref = f"{code}.{chapter}" + (f":{v1}" + (f"-{v2}" if v2 is not None and v2 != v1 else "") if v1 is not None else "")
        if ref in seen:
            continue
        seen.add(ref)
        out.append({"ref": ref, "book": code, "chapter": chapter, "verse_from": v1, "verse_to": v2, "matched": m.group(0).strip(), "invalid": invalid})
    return out


def _hit(doc, chunk, locators, edition) -> dict:
    return {
        "cite_id": cite_id(doc["doc_id"], doc["revision_hash"], chunk["chunk_index"]),
        "source_family": "scripture",
        "source_doc_id": doc["source_doc_id"],
        "revision_hash": doc["revision_hash"],
        "title": doc["title"],
        "text": chunk["text"],
        "attribution": f"Scripture, {edition.get('name') or edition.get('source_id')} {edition.get('version') or ''}".strip(),
        "recorded_at": None,
        "verse_refs": [loc["osis"] for loc in locators if loc["start"] < chunk["end"] and loc["end"] > chunk["start"]],
        "strategy": "explicit_reference",
        "current": True,
    }


def resolve_reference(conn: sqlite3.Connection, ref: dict) -> tuple[list[dict], dict]:
    """Return (hits, report). `report.coverage` is one of:
    - "full": the whole chapter, or every requested verse, is in the supplied chunks;
    - "partial": the chunks holding every retained requested verse are supplied, but some requested
      verse numbers are not in the retained chapter (`verses_missing`);
    - "none": nothing supplied (unknown book, chapter not retained, invalid/absent verses).
    `chunks_supplied` / `chunks_total` count the chapter's chunks actually included.
    """
    report = {**ref, "supplied": False, "coverage": "none", "verses_found": [], "verses_missing": [], "chunks_supplied": 0, "chunks_total": 0, "reason": ref.get("invalid")}
    if ref.get("invalid"):
        return [], report
    doc = conn.execute(
        "SELECT * FROM documents WHERE source_family = 'scripture' AND current = 1 AND source_doc_id LIKE ? ORDER BY doc_id LIMIT 1",
        (f"%/{ref['book']}.{ref['chapter']}",),
    ).fetchone()
    if doc is None:
        report["reason"] = "chapter_not_in_retained_edition"
        return [], report
    locators = [loc for loc in json.loads(doc["locators_json"] or "[]") if loc.get("kind") == "verse"]
    chunks = conn.execute("SELECT chunk_index, start, end, text FROM chunks WHERE doc_id = ? ORDER BY chunk_index", (doc["doc_id"],)).fetchall()
    edition = (json.loads(doc["metadata_json"] or "{}").get("edition")) or {}
    report["chunks_total"] = len(chunks)
    if ref["verse_from"] is not None:
        wanted_numbers = list(range(ref["verse_from"], (ref["verse_to"] or ref["verse_from"]) + 1))
        by_number = {int(loc["verse"]): loc for loc in locators}
        found = [by_number[n] for n in wanted_numbers if n in by_number]
        report["verses_found"] = [loc["osis"] for loc in found]
        report["verses_missing"] = [f"{ref['book']}.{ref['chapter']}:{n}" for n in wanted_numbers if n not in by_number]
        if not found:
            report["reason"] = "verses_not_in_retained_chapter"
            return [], report
        lo = min(loc["start"] for loc in found)
        hi = max(loc["end"] for loc in found)
        wanted = [c for c in chunks if c["start"] < hi and c["end"] > lo]
        coverage = "full" if not report["verses_missing"] else "partial"
        if report["verses_missing"]:
            report["reason"] = "some_verses_not_in_retained_chapter"
    else:
        wanted = chunks
        coverage = "full"
    hits = [_hit(doc, c, locators, edition) for c in wanted]
    report["chunks_supplied"] = len(hits)
    report["supplied"] = bool(hits)
    report["coverage"] = coverage if hits else "none"
    return hits, report


def _compress(numbers: list[int]) -> str:
    """[7, 8, 9, 12] -> '7-9, 12'"""
    out: list[str] = []
    start = prev = None
    for n in numbers:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if prev != start else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if prev != start else str(start))
    return ", ".join(out)


def coverage_note(report: dict) -> str:
    """One sentence for the model describing exactly what of the named reference is in the evidence."""
    name = report["matched"]
    if not report["supplied"]:
        if report.get("reason") in ("invalid_verse", "invalid_chapter", "reversed_range"):
            return f"The question names {name}, which is not a valid reference ({report['reason'].replace('_', ' ')}); nothing from it is included."
        return f"The question names {name}, which is not in the retained Scripture edition; say so and do not quote it."
    if report["coverage"] == "full":
        return f"The question names {name}; that passage is included in the evidence."
    found = _compress([int(v.rsplit(".", 1)[-1]) for v in report["verses_found"]])
    missing = _compress([int(v.split(":")[-1]) for v in report["verses_missing"]])
    return f"The question names {name}; verses {found} are in the retained chapter and included; verses {missing} are not in it."


def passage_verses(conn: sqlite3.Connection, ref: dict) -> dict:
    """The retained edition's verses for one parsed reference, verbatim, with their citations.

    Returns `{available, ref, book, book_name, chapter, verse_from, verse_to, title, edition, verses:
    [{osis, verse, text}], cite_ids, coverage, report}`. `verses` are exact slices of the projected
    chapter body at the reader's verse locators (whole chapter when no verse was named); `cite_ids`
    are the version-qualified chunks that hold them (the same ones `resolve_reference` supplies).
    """
    hits, report = resolve_reference(conn, ref)
    out = {
        "available": False,
        "ref": ref["ref"],
        "book": ref["book"],
        "book_name": BOOK_NAMES.get(ref["book"], ref["book"]),
        "chapter": ref["chapter"],
        "verse_from": ref.get("verse_from"),
        "verse_to": ref.get("verse_to"),
        "title": None,
        "edition": None,
        "verses": [],
        "cite_ids": [hit["cite_id"] for hit in hits],
        "coverage": report["coverage"],
        "report": report,
    }
    if not hits:
        return out
    doc = conn.execute(
        "SELECT * FROM documents WHERE source_family = 'scripture' AND current = 1 AND source_doc_id LIKE ? ORDER BY doc_id LIMIT 1",
        (f"%/{ref['book']}.{ref['chapter']}",),
    ).fetchone()
    body = doc["body"]
    locators = [loc for loc in json.loads(doc["locators_json"] or "[]") if loc.get("kind") == "verse"]
    if ref.get("verse_from") is not None:
        wanted = set(range(ref["verse_from"], (ref.get("verse_to") or ref["verse_from"]) + 1))
        locators = [loc for loc in locators if int(loc["verse"]) in wanted]
    out.update(
        available=bool(locators),
        title=doc["title"],
        edition=(json.loads(doc["metadata_json"] or "{}").get("edition")) or {},
        verses=[{"osis": loc["osis"], "verse": int(loc["verse"]), "text": body[loc["start"]:loc["end"]]} for loc in locators],
    )
    return out
