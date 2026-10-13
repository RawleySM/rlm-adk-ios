"""Phone inspection: device snapshots, remote commands with truthful result states, app-rendered
images, and recovery transfers (manifest, original capture bytes, prayer ASR journal).

States a command can be in: `queued` (stored, phone not connected or not yet pushed), `delivered`
(pushed to a live session), `applied` / `failed` / `unsupported` (reported by the phone with the
command id). Only the phone's report counts as execution. Snapshot freshness is reported as
`live` (younger than `stale_after`), `stale`, or `offline` (no session); a missing snapshot is
never presented as healthy.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path

from .db import now

STALE_AFTER_S = 90.0
COMMAND_KINDS = {"snapshot", "image", "dismiss_keyboard", "select_tab", "copy_capture_files", "sync_manifest", "sync_prayer_asr"}
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _safe(value: str) -> str:
    if not SAFE_ID.fullmatch(value or ""):
        raise ValueError("identifier must be 1-128 letters, digits, underscores or hyphens")
    return value


def record_hello(conn: sqlite3.Connection, install_id: str, *, session_id: str, app_version: str | None, build: str | None) -> None:
    _safe(install_id)
    with conn:
        conn.execute(
            "INSERT INTO devices(install_id, first_seen, last_seen, last_session_id, app_version, build, connected) VALUES (?,?,?,?,?,?,1) "
            "ON CONFLICT(install_id) DO UPDATE SET last_seen = excluded.last_seen, last_session_id = excluded.last_session_id, "
            "app_version = COALESCE(excluded.app_version, devices.app_version), build = COALESCE(excluded.build, devices.build), connected = 1",
            (install_id, now(), now(), session_id, app_version, build),
        )


def reset_connections(conn: sqlite3.Connection) -> int:
    """Server start: no socket survived the restart, so no device is connected until it says hello."""
    with conn:
        return conn.execute("UPDATE devices SET connected = 0 WHERE connected = 1").rowcount


def record_disconnect(conn: sqlite3.Connection, install_id: str) -> None:
    with conn:
        conn.execute("UPDATE devices SET connected = 0, last_seen = ? WHERE install_id = ?", (now(), install_id))


def store_snapshot(conn: sqlite3.Connection, install_id: str, *, session_id: str | None, snapshot: dict, reason: str | None) -> int:
    _safe(install_id)
    with conn:
        conn.execute("UPDATE devices SET last_seen = ? WHERE install_id = ?", (now(), install_id))
        cur = conn.execute(
            "INSERT INTO device_snapshots(install_id, session_id, client_time, server_time, reason, snapshot_json) VALUES (?,?,?,?,?,?)",
            (install_id, session_id, snapshot.get("client_time"), now(), reason, json.dumps(snapshot, ensure_ascii=False)),
        )
    return cur.lastrowid


def latest_snapshot(conn: sqlite3.Connection, install_id: str, stale_after: float = STALE_AFTER_S, live: bool | None = None) -> dict:
    """Freshness: unknown_device | no_snapshot | offline (no socket) | stale (socket, old snapshot) |
    connected_background (socket, app not foreground-active per its own last report) | live.
    `live` is the service's current socket registry; when given it overrides the stored flag."""
    device = conn.execute("SELECT * FROM devices WHERE install_id = ?", (install_id,)).fetchone()
    row = conn.execute("SELECT * FROM device_snapshots WHERE install_id = ? ORDER BY snapshot_id DESC LIMIT 1", (install_id,)).fetchone()
    if device is None:
        return {"install_id": install_id, "known": False, "freshness": "unknown_device", "snapshot": None}
    connected = bool(device["connected"]) if live is None else bool(live)
    out = {"install_id": install_id, "known": True, "connected": connected, "last_seen": device["last_seen"], "app_version": device["app_version"], "build": device["build"], "last_session_id": device["last_session_id"]}
    if row is None:
        out.update({"freshness": "no_snapshot", "snapshot": None, "age_s": None})
        return out
    snapshot = json.loads(row["snapshot_json"])
    age = now() - row["server_time"]
    scene = snapshot.get("scene_phase")
    if not connected:
        freshness = "offline"
    elif age > stale_after:
        freshness = "stale"
    elif scene not in (None, "active"):
        freshness = "connected_background"
    else:
        freshness = "live"
    out.update({"freshness": freshness, "age_s": round(age, 1), "server_time": row["server_time"], "client_time": row["client_time"], "reason": row["reason"], "scene_phase": scene, "snapshot": snapshot})
    return out


def list_devices(conn: sqlite3.Connection, live_ids: set[str] | None = None) -> list[dict]:
    rows = conn.execute("SELECT install_id FROM devices ORDER BY last_seen DESC")
    return [latest_snapshot(conn, r["install_id"], live=(r["install_id"] in live_ids) if live_ids is not None else None) | {"snapshot": None} for r in rows]


def create_command(conn: sqlite3.Connection, install_id: str, kind: str, args: dict | None) -> dict:
    _safe(install_id)
    if kind not in COMMAND_KINDS:
        raise ValueError(f"unknown command kind {kind!r}; allowed: {sorted(COMMAND_KINDS)}")
    command_id = uuid.uuid4().hex
    with conn:
        conn.execute(
            "INSERT INTO device_commands(command_id, install_id, kind, args_json, created_at, status) VALUES (?,?,?,?,?,'queued')",
            (command_id, install_id, kind, json.dumps(args or {}), now()),
        )
    return get_command(conn, command_id)


def get_command(conn: sqlite3.Connection, command_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM device_commands WHERE command_id = ?", (command_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["args"] = json.loads(out.pop("args_json") or "{}")
    out["result"] = json.loads(out.pop("result_json") or "null")
    return out


def pending_commands(conn: sqlite3.Connection, install_id: str) -> list[dict]:
    """Commands with no result yet: queued, and delivered ones whose result never arrived (socket
    lost). Both are pushed again on hello; the phone replays a cached result instead of re-running."""
    rows = conn.execute("SELECT command_id FROM device_commands WHERE install_id = ? AND result_at IS NULL AND status IN ('queued', 'delivered') ORDER BY created_at", (install_id,)).fetchall()
    return [get_command(conn, r["command_id"]) for r in rows]


def mark_delivered(conn: sqlite3.Connection, command_id: str) -> None:
    with conn:
        conn.execute("UPDATE device_commands SET status = 'delivered', delivered_at = ? WHERE command_id = ? AND result_at IS NULL", (now(), command_id))


def record_result(conn: sqlite3.Connection, install_id: str, command_id: str, status: str, result: dict | None) -> dict | None:
    """The phone's report. Only applied/failed/unsupported are accepted; a result for a command
    that already has one is kept as the first report (no re-execution is implied by a repeat)."""
    if status not in ("applied", "failed", "unsupported"):
        raise ValueError("result status must be applied, failed or unsupported")
    with conn:
        conn.execute(
            "UPDATE device_commands SET status = ?, result_at = ?, result_json = ? WHERE command_id = ? AND install_id = ? AND result_at IS NULL",
            (status, now(), json.dumps(result or {}), command_id, install_id),
        )
    return get_command(conn, command_id)


def recent_commands(conn: sqlite3.Connection, install_id: str, limit: int = 20) -> list[dict]:
    rows = conn.execute("SELECT command_id FROM device_commands WHERE install_id = ? ORDER BY created_at DESC LIMIT ?", (install_id, limit)).fetchall()
    return [get_command(conn, r["command_id"]) for r in rows]


def _evidence_dir(root: Path, install_id: str, *parts: str) -> Path:
    path = root / "evidence" / "devices" / _safe(install_id)
    for part in parts:
        path = path / _safe(part)
    path.mkdir(parents=True, exist_ok=True)
    return path


def store_image(conn: sqlite3.Connection, root: Path, install_id: str, *, data: bytes, client_sha256: str, session_id: str | None, command_id: str | None, client_time: float | None, scope: str, snapshot: dict | None) -> dict:
    digest = hashlib.sha256(data).hexdigest()
    if client_sha256 and client_sha256.lower() != digest:
        return {"stored": False, "reason": "client_hash_mismatch", "server_sha256": digest}
    directory = _evidence_dir(root, install_id, "images")
    path = directory / f"{int(now() * 1000)}-{digest[:12]}.jpg"
    path.write_bytes(data)
    with conn:
        cur = conn.execute(
            "INSERT INTO device_images(install_id, session_id, command_id, client_time, server_time, scope, path, bytes, sha256, snapshot_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (install_id, session_id, command_id, client_time, now(), scope, str(path), len(data), digest, json.dumps(snapshot) if snapshot else None),
        )
    return {"stored": True, "image_id": cur.lastrowid, "path": str(path), "bytes": len(data), "sha256": digest, "scope": scope}


def latest_image(conn: sqlite3.Connection, install_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM device_images WHERE install_id = ? ORDER BY image_id DESC LIMIT 1", (install_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["age_s"] = round(now() - row["server_time"], 1)
    out["available"] = Path(row["path"]).is_file()
    out["snapshot"] = json.loads(out.pop("snapshot_json") or "null")
    return out


def store_manifest(conn: sqlite3.Connection, install_id: str, manifest: dict) -> dict:
    _safe(install_id)
    with conn:
        cur = conn.execute(
            "INSERT INTO device_manifests(install_id, client_time, received_at, manifest_json) VALUES (?,?,?,?)",
            (install_id, manifest.get("client_time"), now(), json.dumps(manifest, ensure_ascii=False)),
        )
    return {"manifest_id": cur.lastrowid, "captures": len(manifest.get("captures") or []), "orphans": len(manifest.get("orphans") or [])}


def latest_manifest(conn: sqlite3.Connection, install_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM device_manifests WHERE install_id = ? ORDER BY manifest_id DESC LIMIT 1", (install_id,)).fetchone()
    if row is None:
        return None
    return {"manifest_id": row["manifest_id"], "received_at": row["received_at"], "client_time": row["client_time"], "manifest": json.loads(row["manifest_json"])}


def store_file(conn: sqlite3.Connection, root: Path, install_id: str, capture_id: str, segment_index: int, *, data: bytes, client_sha256: str, filename: str | None, command_id: str | None) -> dict:
    """Original capture bytes as evidence. Verified by digest; identical re-sends are idempotent;
    different bytes for an already stored index are refused (never overwritten)."""
    digest = hashlib.sha256(data).hexdigest()
    if client_sha256 and client_sha256.lower() != digest:
        return {"stored": False, "reason": "client_hash_mismatch", "server_sha256": digest, "size_bytes": len(data)}
    existing = conn.execute("SELECT * FROM device_files WHERE install_id = ? AND capture_id = ? AND segment_index = ?", (install_id, capture_id, segment_index)).fetchone()
    if existing is not None:
        if existing["sha256"] == digest and existing["bytes"] == len(data):
            return {"stored": True, "reused": True, "path": existing["path"], "sha256": digest, "size_bytes": len(data)}
        return {"stored": False, "reason": "different_bytes_already_stored", "existing_sha256": existing["sha256"], "server_sha256": digest}
    directory = _evidence_dir(root, install_id, "captures", capture_id)
    path = directory / (filename if filename and SAFE_ID.fullmatch(filename.replace(".", "_")) else f"{segment_index:08d}.wav")
    if path.exists():
        path = directory / f"{segment_index:08d}-{digest[:8]}.wav"
    path.write_bytes(data)
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        return {"stored": False, "reason": "write_verification_failed"}
    with conn:
        conn.execute(
            "INSERT INTO device_files(install_id, capture_id, segment_index, filename, bytes, sha256, path, received_at, command_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (install_id, capture_id, segment_index, filename, len(data), digest, str(path), now(), command_id),
        )
    return {"stored": True, "reused": False, "path": str(path), "sha256": digest, "size_bytes": len(data)}


def store_prayer_asr(conn: sqlite3.Connection, install_id: str, capture_id: str, items: list[dict]) -> dict:
    """Provisional on-device ASR lines for a prayer capture, keyed by (capture, sequence); idempotent."""
    _safe(install_id)
    _safe(capture_id)
    inserted = 0
    with conn:
        for item in items:
            cur = conn.execute(
                "INSERT OR IGNORE INTO prayer_asr(install_id, capture_id, sequence, client_time, text, received_at) VALUES (?,?,?,?,?,?)",
                (install_id, capture_id, int(item["sequence"]), item.get("client_time"), str(item.get("text") or ""), now()),
            )
            inserted += cur.rowcount
    total = conn.execute("SELECT COUNT(*) FROM prayer_asr WHERE install_id = ? AND capture_id = ?", (install_id, capture_id)).fetchone()[0]
    highest = conn.execute("SELECT MAX(sequence) FROM prayer_asr WHERE install_id = ? AND capture_id = ?", (install_id, capture_id)).fetchone()[0]
    return {"inserted": inserted, "total": total, "max_sequence": highest or 0}


def capture_evidence(conn: sqlite3.Connection, install_id: str, capture_id: str) -> dict:
    files = [dict(r) | {"available": Path(r["path"]).is_file()} for r in conn.execute("SELECT * FROM device_files WHERE install_id = ? AND capture_id = ? ORDER BY segment_index", (install_id, capture_id))]
    asr = [dict(r) for r in conn.execute("SELECT sequence, client_time, text, received_at FROM prayer_asr WHERE install_id = ? AND capture_id = ? ORDER BY sequence", (install_id, capture_id))]
    return {"install_id": install_id, "capture_id": capture_id, "files": files, "asr": asr, "note": "Original bytes copied from the phone as evidence; not part of the prayer intake. ASR lines are provisional on-device recognition."}
