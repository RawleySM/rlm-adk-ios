"""Relay finalized prayer segments to the existing prayer intake (loopback-only on its host).

The phone uploads each local segment with its own SHA-256 and byte length. This service recomputes the
hash, forwards the bytes to `POST /capture-segments`, and records a segment as uploaded only when the
intake's returned digest and length match both the client's claim and the local recomputation.
Finalize is forwarded to `POST /capture-finalize` with the ordered verified segment list and the real
`recording_id` receipt is persisted. Statuses: saved_locally, segments_uploaded, finalized, published,
excluded, failed, intake_unreachable.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import urllib.error
import urllib.request
import uuid

from .db import now

CAPTURE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


class IntakeUnreachable(Exception):
    pass


class IntakeRejected(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"intake HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


def _multipart(fields: dict[str, str], file_field: str, filename: str, content_type: str, data: bytes) -> tuple[bytes, str]:
    boundary = "----voiceportal" + uuid.uuid4().hex
    out = bytearray()
    for name, value in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8")
    out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n".encode("utf-8")
    out += data
    out += f"\r\n--{boundary}--\r\n".encode("utf-8")
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _request(url: str, *, method: str = "GET", data: bytes | None = None, content_type: str | None = None, timeout: float = 120.0) -> dict:
    headers = {}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        raise IntakeRejected(exc.code, exc.read().decode("utf-8", "replace")) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise IntakeUnreachable(str(exc)) from exc


def ensure_capture(conn: sqlite3.Connection, capture_id: str) -> None:
    if not CAPTURE_ID_RE.fullmatch(capture_id):
        raise ValueError("client_capture_id must be 1-128 ASCII letters, digits, underscores, or hyphens")
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO prayer_captures(client_capture_id, created_at, updated_at, status) VALUES (?,?,?,?)",
            (capture_id, now(), now(), "saved_locally"),
        )


def upload_segment(conn: sqlite3.Connection, intake_url: str, *, capture_id: str, segment_index: int, data: bytes, client_sha256: str, filename: str, content_type: str) -> dict:
    ensure_capture(conn, capture_id)
    local_sha = hashlib.sha256(data).hexdigest()
    size = len(data)
    if client_sha256 and client_sha256.lower() != local_sha:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO prayer_segments(client_capture_id, segment_index, client_sha256, client_size, status, last_error) VALUES (?,?,?,?,?,?)",
                (capture_id, segment_index, client_sha256, size, "failed", "client_hash_mismatch"),
            )
        return {"client_capture_id": capture_id, "segment_index": segment_index, "status": "failed", "reason": "client_hash_mismatch", "server_sha256": local_sha}
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO prayer_segments(client_capture_id, segment_index, client_sha256, client_size, status) VALUES (?,?,?,?,?)",
            (capture_id, segment_index, local_sha, size, "uploading"),
        )
    body, ctype = _multipart({"client_capture_id": capture_id, "segment_index": str(segment_index)}, "audio", filename, content_type, data)
    try:
        receipt = _request(f"{intake_url}/capture-segments", method="POST", data=body, content_type=ctype)
    except IntakeUnreachable as exc:
        with conn:
            conn.execute("UPDATE prayer_segments SET status = 'pending', last_error = ? WHERE client_capture_id = ? AND segment_index = ?", (str(exc), capture_id, segment_index))
            conn.execute("UPDATE prayer_captures SET status = 'intake_unreachable', updated_at = ?, last_error = ? WHERE client_capture_id = ?", (now(), str(exc), capture_id))
        return {"client_capture_id": capture_id, "segment_index": segment_index, "status": "intake_unreachable", "reason": str(exc)}
    except IntakeRejected as exc:
        with conn:
            conn.execute("UPDATE prayer_segments SET status = 'failed', last_error = ? WHERE client_capture_id = ? AND segment_index = ?", (str(exc), capture_id, segment_index))
            conn.execute("UPDATE prayer_captures SET status = 'failed', updated_at = ?, last_error = ? WHERE client_capture_id = ?", (now(), str(exc), capture_id))
        return {"client_capture_id": capture_id, "segment_index": segment_index, "status": "failed", "reason": str(exc), "intake_status": exc.status}
    verified = receipt.get("audio_sha256") == local_sha and int(receipt.get("size_bytes", -1)) == size and receipt.get("segment_index") == segment_index and receipt.get("client_capture_id") == capture_id
    status = "uploaded" if verified and receipt.get("durable", True) else "failed"
    with conn:
        conn.execute(
            "UPDATE prayer_segments SET intake_sha256 = ?, intake_size = ?, durable = ?, status = ?, uploaded_at = ?, receipt_json = ?, last_error = ? WHERE client_capture_id = ? AND segment_index = ?",
            (receipt.get("audio_sha256"), receipt.get("size_bytes"), 1 if receipt.get("durable") else 0, status, now(), json.dumps(receipt), None if verified else "intake_receipt_mismatch", capture_id, segment_index),
        )
        conn.execute("UPDATE prayer_captures SET status = ?, updated_at = ?, last_error = NULL WHERE client_capture_id = ?", ("segments_uploaded" if verified else "failed", now(), capture_id))
    return {"client_capture_id": capture_id, "segment_index": segment_index, "status": status, "verified": verified, "audio_sha256": local_sha, "size_bytes": size, "receipt": receipt}


def finalize(conn: sqlite3.Connection, intake_url: str, *, capture_id: str, segments: list[dict], client_captured_at: float | None) -> dict:
    ensure_capture(conn, capture_id)
    rows = {r["segment_index"]: r for r in conn.execute("SELECT * FROM prayer_segments WHERE client_capture_id = ?", (capture_id,))}
    ordered = []
    for index, seg in enumerate(segments):
        row = rows.get(int(seg["segment_index"]))
        if row is None or row["status"] != "uploaded" or int(seg["segment_index"]) != index:
            return {"client_capture_id": capture_id, "status": "segments_pending", "missing_or_unverified": [int(s["segment_index"]) for s in segments if rows.get(int(s["segment_index"]), {"status": None})["status"] != "uploaded"]}
        if row["intake_sha256"] != seg["audio_sha256"] or int(row["intake_size"]) != int(seg["size_bytes"]):
            return {"client_capture_id": capture_id, "status": "failed", "reason": f"segment {index} identity differs from verified upload"}
        ordered.append({"segment_index": index, "audio_sha256": seg["audio_sha256"], "size_bytes": int(seg["size_bytes"])})
    payload = {"client_capture_id": capture_id, "segments": ordered}
    if client_captured_at is not None:
        payload["client_captured_at"] = float(client_captured_at)
    try:
        receipt = _request(f"{intake_url}/capture-finalize", method="POST", data=json.dumps(payload).encode("utf-8"), content_type="application/json")
    except IntakeUnreachable as exc:
        with conn:
            conn.execute("UPDATE prayer_captures SET status = 'intake_unreachable', updated_at = ?, last_error = ? WHERE client_capture_id = ?", (now(), str(exc), capture_id))
        return {"client_capture_id": capture_id, "status": "intake_unreachable", "reason": str(exc)}
    except IntakeRejected as exc:
        with conn:
            conn.execute("UPDATE prayer_captures SET status = 'failed', updated_at = ?, last_error = ? WHERE client_capture_id = ?", (now(), str(exc), capture_id))
        return {"client_capture_id": capture_id, "status": "failed", "reason": str(exc), "intake_status": exc.status}
    with conn:
        conn.execute(
            "UPDATE prayer_captures SET status = 'finalized', recording_id = ?, finalize_receipt_json = ?, updated_at = ?, last_error = NULL WHERE client_capture_id = ?",
            (receipt.get("recording_id"), json.dumps(receipt), now(), capture_id),
        )
    return {"client_capture_id": capture_id, "status": "finalized", "recording_id": receipt.get("recording_id"), "receipt": receipt}


def discard(conn: sqlite3.Connection, intake_url: str, *, capture_id: str) -> dict:
    ensure_capture(conn, capture_id)
    try:
        receipt = _request(f"{intake_url}/capture-discard", method="POST", data=json.dumps({"client_capture_id": capture_id}).encode("utf-8"), content_type="application/json")
    except IntakeUnreachable as exc:
        return {"client_capture_id": capture_id, "status": "intake_unreachable", "reason": str(exc)}
    except IntakeRejected as exc:
        return {"client_capture_id": capture_id, "status": "failed", "reason": str(exc)}
    with conn:
        conn.execute("UPDATE prayer_captures SET status = 'discarded', updated_at = ?, recording_json = ? WHERE client_capture_id = ?", (now(), json.dumps(receipt), capture_id))
    return {"client_capture_id": capture_id, "status": "discarded", "receipt": receipt}


def capture_status(conn: sqlite3.Connection, intake_url: str, *, capture_id: str, refresh: bool = True) -> dict:
    row = conn.execute("SELECT * FROM prayer_captures WHERE client_capture_id = ?", (capture_id,)).fetchone()
    if row is None:
        return {"client_capture_id": capture_id, "status": "unknown"}
    out = dict(row)
    out["segments"] = [dict(s) for s in conn.execute("SELECT * FROM prayer_segments WHERE client_capture_id = ? ORDER BY segment_index", (capture_id,))]
    out["finalize_receipt"] = json.loads(out.pop("finalize_receipt_json") or "null")
    out["recording"] = json.loads(out.pop("recording_json") or "null")
    if refresh and row["recording_id"]:
        try:
            rec = _request(f"{intake_url}/recordings/{row['recording_id']}")
            status = row["status"]
            prayer_id = row["prayer_id"]
            receipt = rec.get("prayer_receipt") or {}
            disposition = rec.get("disposition") or {}
            if receipt.get("transcript_id"):
                status, prayer_id = "published", receipt["transcript_id"]
            elif isinstance(disposition, dict) and disposition.get("disposition") in {"excluded", "non_prayer", "rejected"}:
                status = "excluded"
            elif rec.get("status") == "failed":
                status = "failed"
            with conn:
                conn.execute("UPDATE prayer_captures SET status = ?, prayer_id = ?, recording_json = ?, updated_at = ? WHERE client_capture_id = ?", (status, prayer_id, json.dumps(rec), now(), capture_id))
            out.update({"status": status, "prayer_id": prayer_id, "recording": rec, "intake_reachable": True})
        except IntakeUnreachable as exc:
            out.update({"intake_reachable": False, "intake_error": str(exc)})
        except IntakeRejected as exc:
            out.update({"intake_reachable": True, "intake_error": str(exc)})
    return out


def intake_health(intake_url: str) -> dict:
    try:
        return {"reachable": True, **_request(f"{intake_url}/healthz", timeout=5)}
    except (IntakeUnreachable, IntakeRejected) as exc:
        return {"reachable": False, "error": str(exc)}


def mint_test_run(intake_url: str, test_kind: str = "phone_receipt") -> dict:
    return _request(f"{intake_url}/test-runs", method="POST", data=json.dumps({"test_kind": test_kind}).encode("utf-8"), content_type="application/json")


def fetch_test_audio(intake_url: str, test_run_id: str) -> bytes:
    req = urllib.request.Request(f"{intake_url}/test-runs/{test_run_id}/audio")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def test_run_status(intake_url: str, test_run_id: str) -> dict:
    return _request(f"{intake_url}/test-runs/{test_run_id}")
