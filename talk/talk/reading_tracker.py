"""Track how far a Scripture reading got and tell the phone, verse by verse, as the audio plays.

Placed after the output transport, where frames arrive as their audio is played: each verse is
its own TTS unit, so a ``BotStartedSpeakingFrame`` marks the start of the next queued unit and its
``TTSTextFrame`` (which Pipecat queues behind the unit's audio) marks its end. The queue of units
(``reading_state["queue"]``, see ``talk.passage``) is filled by the floor or ``read_scripture``;
this processor consumes it, advances ``spoken_count`` / ``spoken_through`` and reports through the
floor:

``{"type": "reading", "state": "verse", "verse": n, "index": i}`` when a verse starts playing,
``"verse_done"`` (with ``duration_s``) when it finished, ``"finished"`` after the last one and
``"paused"`` when an interruption (barge-in, a new passage, prayer, quiet, Pause) cut the reading.
Every reading message carries ``reference`` and ``spoken_count``. Nothing here talks to the LLM.
"""

from __future__ import annotations

import time

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    Frame,
    InterruptionFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from talk.passage import norm


class ReadingTracker(FrameProcessor):
    """Consumes the reading queue as verses play and reports progress through the floor."""

    def __init__(self, reading_state: dict, floor=None):
        super().__init__(name="reading-tracker")
        self.state = reading_state
        self.floor = floor
        self.events: list[dict] = []
        self._announced: int | None = None
        self._started_at: float | None = None

    async def _report(self, state: str, **fields) -> None:
        message = {"type": "reading", "state": state, "reference": self.state.get("reference"), "spoken_count": int(self.state.get("spoken_count") or 0), **fields}
        self.events.append({"t": time.time(), **message})
        if self.floor is not None:
            await self.floor.reading_event(message)

    async def _announce_next(self) -> None:
        queue = self.state.get("queue") or []
        if not queue or queue[0].get("index") is None:
            return
        head = queue[0]
        if self._announced == head["index"]:
            return
        self._announced = head["index"]
        self._started_at = time.time()
        await self._report("verse", verse=head["verse"], index=head["index"])

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame):
            if self.state.get("reading"):
                self.state["reading"] = False
                (self.state.get("queue") or []).clear()
                self._announced = None
                logger.info(f"reading tracker: interrupted after {self.state.get('spoken_count')} verses of {self.state.get('reference')}")
                await self._report("paused")
            if self.floor is not None:
                self.floor.interruption_seen.set()
        elif isinstance(frame, BotStartedSpeakingFrame) and self.state.get("reading"):
            await self._announce_next()
        elif isinstance(frame, TTSTextFrame) and direction == FrameDirection.DOWNSTREAM and self.state.get("reading"):
            queue = self.state.get("queue") or []
            spoken = norm(frame.text or "")
            # The unit just played is normally the head; look a little further in case a unit was skipped.
            hit = next((i for i, unit in enumerate(queue[:4]) if norm(unit["text"]) == spoken), None)
            if hit is not None:
                done = queue[: hit + 1]
                del queue[: hit + 1]
                for unit in done:
                    if unit.get("index") is None:
                        continue
                    self.state["spoken_count"] = unit["index"] + 1
                    self.state["spoken_through"] = unit["verse"]
                    duration = round(time.time() - self._started_at, 2) if self._started_at and self._announced == unit["index"] else None
                    await self._report("verse_done", verse=unit["verse"], index=unit["index"], duration_s=duration)
                if queue:
                    await self._announce_next()
                else:
                    self.state["reading"] = False
                    self._announced = None
                    await self._report("finished")
        await self.push_frame(frame, direction)
