# Voice portal prototype: acceptance and evidence

Date: 2026-10-13 UTC. Branch `codex/voice-portal-prototype`. Evidence paths on the desktop are under
`/home/rawley-stanhope/.local/rlm-adk/voice_portal/receipts/evidence/` (prayer text stays there, not
in Git). Simulator screenshots are in [`evidence/`](evidence/). Every item below is labelled
**built/exercised**, **built/unverified** or **blocked**.

## Summary

| Area | Status | Evidence |
| --- | --- | --- |
| Backend and upload-helper tests (48, stdlib unittest) | built/exercised | `python3 -m unittest discover -s tests -t .` → `Ran 48 tests`, `OK`; final implementation CI run `38341345480`, commit `d756deef`, retained `backend-tests.log` independently checked by Codex. Includes 40 backend and 8 upload-helper tests |
| Real-data import (desktop) | built/exercised | 209 prayers (4,749 chunks), 100 teaching records (50 Desiring God + 50 Eleven22, 5,087 chunks), 1,189 WEB chapters (6,934 chunks), Theographic 3,069 people / 1,274 places / 31,102 verses; `GET /sources` receipts with store paths, catalog `v3-c4ac5f4b`, manifest `c4ac5f4b…` |
| Citation resolution on real data | built/exercised | Psalm 23 `810:c39fdb164db6:0` (entry `5f254b497a…`, `eng/json/19.content.json`, verified), Eleven22 sermon `284:b962d1770db1:12` (`source_document_observations:505`, observed 2026-10-06), prayer `78:60325cf9377d:1` (`prayer_corpus_v1:historical-5959…`, `recorded_at_basis=historical_source`); malformed and unknown ids return explicit `available:false` |
| Theographic join | built/exercised | Gen 12 verses → Abraham (58), God (1324), Lot (1830), Sarah (2473); Haran (527), Canaan (272), Shechem (1069), Moreh (817), Bethel (202), Ai (36); no unresolved keys; Ps 23 → God only |
| Local reasoning streaming | built/exercised | `qwen-coding-b` on Spark-B via desktop loopback 28111: probe `ready` (warm); grounded turn first delta 631 ms, done 3,145 ms, 1,681 prompt / 63 completion tokens, citations across Scripture, teaching and prayer |
| Streaming transport (WebSocket) | built/exercised | 29 deltas, first delta 535 ms, done 3,832 ms; duplicate `final_transcript` refused; cancel after the first delta stopped the stream in 0.187 s with `assistant_cancelled`; client disconnect settled the turn as `cancelled`; owner `inflight 0` afterwards |
| Retrieval correction (noisy question) | built/exercised | "Which prayer of mine speaks about my son, and what Scripture fits it?" → focus term `son`, prayer hit `20:e940edb5a6d3:8` ("My son, Creighton…"), answer cites it ([5]) with teaching [3]; first delta 642 ms, done 2,444 ms. Missing-evidence question ("zebras in Antarctica") → "The retrieved excerpts do not include that." Heard-passage question cites the supplied Psalm 23 revision as [1] |
| Prayer intake (real service, sanctioned test run) | built/exercised | test run `9779e17af665492d8b1250e8bf8daab5`, capture `prayer-e2e-9779…`: segment 0 (194,860 bytes, sha `a9092ac0…`) uploaded and verified, repeat upload `reused`, wrong client hash refused locally, finalize → `recording_id b1e63969ae734b48924983a321fbdc3d`, status `finalized` (not published), intake verdict `passed`, hash match true |
| Work outbox against the real inactive allocator | built/exercised | operation `c4eef2b3…`: dispatch → `backend_unavailable` with the exact refusal (`no live run; start it first`); `inbox list --all` returned retained rows from run `rlm-20260930T224441-1953105` without our id → status unchanged; source packet artifact served with `X-Artifact-Kind: source_packet`; identical state after service restart (pid 2306072) |
| Outbox concurrency (OUTBOX-1/2) | built/exercised | regressions: two concurrent dispatches → one post, one attempt row, one directive file; forced interleaving: loser cannot touch the winner's per-attempt payload; crash-stale `posting` → `delivery_unknown`, never replayed; directive write failure → `local_write_failed`, retryable |
| Native XCTest (37) | built/exercised | cloud Mac, Xcode 26.6 / macOS 26.6.2 and push CI: reducer (quiet survives reconnect/relaunch, prayer silence, explicit finish, existing quiet, barge-in, ordinary completion), phrase queue, capture manifest (settlement, contiguous indexes, orphan reconciliation, next index), devotional progress, local work reconciliation, event decoding, recognition startup wiring in both callback orders (VOICE-START-1), SegmentWriter closed guard; `** TEST SUCCEEDED **` |
| Simulator build + launch + screenshots | built/exercised | unsigned build `** BUILD SUCCEEDED **`; app connected to the fixture backend (typed CI demo turn streamed with three citation chips) and to the real desktop backend through a reverse tunnel: John 15 (WEB) devotional with revision `5715112e39c8`, teaching found, qwen-coding-b turn first delta 785 ms; Diagnostics shows the real inference/intake/allocator state. Push CI run 38342930565 (commit `6a4ed2c`): 37 XCTests, 48 backend tests, and the four fixture-mode screenshots are free of permission dialogs and label capture/recognition as "not requested (fixture mode)" ([`evidence/simulator-fixture-portal-after-turn.png`](evidence/simulator-fixture-portal-after-turn.png), [`evidence/simulator-fixture-diagnostics.png`](evidence/simulator-fixture-diagnostics.png)). Earlier runs (05e4e65, b7a393f) were covered by the microphone alert left by the XCTest host, which launches the same bundle; fixed by not starting the portal under XCTest and erasing the simulator before the screenshot step |
| Live voice on a device | built/unverified | build 4 is installable on `iphone-12` through the TestFlight app (internal group); installing it and exercising microphone, on-device recognition, Bluetooth, lock-screen and interruption behaviour is the next step and has not been done. The simulator reports `speech recognition not authorized (1)` and, in fixture mode, does not request it |
| Membership, Team ID, signing assets | built/exercised | Individual membership active, Team `4PJBKY2WQR` (renewal 2027-10-12), verified in the existing authenticated browser session with no credential entry; API key `Z9XH3R3RV2`, bundle ID `87XJJ9FG5S`, Apple Distribution certificate `AUBJ7C9S7Y`, profile `rlm-adk-ios appstore` (`6SW4NCM292`), app record `6822129536`; all retained privately and as GitHub secrets (see runbook) |
| Signed archive | built/exercised | manual Apple Distribution signing on the cloud Mac; `codesign --verify --deep --strict` passes; chain Apple Distribution: Rawley Stanhope (4PJBKY2WQR) → WWDR → Apple Root; IPA Info.plist `0.2.0` / build `3` after the XcodeGen version fix (first archive carried the literal `1.0`/`1`) |
| Upload to App Store Connect (transport) | built/exercised | `xcodebuild` upload: `No Accounts with App Store Connect Access`; `altool`: `Defaults.properties` missing; `iTMSTransporter`: needs the Transporter app. Build Upload API via `scripts/asc_build_upload.py`: record `cce73538-7d53-4cf1-b6ce-0e985b01215e`, file `f9dbb5e8-99cd-4c5e-a17d-2f89cf7873b2`, one part 582,394 bytes HTTP 200, commit `COMPLETE`, Apple state `PROCESSING` (receipt `fable-evidence/signed/v3/upload-receipt.json`); 8 fake-service tests cover the official payloads, retained ids, resumed-PROCESSING polling and the no-resend rule |
| Apple validation | built/exercised | build 3 failed (`90022` missing 120x120 icon, `90713` missing `CFBundleIconName`); build 4 with the asset-catalog icon processed to `VALID` / `READY_FOR_BETA_TESTING`; see "TestFlight upload" below |
| Device install | built/unverified (invitation pending) | build 4 is `VALID` and `IN_BETA_TESTING`; the internal group contains the build and Rawley's account (`INVITED`), independently read from App Store Connect at 2026-10-13 03:22 UTC. Acceptance and installation on the phone remain unverified. This TestFlight route requires no device UDID |
| `spark-small` on Spark-B | blocked (owner condition) | two cold starts failed in ~100 ms with `engine_start_error`, owner log `Docker GET failed: HTTP 404` for the profile image; circuit opened (consecutive_failures 2). Spark-A: `insufficient_memory_for_envelope`. Not an action this prototype may take |

## What was exercised, in order

1. **Source adapters and projection.** Idempotent reimport (`unchanged 209`), shorter corrected body
   becomes current with the old revision inspectable, membership removal cannot remain a hit and
   reappearance reinstates the same revision, version-qualified citations reject a wrong revision,
   unavailable store paths raise `SourceUnavailable`. Prayer titles are dates (`Prayer of 2026-09-27
   (device_capture)`) and refresh without a new revision.
2. **Real reads** (`search-*.json`, `cite-*.json`, `devotional-psa23.json`, `theographic-gen12.json`).
3. **Reasoning owner.** First attempts at 01:29:11Z and 01:31:09Z with `spark-small` returned HTTP 503
   `local_inference_backend_failed` (request ids `ad0bf1ac…`, `7fe15ebb…`); diagnosed on `spark-12b7`;
   switched the configuration to the advertised `qwen-coding-b` (cold start 214 s, then warm).
   The first `qwen-coding-b` probe produced 8 reasoning tokens and no content; thinking is now
   disabled and reasoning deltas are counted separately (`turn-smoke-3.json`, `ws-stream-cancel.txt`).
4. **Retrieval miss.** Before the fix the same question retrieved three prayers without "son" and the
   model asserted corpus-wide absence (`ws-stream-cancel.txt`, first session). After the focus-first
   query plan (`search-son-after-fix.json`, `turn-son-after-fix.json`, `turn-missing-after-fix.json`,
   `turn-heard-passage.json`) the known prayer is found and cited, and absence is scoped to the
   retrieved excerpts.
5. **Prayer intake** through the backend relay against the running `prayer-ingest` (commit `8e0e8df0`)
   using the intake's `POST /test-runs` identity, so no synthetic recording entered the corpus
   (`prayer-testrun-*.json`, `prayer-segment-upload.json`, `prayer-finalize.json`,
   `prayer-capture-status.json`).
6. **Outbox** (`work-dispatch-1.json`, `work-reconcile-1.json`, `work-packet.json`,
   `work-after-restart.json`), with the allocator left `inactive`, `main_pid 0`, launch
   `cycle14d-20261008`, no live runs, throughout.
7. **Native tests and simulator** on cloud-Mac session run 38333131177 (branch
   `codex/voice-portal-prototype`), logs saved on the runner as `/tmp/build-final.log`,
   `/tmp/test-final.log` and uploaded by the push workflow as artifacts.

## Review corrections applied

- OUTBOX-1: atomic claim before the external side effect; concurrency regression.
- OUTBOX-2: per-attempt directive file written only by the claim winner; payload-integrity regression.
- Capture files: writer continues after the highest index on disk or in the manifest; orphan files
  are reconciled on launch; ambient → prayer → ambient never overwrites bytes.
- Premature finalize: the writer settles explicitly (`expectedSegmentCount`), and finalize requires
  closed + settled + every segment verified + contiguous indexes.
- Prayer and barge-in: entering prayer always cancels current and queued speech; real speech while
  idle stops trailing TTS or devotional playback; prayer speech never interrupts.
- Voice continuity: supervised continuous recognition with restart after final/no-speech/error,
  deduplicated finals, speech-triggered barge-in; buttons remain the fallback; the simulator's exact
  unavailability is shown rather than hidden.
- Durable client outbox: local work list with stable operation ids, visible offline, replayed on
  reconnect after checking server existence, settled on confirmed receipt.
- Playback continuity: progress follows `AVSpeechSynthesizer` start/finish callbacks and is tied to
  the passage and source revision; pausing mid-segment resumes that segment.
- Reconcile uses `inbox list --all`; `rlm_result` artifacts require a reference naming a posted
  directive; source packets are unique files.
- Heard passage: supplied at the exact revision heard, flagged if historical; failed references are
  reported as not supplied.
- CI: `pipefail` with wrapped greps so a build failure can never read as success; full logs uploaded.
- VOICE-START-1: recognition startup now follows the authoritative audio-running and availability
  transitions in either order, once per session, with controller-level tests.
- SegmentWriter: appends after close/settlement are dropped (reproduced in a test).
- BUILD-VERSION-1: Info.plist version/build come from `MARKETING_VERSION` / `CURRENT_PROJECT_VERSION`.
- UPLOAD-1: the upload helper follows the official `BuildUploadFileUpdateRequest` (plural
  `sourceFileChecksums`), persists ids before each side effect and reconciles instead of recreating.

## TestFlight upload

| Build | Outcome |
| --- | --- |
| 0.2.0 (3), IPA md5 `a48eec019c978707a2739083eabbe18a`, signed on cloud-Mac session 38339802786 | Transport succeeded end to end through the Build Upload API; Apple validation **FAILED**: `90022` (no 120x120 icon in the bundle) and `90713` (no `CFBundleIconName`). The app had no asset catalog. Build number 3 is spent on App Store Connect. |
| 0.2.0 (4), IPA md5 `1779fcf0630548e7433add388f763be4`, with `Assets.xcassets/AppIcon` and top-level `CFBundleIconName`, signed on session 38339802786 (`rlm-adk-ios-mac-1`) | **Accepted.** Build Upload API record `3d99fc47-22f2-407a-8bef-9bbb369a5a8d`, file `40924b66-d230-4c84-a168-8bb5d90c655c`, one part 613,208 bytes HTTP 200, commit `COMPLETE`, Apple `PROCESSING` → `COMPLETE`; build `3d99fc47…` `processingState VALID`, `usesNonExemptEncryption false`, `internalBuildState READY_FOR_BETA_TESTING`, uploaded 2026-10-13 03:17 UTC. Receipt `fable-evidence/signed/v4/upload-receipt.json`. Internal TestFlight group "Prototype internal" (`93dc08e3-d600-4606-9508-2a37d56c0225`) has access to all builds; the only tester is the account holder `rawley.stanhope@gmail.com` (state `INVITED`, TestFlight's own email to his address); build state `IN_BETA_TESTING`; no external invitation, no public link. |

## Physical-device session findings (2026-10-13, build 0.2.0 (4) on iphone-12)

Evidence: `~/.local/state/rlm-adk-ios-prototype-supervision/physical-device-session-20261013T110137Z.json`
(sessions `3a293e2e…` 10:37:33 UTC and `885246c0…` 10:40:07 UTC from `100.115.237.108`; 78 turns:
27 done, 21 cancelled, 30 error), the backend `turns` table, and the Spark-B owner journal (read-only).

| Finding | Evidence | Repair |
| --- | --- | --- |
| Cancels could not reach a cold owner. The first four phone turns (10:37:44–10:37:52) hit `qwen-coding` stopped; `stream_chat` blocked in `urlopen` waiting for headers, so the phone's cancels could not be acted on before headers arrived; these turns lived 203–211 s (`done_ms` is the whole request lifetime; exact cancel arrival times are not recorded, so cancel-to-settle delay is not measured here). | turns 2d352ca5…, d8674549…, 3b6ae7a8…, e22ff854…; owner `model_ready` at 10:41:16 and 10:45:59 UTC | `reasoning.stream_chat` now runs the request on a socket-owning reader thread and waits in short ticks; cancel shuts the real socket (retained across chunked, close-delimited and `Connection: close` framings) while connecting, before headers or between SSE lines, joins the reader (bounded) and reports `upstream_settled`; a cancel during connect sends no request; precedence is by observation (a cancel observed first suppresses any already-queued delta, success or error; an error observed first stays an error); usage stays unknown when the owner sent none. Stalling-owner tests cover each case. |
| Queue saturation behind the stale leases. Owner capacity for qwen-coding is 4; with the four stale leases plus the cold load every later request waited the owner's 10 s queue and failed `503 queue_timeout` (30 errors, `done_ms` 10.0–10.2 s) from 10:38:01 until 10:45:45, even after the first `model_ready`. | owner access log: 30 × `503 466`; two `200` at 10:46:02 for requests from 10:42:37 | Same repair (leases are released on cancel). The server also supersedes a session's still-running turn when a new final arrives, so one phone never holds more than one owner slot. WebSocket test. No owner configuration was changed. |
| Barge-in on partials is unchanged. Speech during a pending or speaking turn cancels it (so an old answer cannot start over the user's next utterance); the 21 cancelled turns are consistent with that design and with the four cold-start cancels above. | 21 cancelled turns | No change. A new final additionally supersedes the pending turn on both sides (reducer cancels the old turn; server cancels it) as a fallback when no partial preceded it. Reducer and controller tests. |
| Restarted recognition inherited the previous utterance's silence age, so the silence timer could end a fresh request immediately (restart churn). | code path `startSilenceTimer` / `lastSpeechAt`; not directly observable in the turn table | `EndpointClock` measures silence from the later of the last partial and the request start; prayer never end-points. Tests. |
| One/two-word transcripts dominate ("No" ×~25). The backend does not record whether a transcript came from the microphone or typed input, so the turn table alone does not prove microphone audio for each one. | transcripts in the turn table | Not changed: ordinary short answers are legitimate; no word-count filter or wake word. Recognizer segment finalisation vs. the 2.5 s window needs audible-UX feedback from Rawley before any change. |

Warm behaviour after 10:46 UTC: first delta median ≈ 830 ms over the observed first deltas. The
`spark-small` image is still missing on Spark-B (owner `failures`).

## Explicit Scripture references (2026-10-13 follow-up)

Attended real-data smoke after the device repair (`device-repair-live-smoke.json`, case
`normal_completion`): "What does Psalm 23 say about rest? Explain briefly using the source." with no
heard-passage context retrieved 1 Peter 5 and Hebrews 4 (topic hits for "rest"), omitted Psalm 23, and
the model correctly refused to explain a passage it had not been given. The chapter is in the retained
edition (`/devotional?passage=PSA.23`).

Smallest correction (`voice_portal/references.py`, wired in `gather_evidence`): a reference written in
the user's own transcript (canonical book name or its numbered form with a written or spoken ordinal,
plus Psalm/Psalms/Ps; chapter; optional verse or range) is resolved against the current retained
chapter document and its verse locators before topic retrieval, with the chapter's chunks (or only the
chunks holding the named verses) as revision-qualified citations. References are parsed from the
transcript only, never from the topic query augmented with a heard-passage title, so "Why?" after a
devotional does not become a reference and a historical heard revision is not shadowed by the current
chapter. Coverage is reported exactly in `scope.explicit_references[]` and in the prompt:

| Case | `supplied` | `coverage` | `reason` |
| --- | --- | --- | --- |
| whole chapter, up to 8 chunks | true | full | – |
| chapter longer than 8 chunks | true (first 8) | partial | `chapter_longer_than_supplied_chunks` |
| verse or range fully in the chapter | true (chunks holding them) | full | – |
| range partly beyond the chapter (e.g. 23:5-99) | true (chunks holding 5–6) | partial | `some_verses_not_in_retained_chapter`, `verses_missing` listed |
| verses entirely beyond the chapter (e.g. 1 Peter 5:7 in a 4-verse fixture) | false | none | `verses_not_in_retained_chapter` |
| chapter not retained (e.g. Genesis 51) | false | none | `chapter_not_in_retained_edition` |
| verse 0, chapter 0, reversed range | false | none | `invalid_verse` / `invalid_chapter` / `reversed_range` |
| unknown book or bare numbered base ("Peter 5") | not a reference | – | – |

Heard-passage evidence still comes first at its exact revision; focus-first topic retrieval and the
Scripture/teaching/prayer distinction are unchanged. Tests (`tests/test_explicit_reference.py`, 15)
cover the parser forms above, each coverage row, a valid verse inside a multi-chunk chapter, the
chunk-cap partial case tied to the supplied cite ids, the heard-passage ordering, and two
`run_turn`-level cases (a historical heard citation with "Why?" plus a heard title; an explicit
reference in the transcript). Real-data evidence: `fable-explicit-passage-status.md`.

## Proof boundaries

- Simulator runs prove app/service connectivity, state rendering and the typed turn path. They do not
  prove live voice, microphone capture quality, background or lock-screen behaviour, or Bluetooth.
  CI screenshots run in fixture mode (no permission prompts, audio owner not started, fake reasoning
  owner, synthetic corpus).
- The TestFlight build proves the signing, packaging and upload path and Apple's processing; it does
  not prove anything about the app running on the phone until it is installed there.
- The intake test run proves the segment/finalize contract with the real service; publication of a
  real prayer (Cohere transcription, corpus membership) was not exercised in this slice.
- `directive_posted` and `artifact_available` were exercised only with the stub CLI in tests; the
  real allocator has no live run, so only `backend_unavailable` and reconciliation were observed.
- No GPT-Live or provider comparison was made; the reasoning numbers are for `qwen-coding-b` on
  Spark-B through the desktop's existing loopback forward.
