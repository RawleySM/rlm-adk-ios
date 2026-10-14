"""Bridge phrases: when the first spoken reply would be late, say a short phrase so the silence
does not read as a dropped turn (the dossier's acoustic bridge filler).

Placed between the LLM and TTS. The timer starts when the user's turn ends
(``UserStoppedSpeakingFrame``) and is cancelled by the first bot audio (``BotStartedSpeakingFrame``),
an interruption, or a mode that forbids speech. A bridge is spoken at most once per turn.
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
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
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
        if isinstance(frame, UserStoppedSpeakingFrame):
            self._cancel()
            if self._floor.mode == "available":
                self._timer = asyncio.create_task(self._fire(time.time()))
        elif isinstance(frame, (BotStartedSpeakingFrame, InterruptionFrame, UserStartedSpeakingFrame)):
            self._cancel()
        await self.push_frame(frame, direction)
