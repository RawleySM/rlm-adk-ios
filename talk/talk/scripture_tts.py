"""A second voice for Scripture: verses are read by ``cfg.scripture_tts_voice``, everything else by the assistant voice.

``ScriptureSpeakFrame`` marks a unit of Scripture (a verse, or the reference announced before a
reading). ``ReaderKokoroTTSService`` notes the TTS context id Pipecat creates for each such frame
(``on_turn_context_created`` runs inside the frame's handling, before synthesis) and synthesises
those contexts with the reading voice and its language; every other context keeps the assistant's
configured voice. The choice travels with the context id, so it holds however Pipecat schedules
synthesis relative to frame processing.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass

import numpy as np
from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.kokoro.tts import KokoroTTSService


@dataclass
class ScriptureSpeakFrame(TTSSpeakFrame):
    """A unit of Scripture to speak in the reading voice (otherwise an ordinary ``TTSSpeakFrame``)."""


class ReaderKokoroTTSService(KokoroTTSService):
    """Kokoro TTS with a separate reading voice for ``ScriptureSpeakFrame`` units.

    Args:
        reader_voice: Kokoro voice id for Scripture (for example ``am_michael``).
        reader_language: espeak-ng language for that voice (``en-us`` for the American voices).
        **kwargs: Passed to ``KokoroTTSService``.
    """

    def __init__(self, *, reader_voice: str, reader_language: str = "en-us", **kwargs):
        super().__init__(**kwargs)
        if reader_voice not in self._kokoro.voices:
            raise ValueError(f"Kokoro reading voice '{reader_voice}' is not in the voices file")
        self.reader_voice = reader_voice
        self.reader_language = reader_language
        self._next_is_scripture = False
        self._scripture_contexts: set[str] = set()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        self._next_is_scripture = isinstance(frame, ScriptureSpeakFrame)
        try:
            await super().process_frame(frame, direction)
        finally:
            self._next_is_scripture = False

    async def on_turn_context_created(self, context_id: str) -> None:
        await super().on_turn_context_created(context_id)
        if self._next_is_scripture:
            self._scripture_contexts.add(context_id)

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        """Synthesise ``text`` in the reading voice for Scripture contexts, else the assistant voice."""
        scripture = context_id in self._scripture_contexts
        self._scripture_contexts.discard(context_id)
        try:
            await self.start_tts_usage_metrics(text)
            voice = self.reader_voice if scripture else self._settings.voice
            if scripture:
                logger.info(f"tts: reading voice {voice} for {text[:48]!r}")
            lang = self.reader_language if scripture else self._settings.language
            stream = self._kokoro.create_stream(text, voice=voice, lang=lang, speed=self._settings.speed)
            async for samples, sample_rate in stream:
                await self.stop_ttfb_metrics()
                audio = await self._resampler.resample((samples * 32767).astype(np.int16).tobytes(), sample_rate, self.sample_rate)
                yield TTSAudioRawFrame(audio=audio, sample_rate=self.sample_rate, num_channels=1, context_id=context_id)
        except Exception as e:  # noqa: BLE001 - same contract as KokoroTTSService.run_tts
            yield ErrorFrame(error=f"Unknown error occurred: {e}")
        finally:
            await self.stop_ttfb_metrics()
