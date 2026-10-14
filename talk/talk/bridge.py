"""Bridge phrases: when the first spoken reply would be late, say a short phrase so the silence
does not read as a dropped turn (the dossier's acoustic bridge filler).

Placed between the floor and the user aggregator, so it only ever sees transcripts the floor let
through (never prayer text). The timer starts when the utterance's final transcript arrives
(``TranscriptionFrame``: the words are known, the LLM is about to run) and is cancelled by the first
bot audio (``BotStartedSpeakingFrame``, a system frame that reaches every processor), an interruption,
new user speech, or a mode that forbids speech. A bridge is spoken at most once per turn. The
``TTSSpeakFrame`` it pushes travels through the aggregator and the LLM untouched to the TTS.
"""

from __future__ import annotations

import asyncio
import random
import time

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    Frame,
    InterruptionFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from talk.config import TalkConfig


class BridgeProcessor(FrameProcessor):
    """Speaks one of ``cfg.bridge_phrases`` when the reply is later than ``cfg.bridge_after_s``."""

    def __init__(self, cfg: TalkConfig, floor):
        super().__init__(name="bridge")
        self._cfg = cfg
        self._floor = floor
        self._timer: asyncio.Task | None = None
        self.bridges: list[dict] = []

    def _cancel(self) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    async def _fire(self, turn_ended_at: float) -> None:
        await asyncio.sleep(self._cfg.bridge_after_s)
        if self._floor.mode != "available":
            return
        phrase = random.choice(self._cfg.bridge_phrases)
        self.bridges.append({"t": time.time(), "after_s": time.time() - turn_ended_at, "phrase": phrase})
        logger.info(f"bridge: {phrase!r} after {time.time() - turn_ended_at:.2f}s")
        await self.push_frame(TTSSpeakFrame(phrase))

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            self._cancel()
            if self._floor.mode == "available":
                self._timer = asyncio.create_task(self._fire(time.time()))
        elif isinstance(frame, (BotStartedSpeakingFrame, InterruptionFrame, UserStartedSpeakingFrame)):
            self._cancel()
        await self.push_frame(frame, direction)
