"""Cohere Transcribe (spark-inference) as a Pipecat segmented STT service.

The intake and the phone trial proved Cohere on 16 kHz mono WAV. Pipecat's ``SegmentedSTTService``
buffers one user turn (VAD start to VAD stop plus trailing silence) and hands it over as a WAV
container at the transport's input rate, which we keep at 16 kHz, so no resampling happens here.
Each utterance is one POST to ``/upstream/cohere-transcribe/v1/audio/transcriptions``; a cold owner
answers after its ~105 s load, so the first morning turn is slow and later ones are not.
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator

import aiohttp
import numpy as np
from loguru import logger
from pipecat.frames.frames import UserStartedSpeakingFrame, UserStoppedSpeakingFrame, VADUserStoppedSpeakingFrame, ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.settings import STTSettings
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601


class CohereSparkSTTService(SegmentedSTTService):
    """Per-utterance transcription through the spark-inference Cohere upstream."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:28080/upstream/cohere-transcribe",
        model: str = "cohere-transcribe",
        request_timeout_s: float = 180.0,
        min_rms: float = 0.0,
        **kwargs,
    ):
        kwargs.setdefault("settings", STTSettings(model=model, language=Language.EN))
        super().__init__(**kwargs)
        self._url = base_url.rstrip("/") + "/v1/audio/transcriptions"
        self._model = model
        self._min_rms = float(min_rms)
        self._timeout = aiohttp.ClientTimeout(total=request_timeout_s)

    def can_generate_metrics(self) -> bool:
        return True

    # ----- one transcript per turn -----
    # NOTE [Context]: SegmentedSTTService transcribes every VAD segment. A pause inside a sentence
    # ("Psalm twenty | three", "my son, | and what Scripture") then produced two transcripts, and
    # Cohere normalised the first half on its own ("Psalm 20?"), so the model answered the wrong
    # psalm. Smart Turn already decides where the turn ends; here the audio of a turn is kept
    # across its pauses and sent once, when the aggregator broadcasts UserStoppedSpeakingFrame.
    # Speech that never opens a turn (a backchannel under the start strategy's window) is still
    # transcribed per segment, so "mm-hmm" is not glued onto the next question.
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, UserStartedSpeakingFrame):
            self._turn_open = True
        if isinstance(frame, VADUserStoppedSpeakingFrame) and getattr(self, "_turn_open", False):
            # Keep buffering through the pause; the turn is still open.
            await FrameProcessor.process_frame(self, frame, direction)
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, UserStoppedSpeakingFrame):
            self._turn_open = False
            await FrameProcessor.process_frame(self, frame, direction)
            if self._user_speaking or len(self._audio_buffer) > self._audio_buffer_size_1s:
                await self._handle_user_stopped_speaking(frame)  # flush the whole turn
            await self.push_frame(frame, direction)
            return
        await super().process_frame(frame, direction)

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        # Energy gate: Cohere invents text ("Thank you.") on near-silent utterances that the VAD let
        # through (phone ducking artefacts, breaths). Below ``min_rms`` the utterance is not sent.
        pcm = np.frombuffer(audio[44:] if audio[:4] == b"RIFF" else audio, dtype=np.int16)
        rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))) if len(pcm) else 0.0
        seconds = len(pcm) / max(1, self.sample_rate or 16000)
        if rms < self._min_rms:
            logger.info(f"cohere stt: utterance {seconds:.2f}s rms {rms:.0f} below gate {self._min_rms:.0f}; not sent")
            yield None
            return
        await self.start_processing_metrics()
        await self.start_ttfb_metrics()
        started = time.monotonic()
        form = aiohttp.FormData()
        form.add_field("file", audio, filename="utterance.wav", content_type="audio/wav")
        form.add_field("model", self._model)
        form.add_field("response_format", "json")
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(self._url, data=form) as response:
                    body = await response.text()
                    if response.status != 200:
                        logger.warning(f"cohere stt HTTP {response.status}: {body[:200]}")
                        yield ErrorFrame(f"cohere stt HTTP {response.status}: {body[:120]}")
                        return
                    import json

                    text = (json.loads(body).get("text") or "").strip()
        except Exception as exc:  # the owner being cold or the circuit open is an ordinary condition
            logger.warning(f"cohere stt failed: {exc}")
            yield ErrorFrame(f"cohere stt failed: {exc}")
            return
        finally:
            await self.stop_ttfb_metrics()
            await self.stop_processing_metrics()
        elapsed = time.monotonic() - started
        logger.info(f"cohere stt {elapsed:.2f}s ({seconds:.2f}s rms {rms:.0f}): {text!r}")
        if text:
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601(), result={"model": self._model, "elapsed_s": elapsed})
