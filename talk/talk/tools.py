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
from pipecat.flows import NO_RESPONSE, FlowArgs, FlowManager, FlowsFunctionSchema
from pipecat.frames.frames import TTSSpeakFrame

from talk.config import TalkConfig

_cfg: TalkConfig | None = None

LONG_READING_VERSES = 10

UNAVAILABLE_NOTE = (
    "This source is unavailable right now. Tell Rawley plainly that you could not reach it, answer briefly "
    "from what you already know, and do not call any tool again for this question."
)


def new_reading_state() -> dict[str, Any]:
    """One session's reading state, shared by the tools, the floor and the reading tracker.

    Keys: ``reference``, ``chapter_reference``, ``verses`` (``verse``/``text``/``osis`` dicts),
    ``spoken_through`` (last verse number the listener fully heard) and ``last_user_text`` (the
    utterance the floor last forwarded to the LLM; decides whether a long chapter is read or quoted).
    Each session gets its own: two phones, or a phone and a benchmark client, must never share one.
    """
    return {"reference": None, "chapter_reference": None, "verses": [], "spoken_through": 0, "spoken_count": 0, "queue": [], "reading": False, "last_user_text": ""}


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


async def fetch_scripture(reference: str) -> dict[str, Any]:
    """The skill service's ``/tools/scripture`` result for ``reference`` (verbatim retained verses)."""
    return await _get("/tools/scripture", {"reference": reference})


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


def _node(name: str, flow_manager: FlowManager | None = None):
    from talk import flows

    return flows.node(name, reading_state=_state(flow_manager) if flow_manager is not None else None)


def _floor(flow_manager: FlowManager):
    """This session's FloorProcessor (set on the FlowManager's state by the bot), or None in tests."""
    return flow_manager.state.get("floor")


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
    return result, (_node("project", flow_manager) if flow_manager.current_node != "project" else None)


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
        # A question about a long chapter: hand the text to the model to quote from, instead of reading it
        # all; the pane opens the chapter so Rawley sees what the answer is drawn from.
        from talk import passage

        opened = passage.from_tool_result(result, reference)
        floor = _floor(flow_manager)
        if opened is not None and state.get("reference") != opened["reference"]:
            passage.apply(state, opened, source="tool")
            if floor is not None:
                await floor._tell_client(passage.message(state))
        return {
            "reference": reference,
            "verses": [{"verse": v["verse"], "text": v["text"]} for v in verses],
            "instruction": "Rawley asked a question, not for a reading. Quote the two or three verses that answer it (with their verse numbers) and answer briefly in your own words; do not read the whole chapter.",
        }, None
    from talk import passage

    floor = _floor(flow_manager)
    loaded = passage.from_tool_result(result, reference)
    indices = None
    announce = None
    from_index = None
    within = passage.requested_range(result)
    if within is not None:
        # Verses inside one chapter: the pane shows the whole chapter and the reading covers the verses asked for.
        chapter_ref, first, last = within
        if state.get("reference") != chapter_ref:
            chapter = passage.from_tool_result(await fetch_scripture(chapter_ref), chapter_ref)
            if chapter is not None:
                passage.apply(state, chapter, source="tool")
                if floor is not None:
                    await floor._tell_client(passage.message(state))
        if state.get("reference") == chapter_ref and first == last:
            # One verse ("Matthew 25:14"): start there and read on to the end of the chapter.
            from_index = passage.index_of_verse(state, first)
        elif state.get("reference") == chapter_ref:
            indices = [i for i, v in enumerate(state.get("verses") or []) if first <= v["verse"] <= last]
            announce = f"{loaded['reference']}."
    if indices is None and from_index is None:
        passage.apply(state, loaded, source="tool")
        if floor is not None:
            await floor._tell_client(passage.message(state))
    if from_index is not None:
        verses = [v for v in (state.get("verses") or [])[from_index:]]
    if floor is not None:
        # The pane shows what is read; the floor queues the verses in the reading voice and tracks them.
        await floor.read_active(restart=True, reason="read_scripture", interrupt=False, switch_node=False, indices=indices, announce=announce, from_index=from_index)
        if flow_manager.current_node == "devotional":
            await floor.refresh_instruction("read_scripture")
        spoken = " ".join(v["text"] for v in verses)
    else:
        spoken = await _speak_exact(flow_manager, [f"{reference}."] + [v["text"] for v in verses])
    result_out = {"reference": loaded["reference"], "verses_read": [v["verse"] for v in verses], "text": spoken, "cite_ids": result.get("cite_ids"), "coverage": result.get("coverage"), "spoken": True}
    return result_out, (_node("devotional", flow_manager) if flow_manager.current_node != "devotional" else NO_RESPONSE)


async def continue_reading(args: FlowArgs, flow_manager: FlowManager):
    """Read the passage open in the scripture pane, resuming after the last verse fully heard (or from the start)."""
    state = _state(flow_manager)
    if not state.get("verses"):
        return {"error": "no passage open", "instruction": "Tell Rawley no passage is open in the scripture pane and ask what he would like read."}, None
    floor = _floor(flow_manager)
    try:
        from_verse = int(args["from_verse"]) if args.get("from_verse") not in (None, "") else None
    except (TypeError, ValueError):
        from_verse = None
    from talk import passage

    from_index = passage.index_of_verse(state, from_verse) if from_verse is not None else None
    if floor is not None:
        await floor.read_active(restart=bool(args.get("from_start")), reason="continue_reading", interrupt=False, switch_node=False, from_index=from_index)
        if flow_manager.current_node == "devotional":
            await floor.refresh_instruction("continue_reading")
        return {"reference": state.get("reference"), "spoken": True}, (_node("devotional", flow_manager) if flow_manager.current_node != "devotional" else NO_RESPONSE)
    verses = state.get("verses") or []
    count = int(state.get("spoken_count") or 0)
    remaining = verses[count:] if 0 < count < len(verses) else verses
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
    return summary, (_node("conversation", flow_manager) if flow_manager.current_node == "available" else NO_RESPONSE)


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
        description="Read a Scripture reference aloud, verbatim from the retained edition: a chapter ('Psalm 23') from its start, a range ('Romans 8:28-30') just those verses, one verse ('Matthew 25:14') from that verse to the end of its chapter. Always pass the verse with a colon when Rawley names one ('Matthew 25 14' is 'Matthew 25:14'). The tool speaks it; you add nothing.",
        properties={"reference": {"type": "string"}},
        required=["reference"],
        handler=read_scripture,
    ),
    "continue_reading": FlowsFunctionSchema(
        name="continue_reading",
        description="Read the passage open in the scripture pane (the one in the pane note), resuming where its reading stopped, or from the start when it was never read or finished. Use when Rawley says read it, read this, continue, go on, or pick up where you stopped. The tool speaks it; you add nothing.",
        properties={
            "from_start": {"type": "boolean", "description": "True when Rawley asks to hear it again from the beginning."},
            "from_verse": {"type": "integer", "description": "Start at this verse of the open passage and read on, e.g. 16 for 'start at verse 16' or 'back up to verse 16'."},
        },
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
        description="Identify a project from a vague description and report its disposition, need, products, consumers and evidence from the project work register (one record per repository). If the result is not confident, ask the confirmation question it gives; call again with confirmed_project_id after Rawley confirms.",
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
