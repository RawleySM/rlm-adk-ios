"""`GET /policy`: the versioned conversation policy shared by the phone, the Pipecat talk tier and the
voicesim harness (design option 2C in docs/voice-portal/conversational-design-alternatives.md).

Defaults live here; `policy.json` in the state root overrides any top-level key (dicts merge one level
deep). An override whose type differs from the default's is rejected and reported, never applied, so a
typo cannot turn a phrase list into a string. `version` is a hash of the effective policy; clients
cache by it.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from ..config import Config

SCHEMA = "voice-portal.conversation-policy.v1"

DEFAULT_POLICY: dict = {
    # Prayer by address: an utterance whose first words match enters prayer on the partial.
    "address_phrases": ["Lord", "Father", "Heavenly Father", "Dear God", "Jesus", "Holy Spirit", "Abba", "Our Father", "let's pray", "let us pray", "I pray"],
    # Prayer ends when the partial text ends with one of these and amen_silence_s pass with no new words.
    "amen_phrases": ["amen", "amen, amen", "in Jesus' name, amen", "in Jesus name, amen", "in Jesus' name amen", "in Jesus name amen"],
    "amen_silence_s": 1.2,
    # Silence never ends prayer (user needs MODE-03): only the amen rule or an explicit control does.
    "prayer_ends_on_silence": False,
    # Explicit quiet blocks speech (never prayer capture); wake restores it.
    "quiet_phrases": ["quiet please", "be quiet please", "please be quiet"],
    "wake_phrases": ["wake up"],
    # While the assistant speaks: a short utterance whose text is in this set ducks instead of interrupting.
    # "amen" counts only outside prayer.
    "backchannel_words": ["mm-hmm", "mhm", "mm hmm", "uh-huh", "uh huh", "yeah", "right", "okay", "ok", "amen"],
    "bridge_phrases": ["Let me look.", "One moment.", "Let me find that."],
    "thresholds": {
        "backchannel_max_ms": 320,
        "backchannel_duck_db": 3.0,
        "backchannel_duck_ms": 400,
        "barge_in_cut_ms": 120,
        "bridge_after_ms": 350,
        "bridge_start_max_ms": 600,
        # Project check-in: report only when top-1 score >= ratio x top-2, else ask to confirm.
        "project_confidence_ratio": 1.5,
    },
}


def _same_type(default: object, value: object) -> bool:
    if isinstance(default, bool) or isinstance(value, bool):
        return isinstance(default, bool) and isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float))
    if isinstance(default, list):
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    return isinstance(value, type(default))


def load_policy(cfg: Config) -> dict:
    """Effective policy: defaults overlaid with `<state_root>/policy.json` (when present and valid).

    Returns:
        The flat policy fields plus `schema`, `version` and `source` (`path`, `present`, `overrides`,
        `rejected`, `error`).
    """
    path = Path(cfg.root) / "policy.json"
    policy = copy.deepcopy(DEFAULT_POLICY)
    source: dict = {"path": str(path), "present": path.is_file(), "overrides": [], "rejected": [], "error": None}
    if source["present"]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("policy.json must hold a JSON object")
        except (OSError, ValueError) as exc:
            source["error"] = f"{type(exc).__name__}: {exc}"
            data = {}
        for key, value in data.items():
            if key not in DEFAULT_POLICY:
                source["rejected"].append({"key": key, "reason": "unknown_key"})
                continue
            default = DEFAULT_POLICY[key]
            if isinstance(default, dict):
                if not isinstance(value, dict):
                    source["rejected"].append({"key": key, "reason": "expected_object"})
                    continue
                for sub, sub_value in value.items():
                    if sub not in default or not _same_type(default[sub], sub_value):
                        source["rejected"].append({"key": f"{key}.{sub}", "reason": "unknown_key" if sub not in default else "type_mismatch"})
                        continue
                    policy[key][sub] = sub_value
                    source["overrides"].append(f"{key}.{sub}")
            elif _same_type(default, value):
                policy[key] = value
                source["overrides"].append(key)
            else:
                source["rejected"].append({"key": key, "reason": "type_mismatch"})
    version = hashlib.sha256(json.dumps(policy, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return {"schema": SCHEMA, "version": version, **policy, "source": source}
