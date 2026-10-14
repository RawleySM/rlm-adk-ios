"""Per-session event log: every timing-relevant frame with wall-clock and monotonic stamps.

Written as JSON lines under ``<state_root>/sessions/<session_id>.jsonl`` so the benchmark can read
server-side timings for runs where the client is the phone (no probe receipt). The observer sees
frames as they are pushed between processors; one line is written the first time a frame id is
seen, so a frame travelling through several processors is logged once.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    OutputTransportMessageUrgentFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

LOGGED = (
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    TranscriptionFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMFullResponseStartFrame,
    LLMFullResponseEndFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    OutputTransportMessageUrgentFrame,
)


class SessionLog(BaseObserver):
    """Append-only JSONL of the frames above plus the first LLM text of each response."""

    def __init__(self, state_root: Path, session_id: str):
        super().__init__()
        self.path = Path(state_root) / "sessions" / f"{session_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[int] = set()
        self._llm_text_open = False
        self._file = self.path.open("a", encoding="utf-8")
        self._write({"event": "session_start", "session_id": session_id})

    def _write(self, record: dict) -> None:
        record.setdefault("wall", time.time())
        record.setdefault("mono", time.monotonic())
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        if isinstance(frame, LLMTextFrame):
            if not self._llm_text_open:
                self._llm_text_open = True
                self._write({"event": "llm_first_text", "text": frame.text})
            return
        if isinstance(frame, LLMFullResponseEndFrame):
            self._llm_text_open = False
        if not isinstance(frame, LOGGED):
            return
        if frame.id in self._seen:
            return
        self._seen.add(frame.id)
        record: dict = {"event": type(frame).__name__, "src": str(data.source), "dst": str(data.destination)}
        if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame, TTSSpeakFrame)):
            record["text"] = frame.text
        if isinstance(frame, FunctionCallInProgressFrame):
            record["function"] = frame.function_name
            record["arguments"] = frame.arguments
        if isinstance(frame, FunctionCallResultFrame):
            record["function"] = frame.function_name
            result = frame.result
            record["result_keys"] = sorted(result.keys()) if isinstance(result, dict) else type(result).__name__
            if isinstance(result, dict) and "error" in result:
                record["error"] = result["error"]
        if isinstance(frame, OutputTransportMessageUrgentFrame):
            record["message"] = frame.message
        self._write(record)

    def close(self) -> None:
        self._write({"event": "session_end"})
        self._file.close()
