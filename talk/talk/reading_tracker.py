"""Track how far a verbatim Scripture reading got, so ``continue_reading`` can resume it.

``read_scripture`` pushes one ``TTSSpeakFrame`` per verse and records the verses in
``self.state``. This processor sits after the TTS and watches the ``TTSTextFrame`` units
the TTS actually spoke; each one that equals a verse text advances ``spoken_through``. An
interruption (barge-in, prayer, quiet) just stops the advance; whatever was reached is the resume
point. Nothing here talks to the LLM.
"""

from __future__ import annotations

import re

from loguru import logger
from pipecat.frames.frames import Frame, InterruptionFrame, TTSTextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor



def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", text.lower()).split())


class ReadingTracker(FrameProcessor):
    """Advances ``self.state['spoken_through']`` as verses are spoken."""

    def __init__(self, reading_state: dict):
        super().__init__(name="reading-tracker")
        self.state = reading_state
        self.events: list[dict] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSTextFrame) and direction == FrameDirection.DOWNSTREAM:
            spoken = _norm(frame.text or "")
            verses = self.state.get("verses") or []
            if spoken and verses:
                for verse in verses:
                    if _norm(verse["text"]) == spoken:
                        self.state["spoken_through"] = max(int(self.state.get("spoken_through") or 0), verse["verse"])
                        break
        elif isinstance(frame, InterruptionFrame):
            if self.state.get("verses"):
                through = self.state.get("spoken_through")
                self.events.append({"reference": self.state.get("reference"), "spoken_through": through})
                logger.info(f"reading tracker: interrupted after verse {through} of {self.state.get('reference')}")
        await self.push_frame(frame, direction)
