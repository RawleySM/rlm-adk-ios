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
from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.settings import STTSettings
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
        **kwargs,
    ):
        kwargs.setdefault("settings", STTSettings(model=model, language=Language.EN))
        super().__init__(**kwargs)
        self._url = base_url.rstrip("/") + "/v1/audio/transcriptions"
        self._model = model
        self._timeout = aiohttp.ClientTimeout(total=request_timeout_s)

    def can_generate_metrics(self) -> bool:
        return True

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
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
        logger.info(f"cohere stt {elapsed:.2f}s: {text!r}")
        if text:
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601(), result={"model": self._model, "elapsed_s": elapsed})
