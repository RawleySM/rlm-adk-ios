"""Pure-stdlib WAV reading and signal measurements (no numpy on the hosts that run this).

Measurements are reported individually and named for what they are: format, duration, peak,
clipping ratio, overall RMS, and percentiles of the 100 ms frame RMS distribution (p10, p90, their
range, the fraction of frames at least 10 dB above p10). They are not a measured signal-to-noise
ratio and do not label frames as speech or noise. An envelope is used only for alignment.
"""

from __future__ import annotations

import math
import struct
import wave
from array import array
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

CLIP_LEVEL = 32766  # |sample| at or above this counts as clipped for 16-bit PCM
FLOOR_DB = -100.0


@dataclass
class WavInfo:
    path: str
    rate: int
    channels: int
    sampwidth: int
    frames: int

    @property
    def duration_s(self) -> float:
        return self.frames / self.rate if self.rate else 0.0

    def format_key(self) -> tuple[int, int, int]:
        return (self.rate, self.channels, self.sampwidth)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["duration_s"] = round(self.duration_s, 4)
        return d


class WavError(Exception):
    pass


def wav_info(path: Path | str) -> WavInfo:
    try:
        with wave.open(str(path), "rb") as w:
            return WavInfo(str(path), w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes())
    except (wave.Error, EOFError, struct.error, OSError) as exc:
        raise WavError(f"{path}: not a readable PCM WAV ({exc})") from exc


def read_mono(path: Path | str) -> tuple[WavInfo, array]:
    """16-bit PCM WAV -> mono int16 samples (channels averaged). Other widths are refused."""
    info = wav_info(path)
    if info.sampwidth != 2:
        raise WavError(f"{path}: {info.sampwidth * 8}-bit samples; only 16-bit PCM is scored")
    mono = array("h")
    with wave.open(str(path), "rb") as w:
        chunk = 1 << 16
        while True:
            raw = w.readframes(chunk)
            if not raw:
                break
            frames = array("h")
            frames.frombytes(raw)
            if info.channels == 1:
                mono.extend(frames)
            else:
                n = info.channels
                mono.extend(array("h", (sum(frames[i:i + n]) // n for i in range(0, len(frames), n))))
    return info, mono


def write_mono(path: Path | str, samples: array, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


def dbfs(rms: float) -> float:
    return 20 * math.log10(rms / 32768.0) if rms > 0 else FLOOR_DB


def frame_rms_db(samples: array, rate: int, frame_s: float) -> list[float]:
    n = max(1, int(rate * frame_s))
    out: list[float] = []
    for start in range(0, len(samples) - n + 1, n):
        chunk = samples[start:start + n]
        acc = 0
        for v in chunk:
            acc += v * v
        out.append(dbfs(math.sqrt(acc / n)))
    return out


def percentile(values: list[float], p: float) -> float:
    if not values:
        return FLOOR_DB
    s = sorted(values)
    k = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def measure(samples: array, rate: int, *, frame_s: float = 0.1, active_margin_db: float = 10.0) -> dict[str, Any]:
    n = len(samples)
    if n == 0:
        return {"frames": 0, "duration_s": 0.0, "empty": True}
    peak = 0
    clipped = 0
    acc = 0
    for v in samples:
        a = -v if v < 0 else v
        if a > peak:
            peak = a
        if a >= CLIP_LEVEL:
            clipped += 1
        acc += v * v
    env = frame_rms_db(samples, rate, frame_s)
    p10 = percentile(env, 10)
    p90 = percentile(env, 90)
    above = sum(1 for e in env if e >= p10 + active_margin_db) / len(env) if env else 0.0
    return {
        "frames": n,
        "duration_s": round(n / rate, 4),
        "peak": peak,
        "peak_dbfs": round(dbfs(peak), 2),
        "clipping_ratio": round(clipped / n, 6),
        "clipped_samples": clipped,
        "rms_dbfs": round(dbfs(math.sqrt(acc / n)), 2),
        "frame_p10_dbfs": round(p10, 2),
        "frame_p90_dbfs": round(p90, 2),
        "frame_p90_p10_range_db": round(p90 - p10, 2),
        "frames_above_p10_plus_10db_fraction": round(above, 4),
        "frame_s": frame_s,
        "envelope_frames": len(env),
        "note": "frame-RMS percentiles of this recording; not a measured SNR and no speech/noise labelling",
    }


def flat_or_missing(m: dict[str, Any], *, min_rms_dbfs: float = -70.0, min_range_db: float = 6.0) -> str | None:
    """Reason a recording cannot serve as evidence, or None when it carries a varying signal."""
    if m.get("empty"):
        return "no samples"
    rng = m["frame_p90_p10_range_db"]
    if m["rms_dbfs"] <= min_rms_dbfs and rng < min_range_db:
        return f"silent: rms {m['rms_dbfs']} dBFS, p90-p10 range {rng} dB"
    if rng < min_range_db:
        return f"flat: p90-p10 range {rng} dB below {min_range_db} dB"
    return None


# ----- envelope alignment (timing only) -----

def _normalized(env: list[float], floor_clip: float = -80.0) -> list[float]:
    vals = [max(e, floor_clip) for e in env]
    mean = sum(vals) / len(vals) if vals else 0.0
    centered = [v - mean for v in vals]
    norm = math.sqrt(sum(c * c for c in centered)) or 1.0
    return [c / norm for c in centered]


def _xcorr_best(a: list[float], b: list[float], lags: range) -> tuple[int, float]:
    """Lag (in frames) at which b best matches a shifted: positive lag means b starts later."""
    best_lag, best = 0, -2.0
    la, lb = len(a), len(b)
    for lag in lags:
        lo = max(0, lag)
        hi = min(lb, la + lag)
        if hi - lo < 4:
            continue
        s = 0.0
        for j in range(lo, hi):
            s += a[j - lag] * b[j]
        if s > best:
            best, best_lag = s, lag
    return best_lag, best


CREDIBLE_CORRELATION = 0.5


def align(source: array, source_rate: int, target: array, target_rate: int, *, max_lag_s: float | None = None, min_lag_s: float = 0.0, coarse_s: float = 0.1, fine_s: float = 0.02) -> dict[str, Any]:
    """Where the source's energy envelope appears inside the target; coarse then refined.

    Lags are searched from ``min_lag_s`` (default 0: the source is played after the target
    recording started, so a negative lag is physically impossible) to ``max_lag_s`` or the end of
    the target. ``lag_s`` is set only when the peak normalized correlation reaches
    CREDIBLE_CORRELATION; otherwise it is None and the rejected candidate is reported separately
    so an invalid correlation can never be read as a latency. Timing aid only, not quality.
    """
    ea = _normalized(frame_rms_db(source, source_rate, coarse_s))
    eb = _normalized(frame_rms_db(target, target_rate, coarse_s))
    if len(ea) < 4 or len(eb) < 4:
        return {"credible": False, "lag_s": None, "reason": "too short for alignment"}
    lo = int(min_lag_s / coarse_s)
    hi = min(len(eb) - 1, int(max_lag_s / coarse_s) if max_lag_s is not None else len(eb) - 1)
    if hi < lo:
        return {"credible": False, "lag_s": None, "reason": f"target shorter than the minimum lag {min_lag_s}s"}
    lag_c, corr_c = _xcorr_best(ea, eb, range(lo, hi + 1))
    ratio = int(round(coarse_s / fine_s))
    fa = _normalized(frame_rms_db(source, source_rate, fine_s))
    fb = _normalized(frame_rms_db(target, target_rate, fine_s))
    centre = lag_c * ratio
    span = 3 * ratio
    lag_f, corr_f = _xcorr_best(fa, fb, range(max(lo * ratio, centre - span), centre + span + 1))
    peak = max(corr_c, corr_f)
    credible = peak >= CREDIBLE_CORRELATION
    candidate = round(lag_f * fine_s, 3)
    return {
        "lag_s": candidate if credible else None,
        "rejected_candidate_lag_s": None if credible else candidate,
        "coarse_lag_s": round(lag_c * coarse_s, 3),
        "peak_correlation": round(peak, 4),
        "credible": credible,
        "threshold": CREDIBLE_CORRELATION,
        "search_lag_s": [round(lo * coarse_s, 3), round(hi * coarse_s, 3)],
        "note": "envelope cross-correlation; timing only, not perceptual quality; lag_s is null unless credible",
    }
