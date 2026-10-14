# iOS client for the Pipecat talk tier

Branch `claude/ios-pipecat-client`, builds 9 to 12 (`0.2.0 (12)` installed last; see "Builds"). The app's voice is no longer Apple's
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
* Typed input and devotional requests go up as RTVI `send-text` (`sendText(content:)`), which
  Pipecat's RTVI processor handles as deployed (from build 12). `client_mode` and `client_event`
  use `sendClientMessage(msgType:data:)` (RTVI `client-message`, `data.t` = the type, `data.d` = the
  fields). All are queued until bot-ready.

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

## Device evidence (iPhone 12, iOS 26.6.2, 2026-10-14)

All times UTC. The phone is on Spark-A USB. Acoustic runs used `scripts/device_eval.py audio
play-record`: the desktop speakers play a fixture and the Spark-B microphone records the room.
Prayer tests pointed the app at an **isolated** backend and intake on Spark-A (ports 18765/18091,
fresh state under the session scratchpad), so no test prayer reached the production intake.

### Builds

| Build | Commit | IPA sha256 | Why |
| --- | --- | --- | --- |
| 9 | 6090dfd | `4bc3f73e00accf60ef436dcd8aed859fdc44cbd90fb6f80c36f6ea77bc2f4a03` (5,674,826 B) | the rebuild |
| 10 | cbed5d2 | `d94c55b2d88f92a6c31aeb3961e94c09f8d11ee4b99ccff8e0cadaa28b91d175` (5,676,788 B) | device findings: suppression after prayer, amen anchor allowance, journal on iOS 26 |
| 11 | 3b476d3 | `fdb7e85e39829acb78bfcbbdcfba0a65e3ba011b0db87a337691b5c0f3323674` (5,677,228 B) | device findings: level tracker floor after digital silence, `bot-output` display |
| 12 | 6d60653 | `c922a334bc02f57522a010338fe0340bb764787155a3c3e2f7ab026c63a7cba2` (5,678,358 B) | review findings: typed text as RTVI `send-text` on WebRTC; a capture switch during an input-format restart is applied after it instead of being overwritten |

All three were signed by `direct-install.yml` (Ad Hoc profile `rlm-adk-ios adhoc iPhone 12`,
expires 2027-10-13) and installed with `pymobiledevice3 apps install` without uninstalling.

**Install finding.** Every install over the running app waited in installcoordinationd at
"Acquiring termination assertion to install placeholder" (phone syslog); build 9's first attempt
waited 20 minutes. Terminating the app process (DVT `ProcessControl.kill`) let the pending install
complete within 3 s. The recording app is not terminated by the installer on iOS 26.6.2; terminate
it first. A stuck first attempt also left a process-scoped coordinator that made a `--streaming`
retry fail with `IXErrorDomain Code=48`.

**Retention** (manifest via `sync_manifest`, `GET /devices/<id>/manifest`). Builds 10, 11 and
12: 497 of 497, 514 of 514 and 538 of 538 segments identical across each install. Final check
after build 12, against the build 8 baseline: 450 of 450 identical, 9 captures, 540 segments, 0
orphans. **Build 8 to 9:**
before 5 captures, 450 segments, 2,536,078,464 bytes (open `00000445.wav`); after 5 captures, 475
segments; **450 of 450 identical in bytes and sha256**, 0 missing, 0 changed, 25 new (445 to 469;
the segment open at termination was recovered as a closed file), capture continuing. The data
container path changed (`4D009E8A…` to `0036F8A0…`); the data moved with it.

### WebRTC coexistence spike: capture holds

| Window | Talk server | Evidence |
| --- | --- | --- |
| 04:16:12 to 04:20:58 | silent spike instance (`talk.bot` on :7880 with STT and LLM pointed at a closed port, so nothing is ever spoken) | `rtc audio unit started (play/record)` 04:16:11. Baseline `audio_owner_state=Recording engine_running=true last_buffer_age_s=0.1 open_segment=00000473.wav`; at +30 s `last_buffer_age_s=0.1`, `growth_since_connect=2880000 bytes` (exactly 48 kHz x 16 bit x 30 s); segments 473 and 474 closed at full length (5,764,096 B). A fixture played at 04:18:00 reached **both** microphone paths: the server's VAD saw speech over WebRTC (00:18:02.4 EDT), and the phone's segment 474 holds it at 50.75 to 53 s, peak -15.8 dBFS against a -57 dBFS floor. Cohere transcribes that window exactly ("What does Psalm twenty three say about rest?"). |
| 04:21:14 to 04:31:06 | same | 60 snapshots every 10 s during the other agent's room benchmark: engine running in all, maximum `last_buffer_age_s` 0.1. A fixture prayer put the phone in prayer at 04:21:57 (`rtc audio unit stopped`); build 9 then left the unit off (the suppression bug fixed in build 10). |
| 04:38 to 04:40 | real (:7860), build 10 | Bot speech from the phone in the room recording (8.5 to 35.5 s); `webrtc coexistence holding` at 04:38:30; Cohere transcribes the phone's capture of the 04:39 prayer window exactly. |
| 04:41:30 to 04:42 | silent, build 10 | Prayer by address with unit off then on again (`toggles 3`); the server heard speech again after the prayer (00:41:58.6 EDT). |
| 04:48:44 to 04:49:14 | real, build 11 | `webrtc coexistence holding` at 04:49:14 (`last_buffer_age_s=0.0`); reply audible in the room 7 to 29 s; reply text now shown ("One moment. Psalm 23. A Psalm by David. Yahweh is my shepherd; …"). |

The session stayed `PlayAndRecord / VoiceChat / options 12 / 48000 Hz / io 0.023 / MicrophoneBuiltIn
-> Speaker / voice processing on` before, during and after WebRTC. Each launch logs one
`engine_configuration_change` gap and an automatic engine restart (also on the WebSocket path, so
it is not WebRTC's). **Conclusion: the native capture keeps receiving buffers and records
intelligible audio while libwebrtc's unit runs; no fallback was triggered.** What made it
coexist: our session first, libwebrtc's `RTCAudioSessionConfiguration` set to the same category,
mode, options, rate and IO buffer, one held `RTCAudioSession` activation, manual audio, the SDK's
audio manager told the current route is preferred and released once connected, and the engine
restart on configuration change. The capture guard (restart once, then fall back to WebSocket
and record `webrtcFallback-<build>`) is in place but never fired.

**What does not coexist well: on-device recognition.** With libwebrtc's unit running, the on-device
`SFSpeechRecognizer` (fed the same tap buffers that transcribe exactly in Cohere) garbled 3 of 5
utterances (04:18:02 "No", 04:38:23 "What is", 04:39:36 "No"; the prayers at 04:21:57 and 04:41:42
were recognized). On the WebSocket path every utterance tested was recognized. Phone-side prayer
by address is therefore unreliable on WebRTC; the server's own address detection still works
there (it heard the 04:39 prayer and ran prayer and amen itself), but on WebRTC its floor messages
do not reach the phone (server work item 1 above).

### What works on the phone

| Case | WebSocket | WebRTC |
| --- | --- | --- |
| Connect | yes: 04:13:07 (build 9) and every relaunch; reconnected on its own 4 s after the server was restarted under it (04:32:28 down, 04:32:32 up) | yes: `bot ready (rtvi 2.1.0)`; SDK audio manager released after connect |
| Hear the bot | yes: the question at 04:32:05 got the bridge phrase at 7.5 to 8.5 s and the reply at 11 to 26 s in the room recording (about 15 to 19 dB over the -39 dBFS room floor); 420 downlink frames (787 KiB) played, none dropped; no on-device final while it spoke | yes: reply audible in the room 8.5 to 35.5 s (04:38); reply text empty until build 11 (`bot-output`) |
| Prayer by address | yes: build 10 entered at fixture t = 1.91 s and build 11 at 1.94 s; the address utterance ends at 2.40 s. The server followed (`server prayer (client:address:lord)`) | yes when the recognizer heard it (build 9 at 04:21:57, about 0.86 s before the end of the other agent's address utterance; build 10 at 04:41:42, 0.49 s before); missed once (04:39) |
| 3.0 s and 4.5 s pauses | never ended a prayer (5 prayers) | same |
| Amen | build 10: 1.53 s from the end of "Amen." to the end of the prayer (1.46 s against the room-aligned end); build 11: 1.57 s. Both times the server's amen message (its 1.2 s window anchored at its VAD stop) arrived before the phone's own amen partial, so this is the server's timing, at the 1.5 s threshold | build 9: 2.19 s; build 10: 2.30 s, level tracker "ended never" (fixed in build 11, not re-measured on WebRTC) |
| No bot audio in prayer | room recording shows only the fixture's four speech segments, nothing in the pauses or after the amen | unit stopped for the prayer |
| Capture | published by the isolated intake, text equal to the reference (WER 0.0) for 3 prayers, pre-roll 2 s | same |
| Journal | 3 lines per prayer (build 10) | same |
| Floor sync | both directions | not yet: server work items 1 and 2 |

### Coordination notes

The other agent's `voicesim` room benchmark shared the room and the 7860 server. The phone was
live on 7860 from 04:13:07 to 04:13:54 during their `full-room1` run (it answered their fixture
"Which of my prayers mentions my son?") and from 04:20:32 to 04:20:58 during their file-mode run
(no room audio). The server was restarted under the phone at about 04:32:30. For all other tests
the phone pointed at the silent instance or ran while no room run was active.

### State left on the phone

Build 12 (installed 05:08:05; no acoustic re-test, its changes are the WebRTC typed-text path and
a rare capture-switch race, covered by CI), production backend `http://100.90.105.30:8765`, talk
server `http://100.121.221.42:7860`,
transport **WebSocket** (persisted choice; the default for a fresh install is still `webrtc`), mode
Available, recording. The fresh-install default stays `webrtc` as specified (a review suggested WebSocket
until the server changes land; recorded here, not applied). Choice for this phone: WebSocket is
the path with floor sync in both directions today. On
WebRTC the phone's `client_mode` does not reach the server, so after a prayer the phone detected
(WebRTC microphone off during it) the server can stay in prayer until it hears an amen. Select
WebRTC in Diagnostics once the server changes above are in.

### Left out, and why

* The WebRTC floor sync needs the two server changes above; not mine to make.
* `prayer_text` from the server is journaled on the phone (`Documents/prayer-talk/`), not synced
  to the backend (no backend route takes it apart from the on-device journal).
* The phone loop latency and barge-in thresholds of the acceptance matrix were not measured; the
  room recordings here are evidence of the app's behaviour, not benchmark runs.
* Bluetooth routes, interruptions and lock-screen behaviour on build 9 onwards are unverified.
* Several older DeviceInspection tests reuse fixed command ids against the real Documents store,
  so they fail on a second run on the same simulator (fresh simulators, as in CI, pass). Not
  changed here.
