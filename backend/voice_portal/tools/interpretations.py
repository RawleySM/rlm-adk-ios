"""`GET /tools/interpretations`: different teachers' readings of a passage or topic, each attributed.

Sources are the teaching family only (Desiring God and Eleven22 from the publisher catalog; Jonathan
Edwards and BibleProject from the scripture_reference library). Retrieval runs in stages, best first:
the reference as an FTS phrase together with the topic, the reference alone, the topic words, then
phrases of the verse's own wording (a teacher quoting it). The first hit per teacher becomes a voice,
so the answer spreads across teachers instead of repeating the most voluminous one.

Every excerpt is a verbatim span of a cited chunk (whitespace collapsed). The teacher is the record's
own name for it (ThML author, publisher-listed authors), else the publisher. Tradition labels come
from the explicit map below; anything else is "tradition not recorded". Scripture, teacher readings
and personal context (Rawley's own recorded prayers) are labelled separately in `distinctions`.
"""

from __future__ import annotations

import json
import re
import sqlite3

from ..references import explicit_references, passage_verses
from ..retrieval import content_terms, parse_cite_id, search_match
from .spoken import clean_spoken, normalize_spoken_reference, roman, sentences, spoken_date, spoken_reference, spoken_tradition, term_stem, written_reference

NOT_RECORDED = "tradition not recorded"
# Explicit, small and owner-supplied. A name not listed here is never given a tradition.
TRADITION_BY_TEACHER = {
    "John Piper": "Reformed Baptist (Christian Hedonism)",
    "Joby Martin": "Evangelical, non-denominational",
    "Jonathan Edwards": "Reformed/Puritan",
    "BibleProject": "Evangelical, literary-theological",
}
# Used only when the record names no individual teacher (the publisher speaks as itself).
TRADITION_BY_PUBLISHER = {
    "Desiring God": "Reformed Baptist (Christian Hedonism)",
    "The Church of Eleven22": "Evangelical, non-denominational",
    "BibleProject": "Evangelical, literary-theological",
}
FAMILIES = ["teaching"]
STAGE_LIMIT = 40
LONG_PASSAGE_CHARS = 600


def voice_identity(meta: dict, title: str | None) -> dict:
    """Teacher, publisher and tradition of one teaching document from its own metadata."""
    publisher = meta.get("publisher") or meta.get("provider") or "unknown publisher"
    if meta.get("teacher"):
        names = [meta["teacher"]]
        basis = meta.get("teacher_basis") or "record"
    elif meta.get("authors"):
        names = [str(a) for a in meta["authors"]]
        basis = "publisher_listed_authors"
    else:
        names = []
        basis = "publisher"
    if names:
        labels = {TRADITION_BY_TEACHER.get(name) for name in names}
        tradition = labels.pop() if len(labels) == 1 and None not in labels else NOT_RECORDED
        tradition_basis = "teacher" if tradition != NOT_RECORDED else None
    else:
        tradition = TRADITION_BY_PUBLISHER.get(publisher, NOT_RECORDED)
        tradition_basis = "publisher" if tradition != NOT_RECORDED else None
    return {
        "teacher": " and ".join(names) if names else publisher,
        "teacher_basis": basis,
        "publisher": publisher,
        "tradition": tradition,
        "tradition_basis": tradition_basis,
        "work": meta.get("work") or title,
    }


def _reference_phrases(passage: dict | None) -> list[str]:
    if not passage:
        return []
    book = passage["book_name"].lower()
    numbers = [str(passage["chapter"])]
    if passage.get("verse_from") is not None:
        numbers.append(str(passage["verse_from"]))
    return [f'"{book} {" ".join(numbers)}"']


def _verse_phrases(passage: dict | None, count: int | None = 8, step: int = 2) -> list[str]:
    """Four-word windows of the verse wording (a teacher quoting the verse in a similar translation)."""
    if not passage or not passage.get("verses") or passage.get("verse_from") is None:
        return []
    words = re.findall(r"[A-Za-z']+", " ".join(v["text"] for v in passage["verses"][: (3 if count else None)]))
    windows = []
    for index in range(0, max(0, len(words) - 3), step):
        window = [w.lower().strip("'") for w in words[index:index + 4]]
        if sum(len(w) > 3 for w in window) >= 2:
            windows.append('"' + " ".join(window) + '"')
    return windows[:count] if count else windows


def _quote_windows(conn: sqlite3.Connection, passage: dict | None) -> list[str]:
    """Windows of the named verses and their neighbours (one before, three after), used only to tell a
    teacher quoting the passage apart from the teacher commenting on it."""
    if not passage or passage.get("verse_from") is None:
        return []
    ref = {**{k: passage[k] for k in ("book", "chapter")}, "verse_from": max(1, passage["verse_from"] - 1), "verse_to": (passage.get("verse_to") or passage["verse_from"]) + 3, "matched": "", "invalid": None}
    ref["ref"] = f"{ref['book']}.{ref['chapter']}:{ref['verse_from']}-{ref['verse_to']}"
    return _verse_phrases(passage_verses(conn, ref), count=None, step=1)


def _terms_query(terms: list[str], operator: str) -> str:
    return f" {operator} ".join(f'"{t}"' for t in terms[:12])


def _stages(passage: dict | None, topic_terms: list[str]) -> list[tuple[str, str]]:
    phrases = _reference_phrases(passage)
    stages: list[tuple[str, str]] = []
    if phrases and topic_terms:
        stages.append(("reference_and_topic", f"({' OR '.join(phrases)}) AND ({_terms_query(topic_terms, 'OR')})"))
    if phrases:
        stages.append(("reference", " OR ".join(phrases)))
    if len(topic_terms) > 1:
        stages.append(("topic_all", _terms_query(topic_terms, "AND")))
    if topic_terms:
        stages.append(("topic_any", _terms_query(topic_terms, "OR")))
    windows = _verse_phrases(passage)
    if windows:
        stages.append(("verse_wording", " OR ".join(windows)))
    return stages


def _excerpt(text: str, passage: dict | None, topic_terms: list[str], windows: list[str]) -> tuple[str, int, int, bool]:
    """Best one or two sentences of a chunk: (excerpt, start, end, quotes_only) in chunk offsets.

    `quotes_only` is True when every chosen sentence quotes the passage itself rather than comments on it.
    """
    spans = sentences(text)
    if not spans:
        return re.sub(r"\s+", " ", text).strip()[:350], 0, len(text), False
    ref_pattern = None
    if passage:
        book = re.escape(passage["book_name"][:3])
        chapter = rf"(?:{passage['chapter']}|{roman(passage['chapter'])})"  # "Rom. 8:28" or "Rom. viii. 28"
        tail = rf"\s*{chapter}" + (rf"\s*[:.,]\s*{passage['verse_from']}\b" if passage.get("verse_from") is not None else r"\b")
        ref_pattern = re.compile(rf"\b{book}[a-z]*\.?{tail}", re.IGNORECASE)
    stems = [term_stem(t) for t in topic_terms]
    plain_windows = [w.strip('"') for w in windows]

    def quoted_windows(span: tuple[int, int]) -> int:
        plain = re.sub(r"[^a-z' ]+", " ", text[span[0]:span[1]].lower())
        return sum(1 for window in plain_windows if window in plain)

    quotes = [quoted_windows(span) >= 2 for span in spans]  # the sentence quotes the verse itself

    def score(index: int) -> float:
        sentence = text[spans[index][0]:spans[index][1]]
        lower = sentence.lower()
        value = 3.0 if ref_pattern and ref_pattern.search(sentence) else 0.0
        value += sum(1.0 for stem in stems if stem and stem in lower)
        # Prefer the teacher's own words: a sentence that only quotes the verse scores 1, and the
        # sentence right after such a quote (usually the comment on it) gets a bonus.
        value += 1.0 if quotes[index] else 0.5 * quoted_windows(spans[index])
        value += 1.5 if index and quotes[index - 1] and not quotes[index] else 0.0
        return value + (0.1 if 60 <= len(sentence) <= 320 else 0.0)

    best = max(range(len(spans)), key=lambda i: (score(i), -i))
    start, end = spans[best]
    quotes_only = quotes[best]
    if end - start < 160 and best + 1 < len(spans) and spans[best + 1][1] - start <= 380:
        end = spans[best + 1][1]
        quotes_only = quotes_only and quotes[best + 1]
    return re.sub(r"\s+", " ", text[start:end]).strip(), start, end, quotes_only


def _metadata(conn: sqlite3.Connection, cite: str) -> dict:
    doc_id, _, _ = parse_cite_id(cite)
    row = conn.execute("SELECT metadata_json FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
    return json.loads(row["metadata_json"] or "{}") if row else {}


def _sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?\"'”’" else text + "."


def interpretations_tool(conn: sqlite3.Connection, reference: str | None = None, topic: str | None = None, limit: int = 3) -> dict:
    """Up to `limit` voices from distinct teachers on a reference and/or topic, plus distinctions.

    Returns:
        `{available, reference, topic, scripture, voices: [{teacher, tradition, title, excerpt, cite_id,
        ...}], personal_context, distinctions, spoken}`.
    """
    limit = max(1, min(int(limit or 3), 5))
    passage = None
    if reference:
        refs = explicit_references(normalize_spoken_reference(reference))
        if refs:
            passage = passage_verses(conn, refs[0])
    topic_terms = content_terms(topic) if topic else []
    if not passage and not topic_terms:
        return {"available": False, "reference": reference, "topic": topic, "voices": [], "spoken": "Which passage or topic should I find readings on?"}
    windows = _quote_windows(conn, passage)
    voices: list[dict] = []
    teachers: set[str] = set()
    quote_only: dict[str, dict] = {}  # a teacher's first hit that only quotes the passage (fallback)
    stages_run: list[dict] = []
    for strategy, match in _stages(passage, topic_terms):
        hits = search_match(conn, match, families=FAMILIES, limit=STAGE_LIMIT)
        stages_run.append({"strategy": strategy, "match": match, "hits": len(hits)})
        for hit in hits:
            meta = _metadata(conn, hit["cite_id"])
            identity = voice_identity(meta, hit["title"])
            if identity["teacher"] in teachers:
                continue
            excerpt, start, end, quotes_passage = _excerpt(hit["text"], passage, topic_terms, windows)
            voice = {
                **identity,
                "title": hit["title"],
                "excerpt": excerpt,
                "excerpt_span": {"start": start, "end": end},
                "excerpt_quotes_passage": quotes_passage,
                "cite_id": hit["cite_id"],
                "attribution": hit["attribution"],
                "source_doc_id": hit["source_doc_id"],
                "date": meta.get("date"),
                "url": meta.get("url") or (hit["source_doc_id"] if str(hit["source_doc_id"]).startswith("http") else None),
                "strategy": strategy,
            }
            if quotes_passage:
                quote_only.setdefault(identity["teacher"], voice)
                continue
            teachers.add(identity["teacher"])
            voices.append(voice)
            if len(voices) >= limit:
                break
        if len(voices) >= limit:
            break
    for teacher, voice in quote_only.items():  # a teacher found only quoting the passage still counts
        if len(voices) >= limit:
            break
        if teacher not in teachers:
            teachers.add(teacher)
            voices.append(voice)
    personal = None
    prayer_match = " OR ".join(_reference_phrases(passage) + ([_terms_query(topic_terms, "OR")] if topic_terms else []))
    prayer_hits = search_match(conn, prayer_match, families=["prayer"], limit=1) if prayer_match else []
    if prayer_hits:
        hit = prayer_hits[0]
        excerpt, start, end, _ = _excerpt(hit["text"], passage, topic_terms, windows)
        personal = {"label": "personal context: your own recorded prayer, not Scripture or teaching", "cite_id": hit["cite_id"], "title": hit["title"], "recorded_at": hit["recorded_at"], "excerpt": excerpt, "excerpt_span": {"start": start, "end": end}}
    scripture = None
    if passage and passage["available"]:
        scripture = {
            "reference": written_reference(passage["book_name"], passage["chapter"], passage["verse_from"], passage["verse_to"]),
            "verses": passage["verses"],
            "cite_ids": passage["cite_ids"],
            "edition": (passage.get("edition") or {}).get("name"),
        }
    distinctions = {
        "scripture": (f"{scripture['reference']} is quoted verbatim from the retained {scripture['edition'] or 'World English Bible'} and is the only Scripture here." if scripture else "No Scripture passage was quoted."),
        "teacher_reading": "Each voice is that teacher's own interpretation, quoted from their published text with its citation; it is not Scripture.",
        "personal_context": (f"The prayer excerpt is your own recorded prayer from {spoken_date(personal['recorded_at']) or 'an undated recording'}: personal context, neither Scripture nor teaching." if personal else "No recorded prayer of yours matched, so no personal context is included."),
    }
    parts: list[str] = []
    if scripture:
        name = spoken_reference(passage["book_name"], passage["chapter"], passage["verse_from"], passage["verse_to"])
        text = " ".join(v["text"] for v in scripture["verses"])
        parts.append(f"{name}, in the World English Bible: {_sentence(text)}" if len(text) <= LONG_PASSAGE_CHARS else f"These readings are on {name}.")
    elif passage:
        parts.append(f"{spoken_reference(passage['book_name'], passage['chapter'], passage['verse_from'], passage['verse_to'])} is not in the retained edition, so I will not quote it.")
    if voices:
        parts.append(f"Here {'is one reading' if len(voices) == 1 else f'are {len(voices)} readings'}; each is the teacher's interpretation, not Scripture.")
        for voice in voices:
            tradition = spoken_tradition(voice["tradition"]) if voice["tradition"] != NOT_RECORDED else "whose tradition is not recorded"
            parts.append(f"{voice['teacher']}, {tradition}, in {voice['work']}: {_sentence(voice['excerpt'])}")
    else:
        parts.append("I did not find a teacher's reading of that in the retained teaching.")
    if personal:
        parts.append(f"And from your own prayer on {spoken_date(personal['recorded_at']) or 'an earlier day'}, as personal context: {_sentence(personal['excerpt'])}")
    return {
        "available": bool(voices),
        "reference": reference,
        "topic": topic,
        "scripture": scripture,
        "voices": voices,
        "distinct_teachers": len(teachers),
        "personal_context": personal,
        "distinctions": distinctions,
        "stages": stages_run,
        "spoken": clean_spoken(" ".join(parts)),
    }
