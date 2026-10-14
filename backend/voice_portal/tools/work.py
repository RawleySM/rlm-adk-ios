"""`POST /tools/work`: hand a spoken work request to the durable outbox and post it once.

The operation is created in the outbox (`outbox.create_operation`) and dispatched immediately
(`outbox.dispatch`, which runs `allocatorctl inbox post` for the live run's root resident). A posted
directive is only *accepted*: the spoken acknowledgement names the operation and says it is not done.
A refusal from the allocator is spoken verbatim. Nothing here ever reports completion; results arrive
later as outbox artifacts (`artifact_available`) through the existing work endpoints.
"""

from __future__ import annotations

import re
import sqlite3

from .. import outbox
from ..config import Config


def _refusal_text(operation: dict) -> str | None:
    """The allocator's own words from the latest attempt (stderr first, then stdout), whitespace-collapsed."""
    attempts = [a for a in operation.get("attempts") or [] if a.get("outcome") not in ("reconcile",)]
    if not attempts:
        return None
    last = attempts[-1]
    text = (last.get("stderr") or "").strip() or (last.get("stdout") or "").strip()
    return re.sub(r"\s+", " ", text)[:400] or None


def work_tool(conn: sqlite3.Connection, cfg: Config, request_text: str, kind: str = "research", operation_id: str | None = None) -> dict:
    """Create and dispatch one work operation; return its status and a spoken acknowledgement.

    Args:
        conn: Consumer database connection (the outbox tables).
        cfg: Service configuration (allocatorctl path and timeout).
        request_text: The user's request, verbatim.
        kind: Operation kind label (default `research`).
        operation_id: Optional client id; a repeat with the same id never posts twice.

    Returns:
        `{accepted, operation_id, short_id, status, message_id, run_id, refusal, completed: False, spoken}`.
    """
    text = (request_text or "").strip()
    if not text:
        return {"accepted": False, "status": "rejected", "completed": False, "spoken": "I did not hear what work to ask for."}
    operation = outbox.create_operation(conn, kind=kind or "research", request_text=text, operation_id=operation_id)
    op_id = operation["operation_id"]
    dispatched = outbox.dispatch(conn, cfg, op_id)
    operation = outbox.get_operation(conn, op_id) or operation
    status = operation["status"]
    short = op_id[:8]
    posted = next((a for a in reversed(operation.get("attempts") or []) if a.get("message_id")), None)
    result = {
        "accepted": status == "directive_posted",
        "operation_id": op_id,
        "short_id": short,
        "kind": operation["kind"],
        "status": status,
        "dispatch": {k: v for k, v in dispatched.items() if k in ("dispatched", "reason", "attempt_id")},
        "message_id": posted.get("message_id") if posted else None,
        "run_id": posted.get("run_id") if posted else None,
        "refusal": None,
        "completed": False,
    }
    if status == "directive_posted":
        result["spoken"] = f"Accepted as operation {short}. It is not done; I will say when there is a result."
    elif status == "backend_unavailable":
        refusal = _refusal_text(operation) or (operation.get("last_error") or "the allocator is unavailable")
        result["refusal"] = refusal
        result["spoken"] = f"Saved as operation {short}, but the allocator refused it: {refusal}. It is not done."
    elif status == "delivery_unknown":
        result["spoken"] = f"Operation {short} was sent, but delivery is not confirmed. It is not done."
    elif status == "artifact_available":
        result["spoken"] = f"Operation {short} already has a result available."
    else:
        result["spoken"] = f"Saved as operation {short}; it has not been sent. It is not done."
    return result
