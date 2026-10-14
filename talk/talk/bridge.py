"""Bridge phrases: when the first spoken reply would be late, say a short phrase so the silence
does not read as a dropped turn (the dossier's acoustic bridge filler).

Placed between the LLM and the TTS, so its ``TTSSpeakFrame`` is never queued behind a running
completion (the LLM service handles frames in order; a bridge pushed upstream of it waited for the
whole tool round-trip). The timer starts when the aggregator closes the user turn
(``UserStoppedSpeakingFrame``) provided the floor forwarded a transcript for it (``floor.pending_turn``,
never set for prayer text or backchannels) and is cancelled by the first bot audio
(``BotStartedSpeakingFrame``), an interruption or new user speech.
"""

from __future__ import annotations

import asyncio
import random
import time

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    FunctionCallResultFrame,
    LLMTextFrame,
    Frame,
    InterruptionFrame,
    TTSSpeakFrame,
    TTSTextFrame,
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
        self._followup: asyncio.Task | None = None
        self.bridges: list[dict] = []

    def _cancel(self) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    def _cancel_followup(self) -> None:
        if self._followup and not self._followup.done():
            self._followup.cancel()
        self._followup = None

    async def _fire_followup(self, turn_ended_at: float) -> None:
        await asyncio.sleep(self._cfg.bridge_followup_s)
        if self._floor.mode != "available" or not self._cfg.bridge_followup_phrase:
            return
        self.bridges.append({"t": time.time(), "after_s": time.time() - turn_ended_at, "phrase": self._cfg.bridge_followup_phrase, "followup": True})
        logger.info(f"bridge: follow-up after {time.time() - turn_ended_at:.1f}s with no reply")
        await self.push_frame(TTSSpeakFrame(self._cfg.bridge_followup_phrase))

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
            # The aggregator closed the user turn: the LLM runs now if the floor forwarded a transcript.
            self._cancel()
            self._cancel_followup()
            if self._floor.pending_turn and self._floor.mode == "available":
                self._timer = asyncio.create_task(self._fire(time.time()))
                if self._cfg.bridge_followup_s > 0:
                    self._followup = asyncio.create_task(self._fire_followup(time.time()))
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._cancel()
            self._cancel_followup()
        elif isinstance(frame, InterruptionFrame):
            self._cancel()
            self._cancel_followup()
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._cancel()  # the bridge phrase itself starts bot speech; the follow-up waits for a real reply
        elif isinstance(frame, (LLMTextFrame, TTSSpeakFrame, FunctionCallResultFrame)) and direction == FrameDirection.DOWNSTREAM:
            self._cancel_followup()  # the model or a tool is answering
        await self.push_frame(frame, direction)


class ContextScrubber(FrameProcessor):
    """Keeps host-spoken bridge phrases out of the assistant's context.

    The bridge speaks "One moment." through the TTS, and the assistant aggregator records whatever
    was spoken as assistant history. After a few turns the model imitated that history and answered
    "One moment." with no tool call (phone run 2026-10-14 05:48 UTC). This processor sits right
    before the assistant aggregator and drops ``TTSTextFrame`` units equal to a bridge phrase.
    """

    def __init__(self, cfg: TalkConfig):
        super().__init__(name="context-scrubber")
        self._phrases = {p.strip().lower() for p in [*cfg.bridge_phrases, cfg.bridge_followup_phrase] if p}

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSTextFrame) and direction == FrameDirection.DOWNSTREAM and (frame.text or "").strip().lower() in self._phrases:
            return
        await self.push_frame(frame, direction)
