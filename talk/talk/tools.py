"""Tool functions the LLM may call; each is an HTTP request to the skill service on the desktop.

Two kinds of tool:

* **Exact-speech tools** (``read_scripture``, ``continue_reading``, ``word_from_prayer``,
  ``request_work``): the skill service's text is spoken verbatim. The handler pushes one
  ``TTSSpeakFrame`` per sentence straight to the TTS, records the words as the assistant's message
  in the context, and returns the result with ``run_llm=False`` so the model does not paraphrase a
  verse, a frame or an operation id. Verses are pushed one frame each so the reading tracker knows
  exactly how far a reading got when it is interrupted.
* **Composed tools** (``search_sources``, ``interpretations``, ``project_checkin``): the result goes
  back to the model, which answers in its own words under the system prompt's rules.

Pipecat executes function calls asynchronously, so a slow skill never stalls the audio loop.
"""

from __future__ import annotations

import json
import re
from typing import Any

import aiohttp
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import FunctionCallResultProperties, LLMMessagesAppendFrame, TTSSpeakFrame
from pipecat.services.llm_service import FunctionCallParams

from talk.config import TalkConfig

_cfg: TalkConfig | None = None

LONG_READING_VERSES = 10


def new_reading_state() -> dict[str, Any]:
    """One session's reading state, shared by the tools, the floor and the reading tracker.

    Keys: ``reference``, ``chapter_reference``, ``verses`` (``verse``/``text``/``osis`` dicts),
    ``spoken_through`` (last verse number the listener fully heard) and ``last_user_text`` (the
    utterance the floor last forwarded to the LLM; decides whether a long chapter is read or quoted).
    Each session gets its own: two phones, or a phone and a benchmark client, must never share one.
    """
    return {"reference": None, "chapter_reference": None, "verses": [], "spoken_through": 0, "last_user_text": ""}


def _state(params: FunctionCallParams) -> dict[str, Any]:
    state = getattr(params.llm, "reading_state", None)
    if state is None:
        # Warning: a service registered without ``register_tools`` has no per-session state; fall back to its own.
        state = new_reading_state()
        params.llm.reading_state = state
    return state

UNAVAILABLE_NOTE = (
    "This source is unavailable right now. Tell Rawley plainly that you could not reach it, answer briefly "
    "from what you already know, and do not call any tool again for this question."
)


def _unavailable(reason: str) -> dict[str, Any]:
    return {"error": reason, "instruction": UNAVAILABLE_NOTE}


async def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    assert _cfg is not None
    url = _cfg.skill_base_url.rstrip("/") + path
    timeout = aiohttp.ClientTimeout(total=_cfg.skill_timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params={k: v for k, v in params.items() if v is not None}) as response:
            text = await response.text()
            if response.status != 200:
                return _unavailable(f"skill service HTTP {response.status} for {path}")
            return json.loads(text)


async def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    assert _cfg is not None
    url = _cfg.skill_base_url.rstrip("/") + path
    timeout = aiohttp.ClientTimeout(total=_cfg.skill_timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as response:
            text = await response.text()
            if response.status >= 300:
                return _unavailable(f"skill service HTTP {response.status} for {path}")
            return json.loads(text)


async def _speak_exact(params: FunctionCallParams, sentences: list[str], *, history: str, result: dict[str, Any]) -> None:
    """Speak ``sentences`` verbatim, record them as the assistant's words, and skip the LLM."""

    for sentence in sentences:
        sentence = (sentence or "").strip()
        if sentence:
            await params.llm.push_frame(TTSSpeakFrame(sentence))
    if history.strip():
        await params.llm.push_frame(LLMMessagesAppendFrame(messages=[{"role": "assistant", "content": history.strip()}], run_llm=False))
    await params.result_callback(result, properties=FunctionCallResultProperties(run_llm=False))


def _chapter_reference(reference: str) -> str:
    m = re.match(r"^(.*?\d+)", reference.strip())
    return m.group(1) if m else reference


# ----- composed tools -----


async def search_sources(params: FunctionCallParams) -> None:
    """Retrieve excerpts from prayers, Scripture and teaching for a topic."""
    args = params.arguments or {}
    try:
        result = await _get("/search", {"q": args.get("query", ""), "family": args.get("family"), "limit": 4})
        hits = [
            {"family": h["source_family"], "title": h.get("title"), "text": (h.get("text") or "")[:600]}
            for h in result.get("hits", [])
        ]
        await params.result_callback({"hits": hits, "note": "Say which excerpts you drew on by title; the app shows the citations."})
    except Exception as exc:  # the barrier: a failed tool is a spoken fact, not a stall
        logger.warning(f"search_sources failed: {exc}")
        await params.result_callback(_unavailable(str(exc)))


async def interpretations(params: FunctionCallParams) -> None:
    """Teachers' readings of a passage or topic, labelled by teacher and tradition."""
    args = params.arguments or {}
    try:
        result = await _get("/tools/interpretations", {"reference": args.get("reference"), "topic": args.get("topic")})
        await params.result_callback(result)
    except Exception as exc:
        await params.result_callback(_unavailable(str(exc)))


async def project_checkin(params: FunctionCallParams) -> None:
    """Resolve a project from a description against the work register and report its state."""
    args = params.arguments or {}
    try:
        result = await _get("/tools/project", {"description": args.get("description", ""), "confirm": args.get("confirmed_project_id")})
        await params.result_callback(result)
    except Exception as exc:
        await params.result_callback(_unavailable(str(exc)))


# ----- exact-speech tools -----


async def read_scripture(params: FunctionCallParams) -> None:
    """Read the retained edition's verses for a reference, verbatim, one verse per spoken unit."""
    args = params.arguments or {}
    reference = str(args.get("reference", "")).strip()
    try:
        result = await _get("/tools/scripture", {"reference": reference})
    except Exception as exc:
        await params.result_callback(_unavailable(str(exc)))
        return
    if not (isinstance(result, dict) and result.get("available") and result.get("verses")):
        await params.result_callback(result if isinstance(result, dict) else _unavailable("bad scripture result"))
        return
    verses = [{"verse": int(v.get("verse") or 0), "text": str(v.get("text") or "").strip(), "osis": v.get("osis")} for v in result["verses"]]
    state = _state(params)
    wants_reading = bool(re.search(r"\b(read|reads|reading|recite|hear|listen to|play)\b", str(state.get("last_user_text") or "").lower()))
    if len(verses) > LONG_READING_VERSES and not wants_reading:
        # A question about a long chapter: hand the text to the model to quote from, instead of reading it all.
        await params.result_callback({
            "reference": reference,
            "verses": [{"verse": v["verse"], "text": v["text"]} for v in verses],
            "instruction": "Rawley asked a question, not for a reading. Quote the two or three verses that answer it (with their verse numbers) and answer briefly in your own words; do not read the whole chapter.",
        })
        return
    state.update({"reference": reference, "chapter_reference": _chapter_reference(reference), "verses": verses, "spoken_through": 0})
    sentences = [f"{reference}."] + [v["text"] for v in verses]
    summary = {"reference": reference, "verses_read": [v["verse"] for v in verses], "cite_ids": result.get("cite_ids"), "coverage": result.get("coverage"), "spoken": True}
    await _speak_exact(params, sentences, history=" ".join(sentences), result=summary)


async def continue_reading(params: FunctionCallParams) -> None:
    """Resume the interrupted reading from the verse after the last one fully spoken."""
    state = _state(params)
    verses = state.get("verses") or []
    through = int(state.get("spoken_through") or 0)
    remaining = [v for v in verses if v["verse"] > through]
    if not verses:
        await params.result_callback({"error": "no reading in progress", "instruction": "Tell Rawley there is no reading to continue and ask what he would like read."})
        return
    if not remaining:
        await _speak_exact(params, ["That was the end of the passage."], history="That was the end of the passage.", result={"done": True})
        return
    sentences = [v["text"] for v in remaining]
    await _speak_exact(params, sentences, history=" ".join(sentences), result={"reference": state.get("reference"), "resumed_from_verse": remaining[0]["verse"], "spoken": True})


async def word_from_prayer(params: FunctionCallParams) -> None:
    """Latest prayer → themes → passages, read with one-sentence frames."""
    args = params.arguments or {}
    try:
        result = await _get("/tools/word", {"which": args.get("which", "latest")})
    except Exception as exc:
        await params.result_callback(_unavailable(str(exc)))
        return
    if not isinstance(result, dict) or result.get("error") or not result.get("passages"):
        await params.result_callback(result if isinstance(result, dict) else _unavailable("bad word result"))
        return
    prayer = result.get("prayer") or {}
    sentences: list[str] = [result.get("opening") or (f"A word for the prayer you recorded {str(prayer.get('recorded_at', ''))[:10]}." if prayer else "A word for your prayer.")]
    for passage in result["passages"]:
        if passage.get("frame"):
            sentences.append(str(passage["frame"]).strip())
        if passage.get("reference"):
            sentences.append(f"{passage['reference']}.")
        for v in passage.get("verses") or []:
            text = str(v.get("text") or "").strip()
            if text:
                sentences.append(text)
    summary = {"prayer": {k: prayer.get(k) for k in ("id", "recorded_at")}, "themes": result.get("themes"), "passages": [p.get("reference") for p in result["passages"]], "spoken": True}
    await _speak_exact(params, sentences, history=" ".join(sentences), result=summary)


async def request_work(params: FunctionCallParams) -> None:
    """Post a durable work request to rlm-adk's instance mailbox; accepted or refused, never done."""
    args = params.arguments or {}
    try:
        result = await _post("/tools/work", {"request_text": args.get("request", ""), "kind": args.get("kind", "research")})
    except Exception as exc:
        await params.result_callback(_unavailable(str(exc)))
        return
    spoken = str(result.get("spoken") or "").strip() if isinstance(result, dict) else ""
    if not spoken:
        await params.result_callback(result if isinstance(result, dict) else _unavailable("bad work result"))
        return
    await _speak_exact(params, [spoken], history=spoken, result={"operation_id": result.get("operation_id"), "status": result.get("status"), "spoken": True})


FUNCTIONS = {
    "search_sources": search_sources,
    "read_scripture": read_scripture,
    "continue_reading": continue_reading,
    "word_from_prayer": word_from_prayer,
    "interpretations": interpretations,
    "project_checkin": project_checkin,
    "request_work": request_work,
}


def tools_schema() -> ToolsSchema:
    return ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name="search_sources",
                description="Find excerpts from Rawley's prayers, the retained Bible edition and the teaching library about a topic.",
                properties={
                    "query": {"type": "string", "description": "Topic words, e.g. 'rest' or 'my son'."},
                    "family": {"type": "string", "enum": ["scripture", "teaching", "prayer"], "description": "Restrict to one source family."},
                },
                required=["query"],
            ),
            FunctionSchema(
                name="read_scripture",
                description="Read a Scripture reference such as 'Psalm 23' or 'Romans 8:28-30' aloud, verbatim from the retained edition. The tool speaks it; you add nothing.",
                properties={"reference": {"type": "string"}},
                required=["reference"],
            ),
            FunctionSchema(
                name="continue_reading",
                description="Resume the last Scripture reading from where it was interrupted. Use when Rawley says continue, go on, or pick up where you stopped. The tool speaks it; you add nothing.",
                properties={},
                required=[],
            ),
            FunctionSchema(
                name="word_from_prayer",
                description="A word from God in response to Rawley's latest (or this morning's) prayer: the tool names the prayer's themes and reads two to four Scripture passages with one-sentence frames. The tool speaks it; you add nothing.",
                properties={"which": {"type": "string", "enum": ["latest", "morning", "devotional"]}},
                required=[],
            ),
            FunctionSchema(
                name="interpretations",
                description="Different interpretations of a passage or topic from the teaching library and enrichment products, each labelled by teacher and tradition. Present them in your own words, naming each teacher and tradition, and keep Scripture, teacher and personal context distinct.",
                properties={"reference": {"type": "string"}, "topic": {"type": "string"}},
                required=[],
            ),
            FunctionSchema(
                name="project_checkin",
                description="Identify a project from a vague description and report its plan, branches, results, checkpoints and outputs from the project work register. If the result is not confident, ask the confirmation question it gives; call again with confirmed_project_id after Rawley confirms.",
                properties={"description": {"type": "string"}, "confirmed_project_id": {"type": "string"}},
                required=["description"],
            ),
            FunctionSchema(
                name="request_work",
                description="Record a durable work request (research, project task) for the rlm-adk residents. The tool speaks the recorded status (operation id; accepted, or refused with the reason); it is never done by this call. You add nothing.",
                properties={"request": {"type": "string"}, "kind": {"type": "string", "enum": ["research", "project", "task"]}},
                required=["request"],
            ),
        ]
    )


def register_tools(llm, cfg: TalkConfig, reading_state: dict[str, Any]) -> None:
    """Register the function handlers on one session's LLM service and attach its reading state."""
    global _cfg
    _cfg = cfg
    llm.reading_state = reading_state
    for name, fn in FUNCTIONS.items():
        llm.register_function(name, fn)
