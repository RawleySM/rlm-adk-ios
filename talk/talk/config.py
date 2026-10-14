"""Explicit configuration for the voice talk tier (Spark-A).

Everything host-specific is in one JSON file (``~/.config/voice-talk/config.json`` by default) so
the runbook can show exactly which owners the service uses. Environment variables with the
``VOICE_TALK_`` prefix override single fields for experiments.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "voice-talk" / "config.json"


@dataclass
class TalkConfig:
    """Owners and levers of the talk tier."""

    # Speech-to-text: Cohere Transcribe behind spark-inference on this host.
    stt_base_url: str = "http://127.0.0.1:28080/upstream/cohere-transcribe"
    stt_model: str = "cohere-transcribe"
    # Reasoning: an OpenAI-compatible owner. Spark-A's own owner by default; the Spark-B owner is
    # reachable through the local forward on 28111 when Spark-A cannot admit a model.
    llm_base_url: str = "http://127.0.0.1:28080/v1"
    llm_model: str = "qwen-coding"
    llm_api_key: str = "local"
    llm_max_tokens: int = 220
    llm_temperature: float = 0.4
    # Thinking models spend the budget on reasoning that is never spoken; the owner proxies the body.
    llm_extra_body: dict = field(default_factory=lambda: {"chat_template_kwargs": {"enable_thinking": False}})
    # The app's own voice: Kokoro on the GB10 CPU (RTF about 0.2). Not Apple, not the clone.
    tts_voice: str = "bm_george"
    tts_speed: float = 1.0
    tts_language: str = "en-gb"
    kokoro_model_path: str = str(Path.home() / ".cache/pipecat/kokoro-onnx/kokoro-v1.0.onnx")
    kokoro_voices_path: str = str(Path.home() / ".cache/pipecat/kokoro-onnx/voices-v1.0.bin")
    # Skill service (the cognitive backend over data-pipelines products) on the desktop.
    skill_base_url: str = "http://100.90.105.30:8765"
    skill_timeout_s: float = 20.0
    # Floor policy (the conversation policy the app and the benchmark share).
    address_phrases: list[str] = field(
        default_factory=lambda: [
            "lord", "dear lord", "lord jesus", "father", "dear father", "heavenly father", "our father",
            "dear god", "god,", "jesus", "dear jesus", "holy spirit", "abba", "let's pray", "let us pray",
            "i pray", "we pray",
        ]
    )
    amen_phrases: list[str] = field(default_factory=lambda: ["amen", "in jesus' name amen", "in jesus name amen"])
    amen_silence_s: float = 1.2
    quiet_phrases: list[str] = field(default_factory=lambda: ["quiet please", "be quiet", "go quiet"])
    wake_phrases: list[str] = field(default_factory=lambda: ["wake up", "portal wake"])
    backchannel_words: list[str] = field(default_factory=lambda: ["mm-hmm", "mhm", "uh-huh", "yeah", "yes", "right", "okay", "ok", "amen", "hmm"])
    # The dossier's 320 ms assumes energy and semantic cues too; with duration alone, a spoken "mm-hmm"
    # (0.70 s voiced in the suite's fixture) needs a wider window. Barge-in is measured from the window's end.
    backchannel_max_s: float = 0.75
    # Words that mean "stop and wait": they interrupt the bot and are never answered as a turn.
    pause_phrases: list[str] = field(default_factory=lambda: ["hold on", "wait", "wait a moment", "one moment", "hang on", "stop", "stop there", "pause", "hold on a second", "wait wait"])
    duck_db: float = 3.0
    duck_hold_s: float = 0.4
    bridge_after_s: float = 0.35
    bridge_phrases: list[str] = field(default_factory=lambda: ["Let me look.", "One moment.", "Looking into that."])
    # Audio
    audio_in_sample_rate: int = 16000
    audio_out_sample_rate: int = 24000
    # Clause pauses in natural (and synthetic) speech run 0.3 to 0.5 s; a 0.2 s VAD stop asked Smart Turn
    # at every comma and it ended turns mid-sentence. 0.5 s keeps whole questions together.
    vad_stop_secs: float = 0.5
    # Where runs and receipts go.
    state_root: str = str(Path.home() / ".local/state/voice-talk")

    @property
    def root(self) -> Path:
        return Path(self.state_root).expanduser()

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def load_config(path: str | os.PathLike | None = None) -> TalkConfig:
    """Load the JSON config (if present) and apply ``VOICE_TALK_*`` overrides."""

    cfg = TalkConfig()
    config_path = Path(path or os.environ.get("VOICE_TALK_CONFIG", DEFAULT_CONFIG_PATH)).expanduser()
    if config_path.is_file():
        for key, value in json.loads(config_path.read_text(encoding="utf-8")).items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
    for key in list(asdict(cfg)):
        env_key = "VOICE_TALK_" + key.upper()
        if env_key in os.environ:
            current = getattr(cfg, key)
            raw = os.environ[env_key]
            if isinstance(current, bool):
                setattr(cfg, key, raw.lower() in {"1", "true", "yes"})
            elif isinstance(current, int):
                setattr(cfg, key, int(raw))
            elif isinstance(current, float):
                setattr(cfg, key, float(raw))
            elif isinstance(current, list):
                setattr(cfg, key, [item for item in raw.split(",") if item])
            elif isinstance(current, dict):
                setattr(cfg, key, json.loads(raw))
            else:
                setattr(cfg, key, raw)
    cfg.root.mkdir(parents=True, exist_ok=True)
    return cfg
