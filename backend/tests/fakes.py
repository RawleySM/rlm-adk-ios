"""Fake prayer intake (same validation rules as prayer_ingest.intake) and fake OpenAI-compatible SSE owner."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeIntake:
    def __init__(self):
        self.staged: dict[str, dict[int, tuple[str, int]]] = {}
        self.finalized: dict[str, dict] = {}
        self.recordings: dict[str, dict] = {}
        self.calls: list[str] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        intake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _text(self, status, text):
                body = text.encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                intake.calls.append("GET " + self.path)
                if self.path == "/healthz":
                    return self._json(200, {"ok": True, "service": "prayer-ingest", "version": 1, "source_commit": "fake"})
                m = re.match(r"/recordings/(\w+)", self.path)
                if m and m.group(1) in intake.recordings:
                    return self._json(200, intake.recordings[m.group(1)])
                return self._text(404, "not found")

            def do_POST(self):
                intake.calls.append("POST " + self.path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                if self.path == "/capture-segments":
                    ctype = self.headers.get("Content-Type", "")
                    boundary = ctype.split("boundary=")[-1].encode()
                    fields = {}
                    audio = None
                    for part in raw.split(b"--" + boundary):
                        if b"Content-Disposition" not in part:
                            continue
                        head, _, body = part.partition(b"\r\n\r\n")
                        body = body[:-2] if body.endswith(b"\r\n") else body
                        name = re.search(rb'name="([^"]+)"', head).group(1).decode()
                        if name in ("audio", "segment"):
                            audio = body
                        else:
                            fields[name] = body.decode().strip()
                    cid = fields.get("client_capture_id", "")
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", cid):
                        return self._text(400, "client_capture_id must be 1-128 ASCII letters, digits, underscores, or hyphens")
                    if audio is None:
                        return self._text(400, "audio field required")
                    if "segment_index" not in fields:
                        return self._text(400, "segment_index field required")
                    index = int(fields["segment_index"])
                    if cid in intake.finalized:
                        return self._text(400, "client_capture_id is already finalized; no new segments accepted")
                    digest = hashlib.sha256(audio).hexdigest()
                    staged = intake.staged.setdefault(cid, {})
                    if index in staged and staged[index] != (digest, len(audio)):
                        return self._text(400, f"segment_index {index} already contains different audio bytes")
                    reused = index in staged
                    staged[index] = (digest, len(audio))
                    return self._json(200, {"client_capture_id": cid, "segment_index": index, "durable": True, "audio_sha256": digest, "size_bytes": len(audio), "reused": reused, "recording_id": None})
                if self.path == "/capture-finalize":
                    payload = json.loads(raw)
                    cid = payload.get("client_capture_id", "")
                    segments = payload.get("segments")
                    if not isinstance(segments, list) or not segments:
                        return self._text(400, "segments must contain the exact ordered segment list")
                    for i, item in enumerate(segments):
                        if set(item) != {"segment_index", "audio_sha256", "size_bytes"} or item["segment_index"] != i:
                            return self._text(400, "Invalid segment index, digest or byte length")
                    if cid in intake.finalized:
                        if intake.finalized[cid]["segments"] != segments:
                            return self._text(400, "Finalized capture has a different segment list")
                        return self._json(200, {**intake.finalized[cid], "reused": True})
                    staged = intake.staged.get(cid)
                    if not staged:
                        return self._text(400, "no staged audio sessions found for client_capture_id")
                    for item in segments:
                        if staged.get(item["segment_index"]) != (item["audio_sha256"], item["size_bytes"]):
                            return self._text(400, "segment manifest does not match staged audio")
                    rid = "rec" + hashlib.sha256(cid.encode()).hexdigest()[:8]
                    receipt = {"recording_id": rid, "client_capture_id": cid, "audio_sha256": "combined" + rid, "size_bytes": sum(s[1] for s in staged.values()), "segments": segments, "durable": True, "status": "queued", "reused": False, "duplicate_bytes": False}
                    intake.finalized[cid] = receipt
                    intake.recordings[rid] = {"recording_id": rid, "status": "queued", "disposition": None, "prayer_receipt": None, "artifacts": []}
                    return self._json(200, receipt)
                if self.path == "/capture-discard":
                    payload = json.loads(raw)
                    cid = payload.get("client_capture_id", "")
                    removed = len(intake.staged.pop(cid, {}))
                    return self._json(200, {"client_capture_id": cid, "discarded": removed > 0, "staged_files_removed": removed, "temporary_paths_removed": 0, "recording_id": None})
                return self._text(404, "not found")

        return H


class FakeInference:
    """Streams `reply` word by word as OpenAI SSE; `delay` seconds between words; records requests."""

    def __init__(self, reply="The LORD is my shepherd [1]. That is Scripture.", delay=0.02, status=200, reasoning="Let me think about rest."):
        self.reply = reply
        self.delay = delay
        self.status = status
        self.reasoning = reasoning
        self.requests: list[dict] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v1"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        owner = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/v1/models":
                    body = json.dumps({"object": "list", "data": [{"id": "spark-small"}]}).encode()
                elif self.path == "/status":
                    body = json.dumps({"phase": "stopped", "model_admission": {"spark-small": {"available": True, "reason": None}}, "model_status": {"spark-small": {"phase": "stopped"}}, "memory": {"available_gib": 100}}).encode()
                else:
                    body = b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length))
                owner.requests.append(payload)
                if owner.status != 200:
                    body = json.dumps({"error": {"message": "insufficient_memory_for_envelope"}}).encode()
                    self.send_response(owner.status)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()

                def chunk(data: bytes):
                    self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                    self.wfile.flush()

                try:
                    if owner.reasoning:  # thinking models stream reasoning_content before content
                        chunk(b"data: " + json.dumps({"choices": [{"delta": {"reasoning_content": owner.reasoning}, "finish_reason": None}]}).encode() + b"\n\n")
                    words = owner.reply.split(" ")
                    for i, word in enumerate(words):
                        delta = word if i == 0 else " " + word
                        chunk(b"data: " + json.dumps({"choices": [{"delta": {"content": delta}, "finish_reason": None}]}).encode() + b"\n\n")
                        time.sleep(owner.delay)
                    chunk(b"data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 50, "completion_tokens": len(words)}}).encode() + b"\n\n")
                    chunk(b"data: [DONE]\n\n")
                    chunk(b"")
                except (BrokenPipeError, ConnectionResetError):
                    owner.requests[-1]["client_disconnected"] = True

        return H


if __name__ == "__main__":  # CI helper: a fake reasoning owner on a fixed port
    import argparse
    import signal

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()
    owner = FakeInference(reply="Rest in the shepherd psalm is something Yahweh provides [1]: he makes me lie down in green pastures and leads me beside still waters [1]. The fixture teaching calls that rest a gift, not an achievement [2].", delay=0.08)
    owner.server.server_close()
    owner.server = ThreadingHTTPServer(("127.0.0.1", args.port), owner._handler())
    print(f"fake owner on 127.0.0.1:{args.port}", flush=True)
    signal.signal(signal.SIGTERM, lambda *_: owner.server.shutdown())
    owner.server.serve_forever()
