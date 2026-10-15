"""Replay the phone's own capture through the talk tier's VAD, aligned with a talk session's bot speech.

The phone records the same microphone buffers it streams to the talk tier (capture segments,
pulled with ``scripts/pull_device_capture.py``). Replaying them shows what the server's VAD would
have done with that audio: every VAD start is classified as ``silent`` (bot not speaking),
``during`` (inside a bot-speaking span) or ``verse-gap`` (within the hold after a span), for the
stock Silero analyzer and for ``talk.vad.DoubleTalkVADAnalyzer``. With ``--transcribe`` each start
during bot speech gets a Cohere transcript of the next 3 s of capture, so a reader can tell the
user's words from echo or noise.

    python tools/vad_replay.py --segments <dir of NNNNNNNN.wav> --manifest captures.json \
        --capture <capture id> --session ~/.local/state/voice-talk/sessions/<session>.jsonl \
        [--first 1047 --last 1055] [--conf 0.3 --rms 600] [--transcribe]
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import sys
import urllib.request
import uuid
import wave
from pathlib import Path

import numpy as np
import soxr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState

from talk.vad import DoubleTalkVADAnalyzer

APPLE_EPOCH = 978307200
COHERE = "http://127.0.0.1:28080/upstream/cohere-transcribe/v1/audio/transcriptions"


def load_capture(segments: Path, manifest: Path, capture: str, first: int | None, last: int | None) -> tuple[float, np.ndarray]:
    """Concatenate the selected segments at 16 kHz; returns (wall time of the first sample, int16 PCM)."""
    caps = json.loads(manifest.read_text())
    entry = next(c for c in caps if c["captureId"] == capture)
    starts = {s["index"]: s["startedAt"] + APPLE_EPOCH for s in entry["segments"]}
    present = sorted(int(p.stem) for p in segments.glob("*.wav") if int(p.stem) in starts and (first is None or int(p.stem) >= first) and (last is None or int(p.stem) <= last))
    parts = []
    for index in present:
        w = wave.open(str(segments / f"{index:08d}.wav"))
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
        parts.append(soxr.resample(pcm, w.getframerate(), 16000))
    return starts[present[0]], np.clip(np.concatenate(parts), -32768, 32767).astype(np.int16)


def bot_spans(session: Path) -> list[tuple[float, float]]:
    """Bot-speaking spans from the output transport's Bot{Started,Stopped}SpeakingFrame records."""
    spans, current = [], None
    for line in session.read_text().splitlines():
        r = json.loads(line)
        if not str(r.get("src", "")).endswith(tuple(f"OutputTransport#{i}" for i in range(100))) or r.get("dst") != "reading-tracker":
            continue
        if r["event"] == "BotStartedSpeakingFrame" and current is None:
            current = r["wall"]
        elif r["event"] == "BotStoppedSpeakingFrame" and current is not None:
            spans.append((current, r["wall"]))
            current = None
    return spans


def transcribe(pcm: np.ndarray) -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.tobytes())
    boundary = "----" + uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\ncohere-transcribe\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="response_format"\r\n\r\njson\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="c.wav"\r\nContent-Type: audio/wav\r\n\r\n'
    ).encode() + buf.getvalue() + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(COHERE, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return json.loads(urllib.request.urlopen(request, timeout=120).read()).get("text", "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--segments", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--capture", required=True)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--first", type=int)
    parser.add_argument("--last", type=int)
    parser.add_argument("--conf", type=float, default=0.3)
    parser.add_argument("--rms", type=float, default=600.0)
    parser.add_argument("--hold", type=float, default=1.0)
    parser.add_argument("--transcribe", action="store_true")
    args = parser.parse_args()

    t0, pcm = load_capture(args.segments, args.manifest, args.capture, args.first, args.last)
    spans = bot_spans(args.session)
    clock = [t0]

    def bot_speaking(t: float, hold: float = args.hold) -> bool:
        return any(a <= t <= b + hold for a, b in spans)

    def run(analyzer) -> list[float]:
        analyzer.set_sample_rate(16000)
        state, starts = VADState.QUIET, []
        for i in range(0, len(pcm) - 320, 320):
            clock[0] = t0 + i / 16000
            new = analyzer._run_analyzer(pcm[i : i + 320].tobytes())
            if new == VADState.SPEAKING and state != VADState.SPEAKING:
                starts.append(clock[0])
            state = new
        return starts

    fmt = lambda t: datetime.datetime.fromtimestamp(t, datetime.UTC).strftime("%H:%M:%S.%f")[:-4]
    lo, hi = t0, t0 + len(pcm) / 16000
    speech_s = sum(min(b, hi) - max(a, lo) for a, b in spans if b >= lo and a <= hi)
    print(f"capture {fmt(lo)} to {fmt(hi)} UTC; bot speech {speech_s:.0f}s in {sum(1 for a, b in spans if b >= lo and a <= hi)} spans")
    analyzers = {
        "stock Silero (16 kHz, 0.7)": SileroVADAnalyzer(params=VADParams(stop_secs=0.8)),
        f"double-talk (16+8 kHz; loud >= {args.rms:.0f} rms with score >= {args.conf} during bot speech)": DoubleTalkVADAnalyzer(
            params=VADParams(stop_secs=0.8), double_talk_min_confidence=args.conf, double_talk_min_rms=args.rms, bot_speaking=lambda: bot_speaking(clock[0])
        ),
    }
    for name, analyzer in analyzers.items():
        starts = run(analyzer)
        rows = []
        for t in starts:
            label = "during" if any(a <= t <= b for a, b in spans) else ("verse-gap" if bot_speaking(t) else "silent")
            text = ""
            if args.transcribe and label != "silent":
                i = int((t - t0) * 16000)
                text = transcribe(pcm[i : i + 3 * 16000])
            rows.append((t, label, text))
        print(f"\n{name}: {len(starts)} VAD starts; during bot speech {sum(1 for _, label, _ in rows if label == 'during')}")
        for t, label, text in rows:
            print(f"  {fmt(t)} {label:9s} {text[:90]!r}" if text else f"  {fmt(t)} {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
