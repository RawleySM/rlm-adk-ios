#!/usr/bin/env python3
"""Seed the simulator app's Documents with one retained ambient capture (a real 1-second WAV and a
captures.json the app decodes) plus an orphan capture directory no manifest lists. CI only."""

import json
import math
import struct
import sys
import wave
from pathlib import Path

documents, capture_id = Path(sys.argv[1]), sys.argv[2]
seg_dir = documents / "captures" / capture_id
seg_dir.mkdir(parents=True, exist_ok=True)
with wave.open(str(seg_dir / "00000000.wav"), "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 16000))) for i in range(16000)))
orphan = documents / "captures" / "ambient-ci-orphan"
orphan.mkdir(parents=True, exist_ok=True)
(orphan / "00000000.wav").write_bytes(b"RIFF" + bytes(64))
# Swift's default Date coding: seconds since 2001-01-01. Enum with associated values: {"case":{}}.
created = 781_000_000.0
manifest = [{"captureId": capture_id, "kind": "ambient", "createdAt": created, "segments": [], "gaps": [], "status": {"savedLocally": {}}}]
(documents / "captures.json").write_text(json.dumps(manifest))
print(f"seeded {seg_dir / '00000000.wav'} ({(seg_dir / '00000000.wav').stat().st_size} bytes) and orphan {orphan}")
