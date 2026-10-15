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
  `bot-transcription`, `bot-llm-text`, `bot-started-speaking`, `bot-stopped-speaking`, metrics.
  The floor messages below are mirrored as RTVI `server-message` (`data` = the message), because the
  Pipecat iOS SDK drops anything not labelled `rtvi-ai`. Client → server: RTVI `client-message`
  with `data.t` = our message type and `data.d` = its fields is unwrapped by `client_messages.py`,
  so `client_mode` / `client_event` work on this path too; typed text arrives as RTVI `send-text`.

## 2. Tagged-PCM WebSocket (the app's fallback path and the benchmark probe)

* `POST /start` with `{"transport": "websocket"}` → `{"wsUrl": ".../ws-client/<token>", "sessionId"}`.
  Use only the path from `wsUrl` against the server you called (the runner advertises its own host).
* Binary frames: `0x01` + int16 LE mono PCM. Client → server at **16 kHz**; server → client at
  **24 kHz** (Kokoro's rate). Send 20 ms chunks (640 bytes) in real time; keep sending silence while
  idle so VAD and turn detection see a continuous stream.
* Text frames: JSON.
  * Server → client: every RTVI message (same shapes as above), `{"type":"interruption"}` (flush
    the playback queue immediately), and the floor messages below.
  * Client → server: `{"type":"client_mode","mode":"prayer|available|quiet","reason":"..."}`,
    `{"type":"client_text","text":"..."}`, the scripture pane's `client_passage` / `client_read`
    (below) and `{"type":"client_event", ...}` (free-form, recorded in the run's event log).

## Floor messages (both paths)

| Message | Meaning |
|---|---|
| `{"type":"mode","mode":"prayer","from":"available","reason":"address:lord","t":<unix>}` | The floor entered prayer (server-side detection from the transcript, or `client:` when the phone asked). Nothing will be spoken until the mode leaves prayer. |
| `{"type":"mode","mode":"available","reason":"amen"}` | Prayer ended by the amen rule (final transcript ended with an amen phrase and `amen_silence_s` passed without new speech). Nothing is spoken (save and wait). |
| `{"type":"mode","mode":"quiet","reason":"phrase"}` / `{"mode":"available","reason":"wake"}` | Quiet entered by phrase or client; left by the wake phrase. |
| `{"type":"prayer_text","text":"...","final":true}` | Transcript text heard while praying; never sent to the LLM. The app journals it against its capture. |
| `{"type":"node","node":"devotional","reason":"turn","t":<unix>}` | The Pipecat Flows node changed (see below). `mode` messages also carry the current `node`. |

## Scripture pane messages (both paths; `talk/passage.py`, `talk/reading_tracker.py`)

The talk tier holds the passage open in the phone's scripture pane, one per session. The pane shows
exactly what the bot reads, and every Flows node's system instruction carries a pane note (the
reference, whether it was read and how far, and its verses up to 9,000 characters) so "what does
verse 2 say?" means the passage on screen.

| Direction | Message | Meaning |
|---|---|---|
| client → server | `{"type":"client_passage","reference":"Romans 12","reason":"select"}` | Open a passage. Any reading pauses first (the floor waits until the interruption crossed the pipeline), the passage loads from the skill service, a `passage` event answers, a history line "(I opened Romans 12 in the scripture pane.)" is added and the system instruction refreshed. `reason: "sync"` (sent on every reconnect) keeps the position when the session already holds that passage. |
| client → server | `{"type":"client_read","action":"play\|pause\|restart"}` | The pane's speaker button: read the open passage from the verse after the last one fully heard (from the start, with the reference, when never read, finished, or `restart`), or pause it. |
| server → client | `{"type":"passage","reference","title","edition","book","chapter","verses":[{"verse","text","osis"}],"spoken_count","source":"tool\|client"}` | The pane's passage changed: chosen on the phone, or opened by `read_scripture` (a verse reference inside a chapter opens the whole chapter and reads only those verses). |
| server → client | `{"type":"passage_error","reference","reason"}` | The reference could not be opened. |
| server → client | `{"type":"reading","state":"started","from_index","from_verse","spoken_count"}` | A reading of the open passage was queued. |
| server → client | `{"type":"reading","state":"verse","verse","index"}` / `"verse_done"` (+ `duration_s`) | A verse started / finished playing (emitted after the output transport, as the audio plays). The app shades word by word between the two at the calibrated speaking rate. |
| server → client | `{"type":"reading","state":"paused\|finished\|blocked\|none"}` | The reading was interrupted (barge-in, a new passage, Pause, prayer, quiet), reached the end, was refused in prayer/quiet, or no passage is open. |

Read phrases, spoken or typed ("read it", "please read this to me", "continue", "read it again"),
read the open passage without a model turn; "read me Psalm 23" names a new reference and goes to the
model. Scripture is spoken in its own voice (`scripture_tts_voice`, default `am_michael`, through
`ReaderKokoroTTSService` and `ScriptureSpeakFrame`); the assistant keeps `tts_voice` (`bm_george`).
`talk/tools/pane_probe.py` checks the whole flow against a running tier.

## Conversation states (Pipecat Flows, `talk/flows.py`)

One `FlowManager` per session with six `NodeConfig`s, every node sharing the role message and never
speaking on entry (`respond_immediately` False) except `project`:

| Node | Entered by | Functions | Left by |
|---|---|---|---|
| `available` | session start; leaving prayer or quiet | all tools | the first forwarded request (→ `conversation`), a tool's transition, the floor |
| `conversation` | first request; `word_from_prayer` from `available` | all tools | tool transitions, the floor |
| `prayer` | the floor on an address phrase or `client_mode prayer` | none | amen (→ `available`) |
| `devotional` | `read_scripture` after it spoke a reading | `continue_reading` first, then the rest | `project_checkin` (→ `project`), the floor |
| `project` | `project_checkin` (entry runs the LLM once: the report or the confirmation question) | project and work tools first, then the rest | tool transitions, the floor |
| `quiet` | the floor on the quiet phrase or `client_mode quiet`; operator hold | none | wake phrase (→ `available`), release |

Exact-speech tools (`read_scripture`, `continue_reading`, `word_from_prayer`, `request_work`) queue
their sentences as `TTSSpeakFrame`s and return Flows' `NO_RESPONSE` (or a node whose entry does not
respond), so the model adds nothing after them; their spoken text is in the function result so a
devotional question can be answered from the passage heard.

## Turn and floor rules implemented server-side (talk/floor.py)

* Each session has its own floor, reading state and tool bindings; two phones, or a phone and a
  benchmark client, never share one.
* **Pause phrases** (`hold on`, `wait`, `stop`, `one moment`, ...) interrupt the bot and are never
  answered. **Continue phrases** (`continue`, `go on`, `keep reading`, `where you stopped`, ...)
  resume an interrupted reading from the verse after the last one the listener fully heard, without
  a model turn (the reading tracker sits after the output transport, so it counts played verses).
* Long chapters: a *question* about a chapter longer than ten verses (no `read`/`recite`/`hear` in
  the request) hands the verses to the model to quote from; a *reading* request reads verbatim.
* Turn end: Silero VAD `stop_secs` 0.7; Smart Turn v3 decides end of turn, with a 1.5 s silence
  fallback when it keeps judging a pause as mid-sentence.
* STT energy gate: an utterance whose int16 RMS is under `stt_min_rms` (120) is not transcribed;
  Cohere invents "Thank you." on near-silence. The log prints each utterance's RMS.
* The bridge phrase fires 0.35 s after the user stopped if no reply audio has started.

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

## Operator admin (same port)

| Route | Effect |
|---|---|
| `GET /admin/sessions` | live sessions in this process: `session_id`, `mode`, `held`, `bot_speaking`, and the hold state |
| `POST /admin/hold {"reason": "...", "hold_new": true}` | every live session (optionally `sessions: [...]` / `except: [...]`) goes quiet with `held` set, so the wake phrase cannot lift it; with `hold_new` sessions that connect later start held. A client that sends `{"type":"client_event","event":"benchmark_client"}` right after connecting lifts its own hold: that is the one session meant to talk during a benchmark. |
| `POST /admin/release` | clears every hold; held sessions return to `available` (reason `release`) |
| `POST /admin/sessions/{id}/mode {"mode": "available" \| "quiet" \| "prayer", "reason": "..."}` | set one session's floor mode (used to wake a phone whose own quiet was asserted on reconnect) |

`voicesim talk run --mode file|room` holds and releases around the run so the phone, which shares
the room's air, stays silent; `--mode phone` never holds (the phone is the subject) and plays the
`wake` control fixture first.
