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
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputTransportMessageFrame,
    InterimTranscriptionFrame,
    LLMTextFrame,
    InterruptionFrame,
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

    def __init__(self, cfg: TalkConfig, reading_state: dict | None = None, *, session_id: str = ""):
        super().__init__(name="floor")
        self._cfg = cfg
        self.session_id = session_id
        self.reading_state = reading_state if reading_state is not None else {}
        # Operator hold (``/admin/hold``): quiet until released; the wake phrase does not lift it.
        self.held = False
        self.mode = "available"
        self._task = None
        self._rtvi = None
        self._vad_stopped_at: float | None = None
        self._user_speaking = False
        self._pending_amen: asyncio.Task | None = None
        self._address = sorted((_norm(p) for p in cfg.address_phrases), key=len, reverse=True)
        self._amen = sorted((_norm(p) for p in cfg.amen_phrases), key=len, reverse=True)
        self._quiet = [_norm(p) for p in cfg.quiet_phrases]
        self._wake = [_norm(p) for p in cfg.wake_phrases]
        self._backchannel = {_norm(w) for w in cfg.backchannel_words}
        self._pause = {_norm(w) for w in cfg.pause_phrases}
        self._continue = [_norm(w) for w in cfg.continue_phrases]
        self.bot_speaking = False
        # True from the moment a transcript was forwarded to the LLM until the bot starts speaking.
        self.pending_turn = False
        self.events: list[dict] = []

    def bind(self, task, rtvi=None) -> None:
        self._task = task
        self._rtvi = rtvi

    async def _tell_client(self, message: dict) -> None:
        """Our bare JSON for the WebSocket client plus an RTVI server-message for the Pipecat SDK client."""
        await self.push_frame(OutputTransportMessageUrgentFrame(message=message))
        if self._rtvi is not None:
            try:
                await self._rtvi.send_server_message(message)
            except Exception as exc:  # noqa: BLE001 - the WebSocket path already has the message
                logger.warning(f"rtvi server message failed: {exc}")


    async def _swallow(self, frame) -> None:
        """Drop a transcript's words but still tell the aggregator a transcript came.

        Pipecat's turn-stop strategy waits for a finalized transcript after Smart Turn says the turn is
        complete; a transcript dropped here (prayer text, a pause word, a backchannel) left it waiting
        the full ``user_turn_stop_timeout`` (15 s) with the bot interrupted. An empty finalized
        TranscriptionFrame satisfies the wait; the aggregator ignores empty text, so no LLM turn runs.
        """
        if not isinstance(frame, TranscriptionFrame):
            return
        empty = TranscriptionFrame(text="", user_id=getattr(frame, "user_id", ""), timestamp=getattr(frame, "timestamp", ""))
        try:
            empty.finalized = True
        except Exception:  # noqa: BLE001 - older frame classes have no such attribute
            pass
        await self.push_frame(empty, FrameDirection.DOWNSTREAM)

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
        await self._tell_client({"type": "mode", "mode": mode, "from": previous, "reason": reason, "t": stamp})

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
        # VAD reports the stop ``vad_stop_secs`` after the last voiced sample; count silence from that sample.
        anchor = (self._vad_stopped_at - self._cfg.vad_stop_secs) if self._vad_stopped_at else heard_at
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

        if isinstance(frame, BotStartedSpeakingFrame):
            self.bot_speaking = True
            self.pending_turn = False
        elif isinstance(frame, (BotStoppedSpeakingFrame, InterruptionFrame)):
            self.bot_speaking = False
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
                await self._tell_client({"type": "prayer_text", "text": text, "final": final})
                await self._swallow(frame)
                return
            if final and any(n == p or n.endswith(" " + p) for p in self._quiet):
                await self.set_mode("quiet", "phrase")
                await self._swallow(frame)
                return
            if final and self.mode == "quiet" and any(n == p or n.startswith(p) for p in self._wake):
                if self.held:
                    logger.info("floor: wake phrase ignored while held")
                    await self._swallow(frame)
                    return
                await self.set_mode("available", "wake")
                await self._swallow(frame)
                return
            address = self._starts_with_address(text)
            if address and self.mode != "prayer":
                await self.set_mode("prayer", f"address:{address}")
                await self._tell_client({"type": "prayer_text", "text": text, "final": final})
                await self._swallow(frame)
                return
            if self.mode == "quiet" and final:
                # Heard, journaled by the app, not answered.
                await self._swallow(frame)
                return
            if final and n and (n in self._backchannel or (len(n.split()) <= 2 and all(w in self._backchannel for w in n.split()))):
                # "mm-hmm", "yeah", "okay": an acknowledgement, never a turn.
                logger.info(f"floor: backchannel transcript dropped: {text!r}")
                self.events.append({"t": time.time(), "backchannel_dropped": text})
                await self._swallow(frame)
                return
            lead = next((p for p in sorted(self._pause, key=len, reverse=True) if n == p or n.startswith(p + " ")), None) if final else None
            if lead is not None:
                # "hold on", "wait", "stop": stop speaking. Alone (or stacked: "Wait. Stop there."), it is
                # never a question for the LLM; followed by more words ("Hold on. What are the green
                # pastures about?") the rest is the turn.
                remainder = n[len(lead):].strip()
                while remainder:
                    more = next((p for p in sorted(self._pause, key=len, reverse=True) if remainder == p or remainder.startswith(p + " ")), None)
                    if more is None:
                        break
                    remainder = remainder[len(more):].strip()
                logger.info(f"floor: pause phrase {lead!r} in {text!r}; interrupting" + ("; waiting" if not remainder else f"; forwarding {remainder!r}"))
                self.events.append({"t": time.time(), "pause_phrase": lead, "remainder": remainder})
                if self.bot_speaking:
                    await self.broadcast_interruption()
                if not remainder or len(remainder.split()) < 2:
                    await self._swallow(frame)
                    return
                frame.text = text[len(text) - len(remainder) :] if text.lower().endswith(remainder) else remainder
                text = frame.text
                n = _norm(text)
            if final and any(p in n for p in self._continue) and len(n.split()) <= 10:
                verses = self.reading_state.get("verses") or []
                through = int(self.reading_state.get("spoken_through") or 0)
                remaining = [v for v in verses if v["verse"] > through]
                if verses and remaining:
                    # Resume the interrupted reading deterministically, without a model turn.
                    from pipecat.frames.frames import LLMMessagesAppendFrame, TTSSpeakFrame

                    logger.info(f"floor: continue phrase {text!r}; resuming {self.reading_state.get('reference')} at verse {remaining[0]['verse']}")
                    self.events.append({"t": time.time(), "resume_reading": remaining[0]["verse"]})
                    if self.bot_speaking:
                        await self.broadcast_interruption()
                    for v in remaining:
                        await self.push_frame(TTSSpeakFrame(v["text"]))
                    await self.push_frame(LLMMessagesAppendFrame(messages=[{"role": "user", "content": text}, {"role": "assistant", "content": " ".join(v["text"] for v in remaining)}], run_llm=False))
                    await self._swallow(frame)
                    return
            if final and self.bot_speaking:
                # A short utterance that outlasted nothing but is not a backchannel is an interruption
                # decided by its words rather than its length.
                logger.info(f"floor: words interrupt the bot: {text!r}")
                self.events.append({"t": time.time(), "word_interrupt": text})
                await self.broadcast_interruption()
            if final:
                self.pending_turn = True
                self.reading_state["last_user_text"] = text

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
