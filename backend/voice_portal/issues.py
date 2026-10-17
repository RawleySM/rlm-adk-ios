"""Intake issues log: what Rawley sends the Intake resident from the voice portal, recorded as tickets.

Two paths file a ticket. The phone's Bug chip puts the talk tier in ``bug`` mode; when Rawley taps
it again the talk tier posts what it transcribed (``POST /issues``) with the context of the moment
(open passage, reading, recent conversation, app build). In conversation, the talk tier's
``send_to_intake`` tool files his own last words (``POST /tools/work``, ``tools/work.py``), and the
Work tab's dispatch files its request (``outbox.send_to_intake``). Every path goes through
``record_report``, so the mail gate below covers all of them. Tickets live in an append-only JSON
Lines log on the desktop (config ``issues_log``; production ``~/.local/rlm-adk/stores/intake/issues.jsonl``).

A ticket is one of five kinds (``KINDS``): ``bug``, ``feature_request``, ``message``, ``teaching``,
``project_proposal``. The caller may pass a kind hint; a spoken first word ("teach ...",
"remember ...", "project ...", "proposal ...", "bug ...") overrides the hint, and the hint overrides
the model (``spoken_kind``).

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

3. When ``intake_recipient`` names a resident, a triaged ticket whose disposition is in
   ``intake_mail_dispositions`` is mailed to it (``mail_ticket``: plain text whose first line is
   ``voice-portal ticket_id=... kind=...`` and whose last lines give the reply words for its kind).
   With ``intake_mail_mode = "stage"`` (the default while the runtime is not running) the mail and
   the exact ``allocatorctl inbox post --to`` command are written beside the log and a
   ``mail_staged`` event records them; nothing reaches the allocator. With ``"post"`` the command
   runs and a ``mailed`` event records the message id and the mailbox's instance id.
4. ``poll_replies`` reads the mailbox once per poll (``inbox list --to-operator --json``, the whole
   history: there is no cursor) and records each message once by ``message_id``: a reply threaded
   on a ticket's mail of this instance becomes a ``status`` event (its leading ``status: <word>`` is
   the ticket's status), anything else an ``inbound`` event (``ticket_id`` null). ``feed`` serves
   both to the phone (``GET /intake/feed``, and the pushed ``intake_message``).
"""

from __future__ import annotations

import datetime as dt
import fcntl
import subprocess
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
KINDS = ["bug", "feature_request", "message", "teaching", "project_proposal"]
# NOTE [Context]: tickets triaged before 2026-10-17 carry the retired kinds ``question`` and
# ``feedback``; they fold and list as recorded and are mailed with the ``message`` reply words.
LEGACY_KINDS = ["question", "feedback"]
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

TRIAGE_SYSTEM = """You triage what Rawley speaks into his voice portal for Intake, the resident that handles his
requests. The portal is an iPhone app with a Scripture pane (passage display, verse shading while a
passage is read aloud, a book and chapter navigator, read mode), a spoken assistant (British voice;
Scripture is read in a second voice), prayer capture (prayers addressed to God are recorded, never
answered), quiet mode, typed input, and a desktop skill service behind it. The transcript comes from
speech recognition: read it charitably and correct obvious mis-hearings in your own fields, but never
add facts that are not in the transcript or the context.

Return one JSON object:
- kind: bug (something in the portal behaves wrongly), feature_request (something new or different
  is wanted in the portal), message (a question, a note or an opinion for Intake that is none of the
  others), teaching (he teaches Intake something or asks it to remember something: a rule, a
  preference or a method to apply from now on), project_proposal (he proposes a new project: work
  with its own outcome, beyond a change to the portal).
- title: at most 80 characters, specific to what he said (for example "Typed question answered
  about the previous passage").
- summary: two or three plain sentences.
- observed / expected: for a bug, what happened and what he expected, in his terms; otherwise an
  empty string.
- steps: steps to reproduce a bug that he stated or that the context shows; otherwise an empty list.
- component: voice_input (hearing him: microphone, interrupting, transcription), voice_output (the
  bot's audio, voices, speed), conversation (the assistant's answers, tools, wrong information),
  scripture_pane (passage display, shading, navigation, reading), prayer (prayer capture, amen),
  app_ui (buttons, layout, keyboard, screens), connectivity (connecting, dropping, delays), backend
  (skill service data, work requests), other (also a message, teaching or proposal that concerns no
  part of the portal).
- severity: critical (the portal cannot be used or data is lost), major (a main use is broken),
  minor (works with friction), cosmetic (appearance only).
- flags: any of blocks_use, data_loss, regression (it used to work), intermittent, reproducible,
  wrong_content (the assistant or the pane showed or said something false).
- needs_detail: true when an engineer could not act on a bug or feature request without asking him
  something; false for a message, a teaching or a project proposal.
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
_TEACHING_WORDS = ("remember that", "remember this", "keep in mind", "i want you to learn", "learn that")
_PROPOSAL_WORDS = ("new project", "project proposal", "propose a project", "start a project")
# The first word Rawley speaks (after fillers) names the kind outright; see ``spoken_kind``.
_FIRST_WORD_KINDS = {
    "teach": "teaching",
    "teaching": "teaching",
    "remember": "teaching",
    "project": "project_proposal",
    "proposal": "project_proposal",
    "propose": "project_proposal",
    "bug": "bug",
}


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
    """Every well-formed event in file order; a torn or foreign line is skipped, never fatal.

    An event names its ``ticket_id``; only ``inbound`` events (mail from a resident that is not a
    reply to a ticket) carry ``ticket_id: null``.
    """
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
            if isinstance(event, dict) and (event.get("ticket_id") or event.get("event") == "inbound"):
                events.append(event)
    return events


def read_tickets(path: Path) -> dict[str, dict]:
    """Fold the log into tickets keyed by ``ticket_id`` (insertion order = order reported)."""
    return fold_tickets(read_events(path))


def fold_tickets(events: list[dict]) -> dict[str, dict]:
    """Fold events (file order) into tickets keyed by ``ticket_id``; ``inbound`` events belong to none."""
    tickets: dict[str, dict] = {}
    for event in events:
        tid = event.get("ticket_id")
        if not tid:
            continue
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
        elif event.get("event") == "mail_staged":
            ticket.setdefault("mail_staged", []).append({k: event.get(k) for k in ("recipient", "at", "body", "command", "note")})
        elif event.get("event") == "mailed" and event.get("message_id"):
            ticket.setdefault("mail", []).append({k: event.get(k) for k in ("message_id", "recipient", "at", "instance_id", "note")})
        elif event.get("event") == "status" and event.get("status"):
            ticket["status"] = event["status"]
            if event.get("reply_message_id"):
                ticket.setdefault("replies", []).append({k: event.get(k) for k in ("reply_message_id", "in_reply_to", "from", "status", "text", "sent_at", "at", "instance_id")})
    return tickets


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower())


def _first_sentence(text: str, limit: int = 80) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0] if text.strip() else ""
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rsplit(" ", 1)[0] + "…"


def spoken_kind(text: str) -> str | None:
    """The kind Rawley named with his first word, fillers skipped: "teach ..." and "remember ..." are a
    teaching, "project ..." and "proposal ..." a project proposal, "bug ..." a bug; else None."""
    first = next((w for w in _words(text) if w not in _FILLERS), "")
    return _FIRST_WORD_KINDS.get(first)


def _hinted(kind_hint: str | None) -> str | None:
    return kind_hint if kind_hint in KINDS else None


def keyword_triage(text: str, kind_hint: str | None = None) -> dict:
    """Deterministic triage from the transcript's words (the provisional and the fallback triage).

    Args:
        text: The report's words.
        kind_hint: The caller's kind (``KINDS``), used unless the first spoken word names one.
    """
    lowered = f" {text.lower()} "
    component = next((name for name, words in _COMPONENT_WORDS if any(w in lowered for w in words)), "other")
    feature = any(w in lowered for w in _FEATURE_WORDS)
    major = any(w in lowered for w in _MAJOR_WORDS)
    if any(w in lowered for w in _PROPOSAL_WORDS):
        kind = "project_proposal"
    elif any(w in lowered for w in _TEACHING_WORDS):
        kind = "teaching"
    else:
        kind = "feature_request" if feature and not major else "bug"
    kind = spoken_kind(text) or _hinted(kind_hint) or kind
    content_words = [w for w in _words(text) if w not in _FILLERS]
    flags = []
    if "again" in lowered or "every time" in lowered or "always" in lowered:
        flags.append("reproducible")
    if "sometimes" in lowered or "now and then" in lowered:
        flags.append("intermittent")
    if "used to" in lowered:
        flags.append("regression")
    return {
        "kind": kind,
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


def _clean_triage(raw: dict, text: str, kind_hint: str | None = None) -> dict:
    """Coerce a model's reply into the closed vocabulary; anything off-list falls back to the keyword value.

    The kind is Rawley's first spoken word when it names one, else the caller's hint, else the model's.
    """
    base = keyword_triage(text, kind_hint)
    out = dict(base)
    if raw.get("kind") in KINDS:
        out["kind"] = raw["kind"]
    out["kind"] = spoken_kind(text) or _hinted(kind_hint) or out["kind"]
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
    """(disposition, priority) from a triage: the sort key a monitor orders open tickets by.

    Only a bug is held as ``needs_detail``; every other kind is a ``request`` (its open questions, if
    any, still travel in the mail).
    """
    content_words = [w for w in _words(text) if w not in _FILLERS]
    if len(content_words) < 3:
        disposition = "no_report"
    elif triage.get("kind") == "bug":
        disposition = "needs_detail" if triage.get("needs_detail") else "ready"
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
            ``started_at``, ``ended_at``, ``ended_by``, ``source {...}``, ``context {...}``, and
            optionally ``kind`` (a hint from ``KINDS``; anything else is ignored) and ``origin``
            (``bug_chip`` when absent, ``conversation``, ``work_tab`` or ``tool``).

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
        kind_hint = _hinted(payload.get("kind"))
        provisional = _finish_triage(keyword_triage(text, kind_hint), text, state="pending", source="provisional_keywords", model=None, errors=[])
        transcript = payload.get("transcript") or {}
        ticket = {
            "schema": SCHEMA,
            "ticket_id": ticket_id,
            "report_id": report_id,
            "recorded_at": when,
            "recorded_at_iso": dt.datetime.fromtimestamp(when, dt.timezone.utc).isoformat(timespec="seconds"),
            "recorded_by": "voice-portal skill service",
            "surface": payload.get("surface") or "voice_portal",
            "origin": str(payload.get("origin") or "bug_chip")[:40],
            "kind_hint": kind_hint,
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
    kind_hint = ticket.get("kind_hint")
    started = time.monotonic()
    errors: list[str] = []
    triage = None
    if len([w for w in _words(text) if w not in _FILLERS]) < 3:
        triage = _finish_triage(keyword_triage(text, kind_hint), text, state="done", source="no_words", model=None, errors=[])
    else:
        hint_line = f"He sent it as a {kind_hint.replace('_', ' ')}.\n\n" if kind_hint else ""
        messages = [
            {"role": "system", "content": TRIAGE_SYSTEM},
            {"role": "user", "content": f"Context when the report began:\n{_context_lines(ticket)}\n\n{hint_line}Report transcript:\n{text}"},
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
                triage = _finish_triage(_clean_triage(parsed, text, kind_hint), text, state="done", source="reasoning", model=cfg.inference_model, errors=errors, elapsed_ms=int((time.monotonic() - started) * 1000), usage=usage)
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
            triage = _finish_triage(keyword_triage(text, kind_hint), text, state="done", source="fallback_keywords", model=None, errors=errors, elapsed_ms=int((time.monotonic() - started) * 1000))
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
        return
    try:
        ticket = read_tickets(log_path(cfg)).get(ticket_id) or {}
        if cfg.intake_recipient and not ticket.get("mail") and not ticket.get("mail_staged") and (ticket.get("triage") or {}).get("disposition") in (cfg.intake_mail_dispositions or []):
            mail_ticket(cfg, ticket_id)
    except Exception:  # noqa: BLE001 - logged; POST /issues/<id>/mail can send it later
        log.exception("mailing %s failed", ticket_id)


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
        "origin": ticket.get("origin") or "bug_chip",
        "title": triage.get("title"),
        "kind": triage.get("kind"),
        "component": triage.get("component"),
        "severity": triage.get("severity"),
        "priority": triage.get("priority"),
        "disposition": triage.get("disposition"),
        "triage_state": triage.get("state"),
        "chars": len(ticket.get("text") or ""),
        "mailed": bool(ticket.get("mail")),
        "mail_staged": bool(ticket.get("mail_staged")),
        "replies": len(ticket.get("replies") or []),
    }


def list_tickets(cfg: Config, *, status: str | None = None, limit: int = 50) -> list[dict]:
    """Newest first; ``status`` filters (``open``)."""
    rows = [summary(t) for t in read_tickets(log_path(cfg)).values() if status in (None, "", t.get("status"))]
    return list(reversed(rows))[: max(1, limit)]


def new_report_id() -> str:
    return uuid.uuid4().hex


# ----- the Intake resident's mail (instance mailbox) -----

POSTED_RE = re.compile(r"posted\s+message_id=(\S+)\s+to=(\S+)")
TICKET_RE = re.compile(r"\bbug-\d{8}-\d{2,}\b")
# Leading words a resident's reply may carry (``status: <word> ...``); the ticket's status becomes it.
STATUS_WORDS = ("accepted", "answered", "learned", "proposed", "started", "escalated", "needs_detail", "done", "failed", "declined")

# What the mail calls each kind, and the reply words (in the order they are used) and what each names.
_KIND_LINES = {
    "bug": "A bug report about the voice portal.",
    "feature_request": "A feature request for the voice portal.",
    "message": "A message for you.",
    "teaching": "A teaching.",
    "project_proposal": "A project proposal.",
}
_REPLY_WORDS = {
    "bug": ("accepted", "needs_detail", "declined", "done", "failed"),
    "feature_request": ("accepted", "needs_detail", "declined", "done", "failed"),
    "message": ("answered", "needs_detail", "declined"),
    "teaching": ("learned", "needs_detail", "declined"),
    "project_proposal": ("proposed", "started", "needs_detail", "declined"),
}
_REPLY_GUIDES = {
    "bug": "Reply status: accepted when you take it (needs_detail or declined, with the reason, when you cannot), then status: done naming the PR, the merge commit and the build or deploy, or status: failed with what stopped it.",
    "feature_request": "Reply status: accepted when you take it (needs_detail or declined, with the reason, when you cannot), then status: done naming the PR, the merge commit and the build or deploy, or status: failed with what stopped it.",
    "message": "Reply status: answered with the answer, quoting the record ids and dates it rests on (needs_detail when you cannot answer without asking him).",
    "teaching": "Reply status: learned, restating what you learned, where it applies and the package version that now holds it.",
    "project_proposal": "Reply status: proposed with the brief (outcome, first step, sources, risks, how it stays small) and ask once for a go-ahead; only after he says go, reply status: started naming the project id, the repository and the first commit.",
}
_ORIGIN_LINES = {
    "bug_chip": "the phone's Bug chip",
    "conversation": "the conversation (he asked the assistant to send it to you)",
    "work_tab": "the phone's Work tab",
    "tool": "a skill-service tool call",
}


def _mail_kind(kind: str | None) -> str:
    """The kind whose reply words a mail uses (a retired or unknown kind reads as a message)."""
    return kind if kind in KINDS else "message"


def mail_text(ticket: dict, note: str | None = None, *, record_url: str | None = None) -> str:
    """The plain-text mail a resident receives for one ticket (never JSON: a JSON body of the
    shape ``{"kind": "job_request"}`` would be read as cloud-job demand by the host).

    Args:
        ticket: The folded ticket.
        note: Text appended under "Note from the operator's session".
        record_url: Where the resident reads the full ticket (``GET /issues/<id>``); left out when None.
    """
    triage = ticket.get("triage") or {}
    context = ticket.get("context") or {}
    source = ticket.get("source") or {}
    passage = context.get("passage") or {}
    kind = _mail_kind(triage.get("kind"))
    origin = _ORIGIN_LINES.get(ticket.get("origin") or "bug_chip", ticket.get("origin") or "the voice portal")
    build = f" (app build {source['app_build']})" if source.get("app_build") else ""
    lines = [
        f"voice-portal ticket_id={ticket.get('ticket_id')} kind={triage.get('kind')} priority={triage.get('priority')} "
        f"disposition={triage.get('disposition')} component={triage.get('component')}",
        "",
        _KIND_LINES[kind],
        f"Title: {triage.get('title')}",
        f"Recorded: {ticket.get('recorded_at_iso')} from {origin}{build}.",
        "",
        "Rawley said (speech recognition, read charitably):",
        f"  \"{ticket.get('text') or ''}\"",
        "",
        f"Summary: {triage.get('summary') or ''}",
    ]
    if triage.get("observed"):
        lines.append(f"Observed: {triage['observed']}")
    if triage.get("expected"):
        lines.append(f"Expected: {triage['expected']}")
    for step in triage.get("steps") or []:
        lines.append(f"Step: {step}")
    if triage.get("flags"):
        lines.append(f"Flags: {', '.join(triage['flags'])}")
    if triage.get("detail_questions"):
        lines.append("Open questions from triage:")
        lines += [f"  - {q}" for q in triage["detail_questions"]]
    if note:
        lines += ["", "Note from the operator's session:", note]
    lines += ["", "Context when it was recorded:"]
    if passage.get("reference"):
        lines.append(f"  Scripture pane: {passage['reference']} ({'being read aloud' if passage.get('reading') else 'open'}).")
    if context.get("mode_before"):
        lines.append(f"  Portal mode: {context.get('mode_before')}.")
    for turn in (context.get("recent_turns") or [])[-4:]:
        lines.append(f"  {turn.get('role')}: {str(turn.get('text') or '')[:240]}")
    lines.append("")
    if record_url:
        lines.append(f"Full record: GET {record_url} (intake issues log).")
    lines += [
        f"Reply to this message with send_message(\"operator\", \"status: <word> ...\", in_reply_to=<this message id>); <word> is one of {', '.join(_REPLY_WORDS[kind])}.",
        _REPLY_GUIDES[kind],
    ]
    return "\n".join(lines) + "\n"


def _record_url(cfg: Config, ticket_id: str) -> str:
    # NOTE [Context]: the mailed resident runs under the allocator on the desktop, the same host as
    # this service, so the loopback address reaches it; no tailnet address is baked into the mail.
    return f"http://127.0.0.1:{cfg.listen_port}/issues/{ticket_id}"


def _allocatorctl(cfg: Config, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([cfg.allocatorctl, *args], capture_output=True, text=True, timeout=cfg.allocatorctl_timeout_s)


def inbox_listing(cfg: Config) -> dict | None:
    """The instance mailbox's messages to the operator, one ``allocatorctl inbox list --to-operator
    --json`` (schema ``rlm_adk.inbox_list.v1``: ``instance_id`` and ``messages``, oldest first, the
    whole history), or None when the CLI cannot be run or refuses."""
    try:
        proc = _allocatorctl(cfg, ["inbox", "list", "--to-operator", "--json"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("inbox list failed: %s", exc)
        return None
    if proc.returncode != 0:
        log.warning("inbox list refused (exit %s): %s", proc.returncode, ((proc.stderr or "") + (proc.stdout or "")).strip()[:300])
        return None
    try:
        document = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        log.warning("inbox list printed no JSON: %r", (proc.stdout or "")[:200])
        return None
    if not isinstance(document, dict) or not isinstance(document.get("messages"), list):
        return None
    return document


def mail_ticket(cfg: Config, ticket_id: str, *, note: str | None = None, force: bool = False) -> dict:
    """Mail one ticket to ``intake_recipient`` and record a ``mailed`` event.

    Args:
        cfg: Service configuration (``intake_recipient``, ``allocatorctl``).
        ticket_id: The ticket to send.
        note: Text appended under "Note from the operator's session" (answers to open questions).
        force: Send again although the ticket was already mailed.

    Returns:
        ``{"mailed": True, "message_id", "recipient", "instance_id"}``; in stage mode ``{"mailed":
        False, "staged": True, "body", "command"}``; otherwise ``{"mailed": False, "reason", ...}``.
    """
    if not cfg.intake_recipient:
        return {"mailed": False, "reason": "intake_recipient_unset"}
    path = log_path(cfg)
    ticket = read_tickets(path).get(ticket_id)
    if ticket is None:
        return {"mailed": False, "reason": "not_found"}
    if ticket.get("mail") and not force:
        return {"mailed": False, "reason": "already_mailed", "mail": ticket["mail"]}
    if cfg.intake_mail_mode != "post" and ticket.get("mail_staged") and not force:
        return {"mailed": False, "staged": True, "reason": "already_staged", "mail_staged": ticket["mail_staged"]}
    outdir = cfg.root / "outbox" / "issues"
    outdir.mkdir(parents=True, exist_ok=True)
    body = outdir / f"{ticket_id}-{int(time.time())}.txt"
    body.write_text(mail_text(ticket, note, record_url=_record_url(cfg, ticket_id)), encoding="utf-8")
    command = [cfg.allocatorctl, "inbox", "post", "--to", cfg.intake_recipient, "--file", str(body)]
    if cfg.intake_mail_mode != "post":
        # Stub: everything short of delivery. The runtime is not running; the staged command is what
        # "post" mode would run, and POST /issues/<id>/mail {"force": true} re-stages after edits.
        event = {"schema": SCHEMA, "event": "mail_staged", "ticket_id": ticket_id, "at": time.time(), "recipient": cfg.intake_recipient, "body": str(body), "command": command, "note": note}
        with _LogLock(path):
            _append(path, event)
        log.info("issue %s mail staged for %s at %s", ticket_id, cfg.intake_recipient, body)
        return {"mailed": False, "staged": True, "recipient": cfg.intake_recipient, "body": str(body), "command": command}
    try:
        proc = _allocatorctl(cfg, ["inbox", "post", "--to", cfg.intake_recipient, "--file", str(body)])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"mailed": False, "reason": "allocatorctl_failed", "error": str(exc)}
    match = POSTED_RE.search(proc.stdout or "")
    if proc.returncode != 0 or not match:
        return {"mailed": False, "reason": "refused", "returncode": proc.returncode, "output": ((proc.stdout or "") + (proc.stderr or "")).strip()[:600]}
    # The post receipt names no instance; the listing does (the same sealed launch the post used).
    # Replies are threaded only within it, so a later instance's message ids never match this mail.
    listing = inbox_listing(cfg)
    instance_id = listing.get("instance_id") if listing else None
    event = {"schema": SCHEMA, "event": "mailed", "ticket_id": ticket_id, "at": time.time(), "message_id": match.group(1), "recipient": match.group(2), "instance_id": instance_id, "note": note, "body": str(body)}
    with _LogLock(path):
        _append(path, event)
    log.info("issue %s mailed to %s as %s (instance %s)", ticket_id, match.group(2), match.group(1), instance_id)
    return {"mailed": True, "message_id": match.group(1), "recipient": match.group(2), "instance_id": instance_id}


def _status_word(text: str) -> str | None:
    """The reply's leading status word (``status: done ...``, ``Status = done.``, ``Done. ...``), or None."""
    m = re.match(r"\s*status\s*[:=]\s*([a-z_]+)", text or "", re.I)
    word = (m.group(1) if m else (text or "").strip().split(" ", 1)[0]).lower().strip(".,:")
    return word if word in STATUS_WORDS else None


def _reply_status(text: str) -> str:
    """A threaded reply's status: its leading word, else ``answered``."""
    return _status_word(text) or "answered"


def display_text(text: str) -> str:
    """A message's words without the leading ``status: <word>`` (what the phone shows)."""
    return re.sub(r"^\s*status\s*[:=]\s*[a-z_]+[\s.,:;-]*", "", text or "", count=1, flags=re.I).strip() or (text or "").strip()


def poll_replies(cfg: Config) -> list[dict]:
    """Read the mailbox once and record every new message to the operator; returns the events written.

    One ``inbox list --to-operator --json`` per poll. A message is recorded once by ``message_id``
    (the listing has no cursor; every poll returns the whole history). A reply whose ``in_reply_to``
    names a ticket's mail becomes a ``status`` event on that ticket; mail recorded with another
    instance id is never matched (a mail recorded before instance ids were kept matches by id alone:
    message ids are unique ``mail-<uuid>``). Everything else (unthreaded mail, a reply to a message
    that is not a ticket's) becomes an ``inbound`` event with ``ticket_id`` null and the ticket ids its
    text names in ``mentions``.
    """
    listing = inbox_listing(cfg)
    if listing is None:
        return []
    instance_id = listing.get("instance_id")
    path = log_path(cfg)
    recorded: list[dict] = []
    with _LogLock(path):
        # Scan and append under one lock: the poller thread and POST /issues/replies never record twice.
        events = read_events(path)
        seen = {e.get("reply_message_id") for e in events if e.get("event") == "status"} | {e.get("message_id") for e in events if e.get("event") == "inbound"}
        threads: dict[str, str] = {}
        for ticket in fold_tickets(events).values():
            for mail in ticket.get("mail") or []:
                if mail.get("instance_id") in (None, instance_id):
                    threads[mail["message_id"]] = ticket["ticket_id"]
        for message in listing["messages"]:
            if not isinstance(message, dict):
                continue
            message_id = message.get("message_id")
            if not message_id or message_id in seen:
                continue
            text = str(message.get("text") or "")
            reply_to = message.get("in_reply_to")
            base = {"schema": SCHEMA, "at": time.time(), "text": text, "from": message.get("from"), "sent_at": message.get("sent_at"), "in_reply_to": reply_to, "instance_id": instance_id}
            if reply_to in threads:
                event = {**base, "event": "status", "ticket_id": threads[reply_to], "status": _reply_status(text), "reply_message_id": message_id}
            else:
                event = {**base, "event": "inbound", "ticket_id": None, "status": _status_word(text), "message_id": message_id, "mentions": sorted(set(TICKET_RE.findall(text)))}
            _append(path, event)
            seen.add(message_id)
            recorded.append(event)
    for event in recorded:
        if event["event"] == "status":
            log.info("issue %s: reply %s (%s)", event["ticket_id"], event["reply_message_id"], event["status"])
        else:
            log.info("inbound mail %s from %s (mentions %s)", event["message_id"], _sender_name(event.get("from")), event["mentions"] or "-")
    return recorded


def record_status(cfg: Config, ticket_id: str, status: str, *, text: str = "", artifact: str | None = None, by: str = "operator-session") -> dict | None:
    """Append a ``status`` event written by someone other than the mailed resident (an operator
    session delivering the change itself, for example). Returns the event, or None for an unknown ticket."""
    path = log_path(cfg)
    if ticket_id not in read_tickets(path):
        return None
    event = {"schema": SCHEMA, "event": "status", "ticket_id": ticket_id, "at": time.time(), "status": status, "from": by, "text": text, "artifact": artifact}
    with _LogLock(path):
        _append(path, event)
    log.info("issue %s: status %s by %s", ticket_id, status, by)
    return event


# ----- the phone's view of Intake's mail (GET /intake/feed, the pushed intake_message) -----


def _sender_name(sender) -> str | None:
    if isinstance(sender, dict):
        return sender.get("name") or sender.get("resident_id") or sender.get("package_key")
    return str(sender) if sender else None


def _is_feed_event(event: dict) -> bool:
    return (event.get("event") == "status" and bool(event.get("reply_message_id"))) or event.get("event") == "inbound"


def feed_item(event: dict, tickets: dict[str, dict]) -> dict:
    """One ``intake_message``: a resident's reply on a ticket (``kind: reply``) or unthreaded mail
    (``kind: inbound``), with the ticket's title and kind and the words the phone shows (``display``).

    No key is named ``message`` or ``session_id``: the app decodes every frame of its control socket
    as a ``ServerEvent`` whose ``message`` is a string, and a frame that fails that decode is dropped.
    """
    ticket = tickets.get(event.get("ticket_id") or "") or {}
    triage = ticket.get("triage") or {}
    text = str(event.get("text") or "")
    return {
        "type": "intake_message",
        "id": event.get("reply_message_id") or event.get("message_id"),
        "kind": "reply" if event.get("event") == "status" else "inbound",
        "ticket_id": event.get("ticket_id"),
        "ticket_title": triage.get("title"),
        "ticket_kind": triage.get("kind"),
        "status": event.get("status"),
        "text": text,
        "display": display_text(text),
        "from_name": _sender_name(event.get("from")),
        "sent_at": event.get("sent_at"),
        "at": event.get("at"),
        "in_reply_to": event.get("in_reply_to"),
        "instance_id": event.get("instance_id"),
        "mentions": event.get("mentions") or [],
    }


def feed_items(cfg: Config, events: list[dict]) -> list[dict]:
    """``feed_item`` for each of ``events`` (as ``poll_replies`` returns them) that the feed shows."""
    tickets = read_tickets(log_path(cfg))
    return [feed_item(e, tickets) for e in events if _is_feed_event(e)]


def feed(cfg: Config, *, since: float | None = None, limit: int = 50) -> list[dict]:
    """Residents' replies and inbound mail recorded after ``since`` (Unix seconds), newest last.

    Args:
        cfg: Service configuration (``issues_log``).
        since: Only items whose ``at`` (when this service recorded them) is later; None for all.
        limit: At most this many, the newest.
    """
    events = read_events(log_path(cfg))
    tickets = fold_tickets(events)
    items = [feed_item(e, tickets) for e in events if _is_feed_event(e)]
    if since is not None:
        items = [i for i in items if float(i.get("at") or 0) > since]
    return items[-max(1, limit):]


def start_reply_poller(cfg: Config, on_recorded=None, stop: threading.Event | None = None) -> threading.Thread | None:
    """Poll replies every ``intake_poll_s`` seconds on a daemon thread (only when mail is posted).

    Args:
        cfg: Service configuration.
        on_recorded: Called with the new ``feed_item``s after a poll that recorded any (the service
            pushes them to the phone as ``intake_message``).
        stop: Ends the loop when set (tests); the service never sets it.
    """
    if not cfg.intake_recipient or cfg.intake_mail_mode != "post" or cfg.intake_poll_s <= 0:
        return None
    stop = stop or threading.Event()

    def loop() -> None:
        while not stop.wait(cfg.intake_poll_s):
            try:
                recorded = poll_replies(cfg)
                if recorded and on_recorded is not None:
                    on_recorded(feed_items(cfg, recorded))
            except Exception:  # noqa: BLE001 - logged; the next poll tries again
                log.exception("reply poll failed")

    thread = threading.Thread(target=loop, name="intake-replies", daemon=True)
    thread.start()
    return thread
