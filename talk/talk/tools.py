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
from pipecat.flows import NO_RESPONSE, FlowArgs, FlowManager, FlowsFunctionSchema
from pipecat.frames.frames import TTSSpeakFrame

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


def _chapter_reference(reference: str) -> str:
    m = re.match(r"^(.*?\d+)", reference.strip())
    return m.group(1) if m else reference



def _state(flow_manager: FlowManager) -> dict[str, Any]:
    """This session's reading state (``new_reading_state``), kept on the FlowManager's per-session state."""
    return flow_manager.state.setdefault("reading", new_reading_state())


async def _speak_exact(flow_manager: FlowManager, sentences: list[str]) -> str:
    """Queue ``sentences`` for the TTS verbatim (one unit each); returns the spoken text for the result.

    Frames enter at the pipeline source, so the floor's gate still applies (nothing is spoken in
    prayer or quiet), the reading tracker counts the verses played, and no LLM turn runs for them
    (the handler returns ``NO_RESPONSE`` or a node whose ``respond_immediately`` is False).
    """
    spoken = [str(x or "").strip() for x in sentences if str(x or "").strip()]
    await flow_manager.task.queue_frames([TTSSpeakFrame(x) for x in spoken])
    return " ".join(spoken)


# ----- Flows function handlers: (args, flow_manager) -> (result, next_node) -----
# ``next_node`` values name nodes from talk.flows (resolved lazily to avoid an import cycle).


def _node(name: str):
    from talk import flows

    return flows.node(name)


async def search_sources(args: FlowArgs, flow_manager: FlowManager):
    """Retrieve excerpts from prayers, Scripture and teaching for a topic."""
    try:
        result = await _get("/search", {"q": args.get("query", ""), "family": args.get("family"), "limit": 4})
        hits = [{"family": h["source_family"], "title": h.get("title"), "text": (h.get("text") or "")[:600]} for h in result.get("hits", [])]
        return {"hits": hits, "note": "Say which excerpts you drew on by title; the app shows the citations."}, None
    except Exception as exc:  # the barrier: a failed tool is a spoken fact, not a stall
        logger.warning(f"search_sources failed: {exc}")
        return _unavailable(str(exc)), None


async def interpretations(args: FlowArgs, flow_manager: FlowManager):
    """Teachers' readings of a passage or topic, labelled by teacher and tradition."""
    try:
        return await _get("/tools/interpretations", {"reference": args.get("reference"), "topic": args.get("topic")}), None
    except Exception as exc:
        return _unavailable(str(exc)), None


async def project_checkin(args: FlowArgs, flow_manager: FlowManager):
    """Resolve a project from a description against the work register and report its state; enters the project node."""
    try:
        result = await _get("/tools/project", {"description": args.get("description", ""), "confirm": args.get("confirmed_project_id")})
    except Exception as exc:
        return _unavailable(str(exc)), None
    return result, (_node("project") if flow_manager.current_node != "project" else None)


async def read_scripture(args: FlowArgs, flow_manager: FlowManager):
    """Read the retained edition's verses for a reference, verbatim, one verse per spoken unit; enters the devotional node."""
    reference = str(args.get("reference", "")).strip()
    try:
        result = await _get("/tools/scripture", {"reference": reference})
    except Exception as exc:
        return _unavailable(str(exc)), None
    if not (isinstance(result, dict) and result.get("available") and result.get("verses")):
        return (result if isinstance(result, dict) else _unavailable("bad scripture result")), None
    verses = [{"verse": int(v.get("verse") or 0), "text": str(v.get("text") or "").strip(), "osis": v.get("osis")} for v in result["verses"]]
    state = _state(flow_manager)
    wants_reading = bool(re.search(r"\b(read|reads|reading|recite|hear|listen to|play)\b", str(state.get("last_user_text") or "").lower()))
    if len(verses) > LONG_READING_VERSES and not wants_reading:
        # A question about a long chapter: hand the text to the model to quote from, instead of reading it all.
        return {
            "reference": reference,
            "verses": [{"verse": v["verse"], "text": v["text"]} for v in verses],
            "instruction": "Rawley asked a question, not for a reading. Quote the two or three verses that answer it (with their verse numbers) and answer briefly in your own words; do not read the whole chapter.",
        }, None
    state.update({"reference": reference, "chapter_reference": _chapter_reference(reference), "verses": verses, "spoken_through": 0})
    spoken = await _speak_exact(flow_manager, [f"{reference}."] + [v["text"] for v in verses])
    result_out = {"reference": reference, "verses_read": [v["verse"] for v in verses], "text": spoken, "cite_ids": result.get("cite_ids"), "coverage": result.get("coverage"), "spoken": True}
    return result_out, (_node("devotional") if flow_manager.current_node != "devotional" else NO_RESPONSE)


async def continue_reading(args: FlowArgs, flow_manager: FlowManager):
    """Resume the interrupted reading from the verse after the last one fully spoken."""
    state = _state(flow_manager)
    verses = state.get("verses") or []
    through = int(state.get("spoken_through") or 0)
    remaining = [v for v in verses if v["verse"] > through]
    if not verses:
        return {"error": "no reading in progress", "instruction": "Tell Rawley there is no reading to continue and ask what he would like read."}, None
    if not remaining:
        await _speak_exact(flow_manager, ["That was the end of the passage."])
        return {"done": True, "spoken": True}, NO_RESPONSE
    spoken = await _speak_exact(flow_manager, [v["text"] for v in remaining])
    return {"reference": state.get("reference"), "resumed_from_verse": remaining[0]["verse"], "text": spoken, "spoken": True}, NO_RESPONSE


async def word_from_prayer(args: FlowArgs, flow_manager: FlowManager):
    """Latest prayer → themes → passages, read with one-sentence frames."""
    try:
        result = await _get("/tools/word", {"which": args.get("which", "latest")})
    except Exception as exc:
        return _unavailable(str(exc)), None
    if not isinstance(result, dict) or result.get("error") or not result.get("passages"):
        return (result if isinstance(result, dict) else _unavailable("bad word result")), None
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
    spoken = await _speak_exact(flow_manager, sentences)
    summary = {"prayer": {k: prayer.get(k) for k in ("id", "recorded_at")}, "themes": result.get("themes"), "passages": [p.get("reference") for p in result["passages"]], "text": spoken, "spoken": True}
    return summary, (_node("conversation") if flow_manager.current_node == "available" else NO_RESPONSE)


async def request_work(args: FlowArgs, flow_manager: FlowManager):
    """Post a durable work request to rlm-adk's instance mailbox; accepted or refused, never done."""
    try:
        result = await _post("/tools/work", {"request_text": args.get("request", ""), "kind": args.get("kind", "research")})
    except Exception as exc:
        return _unavailable(str(exc)), None
    spoken = str(result.get("spoken") or "").strip() if isinstance(result, dict) else ""
    if not spoken:
        return (result if isinstance(result, dict) else _unavailable("bad work result")), None
    await _speak_exact(flow_manager, [spoken])
    return {"operation_id": result.get("operation_id"), "status": result.get("status"), "text": spoken, "spoken": True}, NO_RESPONSE


# ----- schemas -----

SCHEMAS: dict[str, FlowsFunctionSchema] = {
    "search_sources": FlowsFunctionSchema(
        name="search_sources",
        description="Find excerpts from Rawley's prayers, the retained Bible edition and the teaching library about a topic.",
        properties={
            "query": {"type": "string", "description": "Topic words, e.g. 'rest' or 'my son'."},
            "family": {"type": "string", "enum": ["scripture", "teaching", "prayer"], "description": "Restrict to one source family."},
        },
        required=["query"],
        handler=search_sources,
    ),
    "read_scripture": FlowsFunctionSchema(
        name="read_scripture",
        description="Read a Scripture reference such as 'Psalm 23' or 'Romans 8:28-30' aloud, verbatim from the retained edition. The tool speaks it; you add nothing.",
        properties={"reference": {"type": "string"}},
        required=["reference"],
        handler=read_scripture,
    ),
    "continue_reading": FlowsFunctionSchema(
        name="continue_reading",
        description="Resume the last Scripture reading from where it was interrupted. Use when Rawley says continue, go on, or pick up where you stopped. The tool speaks it; you add nothing.",
        properties={},
        required=[],
        handler=continue_reading,
    ),
    "word_from_prayer": FlowsFunctionSchema(
        name="word_from_prayer",
        description="A word from God in response to Rawley's latest (or this morning's) prayer: the tool names the prayer's themes and reads two to four Scripture passages with one-sentence frames. The tool speaks it; you add nothing.",
        properties={"which": {"type": "string", "enum": ["latest", "morning", "devotional"]}},
        required=[],
        handler=word_from_prayer,
    ),
    "interpretations": FlowsFunctionSchema(
        name="interpretations",
        description="Different interpretations of a passage or topic from the teaching library and enrichment products, each labelled by teacher and tradition. Present them in your own words, naming each teacher and tradition, and keep Scripture, teacher and personal context distinct.",
        properties={"reference": {"type": "string"}, "topic": {"type": "string"}},
        required=[],
        handler=interpretations,
    ),
    "project_checkin": FlowsFunctionSchema(
        name="project_checkin",
        description="Identify a project from a vague description and report its plan, branches, results, checkpoints and outputs from the project work register. If the result is not confident, ask the confirmation question it gives; call again with confirmed_project_id after Rawley confirms.",
        properties={"description": {"type": "string"}, "confirmed_project_id": {"type": "string"}},
        required=["description"],
        handler=project_checkin,
    ),
    "request_work": FlowsFunctionSchema(
        name="request_work",
        description="Record a durable work request (research, project task) for the rlm-adk residents. The tool speaks the recorded status (operation id; accepted, or refused with the reason); it is never done by this call. You add nothing.",
        properties={"request": {"type": "string"}, "kind": {"type": "string", "enum": ["research", "project", "task"]}},
        required=["request"],
        handler=request_work,
    ),
}


def configure(cfg: TalkConfig) -> None:
    """Point the HTTP helpers at the skill service (once per process)."""
    global _cfg
    _cfg = cfg
