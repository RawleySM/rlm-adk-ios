"""`POST /tools/work`: send what Rawley asked for to the Intake resident.

The request becomes a ticket in the intake issues log (`issues.record_report`), exactly like a Bug
chip report: it is triaged on a background thread and then staged or mailed to `intake_recipient`
by `intake_mail_mode`, so this path passes the same stage/post gate as every other one. Nothing here
posts to the allocator, and nothing reports completion: Intake's replies arrive on the ticket
(`GET /intake/feed`, the pushed `intake_message`).

The body is the talk tier's report (`report_id`, `transcript`, `context`, `source`, `kind`,
`origin`: the `send_to_intake` tool files Rawley's own last words this way with origin
`conversation`; the talk tier's escalation safety net files them with origin `auto_escalation` and
the assistant's `assistant_reply`) or the short form `{request_text, kind?, operation_id?}`. `kind`
is a hint (`issues.KINDS`); his first spoken word, and then the triage, decide otherwise.

The call returns as soon as the `reported` event is on disk (one lock, one scan of the log, one
fsync): the triage model call and the allocatorctl post run on background threads, so the talk
tier's spoken acknowledgement is immediate.
"""

from __future__ import annotations

import time

from .. import issues
from ..config import Config


def request_report(payload: dict) -> dict:
    """The ``POST /issues`` report for a ``/tools/work`` body (a report passes through unchanged).

    Args:
        payload: A report (has ``transcript``) or ``{request_text, kind?, operation_id?, report_id?,
            context?, source?, origin?}``.

    Returns:
        A report for ``issues.record_report``; ``origin`` defaults to ``tool``. Without a
        ``report_id`` or ``operation_id`` every call is a new ticket.
    """
    if isinstance(payload.get("transcript"), dict):
        return {**payload, "origin": payload.get("origin") or "tool"}
    text = str(payload.get("request_text") or "").strip()
    return {
        "report_id": str(payload.get("report_id") or payload.get("operation_id") or issues.new_report_id()),
        "surface": "voice_portal",
        "origin": payload.get("origin") or "tool",
        "kind": payload.get("kind"),
        "started_at": time.time(),
        "ended_at": time.time(),
        "ended_by": "tool",
        "transcript": {"segments": [{"t": time.time(), "text": text, "source": "request"}] if text else []},
        "source": payload.get("source") or {},
        "context": payload.get("context") or {},
    }


def spoken_for(cfg: Config, ticket: dict) -> str:
    """What the talk tier says: "Sent to Intake." only when the mail will actually be posted."""
    if (ticket.get("triage") or {}).get("disposition") == "no_report":
        return "Saved in the intake log; it was too short to send to Intake."
    if not cfg.intake_recipient:
        return "Saved in the intake log."
    if cfg.intake_mail_mode != "post":
        return "Saved for Intake. Mail to Intake is on hold, so it has not been sent yet."
    return "Sent to Intake."


def work_tool(cfg: Config, payload: dict) -> dict:
    """Record one request as an intake ticket and triage it in the background.

    Args:
        cfg: Service configuration (``issues_log``, ``intake_recipient``, ``intake_mail_mode``).
        payload: The request body (see ``request_report``).

    Returns:
        ``{accepted, status ("recorded" | "rejected"), ticket_id, report_id, created, kind_hint,
        mail_mode, completed: False, spoken}``. A repeat with the same report id records nothing.
    """
    report = request_report(payload)
    if not issues.transcript_text(report).strip():
        return {"accepted": False, "status": "rejected", "completed": False, "spoken": "I did not hear what to send to Intake."}
    ticket, created = issues.record_report(cfg, report)
    if created:
        issues.triage_in_background(cfg, ticket["ticket_id"])
    return {
        "accepted": True,
        "status": "recorded",
        "ticket_id": ticket["ticket_id"],
        "report_id": ticket["report_id"],
        "created": created,
        "kind_hint": ticket.get("kind_hint"),
        "mail_mode": cfg.intake_mail_mode if cfg.intake_recipient else "unset",
        "completed": False,
        "spoken": spoken_for(cfg, ticket),
    }
