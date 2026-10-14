"""Track how far a verbatim Scripture reading got, so ``continue_reading`` can resume it.

``read_scripture`` pushes one ``TTSSpeakFrame`` per verse and records the verses in
``tools.READING_STATE``. This processor sits after the TTS and watches the ``TTSTextFrame`` units
the TTS actually spoke; each one that equals a verse text advances ``spoken_through``. An
interruption (barge-in, prayer, quiet) just stops the advance; whatever was reached is the resume
point. Nothing here talks to the LLM.
"""

from __future__ import annotations

import re

from loguru import logger
from pipecat.frames.frames import Frame, InterruptionFrame, TTSTextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from talk import tools


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", text.lower()).split())


class ReadingTracker(FrameProcessor):
    """Advances ``tools.READING_STATE['spoken_through']`` as verses are spoken."""

    def __init__(self):
        super().__init__(name="reading-tracker")
        self.events: list[dict] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSTextFrame) and direction == FrameDirection.DOWNSTREAM:
            spoken = _norm(frame.text or "")
            verses = tools.READING_STATE.get("verses") or []
            if spoken and verses:
                for verse in verses:
                    if _norm(verse["text"]) == spoken:
                        tools.READING_STATE["spoken_through"] = max(int(tools.READING_STATE.get("spoken_through") or 0), verse["verse"])
                        break
        elif isinstance(frame, InterruptionFrame):
            if tools.READING_STATE.get("verses"):
                through = tools.READING_STATE.get("spoken_through")
                self.events.append({"reference": tools.READING_STATE.get("reference"), "spoken_through": through})
                logger.info(f"reading tracker: interrupted after verse {through} of {tools.READING_STATE.get('reference')}")
        await self.push_frame(frame, direction)
