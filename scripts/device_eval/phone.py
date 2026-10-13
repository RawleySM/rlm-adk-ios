"""Exact accounting of the phone's retained capture segments against its own manifest.

Input: a copy of ``Documents/captures.json`` and the capture directory ``captures/<id>/`` pulled
from the phone by root (``scripts/pull_device_capture.py`` over house arrest, which may pull only
the closed segments overlapping a time window and writes ``pull-receipt.json``). The app writes
``NNNNNNNN.wav`` 16-bit PCM segments rotated every 60 s and records index, filename, bytes,
sha256, startedAt and endedAt per segment (``RLMADK/Model/CaptureManifest.swift``). Swift's default
Codable date is seconds since 2001-01-01; it is converted to Unix time here.

Two scopes, both strict and both leaving the manifest exactly as pulled (no reindexing):
- ``all``: contiguous indexes from 0, every manifest segment present and closed.
- ``window=(first, last)``: the manifest must list every index first..last contiguously, each
  must be closed (endedAt set) and present on disk with exactly the recorded bytes and sha256.
  Segments outside the window are left alone; a window is the right scope for a two-minute
  interval of an ambient capture that has been running for hours.
Files on disk the manifest does not list are reported as unaccounted and excluded from scoring.
"""

from __future__ import annotations

import json
from array import array
from pathlib import Path
from typing import Any

from .audio import WavError, WavInfo, read_mono, wav_info
from .receipt import sha256_file

APPLE_EPOCH_OFFSET = 978307200.0  # 2001-01-01T00:00:00Z in Unix seconds


def to_unix(ts: Any) -> float | None:
    if ts is None:
        return None
    try:
        v = float(ts)
    except (TypeError, ValueError):
        return None
    return v + APPLE_EPOCH_OFFSET if v < 1e9 else v


def load_captures(path: Path | str) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list):
        raise ValueError("captures.json root must be a list of capture manifests")
    return data


def find_capture(captures: list[dict[str, Any]], capture_id: str) -> dict[str, Any]:
    for c in captures:
        if c.get("captureId") == capture_id:
            return c
    raise KeyError(f"capture {capture_id} not in manifest")


def load_pull_receipt(path: Path | str) -> dict[str, Any]:
    """Root's pull-receipt.json: capture id, pulled indexes, manifest hash, local layout."""
    p = Path(path)
    data = json.loads(p.read_text())
    indexes = sorted(int(f["index"]) for f in data.get("files") or [] if "index" in f)
    if not indexes:
        raise ValueError(f"{p}: pull receipt lists no segment indexes")
    capture_id = data.get("capture_id")
    if not capture_id:
        raise ValueError(f"{p}: pull receipt has no capture_id")
    # Root's script records each pulled file's local path; use its directory when present, else
    # the layout that script uses (<out>/<capture_id>/ next to the receipt).
    paths = [Path(f["path"]) for f in data.get("files") or [] if f.get("path")]
    directory = paths[0].parent if paths and paths[0].parent.is_dir() else p.parent / capture_id
    return {
        "path": str(p),
        "capture_id": capture_id,
        "indexes": indexes,
        "window": (indexes[0], indexes[-1]),
        "manifest_sha256": data.get("manifest_sha256"),
        "proof_scope": data.get("proof_scope"),
        "captures_json": p.parent / "captures.json",
        "directory": directory,
    }


def verify_segments(capture: dict[str, Any], directory: Path | str, window: tuple[int, int] | None = None) -> dict[str, Any]:
    """Strict manifest-versus-disk accounting for the whole capture or an index window."""
    directory = Path(directory)
    errors: list[str] = []
    all_segments = sorted(capture.get("segments") or [], key=lambda s: int(s.get("index", -1)))
    if window is not None:
        first, last = int(window[0]), int(window[1])
        if first < 0 or last < first:
            raise ValueError(f"bad window {window}; expected FIRST<=LAST, FIRST>=0")
        segments = [s for s in all_segments if first <= int(s.get("index", -1)) <= last]
        expected_indexes = list(range(first, last + 1))
    else:
        segments = all_segments
        expected_indexes = list(range(len(all_segments)))
    report: dict[str, Any] = {
        "capture_id": capture.get("captureId"),
        "kind": capture.get("kind"),
        "scope": {"mode": "window" if window else "all", "first_index": expected_indexes[0] if expected_indexes else None, "last_index": expected_indexes[-1] if expected_indexes else None, "manifest_total_segments": len(all_segments)},
        "created_at_unix": to_unix(capture.get("createdAt")),
        "closed_at_unix": to_unix(capture.get("closedAt")),
        "expected_segment_count": capture.get("expectedSegmentCount"),
        "manifest_segments": len(segments),
        "gaps": capture.get("gaps") or [],
        "segments": [],
        "unaccounted_files": [],
        "outside_window_files": [],
        "formats": [],
        "total_frames": 0,
        "total_duration_s": 0.0,
    }
    if not segments:
        errors.append("manifest lists no segments" + (f" in window {window}" if window else ""))
    indexes = [int(s.get("index", -1)) for s in segments]
    if indexes != expected_indexes:
        missing = sorted(set(expected_indexes) - set(indexes))
        errors.append(f"segment indexes not contiguous: expected {expected_indexes[0] if expected_indexes else '-'}..{expected_indexes[-1] if expected_indexes else '-'}, manifest has {indexes}" + (f", missing {missing}" if missing else ""))
    for seg in segments:
        idx = int(seg.get("index", -1))
        entry: dict[str, Any] = {"index": idx, "filename": seg.get("filename"), "manifest_bytes": seg.get("bytes"), "manifest_sha256": seg.get("sha256"), "started_unix": to_unix(seg.get("startedAt")), "ended_unix": to_unix(seg.get("endedAt"))}
        if seg.get("endedAt") is None:
            errors.append(f"segment {idx} has no endedAt (still open); not scorable")
        path = directory / str(seg.get("filename") or f"{idx:08d}.wav")
        entry["path"] = str(path)
        if not path.is_file():
            errors.append(f"segment {idx} file missing: {path.name}")
            report["segments"].append(entry)
            continue
        size = path.stat().st_size
        digest = sha256_file(path)
        entry["disk_bytes"] = size
        entry["disk_sha256"] = digest
        if size != seg.get("bytes"):
            errors.append(f"segment {idx} bytes {size} != manifest {seg.get('bytes')}")
        if digest != seg.get("sha256"):
            errors.append(f"segment {idx} sha256 mismatch")
        try:
            info = wav_info(path)
            entry["format"] = info.as_dict()
            report["formats"].append(info.format_key())
            report["total_frames"] += info.frames
            if entry["started_unix"] is not None and entry["ended_unix"] is not None:
                entry["manifest_duration_s"] = round(entry["ended_unix"] - entry["started_unix"], 3)
                entry["duration_delta_s"] = round(info.duration_s - entry["manifest_duration_s"], 3)
        except WavError as exc:
            errors.append(str(exc))
        report["segments"].append(entry)
    fmts = set(report["formats"])
    if len(fmts) > 1:
        errors.append(f"segments differ in PCM format: {sorted(fmts)}")
    report["format"] = list(fmts)[0] if len(fmts) == 1 else None
    if report["format"]:
        report["total_duration_s"] = round(report["total_frames"] / report["format"][0], 3)
    expected = capture.get("expectedSegmentCount")
    if window is None and expected is not None and expected != len(segments):
        errors.append(f"expectedSegmentCount {expected} != manifest segments {len(segments)}")
    listed_all = {str(s.get("filename")): int(s.get("index", -1)) for s in all_segments}
    in_scope = {str(s.get("filename")) for s in segments}
    for p in sorted(directory.glob("*.wav")) if directory.is_dir() else []:
        if p.name in in_scope:
            continue
        if p.name in listed_all:
            report["outside_window_files"].append({"filename": p.name, "index": listed_all[p.name], "bytes": p.stat().st_size, "note": "in manifest, outside the requested window; not scored"})
        else:
            report["unaccounted_files"].append({"filename": p.name, "bytes": p.stat().st_size, "note": "on disk, not in manifest; excluded (open segment or orphan)"})
    report["errors"] = errors
    report["ok"] = not errors
    return report


def concatenate(report: dict[str, Any]) -> tuple[WavInfo, array]:
    """Mono samples of the verified in-scope segments in index order. Requires ``report['ok']``."""
    if not report.get("ok"):
        raise WavError("segments did not verify; refusing to concatenate")
    samples = array("h")
    info: WavInfo | None = None
    for seg in report["segments"]:
        seg_info, mono = read_mono(seg["path"])
        if info is None:
            info = WavInfo(seg["path"], seg_info.rate, 1, 2, 0)
        samples.extend(mono)
    assert info is not None
    info.frames = len(samples)
    return info, samples


def read_mode(path: Path | str) -> dict[str, Any]:
    """``mode.json`` from the phone: quiet flag and whether a prayer is open (Swift enum encoding)."""
    data = json.loads(Path(path).read_text())
    activity = data.get("activity") or {}
    praying = isinstance(activity, dict) and "prayer" in activity
    capture_id = (activity.get("prayer") or {}).get("captureId") if praying else None
    return {"quiet": bool(data.get("quiet")), "praying": praying, "prayer_capture_id": capture_id, "raw_activity": activity}
