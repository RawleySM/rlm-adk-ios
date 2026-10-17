"""The Work tab's durable operation list, sent to the Intake resident through the intake issues log.

An operation is recorded locally first (``create_operation``, a stable id the phone assigns), then
``send_to_intake`` files its request as an intake ticket (``issues.record_report``, report id
``work-<operation id>``), which passes the same stage/post gate as a spoken request. Nothing here
posts to the allocator: the direct ``inbox post`` dispatch and its ``inbox list --all``
reconciliation are retired (they bypassed ``intake_mail_mode``). States: ``queued_locally``,
``sent_to_intake``; rows written before 2026-10-17 also show ``backend_unavailable``,
``delivery_unknown``, ``directive_posted`` and ``artifact_available``. Source packets stay local
bundles, never results.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import uuid
from pathlib import Path

from . import issues
from .config import Config
from .db import now
from .retrieval import search

# Statuses an operation can be sent to Intake from; a repeat from ``sent_to_intake`` returns the same ticket.
SENDABLE = {"queued_locally", "backend_unavailable", "sent_to_intake"}


def new_operation_id() -> str:
    return uuid.uuid4().hex


def create_operation(conn: sqlite3.Connection, *, kind: str, request_text: str, operation_id: str | None = None) -> dict:
    operation_id = operation_id or new_operation_id()
    existing = conn.execute("SELECT * FROM work_operations WHERE operation_id = ?", (operation_id,)).fetchone()
    if existing is not None:
        return get_operation(conn, operation_id)
    ts = now()
    with conn:
        conn.execute(
            "INSERT INTO work_operations(operation_id, kind, request_text, created_at, updated_at, status) VALUES (?,?,?,?,?,?)",
            (operation_id, kind, request_text, ts, ts, "queued_locally"),
        )
    return get_operation(conn, operation_id)


def get_operation(conn: sqlite3.Connection, operation_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM work_operations WHERE operation_id = ?", (operation_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["attempts"] = [dict(a) for a in conn.execute("SELECT * FROM work_attempts WHERE operation_id = ? ORDER BY attempt_id", (operation_id,))]
    out["artifacts"] = [dict(a) for a in conn.execute("SELECT * FROM work_artifacts WHERE operation_id = ? ORDER BY created_at", (operation_id,))]
    for art in out["artifacts"]:
        art["available"] = Path(art["path"]).is_file()
    return out


def list_operations(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM work_operations ORDER BY created_at DESC")]


def _set_status(conn: sqlite3.Connection, operation_id: str, status: str, *, error: str | None = None, note: str | None = None) -> None:
    with conn:
        conn.execute(
            "UPDATE work_operations SET status = ?, updated_at = ?, last_error = ?, note = COALESCE(?, note) WHERE operation_id = ?",
            (status, now(), error, note, operation_id),
        )


def _run(cmd: list[str], timeout: int) -> tuple[int | None, str, str, str]:
    """Returns (returncode, stdout, stderr, outcome) where outcome in {ok, timeout, missing}."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr, "ok"
    except subprocess.TimeoutExpired as exc:
        return None, (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""), (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or ""), "timeout"
    except FileNotFoundError:
        return None, "", f"allocatorctl not found: {cmd[0]}", "missing"


def send_to_intake(conn: sqlite3.Connection, cfg: Config, operation_id: str) -> dict:
    """File the operation's request with the Intake resident as an intake ticket.

    The ticket is ``issues.record_report`` with report id ``work-<operation id>`` (so a repeat files
    nothing new) and origin ``work_tab``; it is triaged in the background and staged or mailed by
    ``intake_mail_mode`` like every other ticket.

    Args:
        conn: Consumer database connection (the work tables).
        cfg: Service configuration.
        operation_id: The operation to send.

    Returns:
        ``{"operation_id", "status", "sent": True, "ticket_id", "created"}``, or ``{"sent": False,
        "reason": "not_sendable_from_<status>"}`` for an operation already handled another way.

    Raises:
        KeyError: Unknown operation.
    """
    op = get_operation(conn, operation_id)
    if op is None:
        raise KeyError(operation_id)
    if op["status"] not in SENDABLE:
        return {"operation_id": operation_id, "status": op["status"], "sent": False, "reason": f"not_sendable_from_{op['status']}"}
    ticket, created = issues.record_report(cfg, {
        "report_id": f"work-{operation_id}",
        "surface": "voice_portal",
        "origin": "work_tab",
        "kind": op["kind"],
        "started_at": op["created_at"],
        "ended_at": now(),
        "ended_by": "work_tab",
        "transcript": {"segments": [{"t": op["created_at"], "text": op["request_text"], "source": "typed"}]},
        "source": {"client": "ios", "operation_id": operation_id, "work_kind": op["kind"]},
        "context": {},
    })
    if created:
        issues.triage_in_background(cfg, ticket["ticket_id"])
    _set_status(conn, operation_id, "sent_to_intake", note=f"ticket_id={ticket['ticket_id']}")
    return {"operation_id": operation_id, "status": "sent_to_intake", "sent": True, "ticket_id": ticket["ticket_id"], "created": created}


def attach_artifact(conn: sqlite3.Connection, operation_id: str, *, kind: str, path: Path, reference: dict | None = None) -> dict:
    """Record an artifact whose bytes exist (a source packet). Never promotes the operation."""
    if not path.is_file():
        raise FileNotFoundError(path)
    data = path.read_bytes()
    artifact_id = uuid.uuid4().hex
    with conn:
        conn.execute(
            "INSERT INTO work_artifacts(artifact_id, operation_id, kind, path, sha256, bytes, created_at, reference_json) VALUES (?,?,?,?,?,?,?,?)",
            (artifact_id, operation_id, kind, str(path), hashlib.sha256(data).hexdigest(), len(data), now(), json.dumps(reference) if reference else None),
        )
    return {"artifact_id": artifact_id, "kind": kind, "path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def build_source_packet(conn: sqlite3.Connection, cfg: Config, operation_id: str) -> dict:
    """A consumer-made bundle of matching prayers, teaching and Scripture. Never an RLM result."""
    op = get_operation(conn, operation_id)
    if op is None:
        raise KeyError(operation_id)
    results = search(conn, op["request_text"], limit=12)
    packet = {
        "artifact_kind": "source_packet",
        "disclaimer": "Source packet assembled locally by the voice portal from retained sources. Not an RLM-ADK draft, report or completion.",
        "operation_id": operation_id,
        "request_text": op["request_text"],
        "created_at": now(),
        "hits": [{k: h[k] for k in ("cite_id", "source_family", "source_doc_id", "revision_hash", "title", "attribution", "text", "recorded_at", "verse_refs")} for h in results["hits"]],
    }
    directory = cfg.root / "artifacts" / operation_id
    directory.mkdir(parents=True, exist_ok=True)
    packet_id = uuid.uuid4().hex
    packet["packet_id"] = packet_id
    path = directory / f"source_packet-{packet_id}.json"  # never overwrites bytes an earlier artifact hash refers to
    path.write_text(json.dumps(packet, indent=2, ensure_ascii=False), encoding="utf-8")
    info = attach_artifact(conn, operation_id, kind="source_packet", path=path)
    return {**info, "hits": len(packet["hits"])}


def allocator_status(cfg: Config) -> dict:
    """Read-only `allocatorctl status --json` summary (never start/resume)."""
    returncode, stdout, stderr, outcome = _run([cfg.allocatorctl, "status", "--json"], cfg.allocatorctl_timeout_s)
    if outcome != "ok" or returncode != 0:
        return {"available": False, "outcome": outcome, "returncode": returncode, "stderr": stderr.strip()[:300]}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {"available": False, "outcome": "unparseable", "stdout": stdout[:300]}
    rc = data.get("runtime_control") or {}
    return {
        "available": True,
        "active": data.get("active"),
        "main_pid": data.get("main_pid"),
        "launch_instance": ((data.get("launch") or {}).get("instance") or {}).get("id"),
        "service_runs": rc.get("service_runs"),
        "hold": rc.get("hold"),
        "run_pause": rc.get("run_pause"),
        "residents": [r.get("resident_id") for r in data.get("residents") or []],
    }
