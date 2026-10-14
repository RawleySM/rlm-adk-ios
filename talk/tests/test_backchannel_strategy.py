"""Timing of the backchannel-versus-barge-in decision while the bot speaks (real clock, ~3 s)."""

import asyncio
import sys
import time
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipecat.frames.frames import (  # noqa: E402
    BotStartedSpeakingFrame,
    InputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)

from talk.backchannel import BackchannelAwareStartStrategy  # noqa: E402
from talk.config import TalkConfig  # noqa: E402


class RecordingStrategy(BackchannelAwareStartStrategy):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.triggered_at: list[float] = []

    async def trigger_user_turn_started(self, **kwargs):  # noqa: D401 - test seam
        self.triggered_at.append(time.time())


def _frame(rms: float, ms: int = 20, rate: int = 16000) -> InputAudioRawFrame:
    n = rate * ms // 1000
    if rms <= 0:
        pcm = np.zeros(n, dtype=np.int16)
    else:
        t = np.arange(n) / rate
        pcm = (np.sqrt(2) * rms * np.sin(2 * np.pi * 220 * t)).astype(np.int16)
    return InputAudioRawFrame(audio=pcm.tobytes(), sample_rate=rate, num_channels=1)


class BackchannelStrategyTiming(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Production timings: window 0.75 s, VAD stop lag 0.8 s, voiced check over the last 0.3 s.
        self.cfg = TalkConfig()

    async def _speak(self, strategy, voiced_s: float, total_s: float):
        started = time.time()
        while time.time() - started < total_s:
            await strategy.process_frame(_frame(2500 if time.time() - started < voiced_s else 0))
            await asyncio.sleep(0.02)

    async def test_sentence_cuts_at_the_window_end(self):
        s = RecordingStrategy(self.cfg)
        await s.process_frame(BotStartedSpeakingFrame())
        t0 = time.time()
        await s.process_frame(VADUserStartedSpeakingFrame())
        await self._speak(s, voiced_s=1.4, total_s=1.4)
        self.assertEqual(len(s.triggered_at), 1)
        after = s.triggered_at[0] - t0
        self.assertGreaterEqual(after, 0.7)
        self.assertLess(after, 1.05, f"cut {after:.2f}s after the VAD start; expected about backchannel_max_s")
        self.assertEqual(s.decisions[-1]["basis"], "voiced")

    async def test_backchannel_never_cuts(self):
        s = RecordingStrategy(self.cfg)
        await s.process_frame(BotStartedSpeakingFrame())
        await s.process_frame(VADUserStartedSpeakingFrame())
        # "mm-hmm": 0.3 s of voice, then silence; the VAD stop arrives vad_stop_secs after the voice ended.
        await self._speak(s, voiced_s=0.3, total_s=1.1)
        await s.process_frame(VADUserStoppedSpeakingFrame())
        await asyncio.sleep(0.3)
        self.assertEqual(s.triggered_at, [])
        self.assertEqual(s.decisions[-1]["decision"], "backchannel")

    async def test_quiet_speech_still_cuts_after_the_vad_window(self):
        s = RecordingStrategy(self.cfg)
        await s.process_frame(BotStartedSpeakingFrame())
        t0 = time.time()
        await s.process_frame(VADUserStartedSpeakingFrame())
        # Voice below the energy threshold the whole time (the VAD alone says the user is speaking).
        started = time.time()
        while time.time() - started < 1.9:
            await s.process_frame(_frame(20))
            await asyncio.sleep(0.02)
        self.assertEqual(len(s.triggered_at), 1)
        self.assertGreaterEqual(s.triggered_at[0] - t0, 1.5)
        self.assertEqual(s.decisions[-1]["basis"], "no_vad_stop")

    async def test_bot_silent_starts_at_once(self):
        s = RecordingStrategy(self.cfg)
        await s.process_frame(VADUserStartedSpeakingFrame())
        self.assertEqual(len(s.triggered_at), 1)


if __name__ == "__main__":
    unittest.main()
