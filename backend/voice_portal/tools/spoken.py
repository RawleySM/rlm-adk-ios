"""Spoken-language helpers shared by the tool endpoints.

Inbound: spoken Scripture references ("John chapter three verse sixteen", "Romans eight, verses
twenty eight through thirty") are normalised into the written form `references.explicit_references`
parses ("John 3:16", "Romans 8:28-30"). Outbound: references, dates and teacher traditions are
rendered for text-to-speech, and any text bound for `spoken` loses markdown and bracket citations.
"""

from __future__ import annotations

import datetime as dt
import re

from ..references import BOOK_NAME_PATTERNS

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_TOKEN = re.compile(r"[A-Za-z]+|\d+|\s+|[^A-Za-z\d\s]")


def _is_number_word(token: str) -> bool:
    lower = token.lower()
    return lower in _UNITS or lower in _TENS or lower == "hundred"


def _combines(previous: str, following: str) -> bool:
    """Whether two adjacent number words form one number (twenty-three, one hundred, hundred five)."""
    if previous in _TENS:
        return following in _UNITS and 0 < _UNITS[following] < 10
    if following == "hundred":
        return previous in _UNITS and 0 < _UNITS[previous] < 10
    return previous == "hundred" and following != "hundred"


def parse_number_words(words: list[str]) -> list[int]:
    """["one", "hundred", "nineteen"] -> [119]; ["three", "sixteen"] -> [3, 16]; ["twenty", "three"] -> [23]."""
    numbers: list[int] = []
    current: list[int] | None = None  # [hundreds, tens, units]
    last: str | None = None
    for word in words:
        if word == "hundred":
            if current is not None and last == "unit" and current[0] == 0 and current[1] == 0:
                current = [current[2] * 100, 0, 0]
            else:
                if current is not None:
                    numbers.append(sum(current))
                current = [100, 0, 0]
            last = "hundred"
        elif word in _TENS:
            if current is not None and last == "hundred":
                current[1] = _TENS[word]
            else:
                if current is not None:
                    numbers.append(sum(current))
                current = [0, _TENS[word], 0]
            last = "tens"
        else:
            value = _UNITS[word]
            if current is not None and last == "hundred":
                current[1 if value >= 10 else 2] = value
            elif current is not None and last == "tens" and 0 < value < 10:
                current[2] = value
            else:
                if current is not None:
                    numbers.append(sum(current))
                current = [0, 0, value]
            last = "teen" if value >= 10 else "unit"
    if current is not None:
        numbers.append(sum(current))
    return numbers


def words_to_numbers(text: str) -> str:
    """Replace runs of English number words with digits ("one hundred nineteen" -> "119").

    A unit or teen directly after another unit or teen starts a new number, so "three sixteen" stays
    two numbers ("3 16"); "twenty three" and "twenty-three" are one (23); "a hundred and fifty" is
    150; a hyphen between numbers that do not combine is kept ("one-three" -> "1-3"). Other words
    pass through unchanged.
    """
    tokens = _TOKEN.findall(text)
    count = len(tokens)

    def next_word(index: int) -> tuple[int, bool]:
        """Index of the next word token after separators, and whether a hyphen was crossed."""
        hyphen = False
        while index < count and (tokens[index].isspace() or tokens[index] == "-"):
            hyphen = hyphen or tokens[index] == "-"
            index += 1
        return index, hyphen

    out: list[str] = []
    index = 0
    while index < count:
        token = tokens[index]
        after_a, _ = next_word(index + 1)
        starts_with_a = token.lower() == "a" and after_a < count and tokens[after_a].lower() == "hundred"
        if not (_is_number_word(token) or starts_with_a):
            out.append(token)
            index += 1
            continue
        words: list[str] = ["one"] if starts_with_a else []
        cursor = after_a if starts_with_a else index
        end = cursor
        while cursor < count and _is_number_word(tokens[cursor]):
            words.append(tokens[cursor].lower())
            cursor += 1
            end = cursor
            following, hyphen = next_word(cursor)
            if following < count and _is_number_word(tokens[following]):
                if hyphen and not _combines(words[-1], tokens[following].lower()):
                    break
                cursor = following
                continue
            if following < count and tokens[following].lower() == "and" and words[-1] == "hundred":
                beyond, _ = next_word(following + 1)
                if beyond < count and _is_number_word(tokens[beyond]):
                    cursor = beyond
                    continue
            break
        out.append(" ".join(str(number) for number in parse_number_words(words)))
        index = end
    return "".join(out)


# A book name followed by two bare numbers ("John 3 16"): chapter and verse.
_BOOK_CHAPTER_VERSE = re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(name) for name in BOOK_NAME_PATTERNS) + r")\.?\s+(\d{1,3})\s+(\d{1,3})(?![\d:])", re.IGNORECASE)


def normalize_spoken_reference(text: str) -> str:
    """Rewrite a spoken reference into written form for `explicit_references`.

    "John chapter three verse sixteen" and "John three sixteen" -> "John 3:16"; "Romans 8 verses 28
    through 30" -> "Romans 8:28-30"; "Psalm twenty-three" -> "Psalm 23". Already-written references pass through.
    """
    value = words_to_numbers(text or "")
    value = re.sub(r"\bchapters?\s+(\d+)", r"\1", value, flags=re.IGNORECASE)
    value = re.sub(r"(\d+)\s*,?\s+(?:verses?|vv?\.)\s+(\d+)", r"\1:\2", value, flags=re.IGNORECASE)
    value = _BOOK_CHAPTER_VERSE.sub(r"\1 \2:\3", value)  # "John 3 16" (said without "verse")
    value = re.sub(r"(\d+:\d+)\s*(?:-|–|—|\bto\b|\bthrough\b|\bthru\b|\buntil\b)\s*(\d+)\b", r"\1-\2", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()


def spoken_reference(book_name: str, chapter: int, verse_from: int | None = None, verse_to: int | None = None) -> str:
    """"Romans 8, verses 28 to 30" / "Psalm 23" / "John 3, verse 16" for text-to-speech."""
    name = "Psalm" if book_name == "Psalms" else book_name
    if verse_from is None:
        return f"{name} {chapter}"
    if verse_to is None or verse_to == verse_from:
        return f"{name} {chapter}, verse {verse_from}"
    return f"{name} {chapter}, verses {verse_from} to {verse_to}"


def written_reference(book_name: str, chapter: int, verse_from: int | None = None, verse_to: int | None = None) -> str:
    """"Romans 8:28-30" / "Psalm 23" / "John 3:16"."""
    name = "Psalm" if book_name == "Psalms" else book_name
    if verse_from is None:
        return f"{name} {chapter}"
    if verse_to is None or verse_to == verse_from:
        return f"{name} {chapter}:{verse_from}"
    return f"{name} {chapter}:{verse_from}-{verse_to}"


def clean_spoken(text: str) -> str:
    """Strip bracket citations, markdown emphasis/headings/backticks and collapse whitespace."""
    value = re.sub(r"\[\s*\d+(?:\s*[,;-]\s*\d+)*\s*\]", "", text or "")
    value = re.sub(r"(?<=\w)_(?=\w)", " ", value)  # identifiers read as words: plan_organize -> plan organize
    value = re.sub(r"[*_`#>|]+", "", value)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    return re.sub(r"\s+", " ", value).strip()


def spoken_tradition(label: str) -> str:
    """"Reformed Baptist (Christian Hedonism)" -> "Reformed Baptist, Christian Hedonism"; "/" -> "and"."""
    value = re.sub(r"\s*\(([^)]*)\)", r", \1", label)
    return value.replace("/", " and ")


def recorded_datetime(recorded_at: str | float | None) -> dt.datetime | None:
    """Local datetime of an epoch number or ISO string; None when unparseable."""
    if recorded_at in (None, ""):
        return None
    try:
        return dt.datetime.fromtimestamp(float(recorded_at)).astimezone()
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    try:
        parsed = dt.datetime.fromisoformat(str(recorded_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def spoken_date(recorded_at: str | float | None) -> str | None:
    """"October 13" (local time) for an epoch or ISO timestamp."""
    when = recorded_datetime(recorded_at)
    return f"{when.strftime('%B')} {when.day}" if when else None


_SENTENCE = re.compile(r"[^.!?]+(?:[.!?]+[\"'”’)]*|$)")


def sentences(text: str) -> list[tuple[int, int]]:
    """(start, end) spans of the sentences of `text`, whitespace-trimmed, in order."""
    spans: list[tuple[int, int]] = []
    for match in _SENTENCE.finditer(text):
        start, end = match.start(), match.end()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if end > start:
            spans.append((start, end))
    return spans


def term_stem(term: str) -> str:
    """Crude prefix stem for matching a search word in running text ("abide" -> "abid" matches "abiding")."""
    return term[: max(4, len(term) - 2)] if len(term) > 4 else term


def roman(number: int) -> str:
    """Lower-case Roman numeral (8 -> "viii"), for older chapter citations such as "Rom. viii. 28"."""
    out = ""
    for value, letters in ((100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while number >= value:
            out += letters
            number -= value
    return out
