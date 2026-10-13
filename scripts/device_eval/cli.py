"""Command line: separate, composable commands; each writes ``<out>/<command>-receipt.json`` and
exits 0 pass, 1 fail, 2 blocked, 3 usage/crash (with the receipt still written).

  preflight             WDA status, lock state, app state, on-screen prayer/quiet labels
  session attach        attach session (no bundleId, no terminate, no force launch), activate app
  session release       delete a session id
  ui smoke              navigation / keyboard / Diagnostics / quiet toggle scenario with evidence
  fixture excerpt       derive a playback excerpt from an older prayer recording (ffmpeg)
  audio play-record     desktop plays the fixture while Spark-B records the room (finite, owned)
  audio score           source / room / phone measurements, alignment, segment accounting, text
  phone verify-segments manifest-versus-disk accounting only
  phone mode            read a pulled mode.json (quiet / open prayer)
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any

from . import fixture as fixture_mod
from . import phone as phone_mod
from . import remote_audio, scoring, ui
from .audio import wav_info
from .receipt import Cancelled, Receipt, install_cancel_handlers, sha256_file
from .wda import DEFAULT_URL, Wda, WdaError

BUNDLE = "com.rawleystanhope.rlmadk"


def _wda_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--wda", default=DEFAULT_URL, help="WDA base URL (forwarded localhost port)")
    p.add_argument("--bundle-id", default=BUNDLE)
    p.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout per WDA request")


def _out_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", required=True, type=Path, help="directory for receipt and evidence")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="device_eval", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("preflight")
    _wda_args(p)
    _out_arg(p)

    s = sub.add_parser("session")
    ss = s.add_subparsers(dest="sub", required=True)
    p = ss.add_parser("attach")
    _wda_args(p)
    _out_arg(p)
    p = ss.add_parser("release")
    _wda_args(p)
    _out_arg(p)
    p.add_argument("--session-id", required=True)

    u = sub.add_parser("ui")
    us = u.add_subparsers(dest="sub", required=True)
    p = us.add_parser("smoke")
    _wda_args(p)
    _out_arg(p)
    p.add_argument("--session-id", required=True)
    p.add_argument("--type-text", default=None, help="explicit text to type into typedInput (never submitted); default types nothing")
    p.add_argument("--no-quiet-toggle", action="store_true")
    p.add_argument("--step-timeout", type=float, default=8.0)

    f = sub.add_parser("fixture")
    fs = f.add_subparsers(dest="sub", required=True)
    p = fs.add_parser("excerpt")
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--out-wav", required=True, type=Path)
    p.add_argument("--offset", required=True, type=float)
    p.add_argument("--duration", required=True, type=float)
    p.add_argument("--gain-db", type=float, default=-6.0)
    p.add_argument("--rate", type=int, default=48000)
    p.add_argument("--lead", type=float, default=1.0)
    p.add_argument("--tail", type=float, default=1.0)
    p.add_argument("--markers", action="store_true")
    p.add_argument("--allow-boost", action="store_true")
    p.add_argument("--max-duration", type=float, default=fixture_mod.MAX_EXCERPT_S)
    p.add_argument("--provenance", type=Path, default=None, help="drive-provenance.json")
    p.add_argument("--transcript", type=Path, action="append", default=[], help="candidate transcript JSON (repeatable)")
    p.add_argument("--out", type=Path, default=None, help="receipt directory (default: next to --out-wav)")

    a = sub.add_parser("audio")
    as_ = a.add_subparsers(dest="sub", required=True)
    p = as_.add_parser("play-record")
    p.add_argument("--fixture", required=True, type=Path)
    _out_arg(p)
    p.add_argument("--desktop-ssh", default=remote_audio.DESKTOP_SSH)
    p.add_argument("--sparkb-ssh", default=remote_audio.SPARKB_SSH)
    p.add_argument("--runtime-dir", default=remote_audio.RUNTIME_DIR)
    p.add_argument("--sink", default=remote_audio.DESKTOP_SINK)
    p.add_argument("--source", default=remote_audio.SPARKB_SOURCE)
    p.add_argument("--stream-volume", type=float, default=0.5, help="pw-play per-stream volume (0, 1]; global volume is never changed")
    p.add_argument("--pre-roll", type=float, default=2.0)
    p.add_argument("--tail", type=float, default=3.0)
    p.add_argument("--readiness-timeout", type=float, default=15.0)
    p.add_argument("--keep-remote", action="store_true")
    p = as_.add_parser("score")
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--room", type=Path, default=None)
    _out_arg(p)
    p.add_argument("--phone-captures", type=Path, default=None, help="pulled captures.json")
    p.add_argument("--phone-dir", type=Path, default=None, help="pulled captures/<id>/ directory")
    p.add_argument("--capture-id", default=None)
    p.add_argument("--window", default=None, metavar="FIRST:LAST", help="closed segment index window to score (original indexes); default: whole capture from 0")
    p.add_argument("--pull-receipt", type=Path, default=None, help="root's pull-receipt.json; supplies captures.json, directory, capture id and window unless given")
    p.add_argument("--reference-text", type=Path, default=None)
    p.add_argument("--reference-verified", action="store_true", help="assert a person verified the reference matches the excerpt verbatim; enables WER")
    p.add_argument("--hypothesis", action="append", default=[], metavar="NAME=FILE", help="ASR output to compare, e.g. phone_journal=journal.txt (repeatable)")
    p.add_argument("--phrase", action="append", default=[], help="sentinel phrase to confirm (repeatable)")
    p.add_argument("--max-lag", type=float, default=None)

    ph = sub.add_parser("phone")
    phs = ph.add_subparsers(dest="sub", required=True)
    p = phs.add_parser("verify-segments")
    p.add_argument("--captures", type=Path, default=None)
    p.add_argument("--dir", type=Path, default=None)
    p.add_argument("--capture-id", default=None)
    p.add_argument("--window", default=None, metavar="FIRST:LAST")
    p.add_argument("--pull-receipt", type=Path, default=None)
    _out_arg(p)
    p = phs.add_parser("mode")
    p.add_argument("--mode-json", required=True, type=Path)
    _out_arg(p)
    return ap


def run(argv: list[str] | None = None) -> int:
    ap = build_parser()
    ns = ap.parse_args(argv)
    name = ns.cmd + (f"-{ns.sub}" if getattr(ns, "sub", None) else "")
    out_dir: Path = getattr(ns, "out", None) or (ns.out_wav.parent if getattr(ns, "out_wav", None) else Path("."))
    receipt = Receipt(name, {k: v for k, v in vars(ns).items() if k not in ("cmd", "sub")})
    receipt_path = out_dir / f"{name}-receipt.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    result: Any = None
    install_cancel_handlers()
    try:
        result = dispatch(ns, name, receipt, out_dir)
    except (KeyboardInterrupt, Cancelled) as exc:
        receipt.cancelled(exc.__class__.__name__ + (f": {exc}" if str(exc) else ""))
        receipt.finish()
        receipt.write(receipt_path)
        print(json.dumps({"outcome": receipt.data["outcome"], "cancelled": True, "receipt": str(receipt_path), "reasons": receipt.data["reasons"]}, indent=2))
        return receipt.exit_code
    except ui.Blocked as exc:
        receipt.blocked(str(exc))
    except (WdaError, remote_audio.RemoteAudioError, fixture_mod.FixtureError, phone_mod.WavError, OSError, ValueError, KeyError) as exc:
        receipt.data["reasons"].append(f"{exc.__class__.__name__}: {exc}")
        receipt.finish("fail")
    except Exception:  # still leave a receipt behind
        receipt.data["reasons"].append(traceback.format_exc())
        receipt.finish("fail")
        receipt.write(receipt_path)
        print(json.dumps({"outcome": "fail", "receipt": str(receipt_path), "reasons": receipt.data["reasons"]}, indent=2))
        return 3
    if result is not None:
        receipt.data["result"] = result
    receipt.write(receipt_path)
    print(json.dumps({"outcome": receipt.data["outcome"], "receipt": str(receipt_path), "reasons": receipt.data["reasons"], "result_keys": sorted(result) if isinstance(result, dict) else None}, indent=2))
    return receipt.exit_code


def parse_window(text: str | None) -> tuple[int, int] | None:
    if text is None:
        return None
    try:
        first, last = text.split(":", 1)
        return int(first), int(last)
    except ValueError as exc:
        raise ValueError(f"--window expects FIRST:LAST integers, got {text!r}") from exc


def phone_scope(ns: argparse.Namespace, receipt: Receipt) -> dict[str, Any]:
    """captures.json, directory, capture id and window from explicit args or root's pull receipt.
    The pull receipt's manifest hash is cross-checked against the captures.json actually read."""
    captures, directory, capture_id, window = getattr(ns, "phone_captures", None) or getattr(ns, "captures", None), getattr(ns, "phone_dir", None) or getattr(ns, "dir", None), ns.capture_id, parse_window(ns.window)
    if ns.pull_receipt:
        pulled = phone_mod.load_pull_receipt(ns.pull_receipt)
        receipt.action("pull_receipt", **{k: v for k, v in pulled.items() if k != "indexes"}, indexes=pulled["indexes"])
        captures = captures or pulled["captures_json"]
        directory = directory or pulled["directory"]
        capture_id = capture_id or pulled["capture_id"]
        window = window or pulled["window"]
        if pulled.get("manifest_sha256") and Path(captures).is_file():
            receipt.check("pull_receipt_manifest_hash_matches", pulled["manifest_sha256"] == sha256_file(captures), reason="captures.json differs from the manifest the pull receipt hashed")
    return {"captures": captures, "directory": directory, "capture_id": capture_id, "window": window}


def dispatch(ns: argparse.Namespace, name: str, receipt: Receipt, out_dir: Path) -> Any:
    if name == "preflight":
        return ui.preflight(Wda(ns.wda, ns.timeout), ns.bundle_id, receipt, out_dir)
    if name == "session-attach":
        wda = Wda(ns.wda, ns.timeout)
        sid = ui.attach(wda, ns.bundle_id, receipt, out_dir)
        receipt.data["wda_log"] = wda.log
        return {"session_id": sid}
    if name == "session-release":
        ui.release(Wda(ns.wda, ns.timeout), ns.session_id, receipt)
        return {"session_id": ns.session_id}
    if name == "ui-smoke":
        wda = Wda(ns.wda, ns.timeout, session_id=ns.session_id)
        res = ui.smoke(wda, ns.bundle_id, receipt, out_dir, type_text=ns.type_text, toggle_quiet=not ns.no_quiet_toggle, step_timeout=ns.step_timeout)
        receipt.data["wda_log"] = wda.log
        return res
    if name == "fixture-excerpt":
        prov = fixture_mod.derive_excerpt(ns.source, ns.out_wav, offset_s=ns.offset, duration_s=ns.duration, gain_db=ns.gain_db, rate=ns.rate, lead_s=ns.lead, tail_s=ns.tail, markers=ns.markers, provenance_json=ns.provenance, transcripts=ns.transcript, allow_boost=ns.allow_boost, max_duration_s=ns.max_duration)
        receipt.evidence("fixture_wav", ns.out_wav)
        receipt.evidence("fixture_provenance", prov["provenance_path"])
        drive = (prov["source"].get("drive") or {})
        if "hash_matches_download" in drive:
            receipt.check("source_hash_matches_drive_provenance", bool(drive["hash_matches_download"]), reason="downloaded file hash differs from drive-provenance.json")
        receipt.check("excerpt_finite", prov["output"]["duration_s"] <= ns.max_duration + ns.lead + ns.tail + 2)
        receipt.check("excerpt_not_clipped", prov["excerpt"]["clipped_samples"] == 0)
        if ns.gain_db > 0:
            receipt.action("calibration_boost", gain_db=ns.gain_db, note="explicit documented boost; default is -6 dB")
        return prov
    if name == "audio-play-record":
        info = wav_info(ns.fixture)
        receipt.evidence("fixture_wav", ns.fixture, format=info.as_dict())
        cfg = remote_audio.PlayRecordConfig(
            fixture=ns.fixture, out_dir=out_dir, fixture_duration_s=info.duration_s,
            desktop=remote_audio.Host.parse(ns.desktop_ssh, ns.runtime_dir), sparkb=remote_audio.Host.parse(ns.sparkb_ssh, ns.runtime_dir),
            sink=ns.sink, source=ns.source, stream_volume=ns.stream_volume, pre_roll_s=ns.pre_roll, tail_s=ns.tail,
            readiness_timeout_s=ns.readiness_timeout, rate=info.rate, keep_remote=ns.keep_remote,
        )
        res = remote_audio.play_record(cfg, receipt, wav_duration=lambda p: wav_info(p).duration_s)
        res["room_wav"] = str(res["room_wav"]) if res.get("room_wav") else None
        return res
    if name == "audio-score":
        hypotheses = {}
        for item in ns.hypothesis:
            if "=" not in item:
                raise ValueError(f"--hypothesis expects NAME=FILE, got {item!r}")
            k, v = item.split("=", 1)
            hypotheses[k] = Path(v).read_text()
        ref = ns.reference_text.read_text() if ns.reference_text else None
        if ns.reference_verified and ref is None:
            raise ValueError("--reference-verified needs --reference-text")
        scope = phone_scope(ns, receipt)
        res = scoring.score(source=ns.source, room=ns.room, receipt=receipt, phone_captures=scope["captures"], phone_dir=scope["directory"], capture_id=scope["capture_id"], window=scope["window"], reference_text=ref, reference_verified=ns.reference_verified, hypotheses=hypotheses or None, phrases=ns.phrase or None, max_lag_s=ns.max_lag)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "score.json").write_text(json.dumps(res, indent=2) + "\n")
        receipt.evidence("score", out_dir / "score.json")
        return res
    if name == "phone-verify-segments":
        scope = phone_scope(ns, receipt)
        if not (scope["captures"] and scope["directory"] and scope["capture_id"]):
            raise ValueError("need --captures, --dir and --capture-id, or --pull-receipt")
        capture = phone_mod.find_capture(phone_mod.load_captures(scope["captures"]), scope["capture_id"])
        report = phone_mod.verify_segments(capture, scope["directory"], window=scope["window"])
        receipt.check("phone_segments_verified", report["ok"], reason="; ".join(report["errors"]) or None, scope=report["scope"])
        return report
    if name == "phone-mode":
        mode = phone_mod.read_mode(ns.mode_json)
        receipt.action("mode", **mode)
        if mode["praying"]:
            receipt.blocked(f"prayer {mode['prayer_capture_id']} is open on the phone")
        return mode
    raise ValueError(f"unknown command {name}")


def main() -> None:
    sys.exit(run())
