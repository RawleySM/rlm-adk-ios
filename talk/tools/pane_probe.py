#!/usr/bin/env python
"""Scripted check of the scripture pane protocol against a running talk tier (tagged-PCM WebSocket).

    .venv/bin/python tools/pane_probe.py --server http://127.0.0.1:7860 --out run_dir

Scenario: open John 1 from the pane; ask what the chapter is about; tap Read; after three verses
open Romans 12 (the reading must pause); ask what verse 2 says; type "Please read it." (the model
must read the pane's passage from where it stopped); tap Pause after two verses, then Read (it must resume at the next
verse). Writes ``receipt.json`` (every message with times) and ``checks.json``; exits 1 on a failed
check.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import aiohttp


class Probe:
    def __init__(self, ws):
        self.ws = ws
        self.t0 = time.time()
        self.messages: list[dict] = []
        self.event = asyncio.Event()

    async def receiver(self):
        async for msg in self.ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("label") == "rtvi-ai" and data.get("type") == "server-message":
                    continue  # the same floor messages arrive bare; keep one copy
                if data.get("label") == "rtvi-ai" and data.get("type") == "metrics":
                    continue
                self.messages.append({"t": round(time.time() - self.t0, 3), "message": data})
                self.event.set()

    async def send(self, message: dict):
        self.messages.append({"t": round(time.time() - self.t0, 3), "sent": message})
        await self.ws.send_str(json.dumps(message))

    async def wait_for(self, predicate, timeout: float, since: int = 0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for i, entry in enumerate(self.messages[since:], start=since):
                if "message" in entry and predicate(entry["message"]):
                    return i, entry["message"]
            self.event.clear()
            try:
                await asyncio.wait_for(self.event.wait(), timeout=max(0.05, deadline - time.time()))
            except asyncio.TimeoutError:
                break
        return None, None

    def bot_text(self, since: int, until: int | None = None) -> str:
        parts = []
        for entry in self.messages[since:until]:
            m = entry.get("message") or {}
            if m.get("label") == "rtvi-ai" and m.get("type") == "bot-output":
                d = m.get("data") or {}
                if d.get("spoken_status") in (None, "new") and d.get("will_be_spoken", True):
                    parts.append(d.get("text", ""))
        return " ".join(parts)


def reading(state: str, reference: str | None = None):
    return lambda m: m.get("type") == "reading" and m.get("state") == state and (reference is None or m.get("reference") == reference)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:7860")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []

    def check(name: str, ok: bool, **detail):
        checks.append({"name": name, "ok": bool(ok), **detail})
        print(("PASS " if ok else "FAIL ") + name + (f" {detail}" if detail and not ok else ""))

    async with aiohttp.ClientSession() as session:
        async with session.post(args.server.rstrip("/") + "/start", json={"transport": "websocket"}) as response:
            start = await response.json()
        path = "/" + start["wsUrl"].split("://", 1)[1].split("/", 1)[1]
        ws_url = args.server.replace("http", "ws", 1).rstrip("/") + path
        async with session.ws_connect(ws_url, max_msg_size=0) as ws:
            probe = Probe(ws)
            recv = asyncio.create_task(probe.receiver())

            async def silence():
                frame = b"\x01" + bytes(640)
                while True:
                    await ws.send_bytes(frame)
                    await asyncio.sleep(0.02)

            pump = asyncio.create_task(silence())
            await probe.send({"type": "client_event", "event": "benchmark_client"})
            await asyncio.sleep(1.0)

            # 1. Open John 1 from the pane.
            mark = len(probe.messages)
            await probe.send({"type": "client_passage", "reference": "John 1", "reason": "select"})
            _, p = await probe.wait_for(lambda m: m.get("type") == "passage", 15, mark)
            check("john1_passage_event", p is not None and p.get("reference") == "John 1" and len(p.get("verses") or []) >= 50, reference=p and p.get("reference"))

            # 2. Ask about it.
            mark = len(probe.messages)
            await probe.send({"type": "client_text", "text": "What is this chapter about?"})
            i, _ = await probe.wait_for(lambda m: m.get("label") == "rtvi-ai" and m.get("type") == "bot-stopped-speaking", 60, mark)
            await asyncio.sleep(2.0)
            answer = probe.bot_text(mark)
            check("john1_answer_about_pane", any(w in answer.lower() for w in ("word", "light", "john the baptist", "baptist", "beginning")), answer=answer[:300])

            # 3. Read, then open Romans 12 after three verses.
            mark = len(probe.messages)
            await probe.send({"type": "client_read", "action": "play"})
            _, started = await probe.wait_for(reading("started", "John 1"), 10, mark)
            check("john1_reading_started", started is not None)
            _, third = await probe.wait_for(lambda m: m.get("type") == "reading" and m.get("state") == "verse_done" and m.get("verse") == 3, 90, mark)
            check("john1_verse3_done", third is not None)
            mark = len(probe.messages)
            await probe.send({"type": "client_passage", "reference": "Romans 12", "reason": "select"})
            pi, paused = await probe.wait_for(reading("paused", "John 1"), 10, mark)
            _, rom = await probe.wait_for(lambda m: m.get("type") == "passage" and m.get("reference") == "Romans 12", 15, mark)
            check("selection_paused_reading", paused is not None, paused=paused)
            check("romans12_passage_event", rom is not None and rom.get("spoken_count") == 0)
            await asyncio.sleep(4.0)
            late = [e for e in probe.messages[(pi or mark) + 1 :] if (e.get("message") or {}).get("type") == "reading" and (e.get("message") or {}).get("reference") == "John 1"]
            check("no_john_verses_after_pause", not late, late=late[:2])

            # 4. Ask about the new passage.
            mark = len(probe.messages)
            await probe.send({"type": "client_text", "text": "What does verse 2 say?"})
            await probe.wait_for(lambda m: m.get("label") == "rtvi-ai" and m.get("type") == "bot-stopped-speaking", 60, mark)
            await asyncio.sleep(2.0)
            answer = probe.bot_text(mark)
            read_v2 = any((e.get("message") or {}).get("type") == "reading" and (e.get("message") or {}).get("state") == "verse_done" and (e.get("message") or {}).get("verse") == 2 and (e.get("message") or {}).get("reference") == "Romans 12" for e in probe.messages[mark:])
            check("romans12_answer_about_pane", read_v2 or any(w in answer.lower() for w in ("conformed", "transformed", "renewing", "mind")), answer=answer[:300], read_verse_2=read_v2)
            _, still = await probe.wait_for(lambda m: m.get("type") == "passage", 0.1, mark)
            check("pane_keeps_the_chapter", still is None or still.get("reference") == "Romans 12", passage=still and still.get("reference"))

            # 5. Typed "read it": the model must read the pane's passage from its start.
            mark = len(probe.messages)
            await probe.send({"type": "client_text", "text": "Please read it."})
            _, started = await probe.wait_for(reading("started", "Romans 12"), 40, mark)
            check("typed_read_it_reads_pane_passage", started is not None, started=started)
            first_verse = (started or {}).get("from_verse") or 1
            _, second = await probe.wait_for(lambda m: m.get("type") == "reading" and m.get("state") == "verse_done" and m.get("reference") == "Romans 12" and m.get("verse") == first_verse + 1, 90, mark)
            check("romans12_two_verses_done", second is not None, duration=second and second.get("duration_s"))

            # 6. Pause, then Read resumes at the next verse.
            mark = len(probe.messages)
            await probe.send({"type": "client_read", "action": "pause"})
            _, paused = await probe.wait_for(reading("paused", "Romans 12"), 10, mark)
            check("pause_button_pauses", paused is not None and (paused or {}).get("spoken_count", 0) >= first_verse + 1, paused=paused)
            count = (paused or {}).get("spoken_count", 0)
            await asyncio.sleep(1.0)
            mark = len(probe.messages)
            await probe.send({"type": "client_read", "action": "play"})
            _, resumed = await probe.wait_for(reading("started", "Romans 12"), 10, mark)
            check("read_button_resumes_next_verse", resumed is not None and resumed.get("from_index") == count, resumed=resumed, expected_index=count)
            _, verse = await probe.wait_for(reading("verse", "Romans 12"), 20, mark)
            check("resumed_verse_event", verse is not None and verse.get("index") == count, verse=verse)
            await probe.send({"type": "client_read", "action": "pause"})
            await asyncio.sleep(2.0)

            pump.cancel()
            recv.cancel()
    (out / "receipt.json").write_text(json.dumps(probe.messages, indent=1))
    (out / "checks.json").write_text(json.dumps(checks, indent=1))
    passed = sum(c["ok"] for c in checks)
    print(f"{passed}/{len(checks)} checks passed; {out}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
