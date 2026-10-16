"""Client control messages, handled before the RTVI processor would discard them.

The app and the benchmark send plain JSON (``client_text``, ``client_mode``, ``client_event``,
``client_passage`` for the scripture pane's navigation, ``client_read`` for its Read/Pause,
``client_bug`` for the Bug chip); the
RTVI processor drops anything that is not an RTVI message, so this processor sits first in the
pipeline, turns ``client_text`` into a user transcript and forwards the floor messages to the
``FloorProcessor`` directly.
"""

from __future__ import annotations

import time

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InputTransportMessageFrame,
    LLMMessagesAppendFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class ClientMessages(FrameProcessor):
    """Translate our client JSON messages into pipeline actions."""

    def __init__(self, floor):
        super().__init__(name="client-messages")
        self._floor = floor

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputTransportMessageFrame):
            message = frame.message or {}
            if message.get("label") == "rtvi-ai" and message.get("type") == "client-message":
                # The Pipecat SDK (SmallWebRTC) can only send this envelope: {"t": <our type>, "d": {...}}.
                data = message.get("data") or {}
                inner = data.get("d") if isinstance(data.get("d"), dict) else {}
                message = {"type": data.get("t"), **inner}
            kind = message.get("type")
            if kind == "client_text":
                text = str(message.get("text") or "").strip()
                from talk import passage

                if self._floor.mode == "bug":
                    # Typed while reporting a bug: part of the report, never a turn.
                    await self._floor.bug_typed(text)
                    return

                if text and self._floor.reading_state.get("verses") and passage.read_request(passage.norm(text)):
                    # "Read it" typed: the same deterministic read of the pane's passage as when spoken.
                    await self._floor.read_active(restart=passage.read_request(passage.norm(text)) == "restart", user_text=text, reason="typed")
                    return
                if text:
                    # Typed input bypasses VAD and turn detection: append and run the LLM now. The
                    # output gate still enforces prayer and quiet.
                    logger.info(f"client text turn: {text!r}")
                    await self.push_frame(OutputTransportMessageUrgentFrame(message={"type": "client_text_ack", "text": text}))
                    await self.push_frame(LLMMessagesAppendFrame(messages=[{"role": "user", "content": text}], run_llm=True))
                return
            if kind == "client_mode":
                if message.get("reason") == "sync":
                    # The app syncs on every connect: tell it who answers so it can label replies.
                    await self._floor.announce_assistant()
                mode = message.get("mode")
                if mode in {"prayer", "available", "quiet"}:
                    await self._floor.set_mode(mode, f"client:{message.get('reason', 'request')}")
                return
            if kind == "client_passage":
                # The scripture pane's navigation: open this passage (pauses any reading).
                reference = str(message.get("reference") or "").strip()
                if reference:
                    await self._floor.select_passage(reference, f"client:{message.get('reason', 'select')}")
                return
            if kind == "client_read":
                # The scripture pane's Read / Pause button.
                action = message.get("action")
                if action == "pause":
                    await self._floor.pause_reading("client")
                elif action in {"play", "restart"}:
                    await self._floor.read_active(restart=action == "restart", user_text=None, reason="client")
                return
            if kind == "client_bug":
                # The Bug chip: start | stop | cancel a bug report (talk.bug_report).
                await self._floor.client_bug(message)
                return
            if kind == "client_event":
                self._floor.events.append({"t": time.time(), "client": message})
                if message.get("event") == "benchmark_client" and self._floor.held:
                    # A benchmark client connecting under an operator hold is the one session meant to talk.
                    # Nothing has been announced to this client yet, so reset silently: no mode event.
                    self._floor.held = False
                    self._floor.mode = "available"
                return
        await self.push_frame(frame, direction)
