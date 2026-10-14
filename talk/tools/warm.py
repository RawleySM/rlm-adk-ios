#!/usr/bin/env python
"""Warm the owners the talk tier depends on: Cohere (one short WAV) and the LLM (one short chat).

Both cold starts are long (about 105 s for Cohere, 3 to 4 minutes for Qwen). The systemd unit runs
this before the server and the benchmark runs it before each suite, so the first turn of a session
is not a cold start.
"""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from talk.config import load_config  # noqa: E402


def warm_cohere(cfg) -> dict:
    sr = 16000
    t = np.arange(int(sr * 1.2)) / sr
    tone = (0.05 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    pcm = (tone * 32767).astype(np.int16).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm)
    boundary = "----voicetalkwarm"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{cfg.stt_model}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"warm.wav\"\r\nContent-Type: audio/wav\r\n\r\n"
    ).encode() + buf.getvalue() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(cfg.stt_base_url.rstrip("/") + "/v1/audio/transcriptions", data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            text = resp.read().decode()[:120]
        return {"ok": True, "seconds": round(time.monotonic() - started, 1), "reply": text}
    except Exception as exc:
        return {"ok": False, "seconds": round(time.monotonic() - started, 1), "error": str(exc)[:200]}


def warm_llm(cfg) -> dict:
    payload = {"model": cfg.llm_model, "messages": [{"role": "user", "content": "Say ready."}], "max_tokens": 5, **cfg.llm_extra_body}
    req = urllib.request.Request(cfg.llm_base_url.rstrip("/") + "/chat/completions", data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {cfg.llm_api_key}")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            data = json.loads(resp.read().decode())
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return {"ok": True, "seconds": round(time.monotonic() - started, 1), "reply": text[:80]}
    except Exception as exc:
        return {"ok": False, "seconds": round(time.monotonic() - started, 1), "error": str(exc)[:200]}


if __name__ == "__main__":
    cfg = load_config()
    which = sys.argv[1:] or ["cohere", "llm"]
    out = {}
    if "cohere" in which:
        out["cohere"] = warm_cohere(cfg)
    if "llm" in which:
        out["llm"] = warm_llm(cfg)
    print(json.dumps(out))
    sys.exit(0 if all(v["ok"] for v in out.values()) else 1)
