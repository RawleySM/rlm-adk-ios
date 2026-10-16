"""The passage open in the phone's scripture pane: one per session, kept in the reading state.

The pane shows exactly what the talk tier holds: a passage set by ``read_scripture`` (the reading
the bot is speaking) or chosen on the phone (``client_passage``). The reading state
(``talk.tools.new_reading_state``) carries it with these keys:

``reference`` / ``title`` / ``edition`` / ``book`` / ``chapter`` / ``verses`` (``verse``/``text``/
``osis``), ``source`` (``tool`` | ``client``), ``spoken_count`` (verses fully heard, an index into
``verses``), ``spoken_through`` (that verse's number), ``queue`` (units queued for the TTS, in order:
``{"index", "verse", "text"}``, ``index`` None for the announced reference) and ``reading`` (a
reading is queued or playing).

The LLM sees the passage through ``pane_note``, appended to every Flows node's system instruction,
so "what does this chapter say about..." refers to the text on the screen.
"""

from __future__ import annotations

import re
from typing import Any


def norm(text: str) -> str:
    """Lower-case words only, for matching spoken text to queued units."""
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower()).split())


def from_tool_result(result: dict[str, Any], requested: str) -> dict[str, Any] | None:
    """Passage fields from a ``/tools/scripture`` result, or None when it carries no verses."""
    if not (isinstance(result, dict) and result.get("available") and result.get("verses")):
        return None
    refs = result.get("references") or []
    first = refs[0] if refs else {}
    verses = [{"verse": int(v.get("verse") or 0), "text": str(v.get("text") or "").strip(), "osis": v.get("osis")} for v in result["verses"] if str(v.get("text") or "").strip()]
    return {
        "reference": result.get("reference") or requested,
        "title": result.get("title"),
        "edition": (result.get("edition") or {}).get("name") or "World English Bible",
        "book": first.get("book"),
        "chapter": first.get("chapter"),
        "verses": verses,
    }


def requested_range(result: dict[str, Any]) -> tuple[str, int, int] | None:
    """(chapter reference, first verse, last verse) when ``result`` asked for verses inside one chapter, else None."""
    refs = result.get("references") or []
    if len(refs) != 1 or not refs[0].get("verse_from"):
        return None
    reference = str(result.get("reference") or "")
    chapter = re.match(r"^(.*?\d+)(?::|$)", reference)
    first = int(refs[0]["verse_from"])
    return (chapter.group(1) if chapter else reference), first, int(refs[0].get("verse_to") or first)


def apply(state: dict[str, Any], passage: dict[str, Any], source: str) -> None:
    """Make ``passage`` the open passage; nothing of it has been heard yet."""
    state.update(passage)
    state.update({"chapter_reference": passage.get("reference"), "source": source, "spoken_count": 0, "spoken_through": 0, "queue": [], "reading": False})


def message(state: dict[str, Any]) -> dict[str, Any]:
    """The ``passage`` event for the client."""
    return {
        "type": "passage",
        "reference": state.get("reference"),
        "title": state.get("title"),
        "edition": state.get("edition"),
        "book": state.get("book"),
        "chapter": state.get("chapter"),
        "verses": state.get("verses") or [],
        "spoken_count": int(state.get("spoken_count") or 0),
        "source": state.get("source"),
    }


def read_queue(state: dict[str, Any], restart: bool, indices: list[int] | None = None, announce: str | None = None) -> list[dict[str, Any]]:
    """Units to speak: the rest of the passage after the last verse heard, or all of it with its reference.

    A passage that was never started, was heard to the end, or is restarted on request begins with
    the reference ("Romans 12.") followed by every verse. ``indices`` reads only those verses of the
    open passage (a verse reference inside the chapter on screen), announced as ``announce``.
    """
    verses = state.get("verses") or []
    if indices is not None:
        units = [{"index": None, "verse": None, "text": announce}] if announce else []
        return units + [{"index": i, "verse": verses[i]["verse"], "text": verses[i]["text"]} for i in indices if 0 <= i < len(verses)]
    count = int(state.get("spoken_count") or 0)
    if restart or count <= 0 or count >= len(verses):
        units = [{"index": None, "verse": None, "text": f"{state.get('reference')}."}]
        count = 0
    else:
        units = []
    units += [{"index": i, "verse": v["verse"], "text": v["text"]} for i, v in enumerate(verses) if i >= count]
    return units


def index_of_verse(state: dict[str, Any], verse: int) -> int | None:
    """Position in the open passage's verse list of verse number ``verse`` (the first at or after it), or None."""
    return next((i for i, v in enumerate(state.get("verses") or []) if int(v.get("verse") or 0) >= verse), None)


_UNITS = {w: n for n, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * n for n, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}


def spoken_number(words: list[str]) -> int | None:
    """The number at the start of ``words``: "16", "sixteen", "twenty two", "one hundred nineteen"; None if none."""
    if words and words[0].isdigit():
        return int(words[0])
    total, seen = 0, False
    for word in words:
        if word in _UNITS:
            total += _UNITS[word]
        elif word in _TENS:
            total += _TENS[word]
        elif word == "hundred" and seen:
            total *= 100
        elif word == "and" and seen:
            continue
        else:
            break
        seen = True
    return total if seen else None


_VERSE_LEADS = r"start|starting|read|reading|begin|beginning|continue|resume|play|pick up|go back|back up|go|jump|skip"
_VERSE_REQUEST = re.compile(rf"\b(?:{_VERSE_LEADS})\b(?P<between>(?:\s+[a-z']+){{0,4}}?)\s+verse\s+(?P<number>[a-z0-9]+(?:\s+[a-z0-9]+){{0,3}})")


def verse_request(text: str) -> int | None:
    """The verse of the open passage that ``text`` (``norm``-ed) asks to read from, else None.

    "Start at verse 16", "read from verse sixteen", "go back to verse 5", "continue from verse twelve".
    A reference to another passage ("read Romans 8 verse 16", "chapter 3 verse 2") is not one: the
    model opens that passage with read_scripture.
    """
    match = _VERSE_REQUEST.search(text)
    if match is None:
        return None
    if "chapter" in text.split() or any(word.isdigit() for word in text[: match.start("number")].split()):
        return None
    return spoken_number(match.group("number").split())


def pane_note(state: dict[str, Any], max_chars: int) -> str:
    """The system-instruction paragraph describing the open passage ('' when none)."""
    verses = state.get("verses") or []
    if not verses:
        return ""
    count = int(state.get("spoken_count") or 0)
    if state.get("reading"):
        status = "it is being read aloud now"
    elif count <= 0:
        status = "it has not been read aloud yet"
    elif count >= len(verses):
        status = "it was read aloud to the end"
    else:
        status = f"the reading stopped after verse {verses[count - 1]['verse']}"
    lines, used, omitted = [], 0, 0
    for v in verses:
        line = f"{v['verse']} {v['text']}"
        if used + len(line) > max_chars:
            omitted += 1
            continue
        lines.append(line)
        used += len(line) + 1
    tail = f"\n({omitted} later verses omitted here; call read_scripture on a verse reference to see them.)" if omitted else ""
    return (
        f"Scripture pane: Rawley's screen shows {state.get('reference')} ({state.get('edition') or 'World English Bible'}); {status}. "
        "When he says this passage, this chapter, this verse or the text, or asks about Scripture without naming another reference, "
        "he means this passage: answer from its verses below in two or three spoken sentences, quoting at most the verse it concerns. "
        "If he asks to hear it read, call continue_reading (it reads the pane's passage); to start at a verse of it, pass from_verse.\n" + "\n".join(lines) + tail
    )


_READ_LEADS = ("please ", "can you ", "could you ", "would you ", "will you ", "now ", "ok ", "okay ", "go ahead and ", "and ")
_READ_TAILS = (" please", " for me", " to me", " aloud", " out loud", " now", " again", " from the beginning", " from the start", " from the top")
_READ_CORE = {
    "read", "read it", "read this", "read that", "read the passage", "read the chapter", "read the text", "read the scripture",
    "read this passage", "read this chapter", "read that passage", "read that chapter", "start reading", "begin reading", "read on",
    "read it all", "read the whole chapter", "read the whole thing", "start over", "read from the beginning", "read it from the beginning",
}
_RESTART_WORDS = ("again", "from the beginning", "from the start", "from the top", "start over")


def read_request(n: str) -> str | None:
    """'restart' | 'resume' when the normalised utterance only asks to read the open passage, else None.

    "Read me Psalm 23" names a new reference and is left to the model; "read it", "please read
    this to me" and "read it again" are not.
    """
    text = n.strip()
    changed = True
    while changed and text:
        changed = False
        for lead in _READ_LEADS:
            if text.startswith(lead):
                text, changed = text[len(lead):].strip(), True
        for tail in _READ_TAILS:
            if text.endswith(tail):
                text, changed = text[: -len(tail)].strip(), True
    if text not in _READ_CORE:
        return None
    return "restart" if any(w in n for w in _RESTART_WORDS) else "resume"
