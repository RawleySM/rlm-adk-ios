"""Intake issues log: bug reports spoken into the voice portal, recorded as tickets.

The phone's Bug chip puts the talk tier in ``bug`` mode; when Rawley taps it again the talk tier
posts what it transcribed (``POST /issues``) with the context of the moment (open passage, reading,
recent conversation, app build). This module turns that into a ticket and records it in an
append-only JSON Lines log on the desktop (config ``issues_log``; production
``~/.local/rlm-adk/stores/intake/issues.jsonl``) that a resident will later monitor.

Order of work, so the words are never lost to a slow or failed model call:

1. ``record_report`` writes a ``reported`` event at once: the verbatim transcript, the context and a
   provisional keyword triage (``triage.state = "pending"``). A repeat with the same ``report_id``
   returns the existing ticket and writes nothing.
2. ``triage_ticket`` (a background thread) makes one strict-JSON reasoning call that flags (severity,
   flags), sorts (kind, component, priority) and dispositions (``ready``, ``needs_detail``,
   ``request``, ``no_report``) the report, then appends a ``triaged`` event. A failed call appends a
   ``triaged`` event from the keyword rules with the error kept. Tickets still pending when the
   service starts are triaged again (``resume_pending``).

The log is the only store: a ticket is the fold of its events in file order (``read_tickets``).
Later consumers append their own events (claimed, resolved) under the same ``ticket_id``.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path

from .config import Config
from .reasoning import ReasoningError, stream_chat

log = logging.getLogger("voice_portal.issues")

SCHEMA = "intake-issue.v1"
KINDS = ["bug", "feature_request", "question", "feedback"]
COMPONENTS = ["voice_input", "voice_output", "conversation", "scripture_pane", "prayer", "app_ui", "connectivity", "backend", "other"]
SEVERITIES = ["critical", "major", "minor", "cosmetic"]
FLAGS = ["blocks_use", "data_loss", "regression", "intermittent", "reproducible", "wrong_content"]
DISPOSITIONS = ["ready", "needs_detail", "request", "no_report"]
MAX_TRANSCRIPT_CHARS = 6000

TRIAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "title", "summary", "observed", "expected", "steps", "component", "severity", "flags", "needs_detail", "detail_questions"],
    "properties": {
        "kind": {"type": "string", "enum": KINDS},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "observed": {"type": "string"},
        "expected": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "string"}},
        "component": {"type": "string", "enum": COMPONENTS},
        "severity": {"type": "string", "enum": SEVERITIES},
        "flags": {"type": "array", "items": {"type": "string", "enum": FLAGS}},
        "needs_detail": {"type": "boolean"},
        "detail_questions": {"type": "array", "items": {"type": "string"}},
    },
}

TRIAGE_SYSTEM = """You triage bug reports that Rawley speaks into his voice portal: an iPhone app with a
Scripture pane (passage display, verse shading while a passage is read aloud, a book and chapter
navigator, read mode), a spoken assistant (British voice; Scripture is read in a second voice), prayer
capture (prayers addressed to God are recorded, never answered), quiet mode, typed input, and a
desktop skill service behind it. The transcript comes from speech recognition: read it charitably and
correct obvious mis-hearings in your own fields, but never add facts that are not in the transcript or
the context.

Return one JSON object:
- kind: bug (something behaves wrongly), feature_request (something new or different is wanted),
  question (he asks how or why something works), feedback (an opinion without a defect).
- title: at most 80 characters, specific to what he described (for example "Typed question answered
  about the previous passage").
- summary: two or three plain sentences.
- observed / expected: what happened and what he expected, in his terms; empty string when not said.
- steps: steps to reproduce that he stated or that the context shows; empty list when there are none.
- component: voice_input (hearing him: microphone, interrupting, transcription), voice_output (the
  bot's audio, voices, speed), conversation (the assistant's answers, tools, wrong information),
  scripture_pane (passage display, shading, navigation, reading), prayer (prayer capture, amen),
  app_ui (buttons, layout, keyboard, screens), connectivity (connecting, dropping, delays), backend
  (skill service data, work requests), other.
- severity: critical (the portal cannot be used or data is lost), major (a main use is broken),
  minor (works with friction), cosmetic (appearance only).
- flags: any of blocks_use, data_loss, regression (it used to work), intermittent, reproducible,
  wrong_content (the assistant or the pane showed or said something false).
- needs_detail: true when an engineer could not act on it without asking him something.
- detail_questions: the questions to ask him when needs_detail is true, else an empty list."""

_COMPONENT_WORDS = [
    ("scripture_pane", ("verse", "scripture", "chapter", "passage", "pane", "highlight", "shading", "shade", "navigator", "read mode", "bible")),
    ("voice_input", ("hear me", "heard me", "interrupt", "transcri", "microphone", "mic ", "listen", "barge", "cut me off", "my voice")),
    ("voice_output", ("voice", "sound", "loud", "volume", "accent", "speaking too", "audio", "pronounc")),
    ("prayer", ("pray", "amen")),
    ("app_ui", ("button", "screen", "tap", "chip", "keyboard", "scroll", "layout", "display")),
    ("connectivity", ("connect", "offline", "network", "lag", "slow to", "took too long", "latency")),
    ("conversation", ("answer", "respond", "said", "told me", "assistant", "question", "wrong")),
]
_MAJOR_WORDS = ("crash", "doesn't work", "does not work", "didn't work", "can't", "cannot", "won't", "broken", "stuck", "froze", "never", "lost")
_FEATURE_WORDS = ("i want", "i'd like", "i would like", "it would be nice", "please add", "could you add", "should be able", "feature")
_FILLERS = {"um", "uh", "er", "ah", "hmm", "mm", "okay", "ok", "so", "yeah"}


def log_path(cfg: Config) -> Path:
    """The issues log this service writes (``issues_log``, else ``<state root>/intake/issues.jsonl``)."""
    return Path(cfg.issues_log).expanduser() if cfg.issues_log else cfg.root / "intake" / "issues.jsonl"


_THREAD_LOCK = threading.Lock()


class _LogLock:
    """Thread lock plus an advisory file lock beside the log, held across scan-and-append."""

    def __init__(self, path: Path):
        self.path = path.with_name(path.name + ".lock")
        self._fh = None

    def __enter__(self):
        _THREAD_LOCK.acquire()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
        finally:
            _THREAD_LOCK.release()


def _append(path: Path, event: dict) -> None:
    """Append one event as a line and fsync; caller holds ``_LogLock``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_events(path: Path) -> list[dict]:
    """Every well-formed event in file order; a torn or foreign line is skipped, never fatal."""
    if not path.is_file():
        return []
    events = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("ticket_id"):
                events.append(event)
    return events


def read_tickets(path: Path) -> dict[str, dict]:
    """Fold the log into tickets keyed by ``ticket_id`` (insertion order = order reported)."""
    tickets: dict[str, dict] = {}
    for event in read_events(path):
        tid = event["ticket_id"]
        if event.get("event") == "reported" and isinstance(event.get("ticket"), dict):
            tickets[tid] = {**event["ticket"], "events": 1}
            continue
        ticket = tickets.get(tid)
        if ticket is None:
            continue
        ticket["events"] += 1
        if event.get("event") == "triaged" and isinstance(event.get("triage"), dict):
            ticket["triage"] = event["triage"]
            ticket["triaged_at"] = event.get("at")
        elif event.get("event") == "status" and event.get("status"):
            ticket["status"] = event["status"]
    return tickets


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower())


def _first_sentence(text: str, limit: int = 80) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0] if text.strip() else ""
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rsplit(" ", 1)[0] + "…"


def keyword_triage(text: str) -> dict:
    """Deterministic triage from the transcript's words (the provisional and the fallback triage)."""
    lowered = f" {text.lower()} "
    component = next((name for name, words in _COMPONENT_WORDS if any(w in lowered for w in words)), "other")
    feature = any(w in lowered for w in _FEATURE_WORDS)
    major = any(w in lowered for w in _MAJOR_WORDS)
    content_words = [w for w in _words(text) if w not in _FILLERS]
    flags = []
    if "again" in lowered or "every time" in lowered or "always" in lowered:
        flags.append("reproducible")
    if "sometimes" in lowered or "now and then" in lowered:
        flags.append("intermittent")
    if "used to" in lowered:
        flags.append("regression")
    return {
        "kind": "feature_request" if feature and not major else "bug",
        "title": _first_sentence(text) or "Bug chip pressed with nothing said",
        "summary": text.strip()[:400],
        "observed": "",
        "expected": "",
        "steps": [],
        "component": component,
        "severity": "major" if major else "minor",
        "flags": flags,
        "needs_detail": len(content_words) < 8,
        "detail_questions": [],
    }


def _clean_triage(raw: dict, text: str) -> dict:
    """Coerce a model's reply into the closed vocabulary; anything off-list falls back to the keyword value."""
    base = keyword_triage(text)
    out = dict(base)
    if raw.get("kind") in KINDS:
        out["kind"] = raw["kind"]
    if raw.get("component") in COMPONENTS:
        out["component"] = raw["component"]
    if raw.get("severity") in SEVERITIES:
        out["severity"] = raw["severity"]
    for key in ("title", "summary", "observed", "expected"):
        if isinstance(raw.get(key), str):
            out[key] = raw[key].strip()
    if not out["title"]:
        out["title"] = base["title"]
    out["title"] = out["title"][:120]
    for key in ("steps", "detail_questions"):
        if isinstance(raw.get(key), list):
            out[key] = [str(s).strip() for s in raw[key] if str(s).strip()][:12]
    if isinstance(raw.get("flags"), list):
        out["flags"] = sorted({f for f in raw["flags"] if f in FLAGS})
    if isinstance(raw.get("needs_detail"), bool):
        out["needs_detail"] = raw["needs_detail"]
    return out


def disposition_of(triage: dict, text: str) -> tuple[str, str]:
    """(disposition, priority) from a triage: the sort key a monitor orders open tickets by."""
    content_words = [w for w in _words(text) if w not in _FILLERS]
    if len(content_words) < 3:
        disposition = "no_report"
    elif triage.get("needs_detail"):
        disposition = "needs_detail"
    elif triage.get("kind") == "bug":
        disposition = "ready"
    else:
        disposition = "request"
    flags = set(triage.get("flags") or [])
    severity = triage.get("severity")
    if disposition == "no_report":
        priority = "P4"
    elif severity == "critical" or flags & {"blocks_use", "data_loss"}:
        priority = "P1"
    elif severity == "major":
        priority = "P2"
    elif severity == "minor":
        priority = "P3"
    else:
        priority = "P4"
    return disposition, priority


def _finish_triage(triage: dict, text: str, *, state: str, source: str, model: str | None, errors: list[str], elapsed_ms: int | None = None, usage: dict | None = None) -> dict:
    disposition, priority = disposition_of(triage, text)
    return {**triage, "disposition": disposition, "priority": priority, "route": "intake", "state": state, "source": source, "model": model, "errors": errors, "elapsed_ms": elapsed_ms, "usage": usage}


def _next_ticket_id(tickets: dict[str, dict], when: float) -> str:
    day = time.strftime("%Y%m%d", time.localtime(when))
    prefix = f"bug-{day}-"
    used = [int(t[len(prefix):]) for t in tickets if t.startswith(prefix) and t[len(prefix):].isdigit()]
    return f"{prefix}{(max(used) + 1) if used else 1:02d}"


def transcript_text(payload: dict) -> str:
    """The report's words: final server segments in order (typed lines included), else the phone's own."""
    transcript = payload.get("transcript") or {}
    segments = [s for s in (transcript.get("segments") or []) if isinstance(s, dict) and str(s.get("text") or "").strip()]
    if segments:
        return " ".join(str(s["text"]).strip() for s in segments)
    device = [str(s.get("text") if isinstance(s, dict) else s).strip() for s in (transcript.get("device_segments") or [])]
    return " ".join(d for d in device if d)


def record_report(cfg: Config, payload: dict) -> tuple[dict, bool]:
    """Record one report as a ticket (``reported`` event); returns (ticket, created).

    Args:
        cfg: Service configuration (``issues_log``).
        payload: The talk tier's report: ``report_id``, ``transcript {segments, device_segments}``,
            ``started_at``, ``ended_at``, ``ended_by``, ``source {...}``, ``context {...}``.

    Returns:
        The ticket as folded from the log and whether this call created it (False for a repeat).

    Raises:
        ValueError: ``report_id`` missing.
    """
    report_id = str(payload.get("report_id") or "").strip()
    if not report_id:
        raise ValueError("report_id is required")
    path = log_path(cfg)
    with _LogLock(path):
        tickets = read_tickets(path)
        for ticket in tickets.values():
            if ticket.get("report_id") == report_id:
                return ticket, False
        when = time.time()
        ticket_id = _next_ticket_id(tickets, when)
        text = transcript_text(payload)[:MAX_TRANSCRIPT_CHARS]
        provisional = _finish_triage(keyword_triage(text), text, state="pending", source="provisional_keywords", model=None, errors=[])
        transcript = payload.get("transcript") or {}
        ticket = {
            "schema": SCHEMA,
            "ticket_id": ticket_id,
            "report_id": report_id,
            "recorded_at": when,
            "recorded_at_iso": dt.datetime.fromtimestamp(when, dt.timezone.utc).isoformat(timespec="seconds"),
            "recorded_by": "voice-portal skill service",
            "surface": payload.get("surface") or "voice_portal",
            "reporter": "operator",
            "status": "open",
            "text": text,
            "transcript": {
                "segments": transcript.get("segments") or [],
                "device_segments": transcript.get("device_segments") or [],
                "asr": transcript.get("asr"),
                "started_at": payload.get("started_at"),
                "ended_at": payload.get("ended_at"),
                "ended_by": payload.get("ended_by"),
            },
            "source": payload.get("source") or {},
            "context": payload.get("context") or {},
            "triage": provisional,
        }
        _append(path, {"schema": SCHEMA, "event": "reported", "ticket_id": ticket_id, "report_id": report_id, "at": when, "ticket": ticket})
    log.info("issue %s recorded (%d chars, report %s)", ticket_id, len(text), report_id)
    return {**ticket, "events": 1}, True


def _context_lines(ticket: dict) -> str:
    context = ticket.get("context") or {}
    source = ticket.get("source") or {}
    lines = []
    passage = context.get("passage") or {}
    if passage.get("reference"):
        state = "being read aloud" if passage.get("reading") else f"open, {passage.get('spoken_count') or 0} verses heard"
        lines.append(f"Scripture pane: {passage['reference']} ({state}) when the report began.")
    if context.get("mode_before"):
        lines.append(f"Portal mode before the report: {context['mode_before']}.")
    if context.get("bot_speaking"):
        lines.append("The assistant was speaking when the report began.")
    for turn in (context.get("recent_turns") or [])[-6:]:
        if isinstance(turn, dict) and turn.get("text"):
            lines.append(f"{turn.get('role', '?')}: {str(turn['text'])[:300]}")
    build = " ".join(f"{k}={v}" for k, v in source.items() if v not in (None, "") and k in ("app_build", "transport", "audio_route", "client"))
    if build:
        lines.append(f"App: {build}.")
    return "\n".join(lines) or "(no context)"


def triage_ticket(cfg: Config, ticket_id: str) -> dict | None:
    """Triage one recorded ticket with the reasoning owner and append the ``triaged`` event.

    Returns:
        The triage written, or None when the ticket is unknown or already triaged.
    """
    path = log_path(cfg)
    ticket = read_tickets(path).get(ticket_id)
    if ticket is None or (ticket.get("triage") or {}).get("state") == "done":
        return None
    text = ticket.get("text") or ""
    started = time.monotonic()
    errors: list[str] = []
    triage = None
    if len([w for w in _words(text) if w not in _FILLERS]) < 3:
        triage = _finish_triage(keyword_triage(text), text, state="done", source="no_words", model=None, errors=[])
    else:
        messages = [
            {"role": "system", "content": TRIAGE_SYSTEM},
            {"role": "user", "content": f"Context when the report began:\n{_context_lines(ticket)}\n\nReport transcript:\n{text}"},
        ]
        structured = {**(cfg.inference_extra_body or {}), "response_format": {"type": "json_schema", "json_schema": {"name": "issue_triage", "strict": True, "schema": TRIAGE_SCHEMA}}}
        for extra in (structured, cfg.inference_extra_body):
            try:
                chunks: list[str] = []
                usage = None
                for item in stream_chat(cfg.inference_base_url, cfg.inference_model, messages, timeout=cfg.tools_inference_timeout_s, max_tokens=cfg.issue_triage_max_tokens, temperature=0.1, extra_body=extra):
                    if "delta" in item:
                        chunks.append(item["delta"])
                    else:
                        usage = item.get("usage")
                raw = "".join(chunks).strip()
                match = re.search(r"\{.*\}", raw, re.S)
                if not match:
                    raise ValueError(f"no JSON object in reply: {raw[:120]!r}")
                parsed = json.loads(match.group(0))
                if not isinstance(parsed, dict):
                    raise ValueError("reply is not an object")
                triage = _finish_triage(_clean_triage(parsed, text), text, state="done", source="reasoning", model=cfg.inference_model, errors=errors, elapsed_ms=int((time.monotonic() - started) * 1000), usage=usage)
                break
            except ReasoningError as exc:
                errors.append(f"{exc} {(exc.body or '')[:200]}".strip())
                if exc.status != 400:  # only a rejected response_format is worth one plain retry
                    break
            except (ValueError, json.JSONDecodeError) as exc:
                errors.append(f"unparseable reply: {exc}")
            except Exception as exc:  # noqa: BLE001 - a broken owner must still leave a triaged ticket
                errors.append(f"{type(exc).__name__}: {exc}")
                break
        if triage is None:
            triage = _finish_triage(keyword_triage(text), text, state="done", source="fallback_keywords", model=None, errors=errors, elapsed_ms=int((time.monotonic() - started) * 1000))
    with _LogLock(path):
        current = read_tickets(path).get(ticket_id) or {}
        if (current.get("triage") or {}).get("state") == "done":
            return None
        _append(path, {"schema": SCHEMA, "event": "triaged", "ticket_id": ticket_id, "at": time.time(), "triage": triage})
    log.info("issue %s triaged: %s %s %s %s (%s)", ticket_id, triage["kind"], triage["component"], triage["priority"], triage["disposition"], triage["source"])
    return triage


def triage_in_background(cfg: Config, ticket_id: str) -> threading.Thread:
    """Start ``triage_ticket`` on a daemon thread (the POST answers before the model does)."""
    thread = threading.Thread(target=_triage_logged, args=(cfg, ticket_id), name=f"triage-{ticket_id}", daemon=True)
    thread.start()
    return thread


def _triage_logged(cfg: Config, ticket_id: str) -> None:
    try:
        triage_ticket(cfg, ticket_id)
    except Exception:  # noqa: BLE001 - logged; the ticket stays pending and is retried at next start
        log.exception("triage of %s failed", ticket_id)


def resume_pending(cfg: Config) -> list[str]:
    """Triage, in the background, every ticket whose triage is still pending (service start)."""
    pending = [tid for tid, t in read_tickets(log_path(cfg)).items() if (t.get("triage") or {}).get("state") != "done"]
    for tid in pending:
        triage_in_background(cfg, tid)
    return pending


def summary(ticket: dict) -> dict:
    """The listing row of one ticket."""
    triage = ticket.get("triage") or {}
    return {
        "ticket_id": ticket.get("ticket_id"),
        "recorded_at_iso": ticket.get("recorded_at_iso"),
        "status": ticket.get("status"),
        "title": triage.get("title"),
        "kind": triage.get("kind"),
        "component": triage.get("component"),
        "severity": triage.get("severity"),
        "priority": triage.get("priority"),
        "disposition": triage.get("disposition"),
        "triage_state": triage.get("state"),
        "chars": len(ticket.get("text") or ""),
    }


def list_tickets(cfg: Config, *, status: str | None = None, limit: int = 50) -> list[dict]:
    """Newest first; ``status`` filters (``open``)."""
    rows = [summary(t) for t in read_tickets(log_path(cfg)).values() if status in (None, "", t.get("status"))]
    return list(reversed(rows))[: max(1, limit)]


def new_report_id() -> str:
    return uuid.uuid4().hex
