"""Streaming chat completions against the local OpenAI-compatible inference owner (stdlib only)."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator


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
        except Exception as exc:
            out["errors"].append(f"status: {exc}")
    out["ready"] = bool(out["advertised"]) and (out["admission"] is None or bool(out["admission"].get("available")))
    return out


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
) -> Iterator[dict]:
    """Yield {"delta": str} items then one {"done": True, "usage": ..., "finish_reason": ...}.

    Cancellation closes the upstream connection so the owner can settle the request on its side.
    """
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
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:2000]
        raise ReasoningError(f"inference owner returned HTTP {exc.code}", status=exc.code, body=body) from exc
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        raise ReasoningError(f"inference owner unreachable: {exc}") from exc
    usage = None
    finish_reason = None
    reasoning_chars = 0
    try:
        buffer = b""
        while True:
            if cancel is not None and cancel.is_set():
                yield {"done": True, "cancelled": True, "usage": usage, "finish_reason": "cancelled", "reasoning_chars": reasoning_chars}
                return
            chunk = resp.readline()
            if not chunk:
                break
            buffer += chunk
            if not chunk.endswith(b"\n"):
                continue
            line = buffer.decode("utf-8", "replace").strip()
            buffer = b""
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
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
                # Thinking models stream `reasoning_content` first; it is never spoken or shown as the answer.
                reasoning = delta_obj.get("reasoning_content") or delta_obj.get("reasoning")
                if reasoning:
                    reasoning_chars += len(reasoning)
                delta = delta_obj.get("content")
                if delta:
                    yield {"delta": delta}
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
    except (socket.timeout, OSError) as exc:
        raise ReasoningError(f"stream interrupted: {exc}") from exc
    finally:
        try:
            resp.close()
        except Exception:
            pass
    yield {"done": True, "cancelled": False, "usage": usage, "finish_reason": finish_reason, "reasoning_chars": reasoning_chars}


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
