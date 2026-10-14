"""Backchannel versus barge-in, the dossier's dual-criteria rule, with ducking.

``BackchannelAwareStartStrategy`` replaces the plain VAD start strategy. When the bot is silent a
user turn starts on the first VAD frame (fastest path). When the bot is speaking, the strategy waits
``cfg.backchannel_max_s`` (320 ms by default): speech that ends inside that window is a backchannel
("mm-hmm", "yeah") and never interrupts; speech that is still going when the window closes starts
the user turn, which interrupts the bot. Cohere transcribes per utterance, so a word list cannot be
consulted in time; duration is the criterion, as in the dossier.

``DuckProcessor`` sits between TTS and the output gate: from the moment user speech is detected
while the bot speaks until ``cfg.duck_hold_s`` after it ends, bot audio is attenuated by
``cfg.duck_db`` so the bot does not mask the user's voice.
"""

from __future__ import annotations

import asyncio
import time

import numpy as np
from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
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
        self.decisions: list[dict] = []

    def _cancel(self) -> None:
        if self._pending and not self._pending.done():
            self._pending.cancel()
        self._pending = None

    async def _window(self) -> None:
        # The VAD reports the end of speech ``vad_stop_secs`` after it happens, so the stop frame of a
        # backchannel that lasted ``backchannel_max_s`` arrives ``backchannel_max_s + vad_stop_secs``
        # after its start; the window must be measured on that clock.
        await asyncio.sleep(self._cfg.backchannel_max_s + self._cfg.vad_stop_secs)
        # Still speaking after the window: a real turn, which interrupts the bot.
        self.decisions.append({"t": time.time(), "decision": "barge_in", "after_s": time.time() - (self._speech_started_at or time.time())})
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
            self._pending = asyncio.create_task(self._window())
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            if self._pending and not self._pending.done():
                self._cancel()
                dur = time.time() - (self._speech_started_at or time.time()) - self._cfg.vad_stop_secs
                self.decisions.append({"t": time.time(), "decision": "backchannel", "duration_s": dur})
                logger.info(f"floor: backchannel ({dur:.2f}s) while the bot speaks; not interrupting")
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
