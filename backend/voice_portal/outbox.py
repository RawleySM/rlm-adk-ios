"""Durable work outbox over the installed allocator's public inbox CLI.

States: queued_locally, backend_unavailable, posting, delivery_unknown, directive_posted,
artifact_available, failed. A successful `allocatorctl inbox post` establishes only directive_posted.
Every attempt row is written BEFORE the subprocess is started so a crash leaves evidence; an
ambiguous result is reconciled by operation id from `allocatorctl inbox list` before any retry.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import uuid
from pathlib import Path

from .config import Config
from .db import now
from .retrieval import search

POSTED_RE = re.compile(r"posted\s+message_id=(\S+)\s+run=(\S+)\s+target=(\S+)")
LIST_RE = re.compile(r"message_id=(\S+)\s+run=(\S+)\s+target=(\S+)\s+delivered=(\S+)\s+cleared=(\S+)\s+text=(.*)$")
RETRYABLE = {"queued_locally", "backend_unavailable"}
DIRECTIVE_PREFIX = "voice-portal operation_id="


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


def directive_text(op: dict) -> str:
    return f"{DIRECTIVE_PREFIX}{op['operation_id']} kind={op['kind']}\n\n{op['request_text']}\n"


def _run(cmd: list[str], timeout: int) -> tuple[int | None, str, str, str]:
    """Returns (returncode, stdout, stderr, outcome) where outcome in {ok, timeout, missing}."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr, "ok"
    except subprocess.TimeoutExpired as exc:
        return None, (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""), (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or ""), "timeout"
    except FileNotFoundError:
        return None, "", f"allocatorctl not found: {cmd[0]}", "missing"


def _classify_post(returncode: int | None, stdout: str, stderr: str, outcome: str) -> tuple[str, dict]:
    combined = stdout + "\n" + stderr
    if outcome == "missing":
        return "backend_unavailable", {"reason": "allocatorctl_missing"}
    if outcome == "timeout":
        return "delivery_unknown", {"reason": "post_timed_out"}
    match = POSTED_RE.search(stdout)
    if returncode == 0 and match:
        return "directive_posted", {"message_id": match.group(1), "run_id": match.group(2), "target": match.group(3)}
    if "no live run" in combined or "ServiceControlRefused" in combined:
        return "backend_unavailable", {"reason": "no_live_run", "observed": combined.strip()[:500]}
    if returncode == 0:
        return "delivery_unknown", {"reason": "exit_zero_without_receipt"}
    return "delivery_unknown", {"reason": f"exit_{returncode}_without_refusal"}


def recover_stale_posting(conn: sqlite3.Connection, cfg: Config, *, all_rows: bool = False, operation_id: str | None = None) -> list[str]:
    """A `posting` row whose process died (service restart) or whose attempt exceeded the CLI timeout
    can only be `delivery_unknown`: the post may or may not have reached the inbox. Never replayed."""
    recovered = []
    stale_before = now() - (cfg.allocatorctl_timeout_s + 5)
    rows = conn.execute("SELECT operation_id FROM work_operations WHERE status = 'posting'" + ("" if operation_id is None else " AND operation_id = ?"), () if operation_id is None else (operation_id,)).fetchall()
    for row in rows:
        attempt = conn.execute("SELECT attempt_id, started_at, finished_at FROM work_attempts WHERE operation_id = ? AND outcome = 'posting' ORDER BY attempt_id DESC LIMIT 1", (row["operation_id"],)).fetchone()
        if attempt is None or all_rows or attempt["started_at"] < stale_before:
            with conn:
                conn.execute("UPDATE work_operations SET status = 'delivery_unknown', updated_at = ?, last_error = ?, note = ? WHERE operation_id = ? AND status = 'posting'", (now(), "posting attempt did not settle; outcome unknown", "recovered_stale_posting", row["operation_id"]))
                if attempt is not None:
                    conn.execute("UPDATE work_attempts SET finished_at = COALESCE(finished_at, ?), outcome = 'delivery_unknown', note = ? WHERE attempt_id = ? AND outcome = 'posting'", (now(), json.dumps({"reason": "stale_posting_recovered"}), attempt["attempt_id"]))
            recovered.append(row["operation_id"])
    return recovered


def dispatch(conn: sqlite3.Connection, cfg: Config, operation_id: str) -> dict:
    """Post the directive once. The ready-status check and the attempt row are one atomic claim, so
    concurrent dispatches of the same operation produce exactly one external post."""
    op = get_operation(conn, operation_id)
    if op is None:
        raise KeyError(operation_id)
    if op["status"] == "posting":
        recover_stale_posting(conn, cfg, operation_id=operation_id)
        op = get_operation(conn, operation_id)
    if op["status"] not in RETRYABLE:
        return {"operation_id": operation_id, "status": op["status"], "dispatched": False, "reason": f"not_retryable_from_{op['status']}"}
    directive_dir = cfg.root / "outbox"
    directive_dir.mkdir(parents=True, exist_ok=True)
    # Atomic claim: BEGIN IMMEDIATE takes the write lock; the conditional UPDATE only succeeds for a
    # retryable row, and the attempt row is inserted in the same transaction. Only the claim winner
    # writes a directive file, and it is a per-attempt file, so a losing request can never touch
    # the payload the winner's CLI reads.
    conn.execute("BEGIN IMMEDIATE")
    try:
        claimed = conn.execute(
            "UPDATE work_operations SET status = 'posting', updated_at = ? WHERE operation_id = ? AND status IN ('queued_locally', 'backend_unavailable')",
            (now(), operation_id),
        ).rowcount
        if claimed != 1:
            conn.execute("ROLLBACK")
            current = conn.execute("SELECT status FROM work_operations WHERE operation_id = ?", (operation_id,)).fetchone()["status"]
            return {"operation_id": operation_id, "status": current, "dispatched": False, "reason": f"not_retryable_from_{current}"}
        attempt_id = conn.execute(
            "INSERT INTO work_attempts(operation_id, started_at, command_json, outcome) VALUES (?,?,?,?)",
            (operation_id, now(), "[]", "posting"),
        ).lastrowid
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    path = directive_dir / f"{operation_id}-attempt{attempt_id}.txt"
    cmd = [cfg.allocatorctl, "inbox", "post", "--file", str(path)]
    try:
        path.write_text(directive_text(op), encoding="utf-8")
        with conn:
            conn.execute("UPDATE work_attempts SET command_json = ? WHERE attempt_id = ?", (json.dumps(cmd), attempt_id))
    except OSError as exc:
        # Local failure before any external side effect: the attempt is recorded as such and the
        # operation goes back to the queue; nothing was posted.
        with conn:
            conn.execute("UPDATE work_attempts SET finished_at = ?, command_json = ?, outcome = 'local_write_failed', stderr = ? WHERE attempt_id = ?", (now(), json.dumps(cmd), str(exc), attempt_id))
            conn.execute("UPDATE work_operations SET status = 'queued_locally', updated_at = ?, last_error = ? WHERE operation_id = ? AND status = 'posting'", (now(), f"directive file could not be written: {exc}", operation_id))
        return {"operation_id": operation_id, "status": "queued_locally", "dispatched": False, "reason": "local_write_failed", "error": str(exc), "attempt_id": attempt_id}
    returncode, stdout, stderr, outcome = _run(cmd, cfg.allocatorctl_timeout_s)
    status, parsed = _classify_post(returncode, stdout, stderr, outcome)
    with conn:
        conn.execute(
            "UPDATE work_attempts SET finished_at = ?, stdout = ?, stderr = ?, returncode = ?, outcome = ?, message_id = ?, run_id = ?, target = ?, note = ? WHERE attempt_id = ?",
            (now(), stdout, stderr, returncode, status, parsed.get("message_id"), parsed.get("run_id"), parsed.get("target"), json.dumps(parsed), attempt_id),
        )
    _set_status(conn, operation_id, status, error=parsed.get("observed") or parsed.get("reason") if status != "directive_posted" else None)
    return {"operation_id": operation_id, "status": status, "dispatched": True, "attempt_id": attempt_id, **parsed}


def parse_inbox_listing(stdout: str) -> list[dict]:
    rows = []
    for line in stdout.splitlines():
        m = LIST_RE.match(line.strip())
        if m:
            try:
                text = json.loads(m.group(6))
            except json.JSONDecodeError:
                text = m.group(6)
            rows.append({"message_id": m.group(1), "run_id": m.group(2), "target": m.group(3), "delivered": m.group(4), "cleared": m.group(5), "text": text})
    return rows


def reconcile(conn: sqlite3.Connection, cfg: Config, operation_id: str) -> dict:
    """Resolve delivery_unknown by operation id using `inbox list`. Never re-posts."""
    op = get_operation(conn, operation_id)
    if op is None:
        raise KeyError(operation_id)
    if op["status"] == "posting":
        recover_stale_posting(conn, cfg, operation_id=operation_id)
        op = get_operation(conn, operation_id)
    cmd = [cfg.allocatorctl, "inbox", "list", "--all"]
    returncode, stdout, stderr, outcome = _run(cmd, cfg.allocatorctl_timeout_s)
    combined = stdout + "\n" + stderr
    result: dict = {"operation_id": operation_id, "before": op["status"], "command": cmd, "returncode": returncode}
    with conn:
        conn.execute(
            "INSERT INTO work_attempts(operation_id, started_at, finished_at, command_json, stdout, stderr, returncode, outcome, note) VALUES (?,?,?,?,?,?,?,?,?)",
            (operation_id, now(), now(), json.dumps(cmd), stdout, stderr, returncode, "reconcile", outcome),
        )
    if outcome != "ok" or "no live run" in combined or "ServiceControlRefused" in combined or returncode != 0:
        result.update({"after": op["status"], "listing": "unavailable", "observed": combined.strip()[:500]})
        if op["status"] == "delivery_unknown":
            _set_status(conn, operation_id, "delivery_unknown", error="listing unavailable; earlier post remains unproven", note="reconcile:listing_unavailable")
        return result
    rows = parse_inbox_listing(stdout)
    marker = f"{DIRECTIVE_PREFIX}{operation_id}"
    found = [r for r in rows if isinstance(r["text"], str) and marker in r["text"]]
    if found:
        row = found[0]
        _set_status(conn, operation_id, "directive_posted", note=f"reconciled message_id={row['message_id']} run={row['run_id']}")
        with conn:
            conn.execute(
                "UPDATE work_attempts SET message_id = ?, run_id = ?, target = ? WHERE attempt_id = (SELECT MAX(attempt_id) FROM work_attempts WHERE operation_id = ? AND outcome = 'reconcile')",
                (row["message_id"], row["run_id"], row["target"], operation_id),
            )
        result.update({"after": "directive_posted", "message_id": row["message_id"], "run_id": row["run_id"]})
        return result
    # Absent from the listing. Only a same-run listing proves the earlier post failed.
    last_unknown = conn.execute(
        "SELECT run_id, stdout FROM work_attempts WHERE operation_id = ? AND outcome = 'delivery_unknown' ORDER BY attempt_id DESC LIMIT 1", (operation_id,)
    ).fetchone()
    listed_runs = {r["run_id"] for r in rows}
    if op["status"] == "delivery_unknown" and last_unknown is not None and last_unknown["run_id"] and last_unknown["run_id"] in listed_runs:
        _set_status(conn, operation_id, "queued_locally", note="reconcile:absent_from_same_run_listing")
        result.update({"after": "queued_locally", "proof": "absent_from_same_run_listing"})
    else:
        result.update({"after": op["status"], "proof": None, "listed_runs": sorted(listed_runs)})
        if op["status"] == "delivery_unknown":
            _set_status(conn, operation_id, "delivery_unknown", error="absent from current listing; cannot prove the earlier attempt failed", note="reconcile:unproven")
    return result


def attach_artifact(conn: sqlite3.Connection, operation_id: str, *, kind: str, path: Path, reference: dict | None = None) -> dict:
    """Record an artifact whose bytes exist. `rlm_result` additionally requires a result reference
    that names this operation's posted directive (message_id and run_id of a directive_posted attempt);
    a bare existing path never marks the operation artifact_available. Source packets stay 'source_packet'."""
    if not path.is_file():
        raise FileNotFoundError(path)
    if kind == "rlm_result":
        ref = reference or {}
        if not ref.get("message_id") or not ref.get("run_id") or not ref.get("result_ref"):
            raise ValueError("rlm_result needs reference{message_id, run_id, result_ref} associated with this operation")
        posted = conn.execute(
            "SELECT 1 FROM work_attempts WHERE operation_id = ? AND message_id = ? AND run_id = ? AND outcome IN ('directive_posted', 'reconcile')",
            (operation_id, ref["message_id"], ref["run_id"]),
        ).fetchone()
        if posted is None:
            raise ValueError("result reference does not match a posted directive of this operation")
    data = path.read_bytes()
    artifact_id = uuid.uuid4().hex
    with conn:
        conn.execute(
            "INSERT INTO work_artifacts(artifact_id, operation_id, kind, path, sha256, bytes, created_at, reference_json) VALUES (?,?,?,?,?,?,?,?)",
            (artifact_id, operation_id, kind, str(path), hashlib.sha256(data).hexdigest(), len(data), now(), json.dumps(reference) if reference else None),
        )
        if kind == "rlm_result":
            conn.execute("UPDATE work_operations SET status = 'artifact_available', updated_at = ? WHERE operation_id = ?", (now(), operation_id))
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
