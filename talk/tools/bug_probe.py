#!/usr/bin/env python
"""Scripted check of the Bug chip against a running talk tier (tagged-PCM WebSocket) and skill service.

    .venv/bin/python tools/bug_probe.py --server http://127.0.0.1:7861 --skill http://127.0.0.1:18766 --out run_dir

Run it against a talk tier whose ``skill_base_url`` is an isolated skill service (its own state root),
so probe tickets never enter the production intake issues log.

Scenario: open Romans 12 and tap Read; after two verses tap Bug (the reading must pause and the floor
enter ``bug``); speak a bug description and then words that would otherwise act ("Quiet please. Read
it again from the beginning.") as synthesized 16 kHz speech; type one line; check that every line
comes back as ``bug_text``, that no phrase acted and that no bot audio or LLM output arrived; tap Bug
again (the report must be filed, the floor return to available and the reading resume at the verse
after the last one heard); wait for the triaged message; read the ticket back from the skill service.
Then open and cancel a second report and check no ticket was added. Writes ``receipt.json`` and
``checks.json``; exits 1 on a failed check.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import aiohttp
import numpy as np

SPEECH = [
    "When Romans twelve is read aloud, the highlighted verse falls behind the voice. By verse five it is two verses behind.",
    "Quiet please. Read it again from the beginning.",
]
TYPED = "It happens every time on the phone over Bluetooth."


def synthesize(texts: list[str], voice: str) -> list[np.ndarray]:
    """16 kHz int16 speech for each text (Kokoro on CPU, resampled with soxr)."""
    import soxr
    from kokoro_onnx import Kokoro

    home = Path.home() / ".cache/pipecat/kokoro-onnx"
    kokoro = Kokoro(str(home / "kokoro-v1.0.onnx"), str(home / "voices-v1.0.bin"))
    out = []
    for text in texts:
        samples, rate = kokoro.create(text, voice=voice, speed=1.0, lang="en-us")
        audio = soxr.resample(samples.astype(np.float32), rate, 16000)
        out.append((np.clip(audio, -1, 1) * 32767 * 0.8).astype(np.int16))
    return out


class Probe:
    def __init__(self, ws):
        self.ws = ws
        self.t0 = time.time()
        self.messages: list[dict] = []
        self.binary: list[float] = []
        self.event = asyncio.Event()
        self.speaking = False

    def now(self) -> float:
        return round(time.time() - self.t0, 3)

    async def receiver(self):
        async for msg in self.ws:
            if msg.type == aiohttp.WSMsgType.BINARY:
                self.binary.append(self.now())
                continue
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            data = json.loads(msg.data)
            if data.get("label") == "rtvi-ai" and data.get("type") in ("server-message", "metrics"):
                continue
            self.messages.append({"t": self.now(), "message": data})
            self.event.set()

    async def send(self, message: dict):
        self.messages.append({"t": self.now(), "sent": message})
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

    def received(self, since_t: float, until_t: float | None = None) -> list[dict]:
        return [e["message"] for e in self.messages if "message" in e and e["t"] >= since_t and (until_t is None or e["t"] <= until_t)]


def is_type(kind: str, **fields):
    return lambda m: m.get("type") == kind and all(m.get(k) == v for k, v in fields.items())


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:7861")
    ap.add_argument("--skill", required=True, help="the skill service the talk tier files to (read back the ticket)")
    ap.add_argument("--voice", default="am_adam")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []

    def check(name: str, ok: bool, **detail):
        checks.append({"name": name, "ok": bool(ok), **detail})
        print(("PASS " if ok else "FAIL ") + name + (f" {detail}" if detail else ""), flush=True)

    speech = synthesize(SPEECH, args.voice)
    print(f"speech: {[round(len(s) / 16000, 2) for s in speech]} s", flush=True)

    async with aiohttp.ClientSession() as session:
        async with session.get(args.skill.rstrip("/") + "/issues?limit=500") as response:
            before = (await response.json())["issues"]
        async with session.post(args.server.rstrip("/") + "/start", json={"transport": "websocket"}) as response:
            start = await response.json()
        path = "/" + start["wsUrl"].split("://", 1)[1].split("/", 1)[1]
        ws_url = args.server.replace("http", "ws", 1).rstrip("/") + path
        async with session.ws_connect(ws_url, max_msg_size=0) as ws:
            probe = Probe(ws)
            recv = asyncio.create_task(probe.receiver())
            queue: list[np.ndarray] = []

            async def uplink():
                # Silence, or the queued speech, 20 ms per frame in real time (the phone's cadence).
                silence = b"\x01" + bytes(640)
                while True:
                    if queue:
                        audio = queue.pop(0)
                        for i in range(0, len(audio), 320):
                            chunk = audio[i : i + 320]
                            if len(chunk) < 320:
                                chunk = np.pad(chunk, (0, 320 - len(chunk)))
                            await ws.send_bytes(b"\x01" + chunk.tobytes())
                            await asyncio.sleep(0.02)
                    else:
                        await ws.send_bytes(silence)
                        await asyncio.sleep(0.02)

            pump = asyncio.create_task(uplink())

            async def speak(audio: np.ndarray) -> None:
                queue.append(audio)
                await asyncio.sleep(len(audio) / 16000 + 0.3)

            await probe.send({"type": "client_event", "event": "benchmark_client"})
            await asyncio.sleep(1.0)

            # 1. Romans 12 open and being read.
            mark = len(probe.messages)
            await probe.send({"type": "client_passage", "reference": "Romans 12", "reason": "select"})
            _, p = await probe.wait_for(is_type("passage"), 20, mark)
            check("romans12_open", p is not None and p.get("reference") == "Romans 12", reference=p and p.get("reference"))
            mark = len(probe.messages)
            await probe.send({"type": "client_read", "action": "play"})
            _, v = await probe.wait_for(lambda m: m.get("type") == "reading" and m.get("state") == "verse" and (m.get("index") or 0) >= 2, 90, mark)
            check("reading_reached_verse_3", v is not None, verse=v and v.get("verse"))

            # 2. Tap Bug mid-reading.
            mark = len(probe.messages)
            bug_t = probe.now()
            await probe.send({"type": "client_bug", "action": "start", "client": "bug_probe", "app_build": "probe", "transport": "websocket"})
            _, started = await probe.wait_for(is_type("bug_report", state="started"), 10, mark)
            _, mode = await probe.wait_for(is_type("mode", mode="bug"), 10, mark)
            _, paused = await probe.wait_for(is_type("reading", state="paused"), 10, mark)
            check("bug_started", started is not None and mode is not None, report_id=started and started.get("report_id"))
            check("reading_paused_by_bug", paused is not None, spoken_count=paused and paused.get("spoken_count"))
            heard_before = paused.get("spoken_count") if paused else None
            await asyncio.sleep(1.0)
            quiet_from = probe.now()

            # 3. Speak the report, including words that would otherwise act; type one line.
            for audio in speech:
                await speak(audio)
                await asyncio.sleep(1.5)
            await probe.send({"type": "client_text", "text": TYPED})
            n_texts = lambda: [m for m in probe.received(bug_t) if m.get("type") == "bug_text" and m.get("final")]  # noqa: E731
            deadline = time.time() + 20
            while time.time() < deadline and len(n_texts()) < 3:
                await asyncio.sleep(0.2)
            finals = n_texts()
            voiced = " ".join(m["text"] for m in finals if m.get("source") == "voice").lower()
            check("speech_transcribed_into_report", "verse" in voiced and "behind" in voiced, voiced=voiced[:240])
            check("phrases_inert_while_reporting", "quiet" in voiced and not [m for m in probe.received(bug_t) if m.get("type") == "mode" and m.get("mode") != "bug"] and not [m for m in probe.received(quiet_from) if m.get("type") == "reading" and m.get("state") == "started"],
                  modes=[m.get("mode") for m in probe.received(bug_t) if m.get("type") == "mode"])
            check("typed_line_in_report", any(m.get("source") == "typed" and m.get("text") == TYPED for m in finals))
            quiet_until = probe.now()
            bot_audio = [t for t in probe.binary if quiet_from <= t <= quiet_until]
            bot_events = [m for m in probe.received(quiet_from, quiet_until) if m.get("label") == "rtvi-ai" and m.get("type") in ("bot-started-speaking", "bot-output", "bot-llm-text")]
            check("no_bot_output_while_reporting", not bot_audio and not bot_events, audio_frames=len(bot_audio), events=[m.get("type") for m in bot_events][:5], window_s=round(quiet_until - quiet_from, 1))

            # 4. Tap Bug again: filed, back to available, reading resumes where it stopped.
            mark = len(probe.messages)
            await probe.send({"type": "client_bug", "action": "stop", "device_segments": "when romans twelve is read aloud"})
            _, filed = await probe.wait_for(lambda m: m.get("type") == "bug_report" and m.get("state") in ("filed", "spooled"), 40, mark)
            check("report_filed", filed is not None and filed.get("state") == "filed" and str(filed.get("ticket_id") or "").startswith("bug-"), filed=filed)
            _, back = await probe.wait_for(is_type("mode", mode="available"), 10, mark)
            check("floor_back_to_available", back is not None)
            _, resumed = await probe.wait_for(is_type("reading", state="started"), 20, mark)
            check("reading_resumed_where_it_stopped", resumed is not None and resumed.get("from_index") == heard_before, from_index=resumed and resumed.get("from_index"), heard_before=heard_before)
            _, triaged = await probe.wait_for(is_type("bug_report", state="triaged"), 240, mark)
            check("triage_reported_to_phone", triaged is not None, triaged=triaged)
            await probe.send({"type": "client_read", "action": "pause"})

            ticket = None
            if filed and filed.get("ticket_id"):
                async with session.get(args.skill.rstrip("/") + f"/issues/{filed['ticket_id']}") as response:
                    ticket = await response.json()
                (out / "ticket.json").write_text(json.dumps(ticket, indent=2), encoding="utf-8")
            text = (ticket or {}).get("text", "").lower()
            context = (ticket or {}).get("context") or {}
            triage = (ticket or {}).get("triage") or {}
            check("ticket_holds_the_words", "behind" in text and TYPED.lower() in text, text=text[:300])
            check("ticket_holds_the_context", (context.get("passage") or {}).get("reference") == "Romans 12" and (context.get("passage") or {}).get("reading") is True and (ticket or {}).get("source", {}).get("client") == "bug_probe", passage=context.get("passage"))
            check("ticket_triaged", triage.get("state") == "done" and triage.get("disposition") in ("ready", "needs_detail", "request", "no_report") and triage.get("priority") in ("P1", "P2", "P3", "P4"),
                  triage={k: triage.get(k) for k in ("source", "kind", "component", "severity", "priority", "disposition", "title")})

            # 5. A cancelled report files nothing.
            await asyncio.sleep(1.0)
            mark = len(probe.messages)
            await probe.send({"type": "client_bug", "action": "start", "client": "bug_probe"})
            await probe.wait_for(is_type("bug_report", state="started"), 10, mark)
            await probe.send({"type": "client_text", "text": "Never mind."})
            await asyncio.sleep(1.0)
            await probe.send({"type": "client_bug", "action": "cancel"})
            _, cancelled = await probe.wait_for(is_type("bug_report", state="cancelled"), 10, mark)
            await asyncio.sleep(2.0)
            async with session.get(args.skill.rstrip("/") + "/issues?limit=500") as response:
                after = (await response.json())["issues"]
            check("cancel_files_nothing", cancelled is not None and len(after) == len(before) + 1, before=len(before), after=len(after))

            pump.cancel()
            recv.cancel()
    (out / "receipt.json").write_text(json.dumps({"messages": probe.messages, "binary_frames": len(probe.binary)}, indent=2), encoding="utf-8")
    (out / "checks.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    failed = [c["name"] for c in checks if not c["ok"]]
    print(f"{len(checks) - len(failed)}/{len(checks)} checks passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
