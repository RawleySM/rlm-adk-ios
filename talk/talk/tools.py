"""Tool functions the LLM may call; each is an HTTP request to the skill service on the desktop.

The skill service (``voice_portal`` and its successors) owns the data: prayers, Scripture, teaching,
Theographic, enrichment products and the project work register. Pipecat executes function calls
asynchronously, so a slow skill never stalls the audio loop; results re-enter the context when they
arrive. Every tool returns plain spoken-ready text plus structured fields the app can show.
"""

from __future__ import annotations

import json
from typing import Any

import aiohttp
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams

from talk.config import TalkConfig

_cfg: TalkConfig | None = None


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


async def read_scripture(params: FunctionCallParams) -> None:
    """Fetch the retained edition's verses for a reference, verbatim."""
    args = params.arguments or {}
    try:
        result = await _get("/tools/scripture", {"reference": args.get("reference", "")})
        await params.result_callback(result)
    except Exception as exc:
        await params.result_callback(_unavailable(str(exc)))


async def word_from_prayer(params: FunctionCallParams) -> None:
    """Latest prayer → themes → passages from the pairings ledger and retrieval."""
    args = params.arguments or {}
    try:
        result = await _get("/tools/word", {"which": args.get("which", "latest")})
        await params.result_callback(result)
    except Exception as exc:
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


async def request_work(params: FunctionCallParams) -> None:
    """Post a durable work request to rlm-adk's instance mailbox; accepted, never done."""
    args = params.arguments or {}
    try:
        result = await _post("/tools/work", {"request_text": args.get("request", ""), "kind": args.get("kind", "research")})
        await params.result_callback(result)
    except Exception as exc:
        await params.result_callback(_unavailable(str(exc)))


FUNCTIONS = {
    "search_sources": search_sources,
    "read_scripture": read_scripture,
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
                description="Return the exact retained text of a Scripture reference such as 'Psalm 23' or 'Romans 8:28-30' so it can be read aloud verbatim.",
                properties={"reference": {"type": "string"}},
                required=["reference"],
            ),
            FunctionSchema(
                name="word_from_prayer",
                description="A word from God in response to Rawley's latest (or today's morning) prayer: its themes and two to four Scripture passages that answer them, each with a one-sentence frame.",
                properties={"which": {"type": "string", "enum": ["latest", "morning", "devotional"]}},
                required=[],
            ),
            FunctionSchema(
                name="interpretations",
                description="Different interpretations of a passage or topic from the teaching library and enrichment products, each labelled by teacher and tradition.",
                properties={"reference": {"type": "string"}, "topic": {"type": "string"}},
                required=[],
            ),
            FunctionSchema(
                name="project_checkin",
                description="Identify a project from a vague description and report its plan, branches, results, checkpoints and outputs from the project work register. Call again with confirmed_project_id after Rawley confirms an uncertain match.",
                properties={"description": {"type": "string"}, "confirmed_project_id": {"type": "string"}},
                required=["description"],
            ),
            FunctionSchema(
                name="request_work",
                description="Record a durable work request (research, project task) for the rlm-adk residents. It is only accepted, never completed, by this call.",
                properties={"request": {"type": "string"}, "kind": {"type": "string", "enum": ["research", "project", "task"]}},
                required=["request"],
            ),
        ]
    )


def register_tools(llm, cfg: TalkConfig) -> None:
    global _cfg
    _cfg = cfg
    for name, fn in FUNCTIONS.items():
        llm.register_function(name, fn)
