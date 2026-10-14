# Talk tier protocol (Pipecat on Spark-A)

Service: `~/srv/voice-talk` on Spark-A, Python 3.12 venv, Pipecat 1.12.0. Development runner
(`python -m talk.bot`) serves on the tailnet (Spark-A is 100.121.221.42). Two client paths share one
pipeline:

## 1. SmallWebRTC (the Pipecat iOS client)

* `POST /start` with `{"transport": "webrtc"}` → `{"sessionId": ..., "iceConfig"?: ...}`.
* `POST /sessions/{sessionId}/api/offer` with the SDP offer (`pc_id`, `sdp`, `type`); `PATCH` for
  trickle candidates. This is exactly what `PipecatClientIOSSmallWebrtc` does when given the server
  base URL as its start endpoint (it replaces `/start` in the path with `/sessions/{id}/api/offer`).
* Audio: Opus over WebRTC both ways; the server pipeline runs at 16 kHz in and 24 kHz out.
* Messages: RTVI over the data channel (`label: "rtvi-ai"`): `user-transcription`,
  `bot-transcription`, `bot-llm-text`, `bot-started-speaking`, `bot-stopped-speaking`, metrics,
  plus our own server messages below delivered through the same channel as transport messages.

## 2. Tagged-PCM WebSocket (the app's fallback path and the benchmark probe)

* `POST /start` with `{"transport": "websocket"}` → `{"wsUrl": ".../ws-client/<token>", "sessionId"}`.
  Use only the path from `wsUrl` against the server you called (the runner advertises its own host).
* Binary frames: `0x01` + int16 LE mono PCM. Client → server at **16 kHz**; server → client at
  **24 kHz** (Kokoro's rate). Send 20 ms chunks (640 bytes) in real time; keep sending silence while
  idle so VAD and turn detection see a continuous stream.
* Text frames: JSON.
  * Server → client: every RTVI message (same shapes as above), `{"type":"interruption"}` (flush
    the playback queue immediately), and the floor messages below.
  * Client → server: `{"type":"client_mode","mode":"prayer|available|quiet","reason":"..."}` and
    `{"type":"client_event", ...}` (free-form, recorded in the run's event log).

## Floor messages (both paths)

| Message | Meaning |
|---|---|
| `{"type":"mode","mode":"prayer","from":"available","reason":"address:lord","t":<unix>}` | The floor entered prayer (server-side detection from the transcript, or `client:` when the phone asked). Nothing will be spoken until the mode leaves prayer. |
| `{"type":"mode","mode":"available","reason":"amen"}` | Prayer ended by the amen rule (final transcript ended with an amen phrase and `amen_silence_s` passed without new speech). Nothing is spoken (save and wait). |
| `{"type":"mode","mode":"quiet","reason":"phrase"}` / `{"mode":"available","reason":"wake"}` | Quiet entered by phrase or client; left by the wake phrase. |
| `{"type":"prayer_text","text":"...","final":true}` | Transcript text heard while praying; never sent to the LLM. The app journals it against its capture. |

## Turn and floor rules implemented server-side (talk/floor.py)

* Address phrases, amen phrases, quiet and wake phrases and the thresholds come from
  `~/.config/voice-talk/config.json` (`TalkConfig`), mirrored by the skill service's `GET /policy`.
* Prayer entry on the server happens on the first transcript of an utterance that starts with an
  address phrase. Because Cohere transcribes per utterance, that is after the utterance ends; the
  phone enters prayer earlier from its on-device partial and tells the server with `client_mode`.
* While praying: transcripts are returned as `prayer_text` and dropped before the LLM aggregator;
  every TTS audio/text frame is dropped by the output gate; any playing speech is interrupted on
  entry.
* Quiet: the output gate drops speech; transcripts are still heard (for the wake phrase).

## Tools (the RPC barrier)

Function calls the LLM may make, each an HTTP call to the skill service on the desktop
(`http://100.90.105.30:8765`): `search_sources`, `read_scripture` (`/tools/scripture`),
`word_from_prayer` (`/tools/word`), `interpretations` (`/tools/interpretations`),
`project_checkin` (`/tools/project`), `request_work` (`POST /tools/work`). Pipecat runs them
asynchronously; the audio loop never waits on them.
