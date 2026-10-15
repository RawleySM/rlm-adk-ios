"""Gating of the double-talk VAD rule: the lower bar applies only to loud frames while the bot speaks."""

import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

from talk.vad import DoubleTalkVADAnalyzer


def _buffer(rms: float) -> bytes:
    t = np.arange(512) / 16000
    return (np.sqrt(2) * rms * np.sin(2 * np.pi * 300 * t)).astype(np.int16).tobytes()


class DoubleTalkGate(unittest.TestCase):
    def setUp(self):
        self.bot = False
        self.vad = DoubleTalkVADAnalyzer(params=VADParams(stop_secs=0.8), bot_speaking=lambda: self.bot)
        self.vad.set_sample_rate(16000)

    def score(self, wide: float, narrow: float, rms: float) -> float:
        with mock.patch.object(SileroVADAnalyzer, "voice_confidence", return_value=wide), mock.patch.object(DoubleTalkVADAnalyzer, "_narrowband_confidence", return_value=narrow):
            return self.vad.voice_confidence(_buffer(rms))

    def test_higher_of_the_two_scores(self):
        self.assertAlmostEqual(self.score(0.2, 0.8, 100), 0.8)
        self.assertAlmostEqual(self.score(0.9, 0.1, 100), 0.9)

    def test_loud_plausible_speech_passes_only_while_the_bot_speaks(self):
        self.assertLess(self.score(0.1, 0.4, 2000), 0.7)
        self.bot = True
        self.assertGreaterEqual(self.score(0.1, 0.4, 2000), 0.7)

    def test_quiet_or_unlikely_frames_do_not(self):
        self.bot = True
        self.assertLess(self.score(0.1, 0.4, 300), 0.7)  # echo residual level
        self.assertLess(self.score(0.1, 0.2, 5000), 0.7)  # loud non-speech

    def test_real_model_runs_on_both_rates(self):
        self.bot = False
        value = self.vad.voice_confidence(_buffer(1000))
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 1.0)


if __name__ == "__main__":
    unittest.main()
