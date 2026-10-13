# First prototype: a native voice portal over the existing sources

Status: reviewed and updated for Claude Fable implementation. The [Fable review](prototype-fable-review.md) is preserved with a Codex adjudication correcting its host observations.

Decision date: 2026-10-12 America/New_York. Input synthesis: `292bede4ae64d066a4c242c847104f4a3fab13a7`, [top candidates](top-candidates.md). The synthesis covers seven archived reports and explicitly leaves five studies pending. This plan does not claim those missing reports, provider comparisons, or device tests are complete.

## Selected scope and architecture

Build a working, single-user vertical slice in this repository: native iPhone recording and explicit engagement controls; streaming conversation grounded in real prayer, teaching and Scripture products; inspectable citations; and a durable request that remains visible when the conversation disconnects. Keep the existing source producers and RLM-ADK runtime owners intact.

Use these concrete initial choices:

| Responsibility | Initial implementation | Why this is enough to learn |
| --- | --- | --- |
| Phone | Existing SwiftUI app, native audio session, persisted recording/mode state, source and work views | Proves actual interaction and lifecycle behavior in the existing build environment. |
| Voice transport | Native `AVAudioEngine` capture and on-device recognition; text/control WebSocket to the Python service; separate resumable audio-segment uploads | One audio owner keeps recording independent of the network session. SmallWebRTC's stock Swift capture path does not expose the required sharing. Do not send unused PCM over the WebSocket. |
| Conversation | `SFSpeechRecognizer` partial/final transcripts from the same input tap, local Spark reasoning, `AVSpeechSynthesizer` output | Uses installed capacity and separates the interface from the reasoning owner. This is a measured streaming cascade, not a claimed GPT-Live quality match. Cohere remains the existing batch transcription path for saved prayers. |
| Application backend | One Python application service in this repo, initially on the desktop alongside YouTube and the prayer/Apple/Scripture mirrors | One consumer and one deployment, without domain additions to RLM-ADK or new source producers. |
| Retrieval and app state | One consumer-owned SQLite database with exact lookups, FTS5 and explicit source revisions | The synthesis permits a bounded SQLite prototype. Postgres/pgvector becomes an evidence-driven next step; no graph database or vector service is needed to establish this slice. |
| Biblical relationships | A small, validated adapter over retained Theographic assets, with original identifiers and unresolved mappings visible | Reuses existing acquisition and tests useful passage/entity joins. It is not a theological-answer engine. |
| Work handoff | A thin adapter to an existing inspected RLM-ADK work/inbox interface, with a local durable outbox and exact receipt correlation | Preserves execution ownership and truthfully distinguishes locally queued from backend accepted, running and artifact available. |

No new security/privacy hardening, redaction, isolation architecture, approval layers, multi-user account system, replacement ingestion, orchestration framework, graph extractor or media-rendering stack belongs in this prototype. Preserve existing safeguards. Rawley has pre-authorized in-scope engineering, Mac-actions and KeePassXC use; routine execution must not stop to ask for that authorization again.

GPT-Live remains the experience benchmark from the synthesis. There is no OpenAI key in this supervisor's environment or the iOS checkout's usual env files. The first build uses existing local services and does not create keys, purchase credits, or claim a GPT-Live comparison. A later funded benchmark is a distinct step. Do not let its absence stop the working local prototype.

## Native audio and streaming protocol

Fable inspected SmallWebRTC 1.3.0 and its underlying libwebrtc source: the stock transport owns its microphone/audio-session configuration and exposes neither app PCM injection nor a working local capture renderer. Adopt the original fallback: no WebRTC, Pipecat server, aiortc, ICE or media server in the first build. Keep those as future transport candidates only when a measured need justifies their capture integration.

One native `AudioOwner` configures `AVAudioSession.playAndRecord` and one `AVAudioEngine` input tap with voice processing as supported. Fan out the captured buffers to the local segment writer and on-device recognizer. Keep expensive encoding, file writes and network operations off the real-time callback. The first implementation checkpoint must demonstrate local segments surviving conversation disconnect, deterministic output suppression, and cancellation of queued playback. Persist mode and capture identities independently of connection IDs.

Use `SFSpeechRecognizer` with `requiresOnDeviceRecognition` only after checking support for the selected locale/device. If unavailable, show that concrete condition and retain recording plus typed input; do not silently send speech to an unselected external service or pretend typed input proves live voice. Restart recognition as needed without stopping the recorder or ending prayer. Keep recognition gaps separate from capture gaps. Do not re-submit old partial/final transcripts after restart.

The WebSocket carries JSON events with session/turn identifiers: partial/final transcript, assistant text delta/done, cancellation and mode change. Finalized utterances create one reasoning turn; partials may update UI/context but must not trigger duplicate model calls. The phone incrementally speaks complete phrases from text deltas. Barge-in cancels both current speech and queued phrases and prevents old-turn deltas from re-entering playback. Server cancellation also stops or settles the request it owns. Prayer and quiet gate playback before enqueue, independently of model instructions.

The reviewer proposed uploading raw PCM alongside on-device transcripts. That adds a second path without a live consumer because the existing Cohere endpoint is batch-only. Defer that PCM uplink. Saved prayer segments use the actual HTTP intake contract below; local continuous recording remains independent of both transports.

Speech recognition may endpoint ordinary turns; it must never end prayer on silence. A cascade must stream input/partial state and output, support interruption and stay available between exchanges. A record/upload/respond form alone does not meet this scope. Represent recognition or audio gaps and service reconnects explicitly.

## Required user-visible slice

1. Connect to the configured backend, show the actual connection and recording states, and start a verse/devotional from retained Scripture and teaching with inspectable citations. Preserve the selected source revision and playback position.
2. Ask about the current passage or a real personal-context topic by voice or text. Return a grounded answer with source IDs/revisions and readable evidence. Missing evidence must remain missing; do not manufacture a quote or a date.
3. Enter prayer, remain silent through a long pause, end it explicitly, save the local recording/metadata, and wait. Use the verified existing prayer intake; local save, segment durability, finalize and published corpus membership are separate statuses. Repeated uploads use a stable `client_capture_id` and ordered segment hashes/lengths.
4. Enter explicit quiet, keep capture and backend work independent, suppress all speech, then wake explicitly. Prayer completion and reconnect do not clear pre-existing quiet. Ordinary turn completion does not enter quiet.
5. Submit a research/testimony request with a stable operation ID; disconnect/reconnect and retrieve the same request. Use the real existing handoff where available. If the installed runtime is paused, show queued/held, never completed. Do not resume the allocator without an operator request. A source packet produced locally may be shown as a source packet, never as an RLM-produced draft.
6. Open actual returned artifacts and citations in the app. Clearly mark unavailable artifacts and unsupported operations.

Use simple visible controls as the deterministic fallback for quiet, wake and prayer boundaries. Choose initial voice-command phrases in configuration and record them in the runbook; do not block implementation on naming preferences. Retain prototype capture files pending an explicit retention decision; do not add automatic deletion.

## Real source contracts and bounded input selection

Current source inspection on 2026-10-13 UTC found the following. Counts are snapshots, not completeness claims.

| Source | Existing contract / location | First selection and update rule |
| --- | --- | --- |
| Prayers | `data-pipelines/pipelines/prayer_ingest/docs/PRAYER_CORPUS_CONTRACT.md`; Spark `~/.local/rlm-adk/stores/prayer_pipeline/prayer_ingest.sqlite3`; desktop mirror `~/.local/rlm-adk/mirrors/prayer/prayer_ingest.sqlite3` | All 209 currently published `prayer_corpus_v1` rows. Key by `prayer_id`, version by `text_sha256`, reconcile membership. `listed_at` alone misses corrections. Keep `recorded_at_basis`. |
| Teaching / YouTube | Desktop `~/.local/rlm-adk/stores/life_context/sources/youtube/catalog.sqlite3`; 6,324 current publisher records and 15,594 transcript records | Start with at most 50 newest usable full-text records each from Desiring God and Eleven22, selected using actual source metadata. Preserve entire selected bodies, source URLs and selection receipt. Use `source_document_current_v1` observation/hash and transcript `text_hash`; avoid duplicated text across publisher and caption versions. |
| Scripture | `data-pipelines/packages/scripture_reference_readers`; retained source root on Spark and desktop `~/.local/rlm-adk/stores/life_context/sources/scripture_reference` | One complete retained English WEB edition as an initial explicit choice, verified through the reader. Preserve edition, native verse identity, body SHA and text locators. Do not conflate verse numbering across editions. Current source catalog is `v3-c4ac5f4b`. |
| Theographic | Assets already in the Scripture source catalog | Inspect native files, then validate a bounded passage/person/place example against retained data. Missing endpoints and ambiguous passage mappings stay explicit. No new scrape. |
| Apple context | Spark `~/.local/share/apple-ingestion-gateway/normalized.sqlite3`, desktop mirror under `~/.local/rlm-adk/mirrors/apple-ingestion-gateway` | Add only a bounded source type useful to the initial journey after prayer/teaching/Scripture works. Use generation plus change cursor; late blob repairs are not covered by raw sequence alone. Existing Mac-actions owns native calendar writes. |

Read source SQLite databases with `mode=ro` and `PRAGMA query_only=ON`; all indexes, cursors, application modes and outbox records belong in the consuming application's state root outside Git. This is source ownership and continuity, not new security architecture. Verify mirror freshness and paths on the selected host instead of falling back across people or machines.

Keep source-qualified document identity, body revision/hash, source locator, observed/recorded times and source family in the projection. Index complete selected texts with deterministic chunk boundaries for retrieval; cited offsets must resolve to the exact retained text revision. Reimport of identical source data is idempotent. A changed body becomes current, the previous version remains inspectable, and removed membership cannot remain a current hit.

No embeddings are required for the initial FTS baseline. Record query results on a small set of real questions; semantic retrieval can be added when those observations show a specific lexical-recall gap. Preserve the producer text and attribution, including distinguishing Scripture from teaching and personal statements.

## Existing execution and host interfaces

Codex rechecked the correct desktop host after review. The allocator is installed at `/home/rawley-stanhope/srv/rlm-adk/bin/allocatorctl`, its unit is **loaded, inactive/dead, MainPID 0**, and its current launch is `cycle14d-20261008`. The control root has no live service runs. Spark-A's masked legacy allocator and September fresh-start marker do not describe the current desktop installation and must not drive this implementation. The current lack of a live desktop run still prevents a real execution handoff. Do not start or resume it as part of this prototype.

On the desktop, the existing loopback endpoints were rechecked: `28080` is Spark-A inference, `28111` is Spark-B inference, and `8091/healthz` already returns `prayer-ingest`, source commit `8e0e8df07442f80c5f51504702bc16cf6e7d4d03`. **Reuse these existing paths; no new tunnel is required.** Spark-B reports `spark-small` and `qwen-coding-b` in `/v1/models`; prefer configured `http://127.0.0.1:28111/v1`, model `spark-small`, for initial reasoning because it currently has more headroom. Spark-A admission improved during review; availability is a live observation, not a permanent prohibition. Check readiness at actual test time and use advertised model IDs. No inference success has yet been claimed.

The backend runs on the desktop and reads absolute paths under `/home/rawley-stanhope`, never Spark's `/home/rawleysm` paths by accident. Keep host/provider/model configuration explicit and visible in diagnostics.

### Prayer intake

Use the existing desktop loopback access to Spark-A prayer intake:

- `POST /capture-segments`: multipart `client_capture_id`, `segment_index` and one audio part; persist and verify the returned SHA-256 and byte length.
- `POST /capture-finalize`: the same capture ID plus ordered `segments` containing `segment_index`, `audio_sha256`, `size_bytes`, and optional capture time. Persist the real `recording_id` receipt.
- `GET /recordings/{id}` and the published prayer views establish later processing/publication. Do not equate finalize with publication or invent a new source_kind accepted by the producer.

Expose saved locally, segments uploaded, finalized, published, excluded, failed and intake unreachable separately. Keep locally recorded segments while receipts or publication are pending; retry exact upload identities rather than creating new captures. Long pauses never finalize prayer automatically.

### Durable work outbox

Use the desktop's public CLI syntax `allocatorctl inbox post --file PATH` and `allocatorctl inbox list`; `inbox-post` is an internal service-control verb, not the selected public interface. Inspect current installed help/output before parsing. The public post emits a receipt line with message ID, run ID and target; persist the exact stdout/stderr and parsed fields rather than assuming JSON.

States: `queued_locally`, `backend_unavailable`, `posting`, `delivery_unknown`, `directive_posted`, `artifact_available`, `failed`. A successful post establishes only `directive_posted`. Keep the operation ID in the directive text for correlation. The upstream CLI does not provide a caller idempotency key: after a crash or ambiguous post result, reconcile retained inbox evidence by operation ID and scope before retry. If acceptance cannot be determined, retain `delivery_unknown`; absence from a new run's listing does not prove an older attempt failed. Never blindly replay an uncertain post.

An artifact becomes available only when an actual result reference is associated with this operation/project and its bytes are accessible. A random existing path is insufficient. A source packet made by the consumer has its own explicit artifact kind and is never promoted into an RLM-produced draft or completion state.

Implementation must inspect the actual service contracts rather than invent routes. Reference owners:

- `/home/rawleysm/data-pipelines/catalog/products.json` and the source contracts above.
- `/home/rawleysm/spark-inference/docs/operations.md`: source documents text `/v1` and STT `/upstream/cohere-transcribe/v1/audio/transcriptions`. Check live endpoint/model readiness before requesting inference; these were not exercised by the inventory.
- `/home/rawleysm/rlm-adk/rlm_adk_docs/UNDERSTAND.md`, then only the linked relevant execution/work docs. Prefer external consumer code; changes to RLM-ADK source require its native Retrace workflow and F6.
- `/home/rawleysm/data-pipelines/pipelines/apple_ingestion_gateway/mac/actions/README.md`: `mac-actions --target rawley system status` succeeded. Use explicit target and documented receipts for any required operation.
- `/home/rawleysm/plugins/keepassxc-passwords/skills/keepassxc-passwords/SKILL.md`: unattended metadata lookup succeeded; Claude's KeePassXC MCP is connected. A locked GUI is not an authorization block.

Do not replace or restart inference, ingestion, or allocator services merely to get a green prototype. New consumer services may be installed through a small repo-owned run command with logs and a documented stop procedure. Any active model-capacity hold is an operational condition to inspect and respect, not permission to override the owner.

## Build and acceptance evidence

Work in the isolated `codex/voice-portal-prototype` branch. Keep the research agent's checkout and archived reports unchanged. Commit and push the implementation and let the existing GitHub cloud-Mac workflow build it; use a cloud-Mac session for interactive validation when needed, and release the runner afterward. Verify the selected branch/commit inside a cloud session; the helper currently starts the default branch.

Required checks for the first prototype:

- Native state tests: quiet survives reconnect/relaunch; prayer long pauses produce no output/end; explicit finish saves and waits; existing quiet remains; barge-in cancels current and queued output; ordinary completion remains available.
- Recorder evidence: a locally persisted segment, interruption/gap records, pending upload recovery and no duplicate recording submission. Simulator evidence is distinct from signed-device microphone, Bluetooth and lock-screen proof.
- Source-adapter checks: repeated import, shorter corrected body, membership removal, version-qualified citations and explicit unavailable source. Do not create fixtures that only assert that a class exists.
- Live real-data read: a Scripture passage, a teaching record and a real prayer resolve through their returned citations. Keep runtime corpus bodies outside Git. Include the snapshot/selection and exact commands in an evidence receipt.
- Work durability: the same operation is recovered across disconnect and service restart; test the ambiguous-post window and ensure it cannot duplicate delivery. Local queue, directive posting and actual returned results are distinguished. Exercise truthful `backend_unavailable` against the current inactive desktop installation, plus controlled real-format receipt/reconciliation tests. A live downstream RLM result remains blocked until an operator-run allocator exists; do not make the rest of the app contingent on starting it.
- Streaming smoke: exercise the selected actual transport, transcription/reasoning path, cancellation, quiet and prayer. Report first-output/stop observations without claiming a provider comparison.
- Build and UI: cloud-Mac Swift build plus affected tests, launch and screenshot showing real feature states. Record code commit and CI URL.
- Device: inspect signing readiness with already-authorized tools and pursue installation through the existing path. If activation, signing assets or a reachable physical device is actually missing, record the precise blocker and continue all independent implementation/validation; do not label simulator success as device proof.

The final implementation report must distinguish built/exercised, built/unverified and blocked. It should name how to run the backend and phone build, show actual evidence paths and disclose remaining device/provider limits. It must not present a scaffold, mock-only demonstration or HTTP health check as completed end-to-end behavior.

## Device-session amendments (2026-10-13)

After the first TestFlight build ran on the phone, the cascade's cancellation contract was tightened:
a cancel must reach the inference owner while the request is still waiting for headers (cold start)
or for the next SSE line, so that an abandoned turn never keeps an owner slot; a new final transcript
supersedes the session's pending turn on both the phone and the server; barge-in on partial speech is
unchanged. Findings and evidence are in the
[acceptance report](prototype-acceptance.md#physical-device-session-findings-2026-10-13-build-020-4-on-iphone-12).

## Review adjudication and supervision

Claude Fable's review completed successfully, with no permission denials. Accept F1's native audio simplification, F4's on-device recognition/native TTS split, F5's small SQLite design, F6's actual prayer intake, and F7's build configuration checks. Correct F2/F3/D3/D5 using the desktop evidence above; reject the claim that the current allocator is masked or absent on desktop and the unnecessary new tunnel. Remove D2's unused raw-audio uplink. Strengthen outbox uncertain-delivery and artifact-link evidence as above. These are the final implementation choices, superseding contradictory details in the preserved review.

Claude should now build under the user's existing authority and report exact technical errors when tools fail. Codex monitors every five minutes, answers routine implementation questions, reminds Claude of its mandate when authorization is incorrectly treated as missing, and directly resolves repeated stalls. Any RLM-ADK isolated live trial has its separate mandatory 60-second attended supervision procedure; ordinary app checks do not require launching that runtime.
