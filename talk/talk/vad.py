"""Voice activity detection that still hears the user while the bot is speaking.

Evidence (talk session ``websocket-d33b013b89``, 2026-10-14 17:01 to 17:10 UTC; phone capture
segments 1047 to 1055 replayed offline): during two Scripture readings the phone's microphone
carried the user's interjections loudly ("Stop. Stop.", "I have a question. Please pause.", "I
can't interrupt the text.", int16 RMS 2,000 to 5,400) while the bot's own voice was cancelled to
RMS 12 to 60, yet the talk tier logged no VAD start in 390 s of bot speech. Two reasons:

* The phone was on a Bluetooth headset (HFP, 8 kHz): the uplink is band-limited to 4 kHz, and
  Silero run at 16 kHz scores such speech 0.4 to 0.6, below the 0.7 threshold. The same audio
  scored at 8 kHz reaches 0.74 to 0.8; wideband speech scores 0.99 either way.
* While the bot plays, Apple's voice processing leaves the near-end voice intelligible (Cohere
  transcribed every interjection) but Silero scores it far lower: on loud frames 0.3 to 0.75 at
  8 kHz, rarely 0.7 for the 0.2 s the VAD needs to start.

``DoubleTalkVADAnalyzer`` therefore scores every frame with Silero at 16 kHz and at 8 kHz (a
stateful resample of the same frame) and uses the higher score. While the bot speaks (and for
``double_talk_hold_s`` after it stops, which covers the gaps between verses), a frame that is loud
(int16 RMS >= ``double_talk_min_rms``) and plausibly speech (score >= ``double_talk_min_confidence``)
counts as speech. On the session above that rule starts the VAD on six of the eight interjection
spans and on none of the 45 bot-only spans, including a stretch of loud non-speech after the route
change (longest qualifying run 0.10 s against the 0.2 s start requirement). Everything downstream is
unchanged: the backchannel strategy still decides between "mm-hmm" and a barge-in, and Cohere still
transcribes the segment.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pipecat.audio.vad.data as _vad_data
import soxr
from loguru import logger
from pipecat.audio.vad.silero import SileroOnnxModel, SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

_NARROW_RATE = 8000
_NARROW_FRAME = 256  # Silero's frame at 8 kHz (32 ms)


class DoubleTalkVADAnalyzer(SileroVADAnalyzer):
    """Silero VAD scored at 16 and 8 kHz, with a lower speech bar for loud frames during bot speech.

    Args:
        params: Pipecat VAD parameters (confidence, start/stop seconds, minimum volume).
        double_talk_min_confidence: Score a loud frame needs while the bot speaks.
        double_talk_min_rms: int16 RMS a frame needs to use the lower score while the bot speaks.
        bot_speaking: Returns True while the bot speaks or just stopped; set after the floor exists.
    """

    def __init__(
        self,
        *,
        params: VADParams,
        double_talk_min_confidence: float = 0.3,
        double_talk_min_rms: float = 600.0,
        bot_speaking: Callable[[], bool] | None = None,
    ):
        super().__init__(params=params)
        self._narrow_model = SileroOnnxModel(str(Path(_vad_data.__file__).parent / "silero_vad.onnx"), force_onnx_cpu=True)
        self._narrow_last_reset = time.time()
        self._resampler: soxr.ResampleStream | None = None
        self._narrow_pending = np.zeros(0, dtype=np.float32)
        self._narrow_confidence = 0.0
        self.double_talk_min_confidence = double_talk_min_confidence
        self.double_talk_min_rms = double_talk_min_rms
        self.bot_speaking: Callable[[], bool] = bot_speaking or (lambda: False)
        self._relaxed_run = 0
        self.relaxed_runs: list[dict] = []

    def _narrowband_confidence(self, audio_float32: np.ndarray) -> float:
        """Silero at 8 kHz on the same audio; returns the latest 8 kHz score (held between frames)."""
        if self.sample_rate == _NARROW_RATE:
            return 0.0
        if self._resampler is None:
            self._resampler = soxr.ResampleStream(self.sample_rate, _NARROW_RATE, 1, dtype="float32")
        self._narrow_pending = np.concatenate([self._narrow_pending, self._resampler.resample_chunk(audio_float32)])
        while len(self._narrow_pending) >= _NARROW_FRAME:
            chunk, self._narrow_pending = self._narrow_pending[:_NARROW_FRAME], self._narrow_pending[_NARROW_FRAME:]
            self._narrow_confidence = float(np.squeeze(self._narrow_model(chunk, _NARROW_RATE)))
        now = time.time()
        if now - self._narrow_last_reset >= 5.0:
            # NOTE [Context]: the same periodic state reset Pipecat applies to its own Silero model.
            self._narrow_model.reset_states()
            self._narrow_last_reset = now
        return self._narrow_confidence

    def voice_confidence(self, buffer) -> float:
        """Higher of the 16 and 8 kHz scores; loud plausible speech during bot speech passes the threshold."""
        wide = float(np.squeeze(super().voice_confidence(buffer)))
        pcm = np.frombuffer(buffer, np.int16).astype(np.float32)
        if pcm.size == 0:
            return wide
        narrow = self._narrowband_confidence(pcm / 32768.0)
        confidence = max(wide, narrow)
        if confidence >= self._params.confidence:
            self._relaxed_run = 0
            return confidence
        rms = float(np.sqrt(np.mean(pcm**2)))
        if confidence >= self.double_talk_min_confidence and rms >= self.double_talk_min_rms and self.bot_speaking():
            self._relaxed_run += 1
            if self._relaxed_run == max(1, round(self._params.start_secs / 0.032)):
                self.relaxed_runs.append({"t": time.time(), "confidence": round(confidence, 2), "rms": round(rms)})
                logger.info(f"vad: double-talk speech during bot playback (score {confidence:.2f}, rms {rms:.0f}); counting as speech")
            return self._params.confidence
        self._relaxed_run = 0
        return confidence
