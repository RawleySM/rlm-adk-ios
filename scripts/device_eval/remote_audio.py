"""Finite, run-owned desktop playback and Spark-B room recording over SSH and PipeWire.

Hosts (defaults from the plan; override on the command line):
- desktop: ``ssh desktop`` as rawley-stanhope, PipeWire session at ``XDG_RUNTIME_DIR=/run/user/1000``,
  sink ``alsa_output.usb-1130_USB_AUDIO-00.analog-stereo``. Global volume is never changed; the
  per-stream ``pw-play --volume`` is the only gain this harness applies.
- Spark-B: ``ssh -l rawleysm spark-media`` (the default mediaops user has a dummy graph),
  ``XDG_RUNTIME_DIR=/run/user/1000``, source
  ``alsa_input.usb-Razer_Inc_Razer_Seiren_Mini_UC2420L03300945-00.mono-fallback``.

Nodes are resolved by ``node.name`` in ``pw-dump`` output, never by numeric id. Readiness means a
``Stream/Input/Audio`` node carrying this run's unique application name exists and is running;
sleeping is not readiness. Recording ends with SIGINT to the exact remote PID so pw-record
finalizes the WAV header, after playback plus a tail. Remote children run under ``setsid`` and
GNU ``timeout --signal=INT`` so they have a finite lifetime of their own: a dead SSH session or a
dead supervisor cannot orphan an open-ended recording or playback. Local waits are bounded too.
Nothing broad is ever killed.
"""

from __future__ import annotations

import json
import math
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .receipt import Receipt, sha256_file

DESKTOP_SSH = "ssh desktop"
SPARKB_SSH = "ssh -l rawleysm spark-media"
RUNTIME_DIR = "/run/user/1000"
DESKTOP_SINK = "alsa_output.usb-1130_USB_AUDIO-00.analog-stereo"
SPARKB_SOURCE = "alsa_input.usb-Razer_Inc_Razer_Seiren_Mini_UC2420L03300945-00.mono-fallback"
RECORD_APP_NAME = "rlm-adk-device-eval-record"


class RemoteAudioError(Exception):
    pass


@dataclass
class Host:
    ssh: list[str]
    runtime_dir: str = RUNTIME_DIR

    @classmethod
    def parse(cls, ssh_command: str, runtime_dir: str = RUNTIME_DIR) -> "Host":
        parts = shlex.split(ssh_command)
        if not parts or parts[0] != "ssh":
            raise RemoteAudioError(f"ssh command must start with 'ssh': {ssh_command!r}")
        return cls(parts, runtime_dir)

    @property
    def scp_target(self) -> str:
        """``user@host`` for scp, derived from ``ssh [-l user] host``."""
        user = None
        host = None
        args = self.ssh[1:]
        i = 0
        while i < len(args):
            a = args[i]
            if a == "-l" and i + 1 < len(args):
                user = args[i + 1]
                i += 2
                continue
            if a.startswith("-"):
                i += 1
                continue
            host = a
            i += 1
        if host is None:
            raise RemoteAudioError(f"no host in ssh command {self.ssh}")
        return f"{user}@{host}" if user else host

    def command(self, remote: list[str]) -> list[str]:
        """ssh argv running ``remote`` inside the user's PipeWire session."""
        env = ["env", f"XDG_RUNTIME_DIR={self.runtime_dir}"]
        return [*self.ssh, " ".join(shlex.quote(p) for p in [*env, *remote])]


class Runner:
    """Process launcher; tests substitute a fake with the same three methods."""

    def run(self, argv: list[str], timeout: float, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(argv, capture_output=True, timeout=timeout, input=input_bytes)

    def popen(self, argv: list[str]) -> subprocess.Popen:
        return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


# ----- pw-dump -----

def pw_dump(host: Host, runner: Runner, timeout: float = 15.0) -> list[dict[str, Any]]:
    out = runner.run(host.command(["pw-dump"]), timeout=timeout)
    if out.returncode != 0:
        raise RemoteAudioError(f"pw-dump on {host.scp_target} failed rc={out.returncode}: {out.stderr.decode(errors='replace')[-400:]}")
    try:
        data = json.loads(out.stdout.decode("utf-8"))
    except ValueError as exc:
        raise RemoteAudioError(f"pw-dump on {host.scp_target} returned non-JSON") from exc
    return [d for d in data if isinstance(d, dict)]


def node_props(obj: dict[str, Any]) -> dict[str, Any]:
    return ((obj.get("info") or {}).get("props") or {}) if obj.get("type") == "PipeWire:Interface:Node" else {}


def find_node(dump: list[dict[str, Any]], node_name: str) -> dict[str, Any] | None:
    for obj in dump:
        if node_props(obj).get("node.name") == node_name:
            return obj
    return None


def node_summary(obj: dict[str, Any] | None) -> dict[str, Any] | None:
    if obj is None:
        return None
    info = obj.get("info") or {}
    props = info.get("props") or {}
    volumes = None
    for param in (info.get("params") or {}).get("Props") or []:
        if isinstance(param, dict) and "channelVolumes" in param:
            volumes = param.get("channelVolumes")
            break
    return {
        "id": obj.get("id"),
        "node.name": props.get("node.name"),
        "node.description": props.get("node.description"),
        "media.class": props.get("media.class"),
        "state": info.get("state"),
        "channel_volumes": volumes,
        "audio.rate": props.get("audio.rate"),
    }


def record_stream_ready(dump: list[dict[str, Any]], app_name: str) -> dict[str, Any] | None:
    """The running capture stream node whose application/node/media name is exactly ``app_name``."""
    for obj in dump:
        props = node_props(obj)
        if props.get("media.class") != "Stream/Input/Audio":
            continue
        names = {props.get("application.name"), props.get("node.name"), props.get("media.name")}
        if app_name in names and (obj.get("info") or {}).get("state") == "running":
            return node_summary(obj)
    return None


def unique_app_name(run_id: str) -> str:
    return f"{RECORD_APP_NAME}-{run_id}"


def bounded_remote(cmd: list[str], max_seconds: float, *, kill_after: float = 10.0) -> list[str]:
    """``setsid timeout --signal=INT --kill-after=K MAX cmd``: detached from the SSH session, ends
    by itself. INT lets pw-record/pw-play finalize; KILL follows only if INT is ignored."""
    return ["setsid", "timeout", "--signal=INT", f"--kill-after={int(kill_after)}", str(int(math.ceil(max_seconds))), *cmd]


# ----- remote processes -----

@dataclass
class RemoteRecord:
    proc: subprocess.Popen
    pid: int | None = None
    stdout_lines: list[str] = field(default_factory=list)
    reader: threading.Thread | None = None
    pid_event: threading.Event = field(default_factory=threading.Event)
    max_seconds: float = 0.0
    app_name: str = ""


def _read_stdout(rec: RemoteRecord) -> None:
    try:
        for line in rec.proc.stdout:  # type: ignore[union-attr]
            rec.stdout_lines.append(line.rstrip("\n"))
            if rec.pid is None and line.startswith("PID="):
                try:
                    rec.pid = int(line[4:].strip())
                except ValueError:
                    pass
                rec.pid_event.set()
    except (OSError, ValueError):
        pass
    finally:
        rec.pid_event.set()


def start_record(host: Host, runner: Runner, source: str, remote_wav: str, *, rate: int, channels: int, app_name: str, max_seconds: float, pid_timeout: float = 10.0) -> RemoteRecord:
    """Launch pw-record on the host under setsid+timeout and learn its PID within ``pid_timeout``.

    The PID is read by a thread so a stalled SSH session cannot block this call. On timeout or an
    early exit the local ssh child is killed and an error is raised; whatever did start remotely is
    still bounded by its own ``timeout``.
    """
    props = f'{{ application.name = "{app_name}" node.name = "{app_name}" media.name = "{app_name}" }}'
    pw = ["pw-record", "--target", source, "--rate", str(rate), "--channels", str(channels), "--format", "s16", "-P", props, remote_wav]
    wrapper = "{cmd} & p=$!; echo PID=$p; wait $p; echo EXIT=$?".format(cmd=" ".join(shlex.quote(p) for p in bounded_remote(pw, max_seconds)))
    argv = host.command(["sh", "-c", wrapper])
    proc = runner.popen(argv)
    rec = RemoteRecord(proc=proc, max_seconds=max_seconds, app_name=app_name)
    if proc.stdout is None:
        proc.kill()
        raise RemoteAudioError("record process has no stdout")
    rec.reader = threading.Thread(target=_read_stdout, args=(rec,), name="pw-record-stdout", daemon=True)
    rec.reader.start()
    rec.pid_event.wait(pid_timeout)
    if rec.pid is None:
        rc = proc.poll()
        proc.kill()
        err = ""
        try:
            err = proc.stderr.read() if proc.stderr else ""
        except (OSError, ValueError):
            pass
        why = f"exited rc={rc}" if rc is not None else f"no PID line within {pid_timeout}s"
        raise RemoteAudioError(f"pw-record did not report a PID ({why}): {err[-400:]}")
    return rec


def remote_signal(host: Host, runner: Runner, pid: int, sig: str, timeout: float = 10.0) -> int:
    out = runner.run(host.command(["kill", f"-{sig}", str(pid)]), timeout=timeout)
    return out.returncode


def stop_record(host: Host, runner: Runner, rec: RemoteRecord, *, grace: float = 10.0) -> dict[str, Any]:
    """SIGINT the exact pw-record PID so the WAV header is finalized; TERM then local kill on timeout."""
    result: dict[str, Any] = {"pid": rec.pid, "signals": []}
    if rec.pid is not None and rec.proc.poll() is None:
        result["signals"].append({"sig": "INT", "rc": remote_signal(host, runner, rec.pid, "INT")})
        try:
            rec.proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            result["signals"].append({"sig": "TERM", "rc": remote_signal(host, runner, rec.pid, "TERM")})
            try:
                rec.proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                rec.proc.kill()
                result["signals"].append({"sig": "local_kill", "rc": None})
    try:
        rec.proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        rec.proc.kill()
        rec.proc.wait(timeout=grace)
    if rec.reader is not None:
        rec.reader.join(timeout=grace)
    err = ""
    try:
        err = rec.proc.stderr.read() if rec.proc.stderr else ""
    except (OSError, ValueError):
        pass
    result["rc"] = rec.proc.returncode
    result["stdout_tail"] = rec.stdout_lines[-5:]
    result["stderr_tail"] = (err or "")[-400:]
    result["remote_lifetime_cap_s"] = rec.max_seconds
    return result


def play(host: Host, runner: Runner, sink: str, remote_wav: str, *, volume: float, max_seconds: float, local_margin_s: float = 10.0) -> subprocess.CompletedProcess:
    """pw-play under setsid+timeout (remote cap ``max_seconds``) with a slightly longer local wait."""
    if not 0.0 < volume <= 1.0:
        raise RemoteAudioError(f"stream volume must be in (0, 1]: {volume}")
    cmd = bounded_remote(["pw-play", "--target", sink, "--volume", f"{volume:.3f}", remote_wav], max_seconds, kill_after=5)
    try:
        return runner.run(host.command(cmd), timeout=max_seconds + local_margin_s)
    except subprocess.TimeoutExpired as exc:
        raise RemoteAudioError(f"pw-play ssh did not return within {max_seconds + local_margin_s:.0f}s (remote lifetime capped at {max_seconds:.0f}s)") from exc


def copy_to(host: Host, runner: Runner, local: Path, remote: str, timeout: float = 120.0) -> None:
    out = runner.run(["scp", "-q", str(local), f"{host.scp_target}:{remote}"], timeout=timeout)
    if out.returncode != 0:
        raise RemoteAudioError(f"scp to {host.scp_target}:{remote} failed: {out.stderr.decode(errors='replace')[-300:]}")


def copy_from(host: Host, runner: Runner, remote: str, local: Path, timeout: float = 300.0) -> None:
    out = runner.run(["scp", "-q", f"{host.scp_target}:{remote}", str(local)], timeout=timeout)
    if out.returncode != 0:
        raise RemoteAudioError(f"scp from {host.scp_target}:{remote} failed: {out.stderr.decode(errors='replace')[-300:]}")


def remote_sha256(host: Host, runner: Runner, remote: str, timeout: float = 60.0) -> str | None:
    out = runner.run(host.command(["sha256sum", remote]), timeout=timeout)
    if out.returncode != 0:
        return None
    return out.stdout.decode().split()[0] if out.stdout.strip() else None


def remote_mktemp(host: Host, runner: Runner, run_id: str) -> str:
    out = runner.run(host.command(["mktemp", "-d", f"/tmp/device-eval-{run_id}.XXXXXX"]), timeout=15.0)
    if out.returncode != 0:
        raise RemoteAudioError(f"mktemp on {host.scp_target} failed: {out.stderr.decode(errors='replace')[-200:]}")
    return out.stdout.decode().strip()


def remote_remove(host: Host, runner: Runner, paths: list[str]) -> None:
    """Remove only this run's files, by exact path; errors are reported by the caller's receipt."""
    for p in paths:
        if not p.startswith("/tmp/device-eval-"):
            raise RemoteAudioError(f"refusing to remove non-run path {p}")
    runner.run(host.command(["rm", "-f", *paths]), timeout=15.0)
    runner.run(host.command(["rmdir", *sorted({str(Path(p).parent) for p in paths})]), timeout=15.0)


# ----- orchestration -----

@dataclass
class PlayRecordConfig:
    fixture: Path
    out_dir: Path
    fixture_duration_s: float
    desktop: Host = field(default_factory=lambda: Host.parse(DESKTOP_SSH))
    sparkb: Host = field(default_factory=lambda: Host.parse(SPARKB_SSH))
    sink: str = DESKTOP_SINK
    source: str = SPARKB_SOURCE
    stream_volume: float = 0.5
    pre_roll_s: float = 2.0
    tail_s: float = 3.0
    readiness_timeout_s: float = 15.0
    readiness_poll_s: float = 0.5
    rate: int = 48000
    play_margin_s: float = 15.0
    record_margin_s: float = 30.0
    keep_remote: bool = False

    @property
    def record_lifetime_s(self) -> float:
        """Hard cap on the remote pw-record, whatever happens to SSH or this process."""
        return self.pre_roll_s + self.fixture_duration_s + self.tail_s + self.record_margin_s

    @property
    def play_lifetime_s(self) -> float:
        return self.fixture_duration_s + self.play_margin_s


def play_record(cfg: PlayRecordConfig, receipt: Receipt, runner: Runner | None = None, wav_duration: Callable[[Path], float] | None = None) -> dict[str, Any]:
    """Desktop plays the fixture once while Spark-B records the room; returns paths and timings.

    Order: resolve both nodes by name -> copy fixture to the desktop -> start pw-record -> wait
    for the running capture stream (bounded) -> pre-roll -> pw-play (bounded) -> tail -> SIGINT
    pw-record -> copy the room WAV back and verify its hash -> remove this run's remote files.
    """
    runner = runner or Runner()
    out: dict[str, Any] = {"room_wav": None, "timings": {}}
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    rec: RemoteRecord | None = None
    remote_fixture = remote_room = None
    desk_dir = spark_dir = None
    try:
        sink = node_summary(find_node(pw_dump(cfg.desktop, runner), cfg.sink))
        receipt.action("desktop_sink", **(sink or {"missing": cfg.sink}))
        if sink is None:
            receipt.blocked(f"desktop sink {cfg.sink} not found by node.name")
            return out
        source = node_summary(find_node(pw_dump(cfg.sparkb, runner), cfg.source))
        receipt.action("sparkb_source", **(source or {"missing": cfg.source}))
        if source is None:
            receipt.blocked(f"Spark-B source {cfg.source} not found by node.name")
            return out

        desk_dir = remote_mktemp(cfg.desktop, runner, receipt.run_id)
        spark_dir = remote_mktemp(cfg.sparkb, runner, receipt.run_id)
        remote_fixture = f"{desk_dir}/{cfg.fixture.name}"
        remote_room = f"{spark_dir}/room.wav"
        copy_to(cfg.desktop, runner, cfg.fixture, remote_fixture)
        fixture_sha = sha256_file(cfg.fixture)
        receipt.check("fixture_copied_intact", remote_sha256(cfg.desktop, runner, remote_fixture) == fixture_sha, reason="remote sha256 differs", sha256=fixture_sha)

        app_name = unique_app_name(receipt.run_id)
        rec = start_record(cfg.sparkb, runner, cfg.source, remote_room, rate=cfg.rate, channels=1, app_name=app_name, max_seconds=cfg.record_lifetime_s)
        out["timings"]["record_started"] = receipt.elapsed()
        receipt.action("pw_record_started", pid=rec.pid, remote_wav=remote_room, application_name=app_name, remote_lifetime_cap_s=cfg.record_lifetime_s)

        ready = None
        deadline = time.monotonic() + cfg.readiness_timeout_s
        while ready is None:
            if rec.proc.poll() is not None:
                break
            ready = record_stream_ready(pw_dump(cfg.sparkb, runner), app_name)
            if ready is None:
                if time.monotonic() >= deadline:
                    break
                runner.sleep(cfg.readiness_poll_s)
        out["timings"]["stream_ready"] = receipt.elapsed() if ready else None
        if not receipt.check("record_stream_running", ready is not None, reason="no running Stream/Input/Audio node for this run's pw-record before the deadline; playback not started", stream=ready):
            return out

        runner.sleep(cfg.pre_roll_s)
        out["timings"]["play_started"] = receipt.elapsed()
        played = play(cfg.desktop, runner, cfg.sink, remote_fixture, volume=cfg.stream_volume, max_seconds=cfg.play_lifetime_s)
        out["timings"]["play_finished"] = receipt.elapsed()
        receipt.action("pw_play", rc=played.returncode, stderr_tail=played.stderr.decode(errors="replace")[-300:], volume=cfg.stream_volume, duration_s=round(out["timings"]["play_finished"] - out["timings"]["play_started"], 3), remote_lifetime_cap_s=cfg.play_lifetime_s, note="transport only: a zero exit proves pw-play ran, not that the room heard anything")
        receipt.check("playback_exit_ok", played.returncode == 0, reason=f"pw-play rc={played.returncode}")
        receipt.check("record_alive_during_playback", rec.proc.poll() is None, reason="pw-record exited before playback ended")

        runner.sleep(cfg.tail_s)
    finally:
        if rec is not None:
            stopped = stop_record(cfg.sparkb, runner, rec)
            out["timings"]["record_stopped"] = receipt.elapsed()
            receipt.action("pw_record_stopped", **stopped)
            if remote_room:
                local_room = cfg.out_dir / "room.wav"
                try:
                    copy_from(cfg.sparkb, runner, remote_room, local_room)
                    remote_sha = remote_sha256(cfg.sparkb, runner, remote_room)
                    receipt.check("room_wav_copied_intact", remote_sha == sha256_file(local_room), reason="sha256 differs between Spark-B and local copy")
                    receipt.evidence("room_wav", local_room)
                    out["room_wav"] = local_room
                    if wav_duration is not None:
                        dur = wav_duration(local_room)
                        out["room_duration_s"] = dur
                        expected = cfg.pre_roll_s + cfg.fixture_duration_s + cfg.tail_s
                        receipt.check("room_wav_duration_plausible", dur >= 0.9 * expected, reason=f"room.wav is {dur:.1f}s, expected about {expected:.1f}s", duration_s=dur)
                except RemoteAudioError as exc:
                    receipt.check("room_wav_copied", False, reason=str(exc))
        if not cfg.keep_remote:
            for host, paths in ((cfg.desktop, [remote_fixture]), (cfg.sparkb, [remote_room])):
                paths = [p for p in paths if p]
                if paths:
                    try:
                        remote_remove(host, runner, paths)
                        receipt.action("remote_cleanup", host=host.scp_target, removed=paths)
                    except (RemoteAudioError, subprocess.SubprocessError) as exc:
                        receipt.action("remote_cleanup_failed", host=host.scp_target, error=str(exc))
    return out
