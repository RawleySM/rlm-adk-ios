"""Streaming chat completions against the local OpenAI-compatible inference owner (stdlib only).

The request runs on a reader thread that owns the socket, so a cancellation requested while the
owner is still cold-starting (no response headers yet) or between SSE lines closes the upstream
connection immediately; the owner sees the disconnect and settles the request on its side. The
generator never blocks on the socket itself: it waits on a queue in short ticks and checks `cancel`.
"""

from __future__ import annotations

import http.client
import json
import queue
import socket
import threading
import time
from collections.abc import Iterator
from urllib.parse import urlsplit
import urllib.request


class ReasoningError(Exception):
    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


def readiness(base_url: str, status_url: str, model: str, timeout: float = 5.0) -> dict:
    """Read-only readiness: advertised models and the owner's admission verdict for `model`."""
    out: dict = {"base_url": base_url, "model": model, "advertised": False, "admission": None, "phase": None, "errors": []}
    try:
        with urllib.request.urlopen(f"{base_url}/models", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ids = [m.get("id") for m in data.get("data", [])]
        out["models"] = ids
        out["advertised"] = model in ids
    except Exception as exc:  # unreachable owner is a reportable condition, not a crash
        out["errors"].append(f"models: {exc}")
    if status_url:
        try:
            with urllib.request.urlopen(status_url, timeout=timeout) as resp:
                status = json.loads(resp.read().decode("utf-8"))
            out["phase"] = status.get("phase")
            out["active_model"] = status.get("active_model")
            out["host_id"] = status.get("host_id")
            out["memory_available_gib"] = (status.get("memory") or {}).get("available_gib")
            admission = (status.get("model_admission") or {}).get(model)
            out["admission"] = admission
            model_status = (status.get("model_status") or {}).get(model)
            out["model_phase"] = (model_status or {}).get("phase")
            out["inflight_count"] = status.get("inflight_count")
            out["queued"] = status.get("queued")
        except Exception as exc:
            out["errors"].append(f"status: {exc}")
    out["ready"] = bool(out["advertised"]) and (out["admission"] is None or bool(out["admission"].get("available")))
    return out


class _UpstreamStream:
    """One streaming POST on its own reader thread.

    The thread owns the connection; `close()` may be called from any thread and must leave no live
    request behind:
    - the raw socket is captured right after connect (http.client drops `conn.sock` for
      close-delimited / `Connection: close` responses while the HTTPResponse still holds it), so
      shutdown reaches the real socket whatever the response framing;
    - a cancel that lands while the connect is still in progress is honoured after the connect
      returns, before any request bytes are written (the request is never sent);
    - `close()` joins the reader thread (bounded) and reports whether it actually finished.
    """

    def __init__(self, base_url: str, path: str, body: bytes, timeout: float, connect_hook=None):
        parts = urlsplit(base_url)
        self.host = parts.hostname or "127.0.0.1"
        self.port = parts.port or (443 if parts.scheme == "https" else 80)
        self.path = (parts.path.rstrip("/") + path) or path
        self.body = body
        self.timeout = timeout
        self.conn = http.client.HTTPSConnection(self.host, self.port, timeout=timeout) if parts.scheme == "https" else http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        self.events: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.closed = False
        self.sock: socket.socket | None = None
        self.resp = None
        self.sent = False
        self.connect_hook = connect_hook  # test seam: runs inside the connect window
        self.thread = threading.Thread(target=self._run, name="reasoning-upstream", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        try:
            with self.lock:
                if self.closed:
                    return
            if self.connect_hook:
                self.connect_hook()
            self.conn.connect()
            with self.lock:
                self.sock = self.conn.sock
                if self.closed:
                    self._shutdown_locked()
                    return  # cancelled during connect: nothing is sent
                self.conn.request("POST", self.path, body=self.body, headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
                self.sent = True
            resp = self.conn.getresponse()
            with self.lock:
                self.resp = resp
                if self.closed:
                    self._shutdown_locked()
                    return
            if resp.status >= 400:
                raw = resp.read().decode("utf-8", "replace")[:2000]
                self.events.put(("error", resp.status, raw))
                return
            self.events.put(("headers", resp.status, None))
            while True:
                line = resp.readline()
                if not line:
                    break
                self.events.put(("line", line, None))
        except Exception as exc:  # socket closed by cancel, timeout, reset, protocol error
            self.events.put(("exc", exc, self.closed))
        finally:
            self.events.put(("eof", None, None))
            with self.lock:
                self._shutdown_locked()

    def _shutdown_locked(self) -> None:
        # Socket first: that is what unblocks a reader parked in readline(). Closing the buffered
        # HTTPResponse before the socket would wait on the reader's buffer lock instead.
        resp, sock = self.resp, self.sock
        self.resp = None
        self.sock = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        try:
            self.conn.close()
        except Exception:
            pass

    def close(self, join_timeout: float = 2.0) -> bool:
        """Tear the request down and wait (bounded) for the reader to finish. Returns True when it did."""
        with self.lock:
            self.closed = True
            self._shutdown_locked()
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(join_timeout)
        return not self.thread.is_alive()


def stream_chat(
    base_url: str,
    model: str,
    messages: list[dict],
    *,
    cancel: threading.Event | None = None,
    timeout: float = 600.0,
    max_tokens: int = 400,
    temperature: float = 0.3,
    extra_body: dict | None = None,
    tick_seconds: float = 0.1,
    _connect_hook=None,
) -> Iterator[dict]:
    """Yield {"delta": str} items then one {"done": True, "usage": ..., "finish_reason": ...}.

    - A cancel that is already set before the request starts sends nothing to the owner.
    - A cancel while connecting, waiting for headers, or between SSE lines closes the upstream socket
      (whatever the response framing) and settles within about one tick; the reader thread is joined
      (bounded) and the `done` item reports `upstream_settled`; `usage` stays None when the owner
      never sent any; a cancel during connect sends no request at all (`sent` False).
    - Precedence is by observation: once the generator has observed the cancel, no later delta,
      success or error is reported, even if that line was already queued. An owner error observed
      before the cancel is reported as an error.
    """
    if cancel is not None and cancel.is_set():
        yield {"done": True, "cancelled": True, "usage": None, "finish_reason": "cancelled", "reasoning_chars": 0, "sent": False}
        return
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream_options": {"include_usage": True},
    }
    if extra_body:
        payload.update(extra_body)
    stream = _UpstreamStream(base_url, "/chat/completions", json.dumps(payload).encode("utf-8"), timeout, connect_hook=_connect_hook)
    stream.start()
    usage = None
    finish_reason = None
    reasoning_chars = 0
    buffer = b""
    deadline = time.monotonic() + timeout
    def cancelled_item() -> dict:
        settled = stream.close()
        return {"done": True, "cancelled": True, "usage": usage, "finish_reason": "cancelled", "reasoning_chars": reasoning_chars, "sent": stream.sent, "upstream_settled": settled}

    try:
        while True:
            if cancel is not None and cancel.is_set():
                yield cancelled_item()
                return
            try:
                kind, a, b = stream.events.get(timeout=tick_seconds)
            except queue.Empty:
                if time.monotonic() > deadline:
                    stream.close()
                    raise ReasoningError(f"inference owner produced nothing within {timeout:.0f}s")
                continue
            # Observed precedence: a cancel that landed while we were waiting wins over whatever arrived.
            if cancel is not None and cancel.is_set():
                yield cancelled_item()
                return
            if kind == "headers":
                continue
            if kind == "error":
                raise ReasoningError(f"inference owner returned HTTP {a}", status=a, body=b)
            if kind == "exc":
                if isinstance(a, (ConnectionError, OSError, http.client.HTTPException)):
                    raise ReasoningError(f"inference owner unreachable or stream interrupted: {a}")
                raise ReasoningError(f"stream failed: {a}")
            if kind == "eof":
                break
            chunk = a
            buffer += chunk
            if not chunk.endswith(b"\n"):
                continue
            line = buffer.decode("utf-8", "replace").strip()
            buffer = b""
            if not line or line.startswith(":") or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if event.get("usage"):
                usage = event["usage"]
            for choice in event.get("choices") or []:
                delta_obj = choice.get("delta") or {}
                reasoning = delta_obj.get("reasoning_content") or delta_obj.get("reasoning")
                if reasoning:
                    reasoning_chars += len(reasoning)
                delta = delta_obj.get("content")
                if delta:
                    yield {"delta": delta}
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
    finally:
        stream.close()
    if cancel is not None and cancel.is_set():
        yield cancelled_item()
        return
    yield {"done": True, "cancelled": False, "usage": usage, "finish_reason": finish_reason, "reasoning_chars": reasoning_chars, "sent": True, "upstream_settled": True}


def probe_once(base_url: str, model: str, timeout: float = 600.0, extra_body: dict | None = None) -> dict:
    """A tiny streaming request used by the smoke command; returns timing and the reply."""
    started = time.monotonic()
    first = None
    text = []
    usage = None
    reasoning_chars = 0
    finish = None
    for item in stream_chat(base_url, model, [{"role": "user", "content": "Reply with the single word: ready"}], timeout=timeout, max_tokens=64, extra_body=extra_body):
        if "delta" in item:
            if first is None:
                first = time.monotonic() - started
            text.append(item["delta"])
        else:
            usage = item.get("usage")
            reasoning_chars = item.get("reasoning_chars", 0)
            finish = item.get("finish_reason")
    return {"first_delta_s": first, "total_s": time.monotonic() - started, "text": "".join(text), "usage": usage, "reasoning_chars": reasoning_chars, "finish_reason": finish}
