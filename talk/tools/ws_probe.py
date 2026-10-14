#!/usr/bin/env python
"""Benchmark probe for the talk tier's tagged-PCM WebSocket: stream a WAV in real time, record
everything the bot sends back, and write a receipt with timestamps.

    .venv/bin/python tools/ws_probe.py --server http://127.0.0.1:7870 --wav fixture.wav --out run_dir \
        [--tail-seconds 20] [--client-mode prayer]

The receipt holds every JSON message from the bot (mode changes, prayer_text, RTVI transcripts and
speaking state) with wall-clock and stream-relative times, and ``bot.wav`` (24 kHz mono) with the
stream-relative time of its first sample above the silence floor. Those are the timing endpoints the
acceptance matrix defines; the room and phone loops reuse the same receipt shape.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import aiohttp
import numpy as np
import soundfile as sf
import soxr

CHUNK_MS = 20


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:7860")
    ap.add_argument("--wav", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tail-seconds", type=float, default=20.0)
    ap.add_argument("--in-rate", type=int, default=16000)
    ap.add_argument("--out-rate", type=int, default=24000)
    ap.add_argument("--client-mode", default=None, help="send {'type':'client_mode','mode':...} at start")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    data, sr = sf.read(args.wav, dtype="float32")
    if data.ndim > 1:
        data = data[:, 0]
    pcm = soxr.resample(data, sr, args.in_rate) if sr != args.in_rate else data
    pcm16 = (np.clip(pcm, -1, 1) * 32767).astype(np.int16)
    chunk = args.in_rate * CHUNK_MS // 1000

    async with aiohttp.ClientSession() as session:
        async with session.post(args.server.rstrip("/") + "/start", json={"transport": "websocket"}) as response:
            start = await response.json()
        # The runner advertises its own idea of host/scheme; keep only the path and token.
        from urllib.parse import urlsplit

        advertised = start["wsUrl"]
        path = urlsplit(advertised).path if "://" in advertised else advertised
        base = urlsplit(args.server)
        ws_url = f"{'wss' if base.scheme == 'https' else 'ws'}://{base.netloc}{path}"
        events: list[dict] = []
        bot_audio = bytearray()
        bot_chunks: list[tuple[float, int]] = []
        t0 = None

        async with session.ws_connect(ws_url, max_msg_size=0) as ws:
            t0 = time.monotonic()
            events.append({"t": 0.0, "wall": time.time(), "event": "connected", "ws_url": ws_url})
            if args.client_mode:
                await ws.send_str(json.dumps({"type": "client_mode", "mode": args.client_mode, "reason": "probe"}))

            async def receiver():
                async for msg in ws:
                    rel = time.monotonic() - t0
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        if msg.data[:1] == b"\x01":
                            bot_audio.extend(msg.data[1:])
                            bot_chunks.append((rel, len(msg.data) - 1))
                    elif msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            payload = json.loads(msg.data)
                        except json.JSONDecodeError:
                            payload = {"raw": msg.data}
                        events.append({"t": rel, "wall": time.time(), "message": payload})
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                        break

            recv = asyncio.create_task(receiver())
            # Real-time playout of the fixture: one 20 ms chunk per 20 ms.
            next_at = time.monotonic()
            for i in range(0, len(pcm16), chunk):
                await ws.send_bytes(b"\x01" + pcm16[i : i + chunk].tobytes())
                next_at += CHUNK_MS / 1000
                delay = next_at - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
            fixture_end = time.monotonic() - t0
            events.append({"t": fixture_end, "wall": time.time(), "event": "fixture_end"})
            # Keep the line open (silence) for the tail so the bot can answer.
            silence = b"\x01" + bytes(chunk * 2)
            tail_until = time.monotonic() + args.tail_seconds
            while time.monotonic() < tail_until:
                await ws.send_bytes(silence)
                await asyncio.sleep(CHUNK_MS / 1000)
            await ws.close()
            await recv

    # Bot audio to WAV and first-audio onset (first chunk whose RMS exceeds a floor).
    audio = np.frombuffer(bytes(bot_audio), dtype=np.int16)
    onset = None
    if len(audio):
        sf.write(out / "bot.wav", audio, args.out_rate)
        # Map sample positions back to arrival times by walking the chunk log.
        pos = 0
        for rel, nbytes in bot_chunks:
            n = nbytes // 2
            seg = audio[pos : pos + n]
            pos += n
            if len(seg) and np.sqrt(np.mean(seg.astype(np.float32) ** 2)) > 300:
                onset = rel
                break
    receipt = {
        "server": args.server,
        "wav": str(Path(args.wav).resolve()),
        "fixture_seconds": len(pcm16) / args.in_rate,
        "bot_audio_seconds": len(audio) / args.out_rate,
        "bot_first_audio_t": onset,
        "events": events,
    }
    (out / "probe-receipt.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps({k: receipt[k] for k in ("fixture_seconds", "bot_audio_seconds", "bot_first_audio_t")}))
    for e in events:
        if "message" in e:
            m = e["message"]
            kind = m.get("type") or m.get("label")
            if kind in {"mode", "prayer_text", "interruption"} or (m.get("label") == "rtvi-ai" and m.get("type") in {"user-transcription", "bot-transcription", "bot-started-speaking", "bot-stopped-speaking", "bot-llm-text"}):
                print(f"{e['t']:7.2f}s {json.dumps(m)[:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
