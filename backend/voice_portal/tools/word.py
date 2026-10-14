"""`GET /tools/word`: "a word from God in response to my morning prayer".

1. The prayer. `latest`: the newer of (a) the newest `prayer_captures` row that reached a published
   transcript (its text read by transcript id from the prayer store, else the projection) and (b) the
   newest row of the prayer store's `prayer_corpus_v1` (the `publications` table when the view is
   missing; the projection when the store is unreadable). `morning`: the newest of those recorded
   between 04:00 and 12:00 local time. `devotional`: as `latest`, and today's devotional chapter
   (config `devotional_passages`) is searched first for verses that fit each theme. The prayer store
   is opened read-only; nothing is ever written to it.
2. Themes. One reasoning call (config inference owner, thinking disabled through
   `inference_extra_body`) returns strict JSON: 3 to 6 themes, each a short label, a one-sentence
   petition and 2 to 5 search words. Cached per (model, prayer text hash, prompt version). When the
   owner fails, keyword themes from the prayer's own words are used and labelled `fallback_keywords`.
3. Passages. Candidates come from (a) the Prayer resident's pairings ledger rows for this prayer (by
   prayer id or text SHA-256; cited before echoed before editorial), (b) Scripture FTS on each theme's
   words, narrowed to the best-matching verses inside the hit chunk, and (c) for `devotional`, today's
   chapter. Two to four passages are chosen (pairings first, then one per theme without overlap),
   capped at five verses each, and read verbatim from the retained edition with their citations.
4. Frames are deterministic one-sentence introductions built from the theme label and the reference;
   no model writes a claim about a passage. `spoken` is the lead, then each frame and its verses.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from ..config import Config
from ..db import connect_source, now
from ..reasoning import ReasoningError, stream_chat
from ..references import explicit_references, passage_verses
from ..retrieval import STOPWORDS, parse_cite_id, search, search_match
from ..sources.scripture import BOOK_NAMES
from ..sources.theographic import passage_entities
from .spoken import clean_spoken, recorded_datetime, sentences, spoken_date, spoken_reference, term_stem, written_reference

PROMPT_VERSION = "themes-v1"
MAX_PRAYER_CHARS = 24000
MAX_VERSES = 5
TARGET_PASSAGES = 3
MAX_PASSAGES = 4
MIN_PASSAGES = 2
MORNING_HOURS = (4, 12)
BASIS_ORDER = {"cited": 0, "echoed": 1, "editorial": 2}
# Words a prayer is full of that say nothing about its petitions (keyword fallback only).
PRAYER_FILLER = {
    "lord", "god", "jesus", "christ", "father", "heavenly", "holy", "spirit", "amen", "pray", "praying", "prayer",
    "thank", "thanks", "thankful", "please", "help", "name", "yes", "just", "like", "really", "know", "want",
    "oh", "um", "uh", "gonna", "wanna", "right", "thing", "things", "way", "today", "day", "morning", "good",
    "great", "love", "us", "let", "would", "will", "may", "also", "all", "every", "one", "make", "give",
    "hallowed", "kingdom", "come", "done", "earth", "heaven", "thy", "thee", "thou", "art", "be", "being",
}
THEME_SYSTEM = """You read one personal prayer transcript and list what it prays about.
Return only JSON of the form {"themes": [{"label": "...", "petition": "...", "terms": ["...", "..."]}]}
with 3 to 6 themes in the order the prayer raises them.
- label: 2 to 6 plain words naming the theme, for example "rest for a weary heart".
- petition: one short sentence in the second person saying what was asked, confessed or given thanks for, for example "You asked God for rest after a hard week."
- terms: 2 to 5 single lower-case English words likely to appear in a Bible verse on that theme, for example ["rest", "weary", "burden"].
Use only what the prayer says. Do not invent people, events or requests. No text outside the JSON."""
THEME_SCHEMA = {
    "type": "object",
    "properties": {
        "themes": {
            "type": "array",
            "minItems": 3,
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "petition": {"type": "string"}, "terms": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 5}},
                "required": ["label", "petition", "terms"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["themes"],
    "additionalProperties": False,
}

ECHO_WINDOW_LIMIT = 800
_LEDGER_CACHE: dict[str, tuple[tuple[float, int], list[dict]]] = {}
_IDF_CACHE: dict[tuple[int, str], float] = {}
_LEDGER_LOCK = threading.Lock()


# ----- 1. the prayer -----

def _store_rows(cfg: Config, *, prayer_id: str | None = None, limit: int = 1) -> list[dict]:
    """Rows of the prayer store, read-only: by id, else newest first. [] when unreadable."""
    try:
        conn = connect_source(cfg.prayer_store)
    except (FileNotFoundError, sqlite3.Error):
        return []
    try:
        where, params = ("WHERE prayer_id = ?", (prayer_id,)) if prayer_id else ("", ())
        try:
            rows = conn.execute(f"SELECT prayer_id, recorded_at, text, text_sha256, recorded_at_basis, 'prayer_corpus_v1' AS source FROM prayer_corpus_v1 {where} ORDER BY recorded_at DESC LIMIT ?", (*params, limit)).fetchall()
        except sqlite3.OperationalError:
            where = "WHERE transcript_id = ?" if prayer_id else ""
            rows = conn.execute(f"SELECT transcript_id AS prayer_id, recorded_at, body_text AS text, body_sha256 AS text_sha256, 'service_receipt' AS recorded_at_basis, 'publications' AS source FROM publications {where} ORDER BY recorded_at DESC LIMIT ?", (*params, limit)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _projection_rows(conn: sqlite3.Connection, *, prayer_id: str | None = None, limit: int = 1) -> list[dict]:
    where, params = ("AND source_doc_id = ?", (prayer_id,)) if prayer_id else ("", ())
    rows = conn.execute(
        f"SELECT source_doc_id AS prayer_id, recorded_at, body AS text, revision_hash AS text_sha256, recorded_at_basis, 'projection' AS source FROM documents "
        f"WHERE source_family = 'prayer' AND current = 1 {where} ORDER BY CAST(recorded_at AS REAL) DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _newest_capture(conn: sqlite3.Connection, cfg: Config) -> dict | None:
    """Newest relay capture that reached a published transcript and whose text can be read."""
    for row in conn.execute("SELECT client_capture_id, prayer_id, created_at, recording_json FROM prayer_captures WHERE status = 'published' AND prayer_id IS NOT NULL ORDER BY created_at DESC LIMIT 20"):
        found = _store_rows(cfg, prayer_id=row["prayer_id"]) or _projection_rows(conn, prayer_id=row["prayer_id"])
        if found:
            return {**found[0], "basis": "published_capture", "client_capture_id": row["client_capture_id"]}
    return None


def _is_morning(recorded_at: object) -> bool:
    when = recorded_datetime(recorded_at)
    return bool(when) and MORNING_HOURS[0] <= when.hour < MORNING_HOURS[1]


def _recorded_number(row: dict) -> float:
    try:
        return float(row.get("recorded_at"))
    except (TypeError, ValueError):
        when = recorded_datetime(row.get("recorded_at"))
        return when.timestamp() if when else 0.0


def latest_prayer(conn: sqlite3.Connection, cfg: Config, which: str = "latest") -> dict | None:
    """The prayer to answer (see module docstring, step 1), with `basis` naming where it came from."""
    capture = _newest_capture(conn, cfg)
    corpus = _store_rows(cfg, limit=400 if which == "morning" else 1)
    corpus_basis = "corpus_newest"
    if not corpus:
        corpus = _projection_rows(conn, limit=400 if which == "morning" else 1)
        corpus_basis = "projection_newest"
    candidates = ([capture] if capture else []) + [{**row, "basis": corpus_basis} for row in corpus]
    if which == "morning":
        candidates = [c for c in candidates if _is_morning(c.get("recorded_at"))]
    if not candidates:
        return None
    # Newest by recorded time; a published capture wins a tie (it is the same prayer, just relayed).
    return max(candidates, key=lambda c: (_recorded_number(c), c["basis"] == "published_capture"))


# ----- 2. themes -----

def _parse_themes(raw: str) -> list[dict]:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("no JSON object in the reply") from None
        data = json.loads(match.group(0))
    themes = []
    for item in (data.get("themes") if isinstance(data, dict) else None) or []:
        if not isinstance(item, dict):
            continue
        label = re.sub(r"[^\w\s,'’-]", "", str(item.get("label") or "")).strip()
        label = " ".join(label.split()[:8])
        terms = []
        for term in item.get("terms") or []:
            for word in re.findall(r"[a-z']+", str(term).lower()):
                word = word.strip("'")
                if len(word) > 2 and word not in STOPWORDS and word not in terms:
                    terms.append(word)
        if label and terms:
            themes.append({"label": label, "petition": clean_spoken(str(item.get("petition") or "")), "terms": terms[:5]})
    if not themes:
        raise ValueError("the reply holds no usable themes")
    return themes[:6]


def fallback_themes(text: str, count: int = 4) -> list[dict]:
    """Keyword themes without a model: the prayer's sentences richest in distinctive words.

    Each theme keeps a verbatim `quote` (the sentence's first words) instead of a label, and its
    terms are that sentence's content words, so retrieval looks for verses sharing several of them.
    """
    def words_of(sentence: str) -> list[str]:
        found: list[str] = []
        for word in re.findall(r"[a-z]+(?:'[a-z]+)?", sentence.lower()):
            if len(word) > 3 and "'" not in word and word not in STOPWORDS and word not in PRAYER_FILLER and word not in found:
                found.append(word)
        return found

    spans = sentences(text)
    scored = [(len(words_of(text[a:b])), -i, a, b) for i, (a, b) in enumerate(spans)]
    best = sorted(sorted(scored, reverse=True)[:count], key=lambda item: -item[1])
    themes = []
    for size, _, start, end in best:
        if size == 0:
            continue
        sentence = re.sub(r"\s+", " ", text[start:end]).strip()
        quote = " ".join(sentence.rstrip(".!?").split()[:8])
        themes.append({"label": "", "quote": quote, "petition": "", "terms": sorted(words_of(sentence), key=len, reverse=True)[:5]})
    return themes


def extract_themes(conn: sqlite3.Connection, cfg: Config, prayer: dict, *, refresh: bool = False) -> dict:
    """Themes of one prayer revision: cached reasoning result, a fresh call, or the keyword fallback."""
    key = f"{PROMPT_VERSION}:{cfg.inference_model}:{prayer.get('text_sha256')}"
    if not refresh:
        row = conn.execute("SELECT value_json FROM tool_cache WHERE tool = 'word_themes' AND cache_key = ?", (key,)).fetchone()
        if row:
            return {**json.loads(row["value_json"]), "cached": True}
    when = spoken_date(prayer.get("recorded_at")) or "undated"
    messages = [{"role": "system", "content": THEME_SYSTEM}, {"role": "user", "content": f"Prayer transcript (recorded {when}):\n\n{prayer['text'][:MAX_PRAYER_CHARS]}"}]
    structured = {**(cfg.inference_extra_body or {}), "response_format": {"type": "json_schema", "json_schema": {"name": "prayer_themes", "strict": True, "schema": THEME_SCHEMA}}}
    started = time.monotonic()
    errors: list[str] = []
    for extra in (structured, cfg.inference_extra_body):
        try:
            chunks: list[str] = []
            usage = None
            for item in stream_chat(cfg.inference_base_url, cfg.inference_model, messages, timeout=cfg.tools_inference_timeout_s, max_tokens=cfg.word_max_tokens, temperature=0.2, extra_body=extra):
                if "delta" in item:
                    chunks.append(item["delta"])
                else:
                    usage = item.get("usage")
            themes = _parse_themes("".join(chunks))
            result = {"themes": themes, "source": "reasoning", "model": cfg.inference_model, "structured_output": extra is structured, "usage": usage, "elapsed_ms": int((time.monotonic() - started) * 1000), "errors": errors}
            with conn:
                conn.execute("INSERT OR REPLACE INTO tool_cache(tool, cache_key, created_at, value_json) VALUES ('word_themes', ?, ?, ?)", (key, now(), json.dumps(result)))
            return {**result, "cached": False}
        except ReasoningError as exc:
            errors.append(f"{exc} {(exc.body or '')[:200]}".strip())
            if exc.status != 400:  # only a rejected response_format is worth one plain retry
                break
        except ValueError as exc:
            errors.append(f"unparseable reply: {exc}")
    return {"themes": fallback_themes(prayer["text"]), "source": "fallback_keywords", "model": None, "structured_output": False, "usage": None, "elapsed_ms": int((time.monotonic() - started) * 1000), "errors": errors, "cached": False}


# ----- 3. passages -----

def load_ledger(path: str | os.PathLike) -> list[dict]:
    """Rows of the pairings ledger (JSONL), cached by file mtime and size; [] when absent."""
    file = Path(path).expanduser()
    try:
        stat = file.stat()
    except OSError:
        return []
    signature = (stat.st_mtime, stat.st_size)
    with _LEDGER_LOCK:
        cached = _LEDGER_CACHE.get(str(file))
        if cached and cached[0] == signature:
            return cached[1]
        rows = []
        for line in file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        _LEDGER_CACHE[str(file)] = (signature, rows)
        return rows


def ledger_rows_for(rows: list[dict], prayer: dict) -> list[dict]:
    """Usable ledger rows joined by prayer id or text SHA-256, strongest basis first."""
    matched = [r for r in rows if r.get("usable", 1) and (r.get("prayer_id") == prayer.get("prayer_id") or (prayer.get("text_sha256") and r.get("text_sha256") == prayer.get("text_sha256")))]
    return sorted(matched, key=lambda r: (BASIS_ORDER.get(r.get("basis"), 3), r.get("phrase_in_current_text") == "absent", r.get("record_key") or ""))


def _matched_terms(text: str, terms: list[str]) -> set[str]:
    words = re.findall(r"[a-z']+", text.lower())
    return {term for term in terms if any(word.startswith(term_stem(term)) for word in words)}


def _overlap(text: str, terms: list[str]) -> int:
    return len(_matched_terms(text, terms))


def term_weights(conn: sqlite3.Connection, terms: list[str]) -> dict[str, float]:
    """IDF of each term over current Scripture chunks (0 for a word Scripture never uses)."""
    total = conn.execute("SELECT COUNT(*) FROM chunks c JOIN documents d ON d.doc_id = c.doc_id WHERE d.source_family = 'scripture' AND d.current = 1").fetchone()[0]
    weights: dict[str, float] = {}
    for term in terms:
        key = (total, term)
        if key not in _IDF_CACHE:
            try:
                found = conn.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH ? AND source_family = 'scripture'", ('"' + term.replace('"', "") + '"',)).fetchone()[0]
            except sqlite3.OperationalError:
                found = 0
            _IDF_CACHE[key] = math.log((total + 1) / (found + 1)) if found else 0.0
        weights[term] = _IDF_CACHE[key]
    return weights


def _capped(passage: dict) -> dict:
    if len(passage["verses"]) <= MAX_VERSES:
        return passage
    verses = passage["verses"][:MAX_VERSES]
    return {**passage, "verses": verses, "verse_from": verses[0]["verse"], "verse_to": verses[-1]["verse"], "truncated": True}


def _ref_for(book: str, chapter: int, verse_from: int, verse_to: int) -> dict:
    return {"ref": f"{book}.{chapter}:{verse_from}" + (f"-{verse_to}" if verse_to != verse_from else ""), "book": book, "chapter": chapter, "verse_from": verse_from, "verse_to": verse_to, "matched": "", "invalid": None}


def _chapter_verses(conn: sqlite3.Connection, doc_id: int, span: tuple[int, int] | None) -> tuple[dict, list[tuple[dict, str]]] | None:
    doc = conn.execute("SELECT body, locators_json, metadata_json FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
    if doc is None:
        return None
    meta = json.loads(doc["metadata_json"] or "{}")
    verses = [(loc, doc["body"][loc["start"]:loc["end"]]) for loc in json.loads(doc["locators_json"] or "[]") if loc.get("kind") == "verse" and (span is None or (loc["start"] < span[1] and loc["end"] > span[0]))]
    if not verses or not meta.get("book"):
        return None
    return meta, verses


def _best_verses(conn: sqlite3.Connection, doc_id: int, span: tuple[int, int] | None, terms: list[str], weights: dict[str, float]) -> tuple[dict, float, int] | None:
    """Best 1 to 3 consecutive verses of a chapter document (optionally within a chunk span), scored
    by the summed IDF of the distinct theme words they contain."""
    found = _chapter_verses(conn, doc_id, span)
    if found is None:
        return None
    meta, verses = found
    matched = [_matched_terms(text, terms) for _, text in verses]

    def value(sets: list[set[str]]) -> float:
        return sum(weights.get(term, 0.0) for term in set().union(*sets)) if sets else 0.0

    best = max(range(len(verses)), key=lambda i: (value([matched[i]]), -i))
    if value([matched[best]]) <= 0:
        return None
    lo = hi = best
    while hi - lo < 2:  # widen only while a neighbouring verse adds a theme word not yet covered
        current = value(matched[lo:hi + 1])
        right = value(matched[lo:hi + 2]) if hi + 1 < len(verses) else -1
        left = value(matched[lo - 1:hi + 1]) if lo > 0 else -1
        if max(left, right) <= current:
            break
        if right >= left:
            hi += 1
        else:
            lo -= 1
    distinct = len(set().union(*matched[lo:hi + 1]))
    return _ref_for(meta["book"], int(meta["chapter"]), int(verses[lo][0]["verse"]), int(verses[hi][0]["verse"])), round(value(matched[lo:hi + 1]), 3), distinct


def retrieval_candidates(conn: sqlite3.Connection, theme: dict, limit: int = 8) -> list[dict]:
    """Verse ranges from Scripture FTS hits for one theme, best first (IDF-weighted word overlap)."""
    query = " ".join(theme["terms"] + [w for w in re.findall(r"[a-z']+", (theme.get("label") or "").lower()) if w not in STOPWORDS])
    weights = term_weights(conn, theme["terms"])
    found = []
    for rank, hit in enumerate(search(conn, query, families=["scripture"], limit=limit)["hits"]):
        doc_id, _, _ = parse_cite_id(hit["cite_id"])
        best = _best_verses(conn, doc_id, (hit["start"], hit["end"]), theme["terms"], weights)
        if best:
            found.append({"ref": best[0], "score": best[1], "terms_matched": best[2], "rank": rank, "strategy": hit.get("strategy")})
    return sorted(found, key=lambda c: (-c["score"], c["rank"]))


def prayer_windows(text: str, limit: int = ECHO_WINDOW_LIMIT) -> list[str]:
    """Distinct four-word windows of the prayer with at least two content words (sampled evenly)."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    windows: list[str] = []
    seen: set[str] = set()
    for index in range(max(0, len(words) - 3)):
        window = words[index:index + 4]
        if sum(1 for w in window if len(w) > 3 and w not in STOPWORDS) >= 2:
            joined = " ".join(window)
            if joined not in seen:
                seen.add(joined)
                windows.append(joined)
    if len(windows) > limit:
        step = len(windows) / limit
        windows = [windows[int(i * step)] for i in range(limit)]
    return windows


def echo_candidates(conn: sqlite3.Connection, text: str) -> list[dict]:
    """Verses the prayer's own wording echoes: retained verses sharing at least two of its four-word
    windows (exact words), best first. This is the same signal the pairings ledger records as an echo,
    computed live for prayers the ledger has not seen."""
    windows = prayer_windows(text)
    hits: dict[str, dict] = {}
    for start in range(0, len(windows), 48):
        match = " OR ".join(f'"{w}"' for w in windows[start:start + 48])
        for hit in search_match(conn, match, families=["scripture"], limit=40):
            hits.setdefault(hit["cite_id"], hit)
    window_set = set(windows)
    out = []
    for cite, hit in hits.items():
        found = _chapter_verses(conn, parse_cite_id(cite)[0], (hit["start"], hit["end"]))
        if found is None:
            continue
        meta, verses = found

        def windows_in(texts: list[str]) -> set[str]:
            words = re.findall(r"[a-z0-9]+", " ".join(texts).lower())
            return {" ".join(words[i:i + 4]) for i in range(max(0, len(words) - 3))} & window_set

        best = None
        for lo in range(len(verses)):
            for hi in range(lo, min(lo + 3, len(verses))):
                matched = windows_in([t for _, t in verses[lo:hi + 1]])
                key = (len(matched), -(hi - lo))
                if matched and (best is None or key > best[0]):
                    best = (key, lo, hi, matched)
        if best and len(best[3]) >= 2:
            _, lo, hi, matched = best
            out.append({"ref": _ref_for(meta["book"], int(meta["chapter"]), int(verses[lo][0]["verse"]), int(verses[hi][0]["verse"])), "score": len(matched), "rank": 0, "strategy": "echo", "windows": sorted(matched)})
    return sorted(out, key=lambda c: (-c["score"], c["ref"]["ref"]))


def devotional_candidate(conn: sqlite3.Connection, cfg: Config, theme: dict, day: dt.date) -> dict | None:
    """Best verses of today's devotional chapter for one theme, if any verse shares its words."""
    code = cfg.devotional_passages[day.toordinal() % len(cfg.devotional_passages)]
    book, _, chapter = code.partition(".")
    doc = conn.execute("SELECT doc_id FROM documents WHERE source_family = 'scripture' AND current = 1 AND source_doc_id LIKE ? LIMIT 1", (f"%/{book}.{chapter}",)).fetchone()
    if doc is None:
        return None
    best = _best_verses(conn, doc["doc_id"], None, theme["terms"], term_weights(conn, theme["terms"]))
    return {"ref": best[0], "score": best[1], "terms_matched": best[2], "rank": 0, "strategy": "devotional_chapter"} if best else None


def _overlaps(passage: dict, chosen: list[dict]) -> bool:
    for other in chosen:
        if other["book"] == passage["book"] and other["chapter"] == passage["chapter"]:
            if passage["verse_from"] is None or other["verse_from"] is None:
                return True
            if passage["verse_from"] <= other["verse_to"] and other["verse_from"] <= passage["verse_to"]:
                return True
    return False


def _label(theme: dict | None) -> str | None:
    if not theme or not theme.get("label"):
        return None
    label = theme["label"].strip().rstrip(".!?")
    return label[:1].lower() + label[1:] if label[:1].isupper() and not label[1:2].isupper() else label


def frame_for(source: str, basis: str | None, name: str, theme: dict | None) -> str:
    """One deterministic sentence introducing a passage (no claim about what the passage means)."""
    label = _label(theme)
    if source == "pairings":
        if basis == "cited":
            return f"In this prayer you named {name}." if not label else f"You prayed about {label}, and in this prayer you named {name}."
        if basis == "echoed":
            return f"Your words in this prayer echo {name}." if not label else f"Your words about {label} echo {name}."
        return f"A review of this prayer paired it with {name}."
    if basis == "echo":
        return f"Your words in this prayer echo {name}."
    quote = (theme or {}).get("quote")
    if source == "devotional":
        if label:
            return f"From today's devotional passage, for your prayer about {label}, hear {name}."
        return f"From today's devotional passage, for your words, {quote}, hear {name}." if quote else f"From today's devotional passage, hear {name}."
    if label:
        return f"For your prayer about {label}, hear {name}."
    return f"For your words, {quote}, hear {name}." if quote else f"Hear {name}."


def _assign_theme(text: str, themes: list[dict]) -> dict | None:
    scored = [(_overlap(text, t["terms"]), -i, t) for i, t in enumerate(themes)]
    best = max(scored, key=lambda s: (s[0], s[1]), default=None)
    return best[2] if best and best[0] > 0 else None


def _entities(conn: sqlite3.Connection, verses: list[dict]) -> dict | None:
    if not conn.execute("SELECT 1 FROM theographic_verses LIMIT 1").fetchone():
        return None
    found = passage_entities(conn, [v["osis"] for v in verses])
    return {"people": [p["display_title"] or p["name"] for p in found["people"]], "places": [p["display_title"] for p in found["places"]]}


def choose_passages(conn: sqlite3.Connection, cfg: Config, prayer: dict, themes: list[dict], *, which: str, day: dt.date) -> tuple[list[dict], dict]:
    """Two to four passages for the prayer (step 3 of the module docstring) with candidate counts."""
    chosen: list[dict] = []
    ledger = load_ledger(cfg.scripture_pairings_ledger)
    rows = ledger_rows_for(ledger, prayer)
    stats = {"ledger_rows_total": len(ledger), "ledger_rows_for_prayer": len(rows), "retrieval_candidates": 0, "devotional_candidates": 0}
    for row in rows:
        if len([p for p in chosen if p["source"] == "pairings"]) >= 2:
            break
        refs = explicit_references(" ".join(row.get("scripture_refs") or []))
        if not refs or refs[0]["verse_from"] is None:
            continue
        passage = passage_verses(conn, refs[0])
        if not passage["available"] or _overlaps(passage, chosen):
            continue
        chosen.append({**_capped(passage), "source": "pairings", "basis": row.get("basis"), "annotation_id": row.get("annotation_id"), "ledger_ref": (row.get("scripture_refs") or [None])[0]})
    echoes = echo_candidates(conn, prayer["text"])
    stats["echo_candidates"] = len(echoes)
    echo_quota = 1 if chosen else 2
    covered: set[str] = set()
    for candidate in echoes:
        if echo_quota <= 0 or len(chosen) >= TARGET_PASSAGES:
            break
        if len(set(candidate["windows"]) - covered) < 2:  # a parallel passage echoing the same words
            continue
        passage = passage_verses(conn, candidate["ref"])
        if passage["available"] and not _overlaps(passage, chosen):
            covered |= set(candidate["windows"])
            chosen.append({**_capped(passage), "source": "retrieval", "basis": "echo", "score": candidate["score"], "echo_windows": candidate["windows"]})
            echo_quota -= 1
    for index, theme in enumerate(themes):
        if len(chosen) >= TARGET_PASSAGES:
            break
        candidates = retrieval_candidates(conn, theme)
        stats["retrieval_candidates"] += len(candidates)
        if which == "devotional":
            devotional = devotional_candidate(conn, cfg, theme, day)
            if devotional:
                stats["devotional_candidates"] += 1
                candidates = [{**devotional, "source": "devotional"}] + candidates
        # A verse sharing a single theme word is usually a coincidence ("sacred stones" for holiness);
        # with two or more theme words, require two. The second pass below may still use one.
        need = 2 if len([t for t in theme["terms"] if term_weights(conn, [t])[t] > 0]) >= 2 else 1
        for candidate in candidates:
            if candidate["terms_matched"] < need:
                continue
            passage = passage_verses(conn, candidate["ref"])
            if passage["available"] and not _overlaps(passage, chosen):
                chosen.append({**_capped(passage), "source": candidate.get("source", "retrieval"), "basis": candidate["strategy"], "theme_index": index, "score": candidate["score"]})
                break
    if len(chosen) < MIN_PASSAGES:  # second pass: any remaining candidate of any theme
        for index, theme in enumerate(themes):
            for candidate in retrieval_candidates(conn, theme, limit=12):
                passage = passage_verses(conn, candidate["ref"])
                if passage["available"] and not _overlaps(passage, chosen):
                    chosen.append({**_capped(passage), "source": "retrieval", "basis": candidate["strategy"], "theme_index": index, "score": candidate["score"]})
                    break
            if len(chosen) >= MIN_PASSAGES:
                break
    return chosen[:MAX_PASSAGES], stats


# ----- 4. the word -----

def _excerpt(text: str, limit: int = 240) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    if len(flat) <= limit:
        return flat
    cut = flat.rfind(" ", 0, limit)
    return flat[: cut if cut > limit // 2 else limit] + " …"


def word_tool(conn: sqlite3.Connection, cfg: Config, which: str = "latest", *, refresh: bool = False, today: dt.date | None = None) -> dict:
    """Build the word for the chosen prayer (module docstring). Never writes outside the consumer DB."""
    started = time.monotonic()
    which = which if which in ("latest", "morning", "devotional") else "latest"
    prayer = latest_prayer(conn, cfg, which)
    if prayer is None or not (prayer.get("text") or "").strip():
        what = "a morning prayer" if which == "morning" else "a recorded prayer"
        return {"available": False, "which": which, "reason": "no_prayer", "spoken": f"I could not find {what} to respond to."}
    themes_result = extract_themes(conn, cfg, prayer, refresh=refresh)
    themes = themes_result["themes"]
    day = today or dt.date.today()
    chosen, stats = choose_passages(conn, cfg, prayer, themes, which=which, day=day)
    passages = []
    for passage in chosen:
        theme = themes[passage["theme_index"]] if passage.get("theme_index") is not None else _assign_theme(" ".join(v["text"] for v in passage["verses"]), [t for t in themes if t.get("label")])
        book_name = BOOK_NAMES.get(passage["book"], passage["book"])
        name = spoken_reference(book_name, passage["chapter"], passage["verse_from"], passage["verse_to"])
        passages.append({
            "reference": written_reference(book_name, passage["chapter"], passage["verse_from"], passage["verse_to"]),
            "spoken_reference": name,
            "frame": frame_for(passage["source"], passage.get("basis"), name, theme),
            "verses": passage["verses"],
            "cite_ids": passage["cite_ids"],
            "source": passage["source"],
            "basis": passage.get("basis"),
            "theme": (theme.get("label") or theme.get("quote")) if theme else None,
            "annotation_id": passage.get("annotation_id"),
            "ledger_ref": passage.get("ledger_ref"),
            "truncated": bool(passage.get("truncated")),
            "echo_windows": passage.get("echo_windows"),
            "theographic": _entities(conn, passage["verses"]),
        })
    when = spoken_date(prayer.get("recorded_at"))
    recorded = recorded_datetime(prayer.get("recorded_at"))
    lead = f"Here is a word for the prayer you recorded {('on ' + when) if when else 'most recently'}."
    spoken = " ".join([lead] + [f"{p['frame']} {' '.join(v['text'] for v in p['verses'])}" for p in passages])
    if not passages:
        spoken = f"{lead} I could not find a passage in the retained edition that fits it."
    return {
        "available": bool(passages),
        "which": which,
        "prayer": {
            "id": prayer["prayer_id"],
            "recorded_at": prayer.get("recorded_at"),
            "recorded_at_local": recorded.isoformat() if recorded else None,
            "basis": prayer["basis"],
            "source": prayer.get("source"),
            "client_capture_id": prayer.get("client_capture_id"),
            "text_sha256": prayer.get("text_sha256"),
            "chars": len(prayer["text"]),
            "excerpt": _excerpt(prayer["text"]),
        },
        "themes": themes,
        "themes_source": themes_result["source"],
        "themes_detail": {k: themes_result.get(k) for k in ("model", "structured_output", "cached", "usage", "elapsed_ms", "errors")},
        "passages": passages,
        "candidates": stats,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "spoken": spoken,
    }
