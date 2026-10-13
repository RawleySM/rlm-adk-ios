"""JSON action receipts: what was done, when (UTC and monotonic), with what inputs, and the outcome.

Outcome vocabulary: ``pass`` (every check held), ``fail`` (a check did not hold), ``blocked`` (a
precondition outside this run's control was not met: locked phone, active prayer, alert, missing
node). A receipt is written even when a command raises, so a crash leaves evidence too.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = "rlm-adk-ios.device-eval.receipt/1"
OUTCOMES = ("pass", "fail", "blocked", "pending")
# pending: every check that could complete held, but a bounded wait ended before the final stage
# (for example intake publication) settled. It is not acceptance.
EXIT_CODES = {"pass": 0, "fail": 1, "blocked": 2, "pending": 4}
CANCELLED_EXIT = 130


class Cancelled(Exception):
    """SIGTERM/SIGINT turned into an exception so `finally` blocks and receipts still run."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def sha256_file(path: Path | str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def git_commit(start: Path | None = None) -> str | None:
    """HEAD of the repository containing ``start`` (default: this file), or None outside git."""
    cwd = Path(start or __file__).resolve().parent
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def code_fingerprint() -> dict[str, Any]:
    """Hashes of the harness code that actually ran, because HEAD alone hides uncommitted edits.

    Lists every ``scripts/device_eval/*.py`` plus ``scripts/device_eval.py`` with sha256, a
    combined digest over the sorted list, and git's view of which of them are modified or untracked.
    """
    pkg = Path(__file__).resolve().parent
    scripts = pkg.parent
    files = sorted(pkg.glob("*.py")) + [scripts / n for n in ("device_eval.py", "device_prayer_eval.py", "wda_session.py", "pull_device_capture.py")]
    root = scripts.parent
    entries: dict[str, str] = {}
    for f in files:
        if f.is_file():
            entries[str(f.relative_to(root))] = sha256_file(f)
    combined = hashlib.sha256("".join(f"{k}:{v}\n" for k, v in sorted(entries.items())).encode()).hexdigest()
    dirty: list[str] | None
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", str(pkg), *[str(f) for f in files if f.parent == scripts]], cwd=root, capture_output=True, text=True, timeout=10)
        dirty = [line[3:] for line in out.stdout.splitlines() if line.strip()] if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        dirty = None
    return {"files": entries, "combined_sha256": combined, "git_modified_or_untracked": dirty}


class Receipt:
    """Append-only record of one command invocation."""

    def __init__(self, command: str, args: dict[str, Any] | None = None, run_id: str | None = None):
        self.t0 = time.monotonic()
        self.data: dict[str, Any] = {
            "schema": SCHEMA,
            "command": command,
            "run_id": run_id or f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}",
            "started_at": utc_now(),
            "finished_at": None,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "git_commit": git_commit(),
            "code": code_fingerprint(),
            "args": _jsonable(args or {}),
            "actions": [],
            "checks": [],
            "evidence": [],
            "outcome": None,
            "reasons": [],
        }

    @property
    def run_id(self) -> str:
        return self.data["run_id"]

    def elapsed(self) -> float:
        return round(time.monotonic() - self.t0, 3)

    def action(self, name: str, **detail: Any) -> dict[str, Any]:
        entry = {"t": self.elapsed(), "at": utc_now(), "name": name, **_jsonable(detail)}
        self.data["actions"].append(entry)
        return entry

    def check(self, name: str, ok: bool, **detail: Any) -> bool:
        """Record a named check. ``reason`` describes the failure and is dropped when the check holds."""
        reason = detail.pop("reason", None)
        entry = {"t": self.elapsed(), "name": name, "ok": bool(ok), **_jsonable(detail)}
        if not ok:
            if reason:
                entry["reason"] = str(reason)
            self.data["reasons"].append(f"check failed: {name}" + (f" ({reason})" if reason else ""))
        self.data["checks"].append(entry)
        return bool(ok)

    def evidence(self, kind: str, path: Path | str, **detail: Any) -> dict[str, Any]:
        p = Path(path)
        entry: dict[str, Any] = {"t": self.elapsed(), "kind": kind, "path": str(p), **_jsonable(detail)}
        if p.is_file():
            entry["bytes"] = p.stat().st_size
            entry["sha256"] = sha256_file(p)
        self.data["evidence"].append(entry)
        return entry

    def blocked(self, reason: str) -> None:
        self.data["reasons"].append(f"blocked: {reason}")
        self.data["outcome"] = "blocked"

    def pending(self, reason: str) -> None:
        """A bounded wait ended before the final stage settled; recorded as not-accepted."""
        self.data["reasons"].append(f"pending: {reason}")
        if self.data["outcome"] != "blocked":
            self.data["outcome"] = "pending"

    def cancelled(self, reason: str) -> None:
        self.data["reasons"].append(f"cancelled: {reason}")
        self.data["cancelled"] = True

    def finish(self, outcome: str | None = None) -> str:
        """Settle the outcome: explicit, else ``blocked`` if recorded, else pass/fail from the checks."""
        if outcome is None:
            checks_ok = all(c["ok"] for c in self.data["checks"])
            if self.data.get("cancelled") or not checks_ok:
                outcome = "fail"
            elif self.data["outcome"] in ("blocked", "pending"):
                outcome = self.data["outcome"]
            else:
                outcome = "pass"
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {outcome!r}")
        self.data["outcome"] = outcome
        self.data["finished_at"] = utc_now()
        self.data["elapsed_s"] = self.elapsed()
        return outcome

    def write(self, path: Path | str) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if self.data["outcome"] is None:
            self.finish()
        p.write_text(json.dumps(self.data, indent=2, sort_keys=False) + "\n")
        return p

    @property
    def exit_code(self) -> int:
        if self.data.get("cancelled"):
            return CANCELLED_EXIT
        return EXIT_CODES.get(self.data["outcome"] or "fail", 1)


def install_cancel_handlers() -> None:
    """Turn SIGTERM into Cancelled so cleanup and receipts run; SIGINT already raises."""
    import signal

    def _raise(signum, _frame):
        raise Cancelled(f"signal {signum}")

    try:
        signal.signal(signal.SIGTERM, _raise)
    except (ValueError, OSError):  # not the main thread
        pass


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)
