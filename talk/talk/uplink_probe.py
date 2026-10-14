"""Measure what the phone's uplink carries while the bot speaks.

Live use on 2026-10-14 (session websocket-d33b013b89, Romans 4 read for 3 min 18 s) showed no VAD
event at all until the last verse: the app's microphone tap runs through Apple's voice processing
with the bot's playback as echo reference, and that processing attenuates the near-end voice while
the far end plays. This processor sits right after the input transport and logs the level of the
incoming PCM (int16 RMS and peak) once a second while the bot speaks and every five seconds
otherwise, tags every VAD start with the level that triggered it, and, when
``cfg.uplink_record_s`` is positive, writes the first that many seconds of uplink audio of the
session to ``<state_root>/uplink/<session>.wav`` with a sidecar JSONL of bot-speaking spans, so a
double-talk attempt can be inspected offline. Nothing here alters the audio.
"""

from __future__ import annotations

import json
import time
import wave
from pathlib import Path

import numpy as np
from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class UplinkProbe(FrameProcessor):
    """Logs uplink levels against bot speech and optionally records the uplink."""

    def __init__(self, state_root: Path, session_id: str, *, record_s: float = 0.0, sample_rate: int = 16000):
        super().__init__(name="uplink-probe")
        self._sample_rate = sample_rate
        self._bot_speaking = False
        self._bot_since: float | None = None
        self._window: list[np.ndarray] = []
        self._window_started = time.time()
        self._last_level: tuple[float, float] = (0.0, 0.0)
        self._levels: list[dict] = []
        self.events: list[dict] = []
        self._wav: wave.Wave_write | None = None
        self._sidecar = None
        self._recorded_frames = 0
        self._record_frames = int(record_s * sample_rate)
        self._started_wall = time.time()
        if self._record_frames > 0:
            folder = state_root / "uplink"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{session_id}.wav"
            self._wav = wave.open(str(path), "wb")
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)
            self._wav.setframerate(sample_rate)
            self._sidecar = (folder / f"{session_id}.jsonl").open("a", encoding="utf-8")
            self._note({"event": "record_start", "wall": self._started_wall, "limit_s": record_s})
            logger.info(f"uplink probe: recording {record_s:.0f}s of uplink to {path}")

    def _note(self, record: dict) -> None:
        self.events.append(record)
        if self._sidecar is not None:
            self._sidecar.write(json.dumps(record) + "\n")
            self._sidecar.flush()

    def _flush_window(self, force: bool = False) -> None:
        now = time.time()
        cadence = 1.0 if self._bot_speaking else 5.0
        if not force and now - self._window_started < cadence:
            return
        if self._window:
            samples = np.concatenate(self._window).astype(np.float32)
            rms = float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0
            peak = float(np.max(np.abs(samples))) if samples.size else 0.0
            dbfs = 20 * np.log10(max(rms, 1e-3) / 32768.0)
            self._last_level = (rms, peak)
            record = {"wall": now, "span_s": round(now - self._window_started, 2), "rms": round(rms), "peak": round(peak), "dbfs": round(float(dbfs), 1), "bot_speaking": self._bot_speaking}
            self._levels.append(record)
            logger.info(f"uplink level: rms {rms:.0f} ({dbfs:.1f} dBFS) peak {peak:.0f} over {record['span_s']}s bot_speaking={self._bot_speaking}")
            if self._sidecar is not None:
                self._sidecar.write(json.dumps({"event": "level", **record}) + "\n")
                self._sidecar.flush()
        self._window = []
        self._window_started = now

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            if not self._bot_speaking:
                self._flush_window(force=True)
            self._bot_speaking = True
            self._bot_since = time.time()
            self._note({"event": "bot_started", "wall": time.time()})
        elif isinstance(frame, (BotStoppedSpeakingFrame, InterruptionFrame)):
            if self._bot_speaking:
                self._flush_window(force=True)
            self._bot_speaking = False
            self._note({"event": "bot_stopped" if isinstance(frame, BotStoppedSpeakingFrame) else "interruption", "wall": time.time()})
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            rms, peak = self._last_level
            since = round(time.time() - self._bot_since, 2) if self._bot_speaking and self._bot_since else None
            logger.info(f"uplink probe: VAD start at rms {rms:.0f} peak {peak:.0f} bot_speaking={self._bot_speaking} bot_for_s={since}")
            self._note({"event": "vad_start", "wall": time.time(), "rms": round(rms), "peak": round(peak), "bot_speaking": self._bot_speaking, "bot_for_s": since})
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._note({"event": "vad_stop", "wall": time.time(), "bot_speaking": self._bot_speaking})
        elif isinstance(frame, InputAudioRawFrame) and direction == FrameDirection.DOWNSTREAM:
            pcm = np.frombuffer(frame.audio, dtype=np.int16)
            if pcm.size:
                self._window.append(pcm)
                if self._wav is not None and self._recorded_frames < self._record_frames:
                    self._wav.writeframes(frame.audio)
                    self._recorded_frames += pcm.size
                    if self._recorded_frames >= self._record_frames:
                        self._note({"event": "record_limit", "wall": time.time()})
                        self._wav.close()
                        self._wav = None
            self._flush_window()
        elif isinstance(frame, EndFrame):
            self.close()
        await self.push_frame(frame, direction)

    def close(self) -> None:
        """Flush the last window and close the recording files."""
        self._flush_window(force=True)
        if self._wav is not None:
            self._wav.close()
            self._wav = None
        if self._sidecar is not None:
            self._sidecar.close()
            self._sidecar = None
