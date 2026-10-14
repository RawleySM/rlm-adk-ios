# iOS client for the Pipecat talk tier

Branch `claude/ios-pipecat-client`, build 9 (`0.2.0 (9)`). The app's voice is no longer Apple's
synthesizer: spoken output comes from the Pipecat talk tier on Spark-A as audio, over one of two
transports behind one `TalkClient` protocol. The native capture, segment writer, prayer journal,
uploads, device inspection and work list are unchanged in what they do.

Protocol reference: [talk-protocol.md](talk-protocol.md). Acceptance cases:
[acceptance-matrix.md](acceptance-matrix.md). Status of every claim below is marked
**proven** (observed, with the evidence named), **simulator** (observed on the iOS Simulator only)
or **unverified**.

## What changed in the app

| Area | Before (build 8) | Now (build 9) |
| --- | --- | --- |
| Voice | `AVSpeechSynthesizer` speaking `assistant_text_delta` phrases | `VoiceOutput` playing the talk tier's bot audio (Kokoro `bm_george` on Spark-A) |
| Turns | JSON `final_transcript` / `partial_transcript` over the backend `/ws`, `assistant_text_delta` back | Removed. The talk server hears the uplinked audio and transcribes it itself (Cohere); typed input goes to the talk server as `{"type":"client_text","text":...}` |
| Prayer start | Button or exact "begin prayer" / "let us pray" | Also **prayer by address**: the first on-device partial that starts with an address phrase enters prayer |
| Prayer end | Button or "end prayer" suffix | Also the **amen rule**: a partial/final ending in amen, then 1.2 s with no new words |
| Floor sync | none (no server floor) | `client_mode` on every mode change and every (re)connect; server `mode` messages followed |
| Backend `/ws` | turns + inspection | inspection only (hello, snapshot, commands, prayer ASR sync) |

Deleted: `Audio/Speaker.swift`, `Model/PhraseQueue.swift` and its tests, the reducer's turn events
(`finalTranscript`, `assistantDelta/Done/Cancelled`, `startTurn`, `sendCancel`), the `Citation`
model of `assistant_done`. Forward only; no compatibility path.

## Components

| File | Role |
| --- | --- |
| `RLMADK/Talk/TalkClient.swift` | `TalkClient` protocol, `TalkEvent`, `TalkClientMessage`, `VoiceOutput` protocol, `TalkMessageParser` (RTVI + floor messages) |
| `RLMADK/Talk/WebRTCTalkClient.swift` | SmallWebRTC through `PipecatClientIOS` / `PipecatClientIOSSmallWebrtc` 1.3.0; `WebRTCVoiceOutput` |
| `RLMADK/Talk/WebSocketTalkClient.swift` | Tagged-PCM WebSocket, no SDK |
| `RLMADK/Audio/TalkAudio.swift` | `PCMFraming`, `PCMUplink` (16 kHz resampler + 20 ms framer + silence keepalive), `EngineVoiceOutput` (player node on the audio owner's engine) |
| `RLMADK/Audio/AudioOwner.swift` | still the only microphone owner; adds the player node, a pre-roll ring, a voice-level tracker, a talk tap, engine-configuration-change restart and input-format-change restart |
| `RLMADK/Model/ConversationPolicy.swift` | `ConversationPolicy` (phrase tables, 1.2 s), `FloorText`, `PrayerFloor` (pure floor rules), `PolicyStore` (`GET /policy`, cached) |
| `RLMADK/Model/DevotionalTracker.swift` | devotional position from spoken bot text |
| `RLMADK/Model/PortalMode.swift` | reducer: `SpeechGate`, `ClientFloorMode`, bot speaking events, `stopVoice` / `sendClientMode` effects |

## Transports

Selected in Diagnostics (segmented picker, persisted in `UserDefaults` `talkTransport`, default
`webrtc`). Talk server URL in Diagnostics (`talkURL`, default `http://100.121.221.42:7860`),
separate from the backend URL (`backendURL`, default `http://100.90.105.30:8765`). Launch
environment overrides for unattended runs: `RLMADK_TALK_URL`, `RLMADK_TALK_TRANSPORT`
(persisted when given, like `RLMADK_BACKEND_URL`).

### WebSocket (tagged PCM)

* `POST {talk}/start {"transport":"websocket"}`; only the path of `wsUrl` (plus `/<token>` when the
  runner issues one) is used against the host and port the app called, because the runner
  advertises `wss://0.0.0.0:7860/ws-client`.
* Up: the audio owner's existing tap hands copies of its buffers to `PCMUplink`, which resamples
  off the real-time thread with one persistent `AVAudioConverter` (`.noDataNow` between buffers so
  the resampler keeps state) to int16 LE mono 16 kHz, and sends `0x01` + 640 bytes per 20 ms. Real
  audio arrives in tap-sized bursts (4096 frames, about 85 ms); when none has arrived for 250 ms a
  20 ms silence frame is sent every 20 ms. The microphone is never opened by the talk path.
* Down: `0x01` + int16 LE 24 kHz is converted to float and scheduled on an `AVAudioPlayerNode`
  attached to the audio owner's own `AVAudioEngine` (attached before the engine first starts), so
  the input node's voice processing has the app's speech as its echo reference. The receive loop
  stays on the socket queue; JSON events hop to the main actor. `{"type":"interruption"}` and
  RTVI `bot-interrupted` flush the player at once.
* Reconnects with backoff (1 s doubling to 15 s). The phone's mode is sent on every connect.

### WebRTC (SmallWebRTC, official Pipecat Swift client)

* `PipecatClient(options: PipecatClientOptions(transport: SmallWebRTCTransport(), enableMic: true,
  enableCam: false))`, `startBotAndConnect` with `APIRequest(endpoint: {talk}/start,
  requestData: {"transport":"webrtc"})`; the SDK posts the offer to `/sessions/{id}/api/offer`.
* Delegate mapping: transport state, bot ready, user transcript, `bot-transcription`,
  `bot-tts-text`, bot started/stopped speaking, server messages (our floor messages), errors.
* Outgoing `client_mode` / `client_text` / `client_event` use `sendClientMessage(msgType:data:)`
  (RTVI `client-message`, `data.t` = the type, `data.d` = the fields), queued until bot-ready.

**Server work needed for WebRTC floor sync (not done, not mine to do).** Read from the talk server
on 2026-10-13 23:50 EDT (`~/srv/voice-talk/talk/client_messages.py`, `floor.py`):

1. The server sends `mode` and `prayer_text` as plain `OutputTransportMessageUrgentFrame` dicts.
   Over SmallWebRTC these reach the data channel unwrapped, and the iOS SDK's
   `SmallWebRTCTransport.handleMessage` forwards only messages labelled `rtvi-ai`, so the app
   never sees them on WebRTC. They need to go out as RTVI server messages
   (`{"label":"rtvi-ai","type":"server-message","data":{"type":"mode",...}}`, Pipecat
   `RTVIServerMessageFrame`). The app's parser already accepts that wrapping (and the plain form on
   the WebSocket path); a test covers both.
2. `ClientMessages` matches only top-level `type` (`client_mode`, `client_text`, `client_event`).
   The iOS SDK can only send RTVI messages, so on WebRTC these arrive as
   `{"label":"rtvi-ai","type":"client-message","data":{"t":"client_mode","d":{"mode":"prayer","reason":"..."}}}`
   and fall through. It needs to unwrap `client-message` (`t`/`d`) the same way.

Until then the WebRTC path still keeps prayer and quiet silent on the phone (below), but the
server does not learn the phone's floor over WebRTC and the phone does not learn the server's.

## VoiceOutput and SpeechGate

`SpeechGate.mayEnqueue(mode)` (not quiet, not praying) is evaluated on every mode change, before
any effect runs, and set as `voice.gateOpen`.

| | WebSocket (`EngineVoiceOutput`) | WebRTC (`WebRTCVoiceOutput`) |
| --- | --- | --- |
| Gate closes (prayer, quiet) | player flushed at once; every later frame dropped before scheduling | libwebrtc's audio unit stopped (`RTCAudioSession.isAudioEnabled = false`, manual audio) |
| Stop button | flush and drop the rest of the bot turn until `bot-stopped-speaking` or an interruption; `client_event` `stop` sent | unit stopped until the bot turn ends |
| Server interruption | flush | (libwebrtc plays what arrives; the server stops sending) |
| Bot speaks while gated | reducer stops the voice again and re-sends `client_mode` | same |

Entering prayer or quiet also sends `client_mode` (`prayer` / `quiet`; ending a prayer sends
`available`, or `quiet` when quiet was already set). While praying, entering quiet keeps the server
in `prayer`. The devotional position no longer follows utterance callbacks: `DevotionalTracker`
aligns the bot's spoken text (`bot-tts-text`) word by word against the segment texts (a word counts
only inside a run of matching words, so the bot's own introduction never moves the position);
interruptions, Stop, prayer, quiet and a new user turn keep the segment being read, so continue
repeats it in full. Play asks the talk server, by `client_text`, to read the passage from the saved
verse exactly as retained.

## Prayer by address and the amen rule on the phone

`PrayerFloor` (pure, clocked by the caller) runs on the on-device recognizer's partials and finals:

* Not praying: the first partial whose text starts with an address phrase enters prayer
  (`beginPrayer(trigger: .address)`): `client_mode prayer` goes out, playback stops, and the
  recognition request in progress is **adopted** by the prayer (`Recognizer.adoptCurrent`), so the
  utterance keeps listening without a gap and its final, address words included, is journaled
  under the prayer. If nothing adoptable is open, the conversation final that carries the address
  is journaled to the prayer.
* The prayer capture starts with **2.0 s of pre-roll** from a ring of the most recent captured
  buffers (an address is recognized after it was spoken); a server-detected address gets 6.0 s.
  The ambient capture keeps its own copy, so nothing is lost from either. The seconds actually
  written are in the manifest (`preroll_s`), with the trigger (`trigger`).
* Praying: a partial or final ending in an amen phrase starts the window; the prayer ends when
  1.2 s pass with no new words (`endPrayer(reason: "amen")`), counted from the moment the amen
  words appeared or, when the audio owner's level tracker saw voice end within 1 s before that
  partial, from that voice end (the server anchors to its VAD stop the same way). The level tracker
  can only make the end sooner, never later, and never on its own. More words cancel the pending
  end. Repeats and revisions of earlier words are not new words.
* Silence alone never ends a prayer (no amen, no decision; the ordinary endpoint clock still never
  end-points during prayer).
* Server `mode` messages are followed (address or amen the phone missed); the echo of the phone's
  own `client_mode` (`reason` starting `client:`) is ignored.

Normalisation matches the server's `_norm` (lowercase, letters and apostrophes) with typographic
apostrophes folded and whitespace collapsed. Choice: a phrase written with a comma (`"god,"`)
matches only when the transcript has that comma right after the word, so "God, thank you" opens a
prayer and "God is good" does not (the server's normaliser drops the comma and would open both).

`ConversationPolicy` holds the address, amen, quiet and wake tables, the explicit begin/end
commands, the 1.2 s window and the pre-roll seconds, with defaults equal to the talk server's
`TalkConfig`. At launch the app fetches the skill service's `GET {backend}/policy` (any subset of
`address_phrases`, `amen_phrases`, `amen_silence_s`, `quiet_phrases`, `wake_phrases`,
`prayer_start_commands`, `prayer_end_commands`, `prayer_preroll_s`, `server_prayer_preroll_s`,
bare or under `"policy"`), merges it over the defaults (empty tables and out-of-range numbers are
ignored) and caches the body in `Documents/policy.json`. On 2026-10-13 the backend answered
`/policy` with `404 not_found`; the app then uses the cache or the defaults and says which in
Diagnostics and the snapshot (`policy_source`).

## Tests

XCTest (`RLMADKTests`, run by `cloud-mac.yml` on every push): `FloorControllerTests` drives the real
controller with a fake talk client and a test clock: address on a partial enters prayer; amen then
1.25 s ends; amen then more words does not end; 3.0 s and 4.5 s pauses never end; quiet closes the
gate, stops playback and sends `client_mode quiet` (and bot speech while quiet is stopped again);
server floor messages; typed input as `client_text`; devotional position from spoken text.
`ConversationPolicyTests` (phrases, merge, cache, `PrayerFloor`), `TalkProtocolTests` (RTVI and
floor parsing, client messages, PCM framing, socket URL, devotional alignment), the recognizer
adoption case, and the rewritten reducer tests. The prayer and quiet guarantees of the old reducer
tests are kept. CI also runs the app in the simulator against `scripts/ci_fake_talk.py` (the
WebSocket protocol with a scripted bot turn) and fails unless the fixture server saw the
`client_mode` sync, the typed `client_text`, 641-byte tagged uplink frames at about 50 per second
and the bot turn delivered.
