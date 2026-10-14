"""Backchannel versus barge-in, the dossier's dual-criteria rule, with ducking.

``BackchannelAwareStartStrategy`` replaces the plain VAD start strategy. When the bot is silent a
user turn starts on the first VAD frame (fastest path). When the bot is speaking, the strategy waits
``cfg.backchannel_max_s``: speech that ends inside that window is a backchannel ("mm-hmm", "yeah")
and never interrupts; speech that is still going when the window closes starts the user turn, which
interrupts the bot. Cohere transcribes per utterance, so a word list cannot be consulted in time;
duration is the criterion, as in the dossier.

"Still going" is read from the uplink energy, not only from the VAD: the VAD reports the end of
speech ``vad_stop_secs`` (0.8 s) after it happens, so waiting for its verdict put the cut at
``backchannel_max_s + vad_stop_secs`` (1.55 s) after the VAD start. At the window end the strategy
now looks at the last ``cfg.barge_in_voiced_window_s`` of input frames: voiced (RMS above
``cfg.barge_in_min_rms`` and above 30 % of the utterance's peak) means the user is talking and the
bot is cut at once; quiet means the VAD decides as before (a stop frame inside the extended window
is a backchannel, none is a soft turn).

``DuckProcessor`` sits between TTS and the output gate: from the moment user speech is detected
while the bot speaks until ``cfg.duck_hold_s`` after it ends, bot audio is attenuated by
``cfg.duck_db`` so the bot does not mask the user's voice.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

import numpy as np
from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start.base_user_turn_start_strategy import BaseUserTurnStartStrategy

from talk.config import TalkConfig


class BackchannelAwareStartStrategy(BaseUserTurnStartStrategy):
    """VAD start that tolerates short backchannels while the bot is speaking."""

    def __init__(self, cfg: TalkConfig, **kwargs):
        super().__init__(**kwargs)
        self._cfg = cfg
        self._bot_speaking = False
        self._pending: asyncio.Task | None = None
        self._speech_started_at: float | None = None
        # (wall time, int16 RMS) of each input frame since the VAD start, while a decision is pending.
        self._energy: deque[tuple[float, float]] = deque()
        self._peak_rms = 0.0
        self.decisions: list[dict] = []

    def _cancel(self) -> None:
        if self._pending and not self._pending.done():
            self._pending.cancel()
        self._pending = None

    def _recently_voiced(self) -> tuple[bool, float]:
        """Whether the last ``barge_in_voiced_window_s`` of uplink carried voice; returns (voiced, max RMS)."""
        now = time.time()
        recent = [rms for t, rms in self._energy if now - t <= self._cfg.barge_in_voiced_window_s]
        threshold = max(self._cfg.barge_in_min_rms, 0.3 * self._peak_rms)
        loudest = max(recent) if recent else 0.0
        return loudest >= threshold, loudest

    async def _window(self) -> None:
        started = self._speech_started_at or time.time()
        await asyncio.sleep(self._cfg.backchannel_max_s)
        voiced, loudest = self._recently_voiced()
        if voiced:
            # Still talking at the window end: a turn. Cut now rather than ``vad_stop_secs`` later.
            self.decisions.append({"t": time.time(), "decision": "barge_in", "after_s": time.time() - started, "basis": "voiced", "rms": round(loudest)})
            logger.info(f"floor: user speech still voiced (rms {loudest:.0f}) at the end of the backchannel window; interrupting")
            await self.trigger_user_turn_started()
            return
        # Quiet at the window end: the VAD's stop frame, which arrives ``vad_stop_secs`` after the
        # speech ended, tells a backchannel from a soft turn. Wait for it on that clock.
        await asyncio.sleep(self._cfg.vad_stop_secs)
        self.decisions.append({"t": time.time(), "decision": "barge_in", "after_s": time.time() - started, "basis": "no_vad_stop"})
        logger.info("floor: user speech outlasted the backchannel window; interrupting")
        await self.trigger_user_turn_started()

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, (BotStoppedSpeakingFrame, InterruptionFrame)):
            self._bot_speaking = False
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._speech_started_at = time.time()
            if not self._bot_speaking:
                await self.trigger_user_turn_started()
                return ProcessFrameResult.STOP
            self._cancel()
            self._energy.clear()
            self._peak_rms = 0.0
            self._pending = asyncio.create_task(self._window())
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            if self._pending and not self._pending.done():
                self._cancel()
                dur = time.time() - (self._speech_started_at or time.time()) - self._cfg.vad_stop_secs
                self.decisions.append({"t": time.time(), "decision": "backchannel", "duration_s": dur})
                logger.info(f"floor: backchannel ({dur:.2f}s) while the bot speaks; not interrupting")
        elif isinstance(frame, InputAudioRawFrame) and self._pending and not self._pending.done():
            pcm = np.frombuffer(frame.audio, dtype=np.int16)
            if pcm.size:
                rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))
                now = time.time()
                self._energy.append((now, rms))
                self._peak_rms = max(self._peak_rms, rms)
                while self._energy and now - self._energy[0][0] > 1.0:
                    self._energy.popleft()
        return ProcessFrameResult.CONTINUE

    async def reset(self):
        await super().reset()
        self._cancel()


class DuckProcessor(FrameProcessor):
    """Attenuates bot audio while the user speaks over it."""

    def __init__(self, cfg: TalkConfig):
        super().__init__(name="duck")
        self._cfg = cfg
        self._gain = 10 ** (-cfg.duck_db / 20)
        self._bot_speaking = False
        self._duck_until: float = 0.0
        self._user_speaking = False
        self.ducks: list[dict] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, (BotStoppedSpeakingFrame, InterruptionFrame)):
            self._bot_speaking = False
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._user_speaking = True
            if self._bot_speaking:
                self._duck_until = float("inf")
                self.ducks.append({"t": time.time(), "event": "duck"})
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._user_speaking = False
            if self._duck_until == float("inf"):
                self._duck_until = time.time() + self._cfg.duck_hold_s
        if isinstance(frame, TTSAudioRawFrame) and direction == FrameDirection.DOWNSTREAM and time.time() < self._duck_until:
            samples = np.frombuffer(frame.audio, dtype=np.int16).astype(np.float32) * self._gain
            frame.audio = np.clip(samples, -32768, 32767).astype(np.int16).tobytes()
        await self.push_frame(frame, direction)
