"""Bug reports from the phone's Bug chip: what the floor collects in ``bug`` mode and how it is filed.

While the floor is in ``bug`` mode (``FloorProcessor.start_bug``) nothing reaches the LLM and the
``OutputGate`` drops every spoken output, but the microphone stays open and Cohere keeps
transcribing: each final transcript (and any typed ``client_text``) becomes a segment of the report
and is echoed to the phone as ``{"type": "bug_text", ...}``. When the phone taps the chip again
(``client_bug`` stop) the report is posted to the skill service (``POST /issues``), which records it
in the intake issues log on the desktop and triages it.

A report must never be lost to a skill-service outage: a post that fails is written to a spool
directory under the talk tier's state root and retried in the background until the service accepts
it (the service is idempotent on ``report_id``). The ``send_to_intake`` tool files its requests the
same way (``file_report(..., route="/tools/work")``); a spooled one is posted again to ``/issues``,
which records the same ticket.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger

from talk.config import TalkConfig

RETRY_INTERVAL_S = 60.0


@dataclass
class BugReport:
    """One report in progress (``FloorProcessor.bug``)."""

    report_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: float = field(default_factory=time.time)
    mode_before: str = "available"
    # True when a Scripture reading was playing at the press; it resumes once the report is filed.
    resume_reading: bool = False
    segments: list[dict[str, Any]] = field(default_factory=list)
    interim: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)
    device_segments: list[dict[str, Any]] = field(default_factory=list)
    finishing: bool = False
    # The chip's kind hint (``client_bug`` ``kind``), for a chip that files more than bugs; None lets triage decide.
    kind: str | None = None

    def add(self, text: str, source: str) -> dict[str, Any]:
        segment = {"t": round(time.time(), 3), "text": text.strip(), "source": source}
        self.segments.append(segment)
        self.interim = ""
        return segment

    @property
    def text(self) -> str:
        return " ".join(s["text"] for s in self.segments if s["text"])

    def payload(self, *, ended_by: str, asr: str) -> dict[str, Any]:
        """The ``POST /issues`` body."""
        return {
            "report_id": self.report_id,
            "surface": "voice_portal",
            "origin": "bug_chip",
            "kind": self.kind,
            "started_at": self.started_at,
            "ended_at": time.time(),
            "ended_by": ended_by,
            "transcript": {"segments": list(self.segments), "device_segments": list(self.device_segments), "asr": asr},
            "source": dict(self.source),
            "context": dict(self.context),
        }


def recent_turns(messages: list[dict[str, Any]], limit: int = 8) -> list[dict[str, str]]:
    """The last user/assistant turns of the LLM context as plain text (tool calls and system text left out)."""
    out: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
        text = str(content or "").strip()
        if text:
            out.append({"role": role, "text": text[:600]})
    return out[-limit:]


def _spool_dir(cfg: TalkConfig) -> Path:
    path = cfg.root / "bug-spool"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def post_report(cfg: TalkConfig, payload: dict[str, Any], route: str = "/issues") -> dict[str, Any]:
    """POST one report to the skill service (``/issues``, or ``/tools/work`` for ``send_to_intake``);
    returns its JSON or raises on transport/HTTP failure."""
    url = cfg.skill_base_url.rstrip("/") + route
    timeout = aiohttp.ClientTimeout(total=cfg.skill_timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as response:
            text = await response.text()
            if response.status not in (200, 201):
                raise RuntimeError(f"skill service HTTP {response.status} for {route}: {text[:200]}")
            return json.loads(text)


_retry_task: asyncio.Task | None = None


async def file_report(cfg: TalkConfig, payload: dict[str, Any], route: str = "/issues") -> dict[str, Any]:
    """File a report; on failure spool it and keep retrying in the background.

    Args:
        cfg: The talk tier's configuration (skill service URL, state root).
        payload: The report (``BugReport.payload`` or ``tools.intake_report``).
        route: The first post's route; a spooled report is posted again to ``/issues``.

    Returns:
        ``{"filed": True, ...skill service reply}`` or ``{"filed": False, "spooled": path, "error": ...}``.
    """
    try:
        reply = await post_report(cfg, payload, route)
        asyncio.get_running_loop().create_task(flush_spool(cfg))
        return {"filed": True, **reply}
    except Exception as exc:  # noqa: BLE001 - any failure spools the report; nothing is dropped
        path = _spool_dir(cfg) / f"{payload['report_id']}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        logger.warning(f"bug report {payload['report_id']} not filed ({exc}); spooled to {path}")
        _ensure_retry(cfg)
        return {"filed": False, "spooled": str(path), "error": str(exc)}


async def flush_spool(cfg: TalkConfig) -> list[str]:
    """Post every spooled report once; a filed one is removed. Returns the ticket ids filed."""
    filed: list[str] = []
    for path in sorted(_spool_dir(cfg).glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            reply = await post_report(cfg, payload)
        except Exception as exc:  # noqa: BLE001 - stays spooled for the next pass
            logger.info(f"bug spool: {path.name} still not filed: {exc}")
            continue
        path.unlink(missing_ok=True)
        filed.append(str(reply.get("ticket_id")))
        logger.info(f"bug spool: {path.name} filed as {reply.get('ticket_id')}")
    return filed


def _ensure_retry(cfg: TalkConfig) -> None:
    global _retry_task
    if _retry_task is not None and not _retry_task.done():
        return

    async def loop() -> None:
        while any(_spool_dir(cfg).glob("*.json")):
            await asyncio.sleep(RETRY_INTERVAL_S)
            await flush_spool(cfg)

    _retry_task = asyncio.get_running_loop().create_task(loop())


async def wait_for_triage(cfg: TalkConfig, ticket_id: str, *, poll_s: float = 3.0, longest_s: float = 180.0) -> dict[str, Any] | None:
    """Poll ``GET /issues/<ticket_id>`` until its triage is done; None if it is not done by then.

    The triage itself is not bounded by this: the skill service finishes it and logs it whatever
    happens here; this only decides how long the phone's chip waits to show the triaged title.
    """
    url = cfg.skill_base_url.rstrip("/") + f"/issues/{ticket_id}"
    deadline = time.time() + longest_s
    while time.time() < deadline:
        await asyncio.sleep(poll_s)
        try:
            timeout = aiohttp.ClientTimeout(total=cfg.skill_timeout_s)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        continue
                    ticket = json.loads(await response.text())
        except Exception:  # noqa: BLE001 - keep polling until the deadline
            continue
        if (ticket.get("triage") or {}).get("state") == "done":
            return ticket
    return None
