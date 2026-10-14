# Talk tier (Pipecat) for the RLMADK voice portal

The real-time half of the portal: a Pipecat 1.12 pipeline that runs as a user service on Spark-A
and talks to the iPhone over SmallWebRTC (the Pipecat iOS client) or over a tagged-PCM WebSocket
(the app's fallback path and the benchmark). Protocol: `docs/voice-portal/pipecat-rebuild/talk-protocol.md`.

```
transport.input → ClientMessages → RTVI → Cohere STT (per utterance) → Floor (prayer/quiet)
  → user aggregator (Silero VAD + backchannel-aware start + Smart Turn v3 stop)
  → Qwen on spark-inference (tools → skill service over HTTP) → Bridge (filler after 350 ms)
  → Kokoro TTS (bm_george, markdown stripped) → Duck (−3 dB while the user speaks) → OutputGate
  → transport.output → assistant aggregator
```

| File | Role |
|---|---|
| `talk/bot.py` | pipeline assembly and the Pipecat runner entry point (`python -m talk.bot --host 0.0.0.0 --port 7860`) |
| `talk/config.py` | `TalkConfig`: owners, voice, floor policy, thresholds; JSON at `~/.config/voice-talk/config.json`, `VOICE_TALK_*` overrides |
| `talk/cohere_stt.py` | `SegmentedSTTService` posting each utterance to spark-inference's Cohere upstream |
| `talk/floor.py` | mode owner: prayer by address, amen + silence, quiet/wake, `mode` and `prayer_text` messages; `OutputGate` |
| `talk/backchannel.py` | start strategy that tolerates speech under 320 ms while the bot speaks; `DuckProcessor` |
| `talk/bridge.py` | bridge phrase when the first reply is later than `bridge_after_s` |
| `talk/client_messages.py` | `client_text` (typed turn → `LLMMessagesAppendFrame`), `client_mode`, `client_event` |
| `talk/tools.py` | the LLM's functions, each an HTTP call to the skill service; failures tell the model to stop calling tools |
| `talk/pcm_serializer.py` | the tagged-PCM WebSocket framing |
| `talk/session_log.py` | per-session JSONL of timing frames under `~/.local/state/voice-talk/sessions/` |
| `tools/warm.py` | warms Cohere and the LLM (unit `ExecStartPre`) |
| `tools/ws_probe.py` | one-shot probe (superseded by `voicesim talk probe`) |
| `voice-talk.service` | systemd user unit |

## Deploy on Spark-A

```bash
mkdir -p ~/srv/voice-talk && cd ~/srv/voice-talk
uv venv --python 3.12 .venv && uv pip install -p .venv/bin/python "pipecat-ai[webrtc,websocket,silero,openai,kokoro,runner]" soundfile numpy requests
rsync -a --delete <checkout>/talk/talk/ talk/ && rsync -a <checkout>/talk/tools/ tools/
.venv/bin/python -c "from talk.config import TalkConfig; print(TalkConfig().to_json())" > ~/.config/voice-talk/config.json   # first time
cp <checkout>/talk/voice-talk.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now voice-talk.service
curl -s http://127.0.0.1:7860/status
```

Kokoro's model files download to `~/.cache/pipecat/kokoro-onnx` on first synthesis (325 MB). The
owners it depends on: Cohere on Spark-A's `spark-inference.service` (28080), the LLM on **Spark-B's**
owner (`qwen-coding-b`, reached through the Spark-A loopback forward 127.0.0.1:28111: with Qwen and
Cohere both resident, Spark-A had about 20 GiB free because desktop applications hold about 40 GiB there,
and the owner refused with `host_memory_pressure`), and the skill service on the desktop. Both owners
run spark-inference 7e98c9d (per-model `sparkctl reset <model>`; Spark-B allows fish-speech on the GPU).

## Tests

`python -m unittest discover -s talk/tests` (pure helpers). Live proof is the voicesim suite:
`voicesim talk run suites/conversation-core --mode file|room` in `rlm-voice-sim`.
