"""`GET /tools/scripture`: the retained WEB edition's verses for a spoken or written reference.

`spoken` is the verse text exactly as retained, joined with single spaces in reading order; nothing is
paraphrased, numbered or prefixed, so the text harness can compare it to the edition verbatim. A
reference that is unknown, not retained or invalid returns `available: false` with a spoken reason.
"""

from __future__ import annotations

import sqlite3

from ..references import explicit_references, passage_verses
from .spoken import normalize_spoken_reference, spoken_reference, written_reference


def _reason(passage: dict) -> str:
    report = passage["report"]
    name = spoken_reference(passage["book_name"], passage["chapter"], passage["verse_from"], passage["verse_to"])
    reason = report.get("reason")
    if reason in ("invalid_verse", "invalid_chapter", "reversed_range"):
        return f"{name} is not a valid reference."
    if reason == "verses_not_in_retained_chapter":
        return f"{spoken_reference(passage['book_name'], passage['chapter'])} in the retained World English Bible has no such verse."
    return f"{name} is not in the retained World English Bible edition."


def scripture_tool(conn: sqlite3.Connection, reference: str) -> dict:
    """Resolve every reference in `reference` (reading order) to verbatim retained verses.

    Args:
        conn: Consumer projection connection.
        reference: The user's words ("Psalm 23", "Romans 8:28-30", "John chapter three verse sixteen").

    Returns:
        `{available, request, normalized, reference, references, title, edition, verses, cite_ids,
        coverage, spoken, spoken_notice}`; `coverage` is full, partial or none over all references.
    """
    normalized = normalize_spoken_reference(reference)
    refs = explicit_references(normalized)
    base = {"request": reference, "normalized": normalized}
    if not refs:
        return {**base, "available": False, "reason": "no_reference_recognised", "references": [], "verses": [], "cite_ids": [], "coverage": "none", "spoken": "I did not hear a Scripture reference I can look up."}
    passages = [passage_verses(conn, ref) for ref in refs]
    verses = [verse for passage in passages for verse in passage["verses"]]
    cite_ids: list[str] = []
    for passage in passages:
        cite_ids.extend(c for c in passage["cite_ids"] if c not in cite_ids)
    supplied = [p for p in passages if p["available"]]
    missing = [p for p in passages if not p["available"]]
    if not supplied:
        coverage = "none"
    elif missing or any(p["coverage"] != "full" for p in supplied):
        coverage = "partial"
    else:
        coverage = "full"
    notices = [_reason(p) for p in missing]
    for passage in supplied:
        lost = passage["report"].get("verses_missing") or []
        if lost:
            numbers = ", ".join(v.split(":")[-1] for v in lost)
            notices.append(f"Verses {numbers} of {spoken_reference(passage['book_name'], passage['chapter'])} are not in the retained edition.")
    first = supplied[0] if supplied else passages[0]
    result = {
        **base,
        "available": bool(supplied),
        "reference": ", ".join(written_reference(p["book_name"], p["chapter"], p["verse_from"], p["verse_to"]) for p in passages),
        "spoken_reference": ", ".join(spoken_reference(p["book_name"], p["chapter"], p["verse_from"], p["verse_to"]) for p in passages),
        "references": [{k: p[k] for k in ("ref", "book", "chapter", "verse_from", "verse_to", "available", "coverage", "title")} | {"verses_missing": p["report"].get("verses_missing") or [], "reason": p["report"].get("reason")} for p in passages],
        "title": first["title"],
        "edition": first["edition"],
        "verses": verses,
        "cite_ids": cite_ids,
        "coverage": coverage,
        "spoken_notice": " ".join(notices) or None,
    }
    if supplied:
        result["spoken"] = " ".join(verse["text"] for verse in verses)
    else:
        result["reason"] = missing[0]["report"].get("reason")
        result["spoken"] = " ".join(notices)
    return result
