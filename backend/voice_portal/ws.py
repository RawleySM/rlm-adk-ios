"""Minimal RFC 6455 server-side WebSocket over an accepted socket (text/JSON frames, ping/pong, close)."""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketClosed(Exception):
    pass


def accept_key(client_key: str) -> str:
    return base64.b64encode(hashlib.sha1((client_key + GUID).encode("ascii")).digest()).decode("ascii")


class WebSocket:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._send_lock = threading.Lock()
        self.closed = False

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise WebSocketClosed()
            buf += chunk
        return bytes(buf)

    def _read_frame(self) -> tuple[int, bool, bytes]:
        head = self._recv_exact(2)
        fin = bool(head[0] & 0x80)
        opcode = head[0] & 0x0F
        masked = bool(head[1] & 0x80)
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else None
        payload = self._recv_exact(length) if length else b""
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, fin, payload

    def receive(self) -> str | bytes | None:
        """Return the next text (str) or binary (bytes) message; None after close."""
        message = bytearray()
        message_opcode = None
        while True:
            opcode, fin, payload = self._read_frame()
            if opcode == 0x8:
                self.close()
                return None
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2):
                message_opcode = opcode
                message = bytearray(payload)
            elif opcode == 0x0:
                message += payload
            if fin:
                data = bytes(message)
                return data.decode("utf-8") if message_opcode == 0x1 else data

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(n)
        elif n < 65536:
            header.append(126)
            header += struct.pack("!H", n)
        else:
            header.append(127)
            header += struct.pack("!Q", n)
        with self._send_lock:
            if self.closed:
                raise WebSocketClosed()
            self.sock.sendall(bytes(header) + payload)

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def send_json(self, obj: dict) -> None:
        self.send_text(json.dumps(obj, ensure_ascii=False))

    def close(self, code: int = 1000) -> None:
        if self.closed:
            return
        try:
            self._send_frame(0x8, struct.pack("!H", code))
        except Exception:
            pass
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def client_handshake(sock: socket.socket, host: str, path: str) -> None:
    """Tiny client-side handshake for tests and the smoke command."""
    key = base64.b64encode(hashlib.sha1(str(id(sock)).encode()).digest()[:16]).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise WebSocketClosed("handshake failed")
        response += chunk
    status_line = response.split(b"\r\n", 1)[0]
    if b"101" not in status_line:
        raise WebSocketClosed(f"handshake rejected: {status_line!r}")


class ClientWebSocket(WebSocket):
    """Client side: frames must be masked."""

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        import os

        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", n)
        with self._send_lock:
            self.sock.sendall(bytes(header) + mask + masked)
