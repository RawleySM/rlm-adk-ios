"""Floor control: prayer by address, the amen rule, quiet/wake, and the output gate.

``FloorProcessor`` sits right after the STT service. It owns the conversation mode
(``available`` | ``prayer`` | ``quiet``) and announces every change to the client as a transport
message ``{"type": "mode", "mode": ..., "reason": ...}`` so the phone can mark its own capture.

Prayer: an utterance that begins with an address phrase (``cfg.address_phrases``) enters prayer; the
client may also enter it earlier from its on-device partial transcript with
``{"type": "client_mode", "mode": "prayer"}``. While praying, no transcript reaches the LLM aggregator
(nothing is ever answered) and the ``OutputGate`` drops every bot audio and text frame. Prayer ends
when a final transcript ends with an amen phrase and ``cfg.amen_silence_s`` pass with no new speech;
new speech cancels the pending end. Silence alone never ends prayer, and nothing is spoken at the end
(MODE-04: save and wait).

Quiet: ``quiet please`` (or the client message) suppresses output until ``wake up``; capture and
backend work continue because they never depended on this process.
"""

from __future__ import annotations

import asyncio
import re
import time

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InputTransportMessageFrame,
    InterimTranscriptionFrame,
    LLMTextFrame,
    OutputTransportMessageUrgentFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from talk.config import TalkConfig


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z' ]+", " ", text.lower()).split())


class FloorProcessor(FrameProcessor):
    """Mode owner placed after STT; see the module docstring."""

    def __init__(self, cfg: TalkConfig):
        super().__init__(name="floor")
        self._cfg = cfg
        self.mode = "available"
        self._task = None
        self._vad_stopped_at: float | None = None
        self._user_speaking = False
        self._pending_amen: asyncio.Task | None = None
        self._address = sorted((_norm(p) for p in cfg.address_phrases), key=len, reverse=True)
        self._amen = sorted((_norm(p) for p in cfg.amen_phrases), key=len, reverse=True)
        self._quiet = [_norm(p) for p in cfg.quiet_phrases]
        self._wake = [_norm(p) for p in cfg.wake_phrases]
        self.events: list[dict] = []

    def bind(self, task) -> None:
        self._task = task

    # ----- mode transitions -----
    async def set_mode(self, mode: str, reason: str) -> None:
        if mode == self.mode:
            return
        previous = self.mode
        self.mode = mode
        stamp = time.time()
        self.events.append({"t": stamp, "mode": mode, "from": previous, "reason": reason})
        logger.info(f"floor: {previous} -> {mode} ({reason})")
        if mode in {"prayer", "quiet"}:
            # Silence whatever is playing or queued, now.
            await self.broadcast_interruption()
        await self.push_frame(
            OutputTransportMessageUrgentFrame(message={"type": "mode", "mode": mode, "from": previous, "reason": reason, "t": stamp})
        )

    def _starts_with_address(self, text: str) -> str | None:
        n = _norm(text)
        for phrase in self._address:
            if n == phrase or n.startswith(phrase + " "):
                return phrase
        return None

    def _ends_with_amen(self, text: str) -> bool:
        n = _norm(text)
        return any(n == p or n.endswith(" " + p) for p in self._amen)

    def _cancel_pending_amen(self) -> None:
        if self._pending_amen and not self._pending_amen.done():
            self._pending_amen.cancel()
        self._pending_amen = None

    async def _end_prayer_after_silence(self, heard_at: float) -> None:
        # The amen was heard in a final transcript that arrived after the VAD stop; the silence
        # window counts from that stop, not from the transcript's arrival.
        anchor = self._vad_stopped_at or heard_at
        remaining = self._cfg.amen_silence_s - (time.time() - anchor)
        if remaining > 0:
            await asyncio.sleep(remaining)
        if self._user_speaking:
            logger.info("floor: amen pending but speech resumed; prayer continues")
            return
        await self.set_mode("available", "amen")

    # ----- frames -----
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._user_speaking = True
            if self.mode == "prayer":
                self._cancel_pending_amen()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._user_speaking = False
            self._vad_stopped_at = time.time()

        if isinstance(frame, InputTransportMessageFrame):
            message = frame.message or {}
            kind = message.get("type")
            if kind == "client_mode":
                mode = message.get("mode")
                if mode in {"prayer", "available", "quiet"}:
                    await self.set_mode(mode, f"client:{message.get('reason', 'request')}")
                return
            if kind == "client_event":
                self.events.append({"t": time.time(), "client": message})
                return
            if kind == "client_text":
                # Typed input from the app or the text harness: it becomes a user turn like a final transcript.
                text = str(message.get("text") or "").strip()
                if text:
                    from pipecat.utils.time import time_now_iso8601

                    await self.process_frame(TranscriptionFrame(text, "client", time_now_iso8601()), direction)
                return

        if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            text = frame.text or ""
            final = isinstance(frame, TranscriptionFrame)
            n = _norm(text)
            if self.mode == "prayer":
                if final and self._ends_with_amen(text):
                    logger.info(f"floor: amen heard in {text!r}")
                    self._cancel_pending_amen()
                    self._pending_amen = asyncio.create_task(self._end_prayer_after_silence(time.time()))
                # Prayer text never reaches the LLM: it is addressed to God, journaled by the app.
                await self.push_frame(
                    OutputTransportMessageUrgentFrame(message={"type": "prayer_text", "text": text, "final": final})
                )
                return
            if final and any(n == p or n.endswith(" " + p) for p in self._quiet):
                await self.set_mode("quiet", "phrase")
                return
            if final and self.mode == "quiet" and any(n == p or n.startswith(p) for p in self._wake):
                await self.set_mode("available", "wake")
                return
            address = self._starts_with_address(text)
            if address and self.mode != "prayer":
                await self.set_mode("prayer", f"address:{address}")
                await self.push_frame(
                    OutputTransportMessageUrgentFrame(message={"type": "prayer_text", "text": text, "final": final})
                )
                return
            if self.mode == "quiet" and final:
                # Heard, journaled by the app, not answered.
                return

        await self.push_frame(frame, direction)


class OutputGate(FrameProcessor):
    """Drops every spoken output while the floor is in prayer or quiet; evaluated per frame."""

    def __init__(self, floor: FloorProcessor):
        super().__init__(name="output-gate")
        self._floor = floor

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and self._floor.mode in {"prayer", "quiet"}:
            if isinstance(frame, (TTSAudioRawFrame, TTSTextFrame, TTSSpeakFrame, LLMTextFrame, TTSStartedFrame, TTSStoppedFrame)):
                return
        await self.push_frame(frame, direction)
