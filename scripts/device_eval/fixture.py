"""Playback excerpts derived from an older prayer recording (Google Drive original, never modified).

``derive_excerpt`` decodes ``[offset, offset+duration)`` of the source with ffmpeg to 16-bit mono
PCM at the requested rate with a conservative gain (default -6 dB, boosts refused unless allowed),
then assembles: lead silence, optional start marker, the excerpt untouched (pauses preserved),
optional end marker, tail silence. The provenance JSON records the source hash (checked against
``drive-provenance.json`` when given), the Drive id and URL, exact offsets, gain, marker positions
and the output hash. Any transcript found alongside is recorded as a *candidate* reference with
``verified: false``; nothing here claims a verbatim ground truth.
"""

from __future__ import annotations

import json
import math
import subprocess
from array import array
from pathlib import Path
from typing import Any

from .audio import WavError, read_mono, write_mono
from .receipt import sha256_file, utc_now

MAX_EXCERPT_S = 300.0
MAX_GAIN_DB = 24.0  # explicit calibration boosts are allowed up to here; the output must not clip
MARKER_FREQ_HZ = 1000.0
MARKER_BURST_S = 0.1
MARKER_GAP_S = 0.1
MARKER_BURSTS = 3
MARKER_LEVEL_DBFS = -20.0


class FixtureError(Exception):
    pass


def ffprobe(path: Path, run=subprocess.run) -> dict[str, Any]:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration,bit_rate,format_name:stream=codec_name,sample_rate,channels", "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise FixtureError(f"ffprobe failed: {out.stderr[-300:]}")
    return json.loads(out.stdout)


def decode_excerpt(source: Path, out_wav: Path, *, offset_s: float, duration_s: float, gain_db: float, rate: int, run=subprocess.run) -> list[str]:
    argv = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{offset_s:.3f}", "-t", f"{duration_s:.3f}", "-i", str(source),
        "-af", f"volume={gain_db:.2f}dB", "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", "-f", "wav", str(out_wav),
    ]
    out = run(argv, capture_output=True, text=True, timeout=max(120, int(duration_s * 4)))
    if out.returncode != 0:
        raise FixtureError(f"ffmpeg failed: {out.stderr[-400:]}")
    return argv


def marker(rate: int) -> array:
    """Three short 1 kHz bursts at -20 dBFS with gaps; speech-band, short, easy to see in a room
    recording. Apple voice processing may still attenuate it, which is why it is optional."""
    amp = 32767 * (10 ** (MARKER_LEVEL_DBFS / 20))
    burst_n = int(rate * MARKER_BURST_S)
    gap_n = int(rate * MARKER_GAP_S)
    out = array("h")
    for b in range(MARKER_BURSTS):
        for i in range(burst_n):
            fade = min(1.0, i / (rate * 0.005), (burst_n - i) / (rate * 0.005))
            out.append(int(amp * fade * math.sin(2 * math.pi * MARKER_FREQ_HZ * i / rate)))
        if b < MARKER_BURSTS - 1:
            out.extend(array("h", [0] * gap_n))
    return out


def silence(rate: int, seconds: float) -> array:
    return array("h", [0] * int(rate * seconds))


def candidate_transcripts(paths: list[Path]) -> list[dict[str, Any]]:
    """Transcript JSON files next to the source: recorded as candidates, never as verified truth."""
    out = []
    for p in paths:
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError) as exc:
            out.append({"path": str(p), "error": str(exc)})
            continue
        text = data.get("corrected_text") or data.get("full_text") or ""
        out.append({
            "path": str(p),
            "sha256": sha256_file(p),
            "model_id": data.get("model_id") or data.get("llm_model_id"),
            "has_timestamps": bool(data.get("segments")),
            "words": len(text.split()),
            "verified": False,
            "note": "candidate ASR text; excerpt-specific reference must be checked by a person before WER",
        })
    return out


def derive_excerpt(source: Path, out_wav: Path, *, offset_s: float, duration_s: float, gain_db: float = -6.0, rate: int = 48000, lead_s: float = 1.0, tail_s: float = 1.0, markers: bool = False, provenance_json: Path | None = None, transcripts: list[Path] | None = None, allow_boost: bool = False, max_duration_s: float = MAX_EXCERPT_S, run=subprocess.run) -> dict[str, Any]:
    source = Path(source)
    out_wav = Path(out_wav)
    if not source.is_file():
        raise FixtureError(f"source not found: {source}")
    if source.resolve() == out_wav.resolve():
        raise FixtureError("output must not overwrite the source")
    if not (duration_s > 0 and math.isfinite(duration_s)):
        raise FixtureError("duration must be a positive finite number of seconds")
    if duration_s > max_duration_s:
        raise FixtureError(f"duration {duration_s}s exceeds the {max_duration_s}s cap; pass --max-duration to raise it deliberately")
    if not math.isfinite(gain_db) or gain_db > MAX_GAIN_DB:
        raise FixtureError(f"gain {gain_db} dB is not a finite value at or below {MAX_GAIN_DB} dB")
    if gain_db > 0 and not allow_boost:
        raise FixtureError(f"gain {gain_db} dB boosts the source; pass --allow-boost for a documented calibration boost (hard clipping is still refused)")
    if offset_s < 0:
        raise FixtureError("offset must be >= 0")

    probe = ffprobe(source, run=run)
    src_duration = float((probe.get("format") or {}).get("duration") or 0.0)
    if src_duration and offset_s + duration_s > src_duration + 0.01:
        raise FixtureError(f"excerpt [{offset_s}, {offset_s + duration_s}) exceeds source duration {src_duration:.3f}s")
    source_sha = sha256_file(source)

    drive: dict[str, Any] | None = None
    if provenance_json:
        entries = json.loads(Path(provenance_json).read_text())
        for e in entries if isinstance(entries, list) else []:
            if e.get("name") == source.name or Path(e.get("path", "")).name == source.name:
                drive = {k: e.get(k) for k in ("id", "name", "drive_url", "mime_type", "bytes", "sha256")}
                drive["hash_matches_download"] = e.get("sha256") == source_sha
                break
        if drive is None:
            drive = {"error": f"{source.name} not listed in {provenance_json}"}

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    raw_wav = out_wav.with_suffix(".raw-excerpt.wav")
    argv = decode_excerpt(source, raw_wav, offset_s=offset_s, duration_s=duration_s, gain_db=gain_db, rate=rate, run=run)
    try:
        info, excerpt = read_mono(raw_wav)
    except WavError as exc:
        raise FixtureError(f"decoded excerpt unreadable: {exc}") from exc
    finally:
        raw_wav.unlink(missing_ok=True)
    if len(excerpt) == 0:
        raise FixtureError("decoded excerpt is empty (offset beyond the end?)")
    clipped = sum(1 for v in excerpt if v >= 32766 or v <= -32766)
    if clipped:
        raise FixtureError(f"gain {gain_db} dB clips the excerpt ({clipped} of {len(excerpt)} samples at full scale); lower the gain")

    parts: list[tuple[str, array]] = [("lead_silence", silence(rate, lead_s))]
    if markers:
        parts.append(("start_marker", marker(rate)))
    parts.append(("excerpt", excerpt))
    if markers:
        parts.append(("end_marker", marker(rate)))
    parts.append(("tail_silence", silence(rate, tail_s)))
    layout = []
    assembled = array("h")
    for name, part in parts:
        layout.append({"part": name, "start_s": round(len(assembled) / rate, 4), "duration_s": round(len(part) / rate, 4)})
        assembled.extend(part)
    write_mono(out_wav, assembled, rate)
    peak = max((abs(v) for v in excerpt), default=0)

    prov = {
        "schema": "rlm-adk-ios.device-eval.fixture/1",
        "created_at": utc_now(),
        "source": {"path": str(source), "sha256": source_sha, "bytes": source.stat().st_size, "probe": probe, "drive": drive, "unchanged": True},
        "excerpt": {"offset_s": offset_s, "duration_s": duration_s, "decoded_frames": len(excerpt), "decoded_duration_s": round(len(excerpt) / rate, 4), "gain_db": gain_db, "boost_allowed": allow_boost, "rate": rate, "channels": 1, "peak": peak, "peak_dbfs": round(20 * math.log10(peak / 32768) if peak else -100.0, 2), "clipped_samples": clipped, "ffmpeg_argv": argv},
        "layout": layout,
        "markers": markers,
        "output": {"path": str(out_wav), "sha256": sha256_file(out_wav), "frames": len(assembled), "duration_s": round(len(assembled) / rate, 4)},
        "transcript_candidates": candidate_transcripts(transcripts or []),
        "reference_status": "candidate_unverified",
    }
    prov_path = out_wav.with_suffix(".provenance.json")
    prov_path.write_text(json.dumps(prov, indent=2) + "\n")
    prov["provenance_path"] = str(prov_path)
    return prov
