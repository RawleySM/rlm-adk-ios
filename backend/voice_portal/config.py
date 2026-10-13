"""Explicit, visible configuration for the voice portal backend.

Everything host-specific lives in one JSON file under the state root so the
diagnostics view can show exactly which stores, inference owner and intake the
service is using. Nothing here is inferred from the machine name.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_STATE_ROOT = Path.home() / ".local" / "rlm-adk" / "voice_portal"


@dataclass
class Config:
    state_root: str = str(DEFAULT_STATE_ROOT)
    listen_host: str = "0.0.0.0"
    listen_port: int = 8765
    # Read-only source stores (desktop paths; verified 2026-10-13 UTC).
    prayer_store: str = str(Path.home() / ".local/rlm-adk/mirrors/prayer/prayer_ingest.sqlite3")
    youtube_store: str = str(Path.home() / ".local/rlm-adk/stores/life_context/sources/youtube/catalog.sqlite3")
    scripture_store: str = str(Path.home() / ".local/rlm-adk/stores/life_context/sources/scripture_reference")
    # Directory that contains the `scripture_reference_readers` package (pinned copy, see runbook).
    reader_path: str = ""
    scripture_source_id: str = "github:BibleAquifer/WorldEnglishBible"
    scripture_representation: str = "aquifer_json"
    theographic_source_id: str = "github:robertrouse/theographic-bible-metadata"
    teaching_limit_per_publisher: int = 50
    teaching_min_chars: int = 1500
    # Local reasoning owner (OpenAI-compatible). Spark-B through the desktop loopback forward.
    inference_base_url: str = "http://127.0.0.1:28111/v1"
    inference_status_url: str = "http://127.0.0.1:28111/status"
    inference_model: str = "spark-small"
    inference_timeout_s: int = 600
    inference_max_tokens: int = 400
    # Forwarded into the chat request body. Thinking models (Qwen3) otherwise spend the budget on
    # reasoning_content that is never spoken; the owner proxies the body unchanged.
    inference_extra_body: dict = field(default_factory=lambda: {"chat_template_kwargs": {"enable_thinking": False}})
    # Spark-A prayer intake through the desktop loopback forward.
    prayer_intake_url: str = "http://127.0.0.1:8091"
    # Installed allocator CLI (desktop). Never started or resumed by this service.
    allocatorctl: str = str(Path.home() / "srv/rlm-adk/bin/allocatorctl")
    allocatorctl_timeout_s: int = 60
    devotional_passages: list[str] = field(
        default_factory=lambda: ["PSA.23", "JHN.15", "ROM.8", "PSA.121", "MAT.6", "ISA.40", "PHP.4"]
    )
    source_commit: str = ""

    @property
    def root(self) -> Path:
        return Path(self.state_root).expanduser()

    @property
    def db_path(self) -> Path:
        return self.root / "voice_portal.sqlite3"

    def ensure_dirs(self) -> None:
        for name in ("logs", "run", "captures", "artifacts", "receipts"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load `config.json` from the state root (env `VOICE_PORTAL_STATE`), applying env overrides."""
    state_root = Path(os.environ.get("VOICE_PORTAL_STATE", str(DEFAULT_STATE_ROOT))).expanduser()
    cfg = Config(state_root=str(state_root))
    config_path = Path(path) if path else state_root / "config.json"
    if config_path.is_file():
        data = json.loads(config_path.read_text(encoding="utf-8"))
        for key, value in data.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
    for key in list(asdict(cfg)):
        env_key = "VOICE_PORTAL_" + key.upper()
        if env_key in os.environ and key != "state_root":
            current = getattr(cfg, key)
            raw = os.environ[env_key]
            if isinstance(current, bool):
                setattr(cfg, key, raw.lower() in {"1", "true", "yes"})
            elif isinstance(current, int):
                setattr(cfg, key, int(raw))
            elif isinstance(current, list):
                setattr(cfg, key, [item for item in raw.split(",") if item])
            elif isinstance(current, dict):
                setattr(cfg, key, json.loads(raw))
            else:
                setattr(cfg, key, raw)
    if not cfg.source_commit:
        marker = Path(__file__).resolve().parent.parent / "SOURCE_COMMIT"
        if marker.is_file():
            cfg.source_commit = marker.read_text(encoding="utf-8").strip()
    return cfg
