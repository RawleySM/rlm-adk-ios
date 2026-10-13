"""Source / room / phone scoring with provenance. Reports measured quantities per recording and a
per-pair envelope alignment used for timing only. There is no aggregate quality score, and
transport success (files copied, hashes equal, processes exited 0) is reported separately from
acoustic evidence (the room and the phone actually carrying the played signal).

Word error rate is computed only when the caller supplies a reference it marks as verified.
Otherwise the same edit distance is reported as ``candidate_error_ratio`` against an unverified
candidate transcript: it is not a WER, it is not bounded by 1, and it is labelled as such. Confirmed
phrases are exact normalized substring hits in a hypothesis.
"""

from __future__ import annotations

import re
from array import array
from pathlib import Path
from typing import Any

from .audio import WavError, align, flat_or_missing, measure, read_mono
from .phone import concatenate, find_capture, load_captures, verify_segments
from .receipt import Receipt, sha256_file

WORD_RE = re.compile(r"[a-z0-9']+")


def normalize_words(text: str) -> list[str]:
    return WORD_RE.findall(text.lower().replace("’", "'"))


def word_errors(reference: list[str], hypothesis: list[str]) -> dict[str, int]:
    """Levenshtein over words with substitution/insertion/deletion counts."""
    n, m = len(reference), len(hypothesis)
    prev = [(j, 0, j, 0) for j in range(m + 1)]  # cost, sub, ins, del
    for i in range(1, n + 1):
        cur = [(i, 0, 0, i)]
        for j in range(1, m + 1):
            if reference[i - 1] == hypothesis[j - 1]:
                cur.append(prev[j - 1])
                continue
            sub = prev[j - 1]
            ins = cur[j - 1]
            dele = prev[j]
            best = min((sub[0] + 1, 0, sub), (ins[0] + 1, 1, ins), (dele[0] + 1, 2, dele))
            cost, kind, base = best
            cur.append((cost, base[1] + (kind == 0), base[2] + (kind == 1), base[3] + (kind == 2)))
        prev = cur
    cost, s, i_, d = prev[m]
    return {"errors": cost, "substitutions": s, "insertions": i_, "deletions": d, "reference_words": n, "hypothesis_words": m}


def compare_text(reference: str, hypothesis: str, *, verified: bool) -> dict[str, Any]:
    ref, hyp = normalize_words(reference), normalize_words(hypothesis)
    counts = word_errors(ref, hyp)
    ratio = round(counts["errors"] / counts["reference_words"], 4) if counts["reference_words"] else None
    out: dict[str, Any] = {"reference_verified": verified, **counts}
    if verified:
        out["wer"] = ratio
    else:
        out["candidate_error_ratio"] = ratio
        out["note"] = "edit distance against an unverified candidate transcript; not a WER and not bounded by 1"
    return out


def confirmed_phrases(phrases: list[str], hypothesis: str) -> list[dict[str, Any]]:
    hyp = " ".join(normalize_words(hypothesis))
    return [{"phrase": p, "found": (" ".join(normalize_words(p)) in hyp)} for p in phrases]


def score_file(path: Path, receipt: Receipt, role: str) -> tuple[dict[str, Any], array | None, int]:
    try:
        info, samples = read_mono(path)
    except WavError as exc:
        receipt.check(f"{role}_readable", False, reason=str(exc))
        return {"role": role, "path": str(path), "error": str(exc)}, None, 0
    m = measure(samples, info.rate)
    entry = {"role": role, "path": str(path), "sha256": sha256_file(path), "format": info.as_dict(), "measures": m}
    receipt.evidence(f"{role}_wav", path)
    return entry, samples, info.rate


def score(*, source: Path, room: Path | None, receipt: Receipt, phone_captures: Path | None = None, phone_dir: Path | None = None, capture_id: str | None = None, window: tuple[int, int] | None = None, reference_text: str | None = None, reference_verified: bool = False, hypotheses: dict[str, str] | None = None, phrases: list[str] | None = None, max_lag_s: float | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"recordings": {}, "alignments": {}, "phone": None, "text": {}, "transport": {}, "acoustic_evidence": None}

    src_entry, src, src_rate = score_file(source, receipt, "source")
    result["recordings"]["source"] = src_entry
    if src is not None:
        receipt.check("source_has_signal", flat_or_missing(src_entry["measures"]) is None, reason=flat_or_missing(src_entry["measures"]))
        receipt.check("source_not_clipped", src_entry["measures"]["clipping_ratio"] < 1e-4, reason=f"clipping ratio {src_entry['measures']['clipping_ratio']}")

    room_ok = False
    rm: array | None = None
    rm_rate = 0
    if room is not None:
        room_entry, rm, rm_rate = score_file(room, receipt, "room")
        result["recordings"]["room"] = room_entry
        result["transport"]["room_wav_readable"] = rm is not None
        if rm is not None:
            why = flat_or_missing(room_entry["measures"])
            room_ok = receipt.check("room_has_signal", why is None, reason=why or None)
            receipt.check("room_not_clipped", room_entry["measures"]["clipping_ratio"] < 1e-3, reason=f"clipping ratio {room_entry['measures']['clipping_ratio']}")
            if room_ok and src is not None:
                a = align(src, src_rate, rm, rm_rate, max_lag_s=max_lag_s)
                result["alignments"]["source_in_room"] = a
                receipt.check("room_alignment_credible", bool(a.get("credible")), reason=f"peak correlation {a.get('peak_correlation')} below {a.get('threshold')}; the room did not demonstrably carry the fixture (check the hardware mixer path, not only PipeWire volume)")
                if a.get("credible") and src_entry["format"]["duration_s"] + a["lag_s"] > room_entry["format"]["duration_s"] + 0.5:
                    receipt.check("room_covers_whole_source", False, reason="room recording ends before the source would")
    else:
        receipt.action("room_missing", note="no room recording given; no independent acoustic evidence")

    if phone_captures and phone_dir and capture_id:
        captures = load_captures(phone_captures)
        try:
            capture = find_capture(captures, capture_id)
        except KeyError as exc:
            receipt.check("phone_capture_in_manifest", False, reason=str(exc))
            capture = None
        if capture is not None:
            report = verify_segments(capture, phone_dir, window=window)
            result["phone"] = report
            result["transport"]["phone_segments_verified"] = report["ok"]
            receipt.check("phone_segments_verified", report["ok"], reason="; ".join(report["errors"]) or None, scope=report["scope"], segments=report["manifest_segments"], unaccounted=len(report["unaccounted_files"]))
            if report["ok"]:
                info, ph = concatenate(report)
                m = measure(ph, info.rate)
                result["recordings"]["phone"] = {"role": "phone", "capture_id": capture_id, "scope": report["scope"], "format": info.as_dict(), "measures": m, "segments": [s["disk_sha256"] for s in report["segments"]]}
                why = flat_or_missing(m)
                phone_ok = receipt.check("phone_has_signal", why is None, reason=why or None)
                receipt.check("phone_not_clipped", m["clipping_ratio"] < 1e-3, reason=f"clipping ratio {m['clipping_ratio']}")
                if phone_ok and src is not None:
                    a = align(src, src_rate, ph, info.rate, max_lag_s=max_lag_s)
                    result["alignments"]["source_in_phone"] = a
                    receipt.check("phone_alignment_credible", bool(a.get("credible")), reason=f"peak correlation {a.get('peak_correlation')}; Apple voice processing changes the envelope, so this can fail with intact capture")
                if room_ok and phone_ok and rm is not None:
                    result["alignments"]["room_in_phone"] = align(rm, rm_rate, ph, info.rate, max_lag_s=max_lag_s)

    if hypotheses:
        for name, hyp in hypotheses.items():
            entry: dict[str, Any] = {}
            if reference_text is not None:
                entry.update(compare_text(reference_text, hyp, verified=reference_verified))
            else:
                entry["reference_verified"] = False
            if phrases:
                entry["confirmed_phrases"] = confirmed_phrases(phrases, hyp)
            result["text"][name] = entry

    result["acoustic_evidence"] = {
        "room_heard_source": bool(result["alignments"].get("source_in_room", {}).get("credible")),
        "phone_segments_verified": bool((result.get("phone") or {}).get("ok")),
        "phone_heard_source": bool(result["alignments"].get("source_in_phone", {}).get("credible")),
        "note": "transport (copies, hashes, exit codes) is reported under 'transport'; these flags are separate measured facts about the audio and none is a perceptual quality judgement",
    }
    return result
