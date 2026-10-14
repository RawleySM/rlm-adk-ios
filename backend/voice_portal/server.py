"""HTTP + WebSocket service (stdlib). One process, threaded; SQLite connection per request thread."""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, conversation, devices, outbox, prayer_intake, retrieval
from .tools.interpretations import interpretations_tool
from .tools.policy import load_policy
from .tools.project import project_tool
from .tools.scripture import scripture_tool
from .tools.word import word_tool
from .tools.work import work_tool
from .config import Config
from .db import connect, now
from .importer import family_counts, latest_receipts
from .reasoning import readiness
from .sources.theographic import passage_entities
from .ws import WebSocket, WebSocketClosed, accept_key

log = logging.getLogger("voice_portal")


class SessionTurns:
    """Turn cancel events of one WebSocket session with one lock for every mutation and snapshot."""

    def __init__(self):
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}

    def register(self, turn_id: str) -> threading.Event:
        event = threading.Event()
        with self._lock:
            self._events[turn_id] = event
        return event

    def pop(self, turn_id: str) -> None:
        with self._lock:
            self._events.pop(turn_id, None)

    def contains(self, turn_id: str) -> bool:
        with self._lock:
            return turn_id in self._events

    def cancel(self, turn_id: str) -> bool:
        with self._lock:
            event = self._events.get(turn_id)
        if event is None:
            return False
        event.set()
        return True

    def supersede(self) -> list[str]:
        """Set every still-running turn's cancel event; safe against concurrent pops."""
        with self._lock:
            pairs = [(turn_id, event) for turn_id, event in self._events.items() if not event.is_set()]
        for _, event in pairs:
            event.set()
        return [turn_id for turn_id, _ in pairs]

    def cancel_all(self) -> None:
        with self._lock:
            events = list(self._events.values())
        for event in events:
            event.set()


class PortalService:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        cfg.ensure_dirs()
        self._local = threading.local()
        self.started_at = now()
        self.active_turns: dict[str, threading.Event] = {}
        self.active_lock = threading.Lock()
        # install_id -> (session_id, send) for the phone's live WebSocket, so HTTP can push commands.
        # `send` raises on transport failure (unlike the best-effort `emit` used for conversation).
        self.device_sessions: dict[str, tuple[str, callable]] = {}
        self.device_lock = threading.Lock()
        reset = devices.reset_connections(self.conn())  # nothing survives a restart as "connected"
        if reset:
            log.info("device connections reset on start: %d", reset)
        recovered = outbox.recover_stale_posting(self.conn(), cfg, all_rows=True)
        if recovered:
            log.warning("recovered stale posting operations as delivery_unknown: %s", recovered)

    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = connect(self.cfg.db_path)
            self._local.conn = conn
        return conn

    def status(self) -> dict:
        conn = self.conn()
        return {
            "service": "voice-portal",
            "version": __version__,
            "source_commit": self.cfg.source_commit or None,
            "started_at": self.started_at,
            "state_root": str(self.cfg.root),
            "listen": f"{self.cfg.listen_host}:{self.cfg.listen_port}",
            "stores": {"prayer": self.cfg.prayer_store, "youtube": self.cfg.youtube_store, "scripture": self.cfg.scripture_store, "reader_path": self.cfg.reader_path},
            "inference": readiness(self.cfg.inference_base_url, self.cfg.inference_status_url, self.cfg.inference_model),
            "prayer_intake": {"url": self.cfg.prayer_intake_url, **prayer_intake.intake_health(self.cfg.prayer_intake_url)},
            "allocator": {"cli": self.cfg.allocatorctl, **outbox.allocator_status(self.cfg)},
            "counts": family_counts(conn),
            "imports": latest_receipts(conn),
            "active_turns": sorted(self.active_turns),
        }

    def push_command(self, install_id: str, command: dict) -> bool:
        """Deliver a command to the phone's live session; True only if the frame was written. A
        transport failure leaves the command queued, drops the dead registration and marks the
        device disconnected, so the next hello pushes it again."""
        with self.device_lock:
            entry = self.device_sessions.get(install_id)
        if entry is None:
            return False
        session_id, send = entry
        try:
            send({"type": "command", "command_id": command["command_id"], "kind": command["kind"], "args": command["args"]})
        except Exception as exc:  # WebSocketClosed, OSError, or any broken transport
            log.warning("command %s to %s not sent: %s", command["command_id"], install_id, exc)
            self.drop_device_session(install_id, session_id)
            return False
        devices.mark_delivered(self.conn(), command["command_id"])
        return True

    def drop_device_session(self, install_id: str, session_id: str) -> None:
        with self.device_lock:
            if self.device_sessions.get(install_id, (None, None))[0] == session_id:
                self.device_sessions.pop(install_id, None)
                devices.record_disconnect(self.conn(), install_id)

    def live_device_ids(self) -> set[str]:
        with self.device_lock:
            return set(self.device_sessions)

    def cancel_turn(self, turn_id: str) -> bool:
        with self.active_lock:
            event = self.active_turns.get(turn_id)
        if event is None:
            return False
        event.set()
        return True


class Handler(BaseHTTPRequestHandler):
    service: PortalService  # set by make_server
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # route through logging
        log.info("%s %s", self.address_string(), fmt % args)

    # ----- helpers -----
    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _read_json(self) -> dict:
        raw = self._read_body()
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _route(self, method: str) -> None:
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        svc = self.service
        cfg = svc.cfg
        conn = svc.conn()
        try:
            if method == "GET" and parts == ["healthz"]:
                return self._json(200, {"ok": True, "service": "voice-portal", "version": __version__, "source_commit": cfg.source_commit or None})
            if method == "GET" and parts == ["status"]:
                return self._json(200, svc.status())
            if method == "GET" and parts == ["config"]:
                return self._json(200, json.loads(cfg.to_json()))
            if method == "GET" and parts == ["ws"]:
                return self._websocket()
            if method == "GET" and parts == ["sources"]:
                return self._json(200, {"counts": family_counts(conn), "imports": latest_receipts(conn)})
            if method == "GET" and parts == ["search"]:
                fams = [f for f in (query.get("family") or "").split(",") if f] or None
                return self._json(200, retrieval.search(conn, query.get("q", ""), families=fams, limit=int(query.get("limit", 8))))
            if method == "GET" and len(parts) == 2 and parts[0] == "citations":
                try:
                    return self._json(200, retrieval.resolve_citation(conn, parts[1]))
                except ValueError as exc:
                    return self._json(400, {"available": False, "reason": str(exc)})
            if method == "GET" and len(parts) == 4 and parts[0] == "documents" and parts[3] == "revisions":
                return self._json(200, retrieval.document_revisions(conn, parts[1], parts[2]))
            if method == "GET" and parts == ["devotional"]:
                return self._json(200, conversation.build_devotional(conn, cfg, passage=query.get("passage"), date=query.get("date")))
            # ----- skill-service tools (Pipecat RPC barrier) -----
            if method == "GET" and parts == ["policy"]:
                return self._json(200, load_policy(cfg))
            if parts[:1] == ["tools"] and len(parts) == 2:
                return self._tool(method, parts[1], query, conn, cfg)
            if method == "GET" and parts == ["theographic", "passage"]:
                refs = [r for r in (query.get("osis") or "").split(",") if r]
                return self._json(200, passage_entities(conn, refs))
            if method == "POST" and parts == ["turns"]:
                payload = self._read_json()
                turn_id = payload.get("turn_id") or uuid.uuid4().hex
                events: list[dict] = []
                cancel = threading.Event()
                with svc.active_lock:
                    svc.active_turns[turn_id] = cancel
                try:
                    result = conversation.run_turn(conn, cfg, turn_id=turn_id, session_id=payload.get("session_id"), transcript=payload.get("text", ""), context=payload.get("context"), emit=events.append, cancel=cancel, families=payload.get("families"))
                finally:
                    with svc.active_lock:
                        svc.active_turns.pop(turn_id, None)
                return self._json(200, {**result, "events": len(events)})
            if method == "GET" and len(parts) == 2 and parts[0] == "turns":
                row = conn.execute("SELECT * FROM turns WHERE turn_id = ?", (parts[1],)).fetchone()
                return self._json(200 if row else 404, dict(row) if row else {"error": "not_found"})
            # ----- work outbox -----
            if method == "GET" and parts == ["work"]:
                return self._json(200, {"operations": outbox.list_operations(conn), "allocator": outbox.allocator_status(cfg)})
            if method == "POST" and parts == ["work"]:
                payload = self._read_json()
                op = outbox.create_operation(conn, kind=payload.get("kind", "research"), request_text=payload.get("request_text", ""), operation_id=payload.get("operation_id"))
                return self._json(201, op)
            if len(parts) >= 2 and parts[0] == "work":
                op_id = parts[1]
                if method == "GET" and len(parts) == 2:
                    op = outbox.get_operation(conn, op_id)
                    return self._json(200 if op else 404, op or {"error": "not_found"})
                if method == "POST" and parts[2:] == ["dispatch"]:
                    return self._json(200, outbox.dispatch(conn, cfg, op_id))
                if method == "POST" and parts[2:] == ["reconcile"]:
                    return self._json(200, outbox.reconcile(conn, cfg, op_id))
                if method == "POST" and parts[2:] == ["source-packet"]:
                    return self._json(201, outbox.build_source_packet(conn, cfg, op_id))
                if method == "GET" and len(parts) == 4 and parts[2] == "artifacts":
                    art = conn.execute("SELECT * FROM work_artifacts WHERE artifact_id = ? AND operation_id = ?", (parts[3], op_id)).fetchone()
                    if art is None:
                        return self._json(404, {"error": "not_found"})
                    from pathlib import Path

                    path = Path(art["path"])
                    if not path.is_file():
                        return self._json(410, {"artifact_id": parts[3], "available": False, "reason": "file_missing", "path": str(path)})
                    data = path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json" if path.suffix == ".json" else "application/octet-stream")
                    self.send_header("X-Artifact-Kind", art["kind"])
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return None
            # ----- phone inspection and recovery -----
            if parts and parts[0] == "devices":
                if method == "GET" and len(parts) == 1:
                    return self._json(200, {"devices": devices.list_devices(conn, svc.live_device_ids())})
                if len(parts) >= 2:
                    install_id = parts[1]
                    live = install_id in svc.live_device_ids()
                    if method == "GET" and len(parts) == 2:
                        return self._json(200, {**devices.latest_snapshot(conn, install_id, live=live), "commands": devices.recent_commands(conn, install_id), "latest_image": devices.latest_image(conn, install_id), "latest_manifest": (devices.latest_manifest(conn, install_id) or {}).get("received_at")})
                    if method == "GET" and parts[2:] == ["snapshot"]:
                        return self._json(200, devices.latest_snapshot(conn, install_id, live=live))
                    if method == "POST" and parts[2:] == ["commands"]:
                        payload = self._read_json()
                        try:
                            command = devices.create_command(conn, install_id, payload.get("kind", ""), payload.get("args"))
                        except ValueError as exc:
                            return self._json(400, {"error": str(exc)})
                        pushed = svc.push_command(install_id, command)
                        return self._json(201, {**devices.get_command(conn, command["command_id"]), "pushed": pushed, "note": "status becomes applied/failed/unsupported only when the phone reports the result"})
                    if method == "GET" and len(parts) == 4 and parts[2] == "commands":
                        cmd = devices.get_command(conn, parts[3])
                        return self._json(200 if cmd else 404, cmd or {"error": "not_found"})
                    if method == "POST" and parts[2:] == ["images"]:
                        data = self._read_body()
                        snap_b64 = self.headers.get("X-Snapshot-B64")
                        try:
                            snap = json.loads(base64.b64decode(snap_b64)) if snap_b64 else None
                        except (ValueError, json.JSONDecodeError):
                            snap = {"undecodable": True}
                        return self._json(200, devices.store_image(conn, cfg.root, install_id, data=data, client_sha256=self.headers.get("X-SHA256", ""), session_id=self.headers.get("X-Session-Id"), command_id=self.headers.get("X-Command-Id"), client_time=float(self.headers.get("X-Client-Time") or 0) or None, scope=self.headers.get("X-Scope") or "app_window", snapshot=snap))
                    if method == "GET" and parts[2:] == ["images", "latest", "meta"]:
                        meta = devices.latest_image(conn, install_id)
                        return self._json(200 if meta else 404, meta or {"error": "no_image"})
                    if method == "GET" and parts[2:] == ["images", "latest"]:
                        meta = devices.latest_image(conn, install_id)
                        if meta is None or not meta["available"]:
                            return self._json(404, {"error": "no_image"})
                        from pathlib import Path

                        data = Path(meta["path"]).read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", "image/jpeg")
                        self.send_header("X-Image-Age-S", str(meta["age_s"]))
                        self.send_header("X-Image-Scope", meta["scope"] or "")
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        return None
                    if method == "POST" and parts[2:] == ["manifest"]:
                        return self._json(200, devices.store_manifest(conn, install_id, self._read_json()))
                    if method == "GET" and parts[2:] == ["manifest"]:
                        m = devices.latest_manifest(conn, install_id)
                        return self._json(200 if m else 404, m or {"error": "no_manifest"})
                    if len(parts) >= 4 and parts[2] == "captures":
                        capture_id = parts[3]
                        if method == "POST" and len(parts) == 6 and parts[4] == "files":
                            data = self._read_body()
                            return self._json(200, devices.store_file(conn, cfg.root, install_id, capture_id, int(parts[5]), data=data, client_sha256=self.headers.get("X-SHA256", ""), filename=self.headers.get("X-Filename"), command_id=self.headers.get("X-Command-Id")))
                        if method == "POST" and parts[4:] == ["asr"]:
                            payload = self._read_json()
                            return self._json(200, devices.store_prayer_asr(conn, install_id, capture_id, payload.get("items") or []))
                        if method == "GET" and len(parts) == 4:
                            return self._json(200, devices.capture_evidence(conn, install_id, capture_id))
            # ----- prayer intake relay -----
            if len(parts) >= 3 and parts[0] == "prayer" and parts[1] == "captures":
                cid = parts[2]
                if method == "GET" and len(parts) == 3:
                    return self._json(200, prayer_intake.capture_status(conn, cfg.prayer_intake_url, capture_id=cid, refresh=query.get("refresh", "1") != "0"))
                if method == "POST" and len(parts) == 5 and parts[3] == "segments":
                    data = self._read_body()
                    return self._json(200, prayer_intake.upload_segment(conn, cfg.prayer_intake_url, capture_id=cid, segment_index=int(parts[4]), data=data, client_sha256=self.headers.get("X-Audio-SHA256", ""), filename=self.headers.get("X-Filename") or f"{cid}-{parts[4]}.wav", content_type=self.headers.get("Content-Type") or "audio/wav"))
                if method == "POST" and parts[3:] == ["finalize"]:
                    payload = self._read_json()
                    return self._json(200, prayer_intake.finalize(conn, cfg.prayer_intake_url, capture_id=cid, segments=payload.get("segments") or [], client_captured_at=payload.get("client_captured_at")))
                if method == "POST" and parts[3:] == ["discard"]:
                    return self._json(200, prayer_intake.discard(conn, cfg.prayer_intake_url, capture_id=cid))
            if method == "GET" and parts == ["prayer", "captures"]:
                rows = [dict(r) for r in conn.execute("SELECT client_capture_id, status, recording_id, prayer_id, updated_at FROM prayer_captures ORDER BY created_at DESC")]
                return self._json(200, {"captures": rows, "intake": prayer_intake.intake_health(cfg.prayer_intake_url)})
            if method == "POST" and parts == ["prayer", "test-runs"]:
                return self._json(201, prayer_intake.mint_test_run(cfg.prayer_intake_url, self._read_json().get("test_kind", "phone_receipt")))
            if method == "GET" and len(parts) == 3 and parts[:2] == ["prayer", "test-runs"]:
                return self._json(200, prayer_intake.test_run_status(cfg.prayer_intake_url, parts[2]))
            return self._json(404, {"error": "not_found", "path": url.path})
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            return self._json(400, {"error": str(exc)})
        except prayer_intake.IntakeUnreachable as exc:
            return self._json(502, {"error": "intake_unreachable", "detail": str(exc)})
        except prayer_intake.IntakeRejected as exc:
            return self._json(502, {"error": "intake_rejected", "detail": str(exc)})
        except Exception as exc:  # report, don't hide
            log.error("unhandled: %s\n%s", exc, traceback.format_exc())
            return self._json(500, {"error": "internal", "detail": str(exc)})

    def _tool(self, method: str, name: str, query: dict, conn: sqlite3.Connection, cfg: Config) -> None:
        """Dispatch one /tools/<name> call. A missing required input is a 400 that still carries `spoken`."""
        if method == "GET" and name == "scripture":
            if not (query.get("reference") or "").strip():
                return self._json(400, {"error": "reference is required", "spoken": "Which passage should I read?"})
            return self._json(200, scripture_tool(conn, query["reference"]))
        if method == "GET" and name == "word":
            return self._json(200, word_tool(conn, cfg, query.get("which") or "latest", refresh=query.get("refresh") in ("1", "true", "yes")))
        if method == "GET" and name == "interpretations":
            if not (query.get("reference") or query.get("topic") or "").strip():
                return self._json(400, {"error": "reference or topic is required", "spoken": "Which passage or topic should I find readings on?"})
            return self._json(200, interpretations_tool(conn, query.get("reference"), query.get("topic"), limit=int(query.get("limit") or 3)))
        if method == "GET" and name == "project":
            if not (query.get("description") or query.get("confirm") or "").strip():
                return self._json(400, {"error": "description or confirm is required", "spoken": "Which project do you mean?"})
            return self._json(200, project_tool(cfg, query.get("description") or "", query.get("confirm")))
        if method == "POST" and name == "work":
            payload = self._read_json()
            result = work_tool(conn, cfg, payload.get("request_text") or "", payload.get("kind") or "research", operation_id=payload.get("operation_id"))
            return self._json(400 if result["status"] == "rejected" else 201, result)
        return self._json(404, {"error": "not_found", "tool": name, "method": method})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    # ----- WebSocket voice session -----
    def _websocket(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key")
        if self.headers.get("Upgrade", "").lower() != "websocket" or not key:
            return self._json(400, {"error": "websocket upgrade required"})
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept_key(key))
        self.end_headers()
        self.wfile.flush()
        ws = WebSocket(self.connection)
        self.close_connection = True
        svc = self.service
        cfg = svc.cfg
        conn = svc.conn()
        session_id = uuid.uuid4().hex
        with conn:
            conn.execute("INSERT INTO sessions(session_id, started_at, remote) VALUES (?,?,?)", (session_id, now(), self.address_string()))
        ws.send_json({"type": "session", "session_id": session_id, "server_time": now(), "model": cfg.inference_model, "inference_base_url": cfg.inference_base_url, "source_commit": cfg.source_commit or None})
        session_turns = SessionTurns()
        send_lock = threading.Lock()
        device_install_id: str | None = None

        def send_strict(event: dict) -> None:
            """Raises on transport failure; used for command delivery, where silence must not pass as sent."""
            with send_lock:
                ws.send_json(event)

        def emit(event: dict) -> None:
            try:
                send_strict(event)
            except (WebSocketClosed, OSError):
                pass

        def run(turn_id: str, text: str, context: dict | None, cancel: threading.Event) -> None:
            local = connect(cfg.db_path)
            try:
                conversation.run_turn(local, cfg, turn_id=turn_id, session_id=session_id, transcript=text, context=context, emit=emit, cancel=cancel)
            finally:
                local.close()
                session_turns.pop(turn_id)
                with svc.active_lock:
                    svc.active_turns.pop(turn_id, None)

        try:
            while True:
                message = ws.receive()
                if message is None:
                    break
                if isinstance(message, bytes):
                    emit({"type": "error", "message": "binary frames are not part of this protocol (no PCM uplink)"})
                    continue
                try:
                    event = json.loads(message)
                except json.JSONDecodeError:
                    emit({"type": "error", "message": "invalid JSON"})
                    continue
                kind = event.get("type")
                if kind == "hello":
                    with conn:
                        conn.execute("UPDATE sessions SET client_mode = ? WHERE session_id = ?", (json.dumps(event.get("mode")), session_id))
                    install_id = event.get("install_id")
                    if install_id:
                        # Registered before the ack so an inspector that reads /devices right after the
                        # phone reports connected sees the device.
                        try:
                            devices.record_hello(conn, install_id, session_id=session_id, app_version=event.get("app_version"), build=event.get("build"))
                        except ValueError as exc:
                            emit({"type": "error", "message": str(exc)})
                            continue
                        device_install_id = install_id
                        with svc.device_lock:
                            svc.device_sessions[install_id] = (session_id, send_strict)
                    emit({"type": "hello_ack", "session_id": session_id, "mode": event.get("mode"), "install_id": install_id})
                    if install_id:
                        for pending in devices.pending_commands(conn, install_id):
                            svc.push_command(install_id, pending)
                elif kind == "snapshot":
                    if device_install_id:
                        devices.store_snapshot(conn, device_install_id, session_id=session_id, snapshot=event.get("snapshot") or {}, reason=event.get("reason"))
                        emit({"type": "snapshot_ack", "reason": event.get("reason")})
                elif kind == "command_result":
                    if device_install_id:
                        try:
                            cmd = devices.record_result(conn, device_install_id, event.get("command_id") or "", event.get("status") or "", event.get("result"))
                        except ValueError as exc:
                            emit({"type": "error", "message": str(exc)})
                            continue
                        emit({"type": "command_result_ack", "command_id": event.get("command_id"), "status": (cmd or {}).get("status")})
                elif kind == "prayer_asr":
                    if device_install_id:
                        emit({"type": "prayer_asr_ack", "capture_id": event.get("capture_id"), **devices.store_prayer_asr(conn, device_install_id, event.get("capture_id") or "", event.get("items") or [])})
                elif kind == "ping":
                    emit({"type": "pong", "t": event.get("t"), "server_time": now()})
                elif kind == "partial_transcript":
                    # Partials update UI/context only; they never start a model call.
                    emit({"type": "partial_ack", "turn_id": event.get("turn_id"), "chars": len(event.get("text") or "")})
                elif kind == "final_transcript":
                    turn_id = event.get("turn_id") or uuid.uuid4().hex
                    text = (event.get("text") or "").strip()
                    if not text:
                        emit({"type": "error", "turn_id": turn_id, "message": "empty transcript"})
                        continue
                    if session_turns.contains(turn_id) or conn.execute("SELECT 1 FROM turns WHERE turn_id = ?", (turn_id,)).fetchone():
                        emit({"type": "duplicate_turn", "turn_id": turn_id})
                        continue
                    # One conversation per session: a new final supersedes any turn still running for
                    # this session, so a pending request never keeps an owner slot behind the user.
                    superseded = session_turns.supersede()
                    cancel = session_turns.register(turn_id)
                    with svc.active_lock:
                        svc.active_turns[turn_id] = cancel
                    emit({"type": "turn_accepted", "turn_id": turn_id, "superseded": superseded})
                    threading.Thread(target=run, args=(turn_id, text, event.get("context"), cancel), daemon=True).start()
                elif kind == "cancel":
                    turn_id = event.get("turn_id")
                    cancelled = session_turns.cancel(turn_id)
                    emit({"type": "cancel_ack", "turn_id": turn_id, "active": cancelled})
                elif kind == "mode_changed":
                    with conn:
                        conn.execute("UPDATE sessions SET client_mode = ? WHERE session_id = ?", (json.dumps(event.get("mode")), session_id))
                    emit({"type": "mode_ack", "mode": event.get("mode")})
                else:
                    emit({"type": "error", "message": f"unknown event type {kind!r}"})
        except (WebSocketClosed, ConnectionError, OSError):
            pass
        finally:
            session_turns.cancel_all()  # disconnect settles the requests this session owns
            if device_install_id:
                svc.drop_device_session(device_install_id, session_id)
            with conn:
                conn.execute("UPDATE sessions SET ended_at = ? WHERE session_id = ?", (now(), session_id))
            ws.close()


def make_server(cfg: Config) -> ThreadingHTTPServer:
    service = PortalService(cfg)
    handler = type("PortalHandler", (Handler,), {"service": service})
    server = ThreadingHTTPServer((cfg.listen_host, cfg.listen_port), handler)
    server.daemon_threads = True
    server.service = service  # type: ignore[attr-defined]
    return server
