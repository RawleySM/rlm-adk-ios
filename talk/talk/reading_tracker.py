"""Remember where a Scripture reading was interrupted so the next turn can resume it.

``read_scripture`` records the verses it returned (``tools.LAST_READING``). This processor sits
after the TTS and accumulates the sentences actually sent to the speaker (``TTSTextFrame``). When an
interruption arrives while a reading is in progress, it finds the last verse whose text had been
spoken in full and appends a system note to the LLM context: the reading of <reference> stopped
after verse N; if asked to continue, call ``read_scripture`` with ``<book> <chapter>:N+1-<last>``
and read from there without repeating. The note travels downstream to the assistant context
aggregator, so it is in the context for the next user turn without running the LLM now.
"""

from __future__ import annotations

import re

from loguru import logger
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    LLMMessagesAppendFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from talk import tools


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", text.lower()).split())


class ReadingTracker(FrameProcessor):
    """Tracks spoken verses of the last ``read_scripture`` result and notes interruptions."""

    def __init__(self):
        super().__init__(name="reading-tracker")
        self._spoken: list[str] = []
        self.notes: list[dict] = []

    def _last_spoken_verse(self) -> int | None:
        reading = tools.LAST_READING
        if not reading:
            return None
        spoken = _norm(" ".join(self._spoken))
        last = None
        for verse in reading.get("verses") or []:
            text = _norm(str(verse.get("text") or ""))
            if not text:
                continue
            # A verse counts as heard when its first and last few words were both spoken.
            words = text.split()
            head = " ".join(words[:4])
            tail = " ".join(words[-4:])
            if head in spoken and tail in spoken:
                last = int(verse.get("verse") or 0)
        return last

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSTextFrame) and direction == FrameDirection.DOWNSTREAM:
            self._spoken.append(frame.text or "")
        elif isinstance(frame, InterruptionFrame):
            reading = tools.LAST_READING
            if reading and self._spoken:
                last = self._last_spoken_verse()
                verses = reading.get("verses") or []
                final = max((int(v.get("verse") or 0) for v in verses), default=0)
                if last is not None and last < final:
                    ref = reading.get("reference") or ""
                    chapter_ref = reading.get("chapter_reference") or ref
                    note = (
                        f"The reading of {ref} was interrupted after verse {last}. If Rawley asks to continue or "
                        f"to pick up where you stopped, call read_scripture with the reference "
                        f"'{chapter_ref}:{last + 1}-{final}' and read it from there; do not repeat verses 1 to {last}."
                    )
                    self.notes.append({"reference": ref, "last_verse": last, "final": final})
                    logger.info(f"reading tracker: {note}")
                    await self.push_frame(LLMMessagesAppendFrame(messages=[{"role": "system", "content": note}], run_llm=False))
            self._spoken = []
        elif isinstance(frame, BotStoppedSpeakingFrame):
            pass
        await self.push_frame(frame, direction)
