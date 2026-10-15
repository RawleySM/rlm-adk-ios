#!/usr/bin/env python3
"""Fixture talk server for CI: the talk tier's WebSocket transport, stdlib only.

It answers ``POST /start {"transport":"websocket"}`` with a deliberately wrong advertised host and
scheme (the app must use only the path), accepts the tagged-PCM WebSocket on ``/ws-client``, and
records what the app sends: JSON text frames (``client_mode``, ``client_text``, ``client_event``) and
binary frames (``0x01`` + int16 LE 16 kHz mono, 20 ms each). On ``client_text`` it plays a short
scripted bot turn: RTVI ``user-transcription``, ``bot-started-speaking``, ``bot-transcription``,
0.4 s of 24 kHz tone as tagged binary frames, ``bot-tts-text``, ``bot-stopped-speaking``. On
``client_mode`` it echoes the floor change as the real server does (``reason`` = ``client:<reason>``).
The scripture pane: ``client_passage`` answers with a ``passage`` event (Psalm 23 of the World
English Bible, whatever was asked), ``client_read`` ``play`` reads it from where it stopped with the
real server's ``reading`` events (started, verse, verse_done every 2.6 s, finished) and ``pause``
pauses it.

``GET /log`` returns everything recorded, for the CI assertions. Not the real pipeline: no STT,
LLM or TTS; this proves the app's wire protocol and its silence keepalive only.

    python3 scripts/ci_fake_talk.py --port 7860
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import struct
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
LOG: dict = {"started": time.time(), "starts": [], "connections": 0, "text": [], "binary_frames": 0, "binary_bytes": 0,
             "binary_sizes": {}, "bad_tags": 0, "first_binary_t": None, "last_binary_t": None, "bot_turns": 0}
LOCK = threading.Lock()
FLOOR = {"mode": "available"}
PSALM_23 = [
    "Yahweh is my shepherd; I shall lack nothing.",
    "He makes me lie down in green pastures. He leads me beside still waters.",
    "He restores my soul. He guides me in the paths of righteousness for his name\u2019s sake.",
    "Even though I walk through the valley of the shadow of death, I will fear no evil, for you are with me. Your rod and your staff, they comfort me.",
    "You prepare a table before me in the presence of my enemies. You anoint my head with oil. My cup runs over.",
    "Surely goodness and loving kindness shall follow me all the days of my life, and I will dwell in Yahweh\u2019s house forever.",
]
PANE = {"spoken": 0, "reading": 0}


def tone_frames(seconds: float = 0.4, rate: int = 24000, frame_ms: int = 20) -> list[bytes]:
    """Quiet 440 Hz tone as tagged frames (what the app's player node receives)."""
    samples = int(seconds * rate)
    pcm = b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(samples))
    step = rate * frame_ms // 1000 * 2
    return [b"\x01" + pcm[i : i + step] for i in range(0, len(pcm), step)]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        pass

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/start":
            with LOCK:
                LOG["starts"].append({"t": time.time(), "body": body})
            if body.get("transport") != "websocket":
                return self._json(400, {"detail": "fixture serves websocket only"})
            return self._json(200, {"wsUrl": "wss://0.0.0.0:7860/ws-client", "sessionId": str(uuid.uuid4())})
        self._json(404, {"error": "not_found"})

    def do_GET(self):
        if self.path == "/log":
            with LOCK:
                return self._json(200, dict(LOG))
        if self.path.startswith("/ws-client") and self.headers.get("Upgrade", "").lower() == "websocket":
            return self._websocket()
        self._json(404, {"error": "not_found"})

    # ----- WebSocket -----
    def _websocket(self) -> None:
        key = self.headers["Sec-WebSocket-Key"]
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()
        with LOCK:
            LOG["connections"] += 1
        self.send_lock = threading.Lock()
        try:
            while True:
                opcode, payload = self._read_frame()
                if opcode is None or opcode == 0x8:
                    break
                if opcode == 0x9:
                    self._send(0xA, payload)
                elif opcode == 0x1:
                    self._on_text(payload.decode("utf-8", "replace"))
                elif opcode == 0x2:
                    self._on_binary(payload)
        except (ConnectionError, OSError):
            pass

    def _read_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self.rfile.read(n - len(data))
            if not chunk:
                raise ConnectionError("closed")
            data += chunk
        return data

    def _read_frame(self):
        head = self._read_exact(2)
        opcode = head[0] & 0x0F
        masked = head[1] & 0x80
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b"\x00\x00\x00\x00"
        payload = bytearray(self._read_exact(length))
        for i in range(length):
            payload[i] ^= mask[i % 4]
        return opcode, bytes(payload)

    def _send(self, opcode: int, payload: bytes) -> None:
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([n])
        elif n < 65536:
            header += bytes([126]) + struct.pack(">H", n)
        else:
            header += bytes([127]) + struct.pack(">Q", n)
        with self.send_lock:
            self.wfile.write(header + payload)
            self.wfile.flush()

    def _send_json(self, message: dict) -> None:
        self._send(0x1, json.dumps(message).encode())

    def _on_binary(self, payload: bytes) -> None:
        now = time.time()
        with LOCK:
            LOG["binary_frames"] += 1
            LOG["binary_bytes"] += len(payload)
            LOG["binary_sizes"][str(len(payload))] = LOG["binary_sizes"].get(str(len(payload)), 0) + 1
            if payload[:1] != b"\x01":
                LOG["bad_tags"] += 1
            LOG["first_binary_t"] = LOG["first_binary_t"] or now
            LOG["last_binary_t"] = now

    def _on_text(self, text: str) -> None:
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            message = {"raw": text}
        with LOCK:
            LOG["text"].append({"t": time.time(), "message": message})
        kind = message.get("type")
        if kind == "client_mode":
            mode = message.get("mode")
            if mode in {"prayer", "available", "quiet"} and mode != FLOOR["mode"]:
                previous, FLOOR["mode"] = FLOOR["mode"], mode
                self._send_json({"type": "mode", "mode": mode, "from": previous, "reason": f"client:{message.get('reason', 'request')}", "t": time.time()})
        elif kind == "client_text":
            threading.Thread(target=self._bot_turn, args=(str(message.get("text", "")),), daemon=True).start()
        elif kind == "client_passage":
            PANE["reading"] += 1  # a selection pauses any reading
            PANE["spoken"] = 0
            self._send_json({"type": "passage", "reference": "Psalm 23", "title": "Psalm 23 (World English Bible)", "edition": "World English Bible", "book": "PSA", "chapter": 23,
                             "verses": [{"verse": i + 1, "text": t, "osis": f"Ps.23.{i + 1}"} for i, t in enumerate(PSALM_23)], "spoken_count": 0, "source": "client"})
        elif kind == "client_read":
            if message.get("action") == "pause":
                PANE["reading"] += 1
                self._send_json({"type": "reading", "state": "paused", "reference": "Psalm 23", "spoken_count": PANE["spoken"]})
            else:
                PANE["reading"] += 1
                threading.Thread(target=self._read, args=(PANE["reading"],), daemon=True).start()

    def _read(self, token: int) -> None:
        """Reads the fixture psalm from where it stopped, one verse every 2.6 s, as the real server reports it."""
        start = PANE["spoken"] if 0 < PANE["spoken"] < len(PSALM_23) else 0
        self._send_json({"type": "reading", "state": "started", "reference": "Psalm 23", "from_index": start, "from_verse": start + 1, "spoken_count": start})
        rtvi = lambda kind: {"label": "rtvi-ai", "type": kind}  # noqa: E731
        for i in range(start, len(PSALM_23)):
            if PANE["reading"] != token:
                return
            self._send_json(rtvi("bot-started-speaking"))
            self._send_json({"type": "reading", "state": "verse", "reference": "Psalm 23", "verse": i + 1, "index": i, "spoken_count": i})
            time.sleep(2.6)
            if PANE["reading"] != token:
                return
            PANE["spoken"] = i + 1
            self._send_json({"type": "reading", "state": "verse_done", "reference": "Psalm 23", "verse": i + 1, "index": i, "spoken_count": i + 1, "duration_s": 2.6})
            self._send_json(rtvi("bot-stopped-speaking"))
        self._send_json({"type": "reading", "state": "finished", "reference": "Psalm 23", "spoken_count": len(PSALM_23)})

    def _bot_turn(self, heard: str) -> None:
        rtvi = lambda kind, data=None: {"label": "rtvi-ai", "type": kind, **({"data": data} if data is not None else {})}  # noqa: E731
        reply = "Fixture reply: He makes me lie down in green pastures."
        self._send_json(rtvi("user-transcription", {"text": heard, "user_id": "", "final": True}))
        if FLOOR["mode"] != "available":
            return
        self._send_json(rtvi("bot-started-speaking"))
        self._send_json(rtvi("bot-transcription", {"text": reply}))
        for frame in tone_frames():
            self._send(0x2, frame)
            time.sleep(0.02)
        self._send_json(rtvi("bot-tts-text", {"text": reply}))
        self._send_json(rtvi("bot-stopped-speaking"))
        with LOCK:
            LOG["bot_turns"] += 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7860)
    args = ap.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
