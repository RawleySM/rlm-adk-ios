# Prototype plan review (Claude Fable)

**Codex adjudication:** This original review is retained as submitted. Its F2/F3/D3/D5 host conclusions are corrected in the [updated implementation plan](prototype-plan.md#existing-execution-and-host-interfaces): the current allocator is installed on desktop, loaded but inactive (not masked); Spark-A's allocator is retired. Desktop already exposes Spark-B inference on loopback 28111 and prayer intake on loopback 8091, so a new tunnel is unnecessary. The implementation also omits the unused raw-PCM uplink and adds uncertain-post reconciliation. Follow the updated plan where these documents differ.

Review date: 2026-10-13 00:54 UTC (2026-10-12 evening America/New_York). Reviewed: `AGENTS.md`, [user needs](user-needs-and-requirements.md), [top candidates](top-candidates.md), [prototype plan](prototype-plan.md) (uncommitted), the existing Swift scaffold, and the live state of the source stores, inference owner, prayer intake and RLM-ADK allocator on Spark-A and the desktop. Nothing was restarted, launched or written outside this worktree. The fetched Pipecat and libwebrtc sources were read under `/tmp/fable-pipecat`.

## Verdict

**Build it, with four corrections applied to the plan before delegation.** The slice is sound and the source side is in better shape than the plan assumes: every source contract it names exists and is readable, FTS5 is available on both hosts, and a real prayer intake HTTP contract exists. Two of the plan's load-bearing assumptions are wrong today and must be replaced, not deferred:

1. **Pipecat SmallWebRTC cannot be the first transport.** Its Swift package hands microphone ownership to libwebrtc with no shared-capture or frame-injection path, so the plan's own fallback clause ("simplify the first transport around native audio/streaming text") applies. The native app must be the single audio owner and the backend transport must be audio-agnostic.
2. **There is no running RLM-ADK execution owner.** The allocator unit is masked and inactive, a fresh-start marker from 2026-09-14 is still pending, and the operator inbox refuses posts without a live run. The handoff must be specified around what can truthfully happen: a local durable outbox that can only reach `queued_locally`, with `directive_posted` as the sole backend receipt that is possible once a run exists.

The other corrections are a host-placement decision (the plan's desktop choice is right, with one loopback forward added) and a speech-path decision (on-device recognition for live turns, Cohere for saved segments, native TTS for output, reasoning on whichever Spark currently admits a model).

## Findings, prioritized

### F1. SmallWebRTC Swift owns the microphone and exposes no shared capture path (blocker, resolved by D1)

Evidence, `pipecat-client-ios-small-webrtc` tag `1.3.0`, `main` at `dfcc497` (2026-09-27):

- `Package.swift` depends on `stasel/WebRTC` from 134.0.0, a stock prebuilt libwebrtc binary, and `pipecat-client-ios` 1.3.0. No custom audio device module is exposed.
- `SmallWebRTCConnection.swift` `createAudioTrack()` builds the local track from `RTCPeerConnectionFactory.audioSource(with:)`, so capture is libwebrtc's own audio unit (VoiceProcessingIO). `muteAudio()`/`unmuteAudio()` only toggle `RTCMediaStreamTrack.isEnabled`; the device stays open. There is no method that accepts app-supplied PCM or returns captured frames.
- `util/AudioManager.swift` `applyConfiguration()` sets the shared `AVAudioSession` to `.playAndRecord` with mode `.voiceChat`/`.videoChat` and options `[.allowBluetooth, .mixWithOthers]`, forces `overrideOutputAudioPort`, and re-applies this on every route change, media-services reset, and through a 1 s polling timer while a connection is live. Any app-owned session configuration is overwritten while the transport is connected.
- libwebrtc `pc/local_audio_source.h` lines 35–36: `AddSink`/`RemoveSink` are empty. Attaching an `RTCAudioRenderer` to the local track therefore delivers nothing; only remote tracks render.
- `SmallWebRTCTransport.swift` `release()` calls `audioManager.stopManaging()` and `disconnect()` drops the peer connection and its local track. Recording that outlives the session is not a state this SDK models.

Consequence: the plan's first checkpoint ("one coherent capture owner, local segments that survive conversation disconnect") cannot be met by combining this SDK with an app recorder. Two VoiceProcessingIO owners in one process is the exact competing-owner situation the plan forbids. The research report VOICE-2 recommended this package on the strength of its peer-to-peer topology, not on an inspection of its audio ownership; that claim is now source-checked and does not hold for this requirement.

Smallest correction: remove SmallWebRTC, libwebrtc, aiortc and ICE from the first slice. The app records with one `AVAudioEngine` input tap (voice processing enabled for echo cancellation) and streams PCM16/16 kHz frames plus JSON events over a single WebSocket to the backend only while a voice session is open. The LiveKit Swift SDK remains the documented WebRTC alternative because its fork exposes capture renderers and custom audio processing, but it adds a media server and agent worker that this slice does not need.

### F2. No RLM-ADK execution owner is running; the inbox refuses posts without a live run (blocker, resolved by D2)

Evidence, read-only on Spark-A at 00:40 UTC:

- `~/srv/rlm-adk/bin/allocatorctl status`: `active=inactive main_pid=0`, six residents in `state=resume status=admitted`, `hold=? (allocator service is not loaded)`.
- `systemctl --user status rlm-allocator.service`: `Loaded: masked`, `Active: inactive (dead)`.
- `~/srv/rlm-adk/FRESH_START_PENDING.md` (2026-09-14): the old unit must not be restarted; the next update needs a reviewed launch document. `~/.local/rlm-adk/runtime_control/operator_pause.json`: `state=pause_requested`, reason "operator regroup", requested 2026-09-27.
- `rlm_adk/host/service_control.py` lines 70–102, `post_directive`: resolves the live run from the session file and raises `ServiceControlRefused` when there is none. `rlm_adk_docs/runtime_monitoring.md` lines 286–300: run-level directives are bound to a run id and are not carried across restarts; resident-addressed directives persist but still require a live run to post.
- The `project-work-registry` the architecture research cites has no local checkout on Spark-A (searched to depth 3), and `srv_cloud_jobs.md` submission requires an open project branch inside a running resident.

Consequence: "a thin adapter to an existing inspected RLM-ADK work/inbox interface" has exactly one verified upstream call, `allocatorctl inbox-post`, and it cannot succeed today. Any state beyond `queued_locally` would be fabricated.

Smallest correction: the outbox record carries `operation_id`, request text, source packet reference and a status from the closed set `{queued_locally, backend_unavailable, directive_posted, artifact_available, failed}`. `directive_posted` requires the posting receipt (`message_id`, `run_id`, targets) to be stored verbatim; `artifact_available` requires an existing path. The adapter never calls `resume`, `start`, `update` or `relaunch`. A locally retrieved bundle of prayers, teaching and Scripture is displayed as a "source packet" with its own label and is never described as a draft, report or RLM project.

### F3. Host placement is unresolved in the plan and the two hosts differ in what they can reach (contradiction, resolved by D3)

Verified placement of every dependency:

| Dependency | Spark-A (`spark-e4f0`, tailnet `rlm-adk-spark`) | Desktop (`rawley-stanhope`) |
| --- | --- | --- |
| Prayer corpus | source `stores/prayer_pipeline/prayer_ingest.sqlite3`, 209 rows | mirror `mirrors/prayer/prayer_ingest.sqlite3`, 209 rows, modified 20:50 local |
| YouTube catalog | absent | source, 3.1 GB, 6,324 current documents, 15,594 transcripts |
| Scripture catalog + objects | source, catalog `c4ac5f4b`, `objects/` present | copy with `objects/` present (258 subdirectories) |
| Apple normalized | source, 5.0 GB | mirror, modified 20:50 local |
| Inference owner | loopback `127.0.0.1:28080` | loopback `127.0.0.1:28080` forwards to Spark-A (same `owner_boot`) |
| Prayer intake | loopback `127.0.0.1:8091` only | no forward found |
| RLM-ADK allocator | installed, inactive | not present |
| Memory | 121 GiB, 77 GiB available | 31 GiB, 28 GiB available, 12 cores |

The plan's table sends the backend to the desktop but cites Spark paths for prayer and Scripture and a desktop-only path for YouTube, and the prayer intake it wants to reach is loopback-only on Spark-A. Either host works; what does not work is leaving it ambiguous.

Smallest correction: the desktop, as the plan intends, plus one loopback forward `desktop:8091 → Spark-A:8091` installed by the same repo-owned run command as the backend. `prayer_ingest/service.py` lines 58–66 accept loopback callers and apply the optional `Tailscale-User-Login` allowlist only when the header is present, so a forwarded loopback request is admitted.

### F4. Local speech and reasoning: what is actually available now (fact, resolved by D4)

- Spark-A `/status` at 00:40 UTC: every chat profile reports `insufficient_memory_for_envelope` (77 GiB available against 80–90 GiB cold admission); the host's resident processes total about 60 GiB RSS. `cohere-transcribe` is admissible but its circuit is `half_open` after `engine_exited_during_start` at 15:41 UTC today; the next cold start is the probe.
- Spark-B through the existing tunnel `127.0.0.1:28111 → spark-media:18300`: all four profiles admissible, 115 GiB available.
- The speech route is batch only: `spark_inference/server.py` line 196 proxies multipart uploads to `/v1/audio/transcriptions`; there is no streaming or partial-result path. Cold admission for the Cohere profile is 32 GiB with a 600 s idle TTL (`config.py` profile `cohere-transcribe`).
- Chat is OpenAI-compatible `/v1/chat/completions`; `server.py` accepts model names `qwen-coding` (27B coding recipe) and `spark-small` (Nemotron 3.5 Lightning 30B-A3B, 65k context).

Consequence: the plan's requirement that the cascade "stream input/partial state" cannot be met by Cohere, and Spark-A reasoning may be unavailable at build time through no fault of the prototype.

Smallest correction: live turns use on-device `SFSpeechRecognizer` fed from the same `AVAudioEngine` tap, which yields partial transcripts without a second microphone owner; Cohere transcribes only saved segments (prayer through the intake in F6, other segments through the backend). Reasoning uses `spark-small` when admitted, else `qwen-coding`, at a configurable base URL defaulting to the desktop's loopback forward; an operator may point it at Spark-B. Output uses `AVSpeechSynthesizer`, whose `stopSpeaking(at: .immediate)` gives deterministic cancellation and whose queue the app owns, so quiet and barge-in are app state rather than prompt instructions.

### F5. The SQLite consumer is adequate; keep its schema to what the contracts supply (confirmed, trimmed)

Verified contracts, all read with `mode=ro` and `PRAGMA query_only=ON`:

- `prayer_corpus_v1`: `prayer_id`, `text`, `text_sha256`, `recorded_at`, `recorded_at_basis`, `listed_at`, `gateway_sequence`; corrections change `text_sha256` and removals leave the view (contract v1.1).
- `source_document_current_v1`: `url`, `observation_id`, `observed_at`, `body_sha256`, `body_text`, `metadata_json`, `selection_rule=latest_ok_nonempty_v1`; `transcripts`: `id`, `video_id`, `text`, `text_hash`, `is_generated`. The importer accepts only Desiring God and coe22 records (YouTube README line 43).
- Scripture catalog `c4ac5f4b`: 51,454 entries across 100 sources; World English Bible present as `github:BibleAquifer/WorldEnglishBible` (333 entries) and `WorldEnglishBibleUpdated` (267) and in eBible; the reader package's `iter_verses` returns verse atoms with `osis`, locators and `text_sha256`. Theographic is retained as `github:robertrouse/theographic-bible-metadata` (60 entries: `CSV/People.csv`, `Places.csv`, `Events.csv`, `Verses.csv`, `Books.csv`, `Chapters.csv`, `PeopleGroups.csv`, `Easton.csv`, `WordIndex.csv`, license).
- SQLite 3.45.1 with FTS5 on both hosts.

Over-complication to cut: a generic versioned projection with per-source reconciliation strategies. One `documents` table keyed by (`source_family`, `source_doc_id`, `revision_hash`) with a `current` flag, one `chunks` table with deterministic offsets, one FTS5 index over chunks, and one `citations` table satisfy every acceptance check in the plan (idempotent reimport, shorter corrected body, membership removal, version-qualified citation, explicit unavailable source). Theographic becomes three tables loaded verbatim from the CSVs with original IDs, joined to WEB verse atoms by OSIS reference, with unmatched references counted and listed.

### F6. The prayer intake contract exists and is verified (upgrade from "when verified" to "use")

`rlm-prayer-ingest.service` is running on Spark-A at `127.0.0.1:8091` (`/healthz` reports source commit `8e0e8df`). Routes in `prayer_ingest/service.py` lines 570–585:

- `POST /capture-segments`: multipart with `client_capture_id`, `segment_index`, exactly one audio part; returns capture id, index, SHA-256, byte length and durability.
- `POST /capture-finalize`: JSON `client_capture_id`, ordered `segments` of `{segment_index, audio_sha256, size_bytes}`, optional `client_captured_at`; returns a receipt with `recording_id`. A count alone is refused.
- `POST /capture-discard`, `GET /recordings/{id}`, `GET /prayers/{transcript_id}`, `GET /prayers`.
- Existing `source_kind` values are `apple_voice_memos`, `gdrive_voice_memo`, `tailnet_pwa` (`store.py` line 303); this route family was built for the tailnet PWA recorder and reuses byte-identical audio by SHA-256.

Consequence: the stable recording identity the plan wants is `client_capture_id`; repeated uploads are deduplicated by the producer. Publication to `prayer_corpus_v1` still depends on Cohere transcription and the 60-minute delivery budget, so `finalized` and `published` are distinct observable states.

Smallest correction: prayer status set becomes `{saved_locally, segments_uploaded, finalized(recording_id), published(prayer_id), excluded, failed, intake_unreachable}`. No producer-side change is needed for the slice.

### F7. Build configuration gaps that would otherwise surface late (minor)

- `project.yml` uses `GENERATE_INFOPLIST_FILE`. Microphone and speech-recognition usage descriptions and the `audio` background mode need explicit Info.plist keys; switch to an XcodeGen `info:` block so these are declared in the committed spec.
- Dropping Pipecat means no Swift package resolution in CI; `.github/workflows/cloud-mac.yml` needs no change for the first slice. Adding `xcodebuild test` for the state-machine tests is the only CI addition.
- The tailnet lists the target phone as `iphone-12`; its iOS version is not recorded. The deployment target of iOS 17 is compatible with that model. Signing still waits on the Team ID; the KeePassXC entry "Apple Developer + App Store Connect - Individual - Enrollment 9Z9523324Y" exists with metadata only. Simulator runs on GitHub runners have no microphone, so recorder tests on CI must inject audio files into the engine rather than claim live capture.
- `scripts/cloud-mac start` does not pass a ref; the plan's note that the helper starts the default branch is correct and the implementation should dispatch the session workflow with `--ref codex/voice-portal-prototype`.

### F8. Things the plan gets right and should keep unchanged

Explicit quiet/prayer/wake as app state with visible controls; prayer never ends on silence; save-and-wait after prayer; recordings retained pending a retention decision; no embeddings in the baseline; read-only source access with consumer state outside Git; no new security, privacy or account architecture; simulator evidence reported separately from device evidence; no resumption of the allocator; GPT-Live benchmark deferred as a funded step rather than a blocker.

## Resolved implementation decisions

**D1. Audio ownership.** One `AudioOwner` actor in the app wraps `AVAudioEngine` with voice processing enabled on the input node and owns the `AVAudioSession` configuration (`.playAndRecord`, `.voiceChat`, `.allowBluetooth`, `.defaultToSpeaker` as configured). It fans captured buffers out to: the segment writer (always on, including quiet and prayer), the on-device recognizer (on when a turn or wake listening is active), and the voice-session uplink (on only while a session is open). Interruptions and route changes are recorded as gap records with timestamps and reasons. Output is `AVSpeechSynthesizer` behind a `SpeechGate` that refuses enqueue during quiet and prayer and cancels current and queued utterances on barge-in or explicit stop. No WebRTC in the first slice.

**D2. Voice transport.** One WebSocket per voice session from the app to the backend carrying binary PCM16/16 kHz frames and JSON control events (`turn_started`, `partial_transcript`, `final_transcript`, `assistant_text_delta`, `assistant_done`, `cancel`, `mode_changed`). The backend streams text; the phone speaks it. Reconnect creates a new session id; app mode state is never derived from session state.

**D3. Backend host.** The Python application service runs on the desktop under a repo-owned run command with logs and a stop procedure. It reads the four stores at their verified desktop paths, reaches inference at the desktop's existing loopback forward, and reaches prayer intake through a new loopback forward to Spark-A port 8091 installed by the same command. The phone reaches the backend over the tailnet.

**D4. Speech and reasoning.** Live turns: on-device `SFSpeechRecognizer` with `requiresOnDeviceRecognition`, restarted per utterance. Saved segments and prayers: Cohere through the existing batch route, with explicit `transcription_pending` while the circuit is open or the engine is cold. Reasoning: OpenAI-compatible chat completions with `spark-small` preferred and `qwen-coding` as fallback, configurable base URL. No OpenAI credentials are provisioned.

**D5. Durable handoff.** The outbox record and status set in F2. The only backend call is `allocatorctl inbox-post` with the operation id in the text; its receipt is stored verbatim and shown as "directive posted", never as accepted, running or completed. While the allocator is masked the record stays `backend_unavailable` with the observed `allocatorctl status` line attached. Source packets are labeled as such.

**D6. Retrieval store.** The consumer SQLite schema in F5, in the application state root outside Git. Initial selection: all 209 prayers, the 50 newest usable full-text records each from Desiring God and Eleven22 chosen from `source_document_current_v1` and `transcripts` without duplicating bodies, the WEB edition verse atoms from the reader, and the Theographic CSVs. A selection receipt records source paths, catalog ids, counts and hashes.

**D7. Prayer intake.** Use the verified routes in F6 with `client_capture_id` as the stable identity; keep local segments until matching receipts are verified, mirroring the current recorder's behaviour.

## Unresolved facts that do not block the build

- iOS version on `iphone-12` and whether Tailscale on the phone is currently connected (peer listed, state `-`).
- Apple Developer activation and Team ID; device installation remains a distinct, reported blocker until then.
- Whether the Spark-A Cohere engine starts cleanly on its next probe; the backend must tolerate `model_circuit_open` and `endpoint_busy` and show them.
- Whether the desktop already has a tunnel to Spark-B; if not, Spark-B reasoning is reachable only from Spark-A for now.
- Which `spark-small` prompt format yields acceptable conversational latency; measure, do not assume.
- The exact operator decision on when the fresh RLM-ADK launch happens; the prototype must keep working, truthfully, without it.

## Evidence index

| Claim | Where it was checked |
| --- | --- |
| SmallWebRTC capture ownership and session reconfiguration | `pipecat-client-ios-small-webrtc@dfcc497`: `SmallWebRTCConnection.swift` (`createAudioTrack`, `muteAudio`), `util/AudioManager.swift` (`applyConfiguration`, poll timer), `SmallWebRTCTransport.swift` (`connect`, `disconnect`, `release`), `Package.swift` |
| Local-track renderers receive nothing | libwebrtc `pc/local_audio_source.h` lines 35–36 |
| Allocator inactive, masked, fresh start pending, pause requested | `allocatorctl status`, `systemctl --user status rlm-allocator.service`, `~/srv/rlm-adk/FRESH_START_PENDING.md`, `~/.local/rlm-adk/runtime_control/operator_pause.json` |
| Inbox post needs a live run | `rlm_adk/host/service_control.py` lines 70–102; `rlm_adk_docs/runtime_monitoring.md` lines 286–345 |
| Inference admission, circuits, routes | `http://127.0.0.1:28080/status` on Spark-A and desktop; `http://127.0.0.1:28111/status` (Spark-B); `spark_inference/server.py` lines 180–230, 338; `spark_inference/config.py` profiles |
| Prayer intake routes and identity check | `pipelines/prayer_ingest/prayer_ingest/service.py` lines 58–66, 224–320, 570–585; `config.py` lines 78–81; `http://127.0.0.1:8091/healthz` |
| Source stores and schemas | `catalog/products.json`; read-only SQLite inspection on Spark-A and over Tailscale SSH on the desktop; `packages/scripture_reference_readers/README.md`; `pipelines/prayer_ingest/docs/PRAYER_CORPUS_CONTRACT.md`; `pipelines/youtube_ingest/README.md` |
| Theographic and WEB presence | `entries` table of the Scripture catalog, `source_id` filters |
| Build workflow and helper | `.github/workflows/cloud-mac.yml`, `scripts/cloud-mac`, `project.yml` |
| Apple account entry | KeePassXC `find_entries` metadata only |
