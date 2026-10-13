"""Physical iPhone evaluation harness: WDA attach without relaunch, UI smoke evidence, finite
SSH-driven desktop playback and Spark-B room recording, and source/room/phone audio scoring.

Stdlib only (ffmpeg/ffprobe, ssh/scp, pw-play/pw-record/pw-dump are invoked as child processes).
Every command writes a JSON receipt; nothing here signs, installs, launches runners or touches
the main app's code. See docs/voice-portal/device-evaluation-runbook.md.
"""

__all__ = ["receipt", "wda", "ui", "remote_audio", "fixture", "audio", "phone", "scoring", "cli"]
