# Pipecat rebuild: benchmark report

Measurements against `acceptance-matrix.md` (with its 2026-10-14 amendments). Every number comes
from a run directory on Spark-A under `~/.local/state/voicesim/runs/talk-suites/conversation-core/runs/`
(talk tier), the harness receipts under `~/.local/state/voice-talk/harness/` (skill service), the
wrapper evidence under `~/.local/state/voicesim/phone-suite/` (isolated intake publications), and the
phone-trial run `~/.local/state/voicesim/phone-trial/runs/20261014T004931Z` (prayer capture). Tables
are rendered by `rlm-voice-sim/scripts/benchmark_tables.py <run_dir>`.

## Versions under test (Flows campaign, 2026-10-14 09:21 to 10:08 UTC)

| Piece | Version |
|---|---|
| Talk tier | rlm-adk-ios main `08327bc` (PR #14 on PR #13) plus the devotional prompt change in this PR: Pipecat Flows with six nodes (`talk/flows.py`) (Pipecat 1.12.0, Python 3.12.3, Spark-A `~/srv/voice-talk`), voice Kokoro `bm_george`; config: VAD stop 0.8 s, Smart Turn v3 fallback 1.2 s, bridge 0.2 s, amen silence 1.2 s, STT gate 120 RMS; `voice-talk.service` with `TimeoutStartSec` 600 |
| Skill service | rlm-adk-ios `8dee970d6f47c1b3e36e89d2ae23437ab854b79c` on the desktop (`/healthz` reports it); projection with Edwards and BibleProject |
| spark-inference | `7e98c9d58fb5d8997dd35b1feae3450546148aa9` on Spark-A (28080, Cohere `cohere-transcribe-03-2026`) and Spark-B (`qwen-coding-b` through 28111); Spark-A moved to `85e8fc3dcc0734a8d8d906252bb2563d3d7cbac4` at 13:53 UTC for the PhoneLLM A/B (adds the `phonellm` profile; Qwen kept, see `../llm-selection.md` §6) |
| voicesim | rlm-voice-sim `26540cb` (`voicesim talk`, modes text/file/room/phone, `scripts/phone-suite.sh`), fixtures voice `rawley-prayer` (S2-Pro, seed 7), 21 fixtures incl. `wake`/`quiet` controls |
| iPhone app | build 12 for the campaigns; **build 13** (`0.2.0 (13)`, PR #17 `a863534`, installed 12:44 UTC) for the WebRTC proof; iPhone 12 `00008101-000C35463A32001E`, iOS 26.6.2 (23G90), WebSocket transport, backend production except during wrapped runs |

## Layer 1: text harness against the skill service

`backend/scripts/skill_harness.py` against `http://100.90.105.30:8765`, receipt
`skill-harness-20261014T070402Z.json`: **22 of 22 checks passed** (held-out project descriptions
top-1 5 of 5; Psalm 23 verbatim WEB; Romans 8:28-30; word tool 0.3 s warm with 3 passages, frames one
sentence, every passage cited; interpretations 3 teachers with traditions; policy version
`1a77aaff19de1ba3`, 17 address phrases, `amen_silence_s` 1.2).

## Layers 2 and 3: the conversation-core suite, final campaign (Flows build)

Thirteen cases per layer against the merged Flows build (`08327bc`, `voice-talk.service` active),
2026-10-14 09:21 to 10:07 UTC, phone and room through `scripts/phone-suite.sh` (isolated intake;
each acoustic run published its two fixture prayers there; production stayed at 76 publications,
latest 2026-10-13 16:21 UTC). The phone layer's three misses were rerun at 10:08 UTC after two
fixes made during the campaign (the scorer now counts the phone's own amen end, `client:amen`, as an
amen end; the devotional node's instruction asks for a plain answer before teachers' readings), and
all passed; the rerun is reported separately, not folded into the first pass.

| Layer | Passed | First-audio p50 / p95 (cases 2 to 5) | Amen to end |
|---|---|---|---|
| Digital injection (`file`) | **13 of 13** | 1.44 s / 2.59 s | 0.95 to 0.98 s |
| Room loop (speakers → Spark-B mic → talk tier) | **13 of 13** | 1.70 s / 2.95 s | 1.26 to 1.47 s |
| Phone loop, WebSocket transport (speakers → iPhone → talk tier → phone speaker → Spark-B mic) | **10 of 13** first pass; **4 of 4** on the rerun of 01, 01b, 07b, 08 | 1.78 s / 2.22 s | 1.13 to 1.44 s (bound 1.8 s, uplink) |
| Phone loop, WebRTC transport (4 cases), build 12 | **0 of 5** | no reply audio | app stopped its audio unit (and microphone) in quiet |
| Phone loop, WebRTC transport (4 cases), **build 13** (12:46 UTC) | **5 of 5** | 2.45 s (02a) | 1.18 to 1.25 s |

Flows nodes observed in the receipts: `available` at start, `conversation` after the first reply,
`prayer` and back on amen, `devotional` after a reading (07b, 08), `quiet` and back on wake (09);
`project` is entered by the check-in tool (05).

**Phone-layer misses on the first pass and what they were:**

* 01 prayer by address: the phone's own amen rule ended the prayer first (`client:amen`, 1.44 s)
  and the scorer only accepted the talk tier's `amen` reason; fixed in the scorer; rerun passed
  (amen to end 1.44 s / 1.13 s).
* 07b barge-in and 08 devotional question: the model quoted the verse and recited teachers'
  readings instead of answering the question; the devotional node's instruction now asks for the
  plain answer first; rerun passed with judge 5 on both. On the phone path the soft "Hold on" did
  reach the server this time (it was lost once earlier in the night; see limits).

**WebRTC transport, build 13 (12:46 UTC, run `20261014T124621Z-phone-phone-webrtc-2`):** after
`WebRTCVoiceOutput` stopped disabling libwebrtc's audio unit (PR #17, merged `a863534`), the same
four cases passed 5 of 5 on the phone over SmallWebRTC: prayer entered by the **talk tier's** address
rule (`address:lord`), ended by the phone's amen rule 1.25 s / 1.18 s after the amen, no bot audio in
prayer, Psalm 23 verbatim (first audio 2.45 s), barge-in answered, quiet by phrase and wake by voice.
Build 13 was installed over USB at 12:44 UTC (IPA sha256
`1c0f72ddee4e540821a99d91ece6a182b7e470a6255b057db3ca904ace8a82fb`, signed from PR #17's branch);
the capture manifest before and after the install held 894 of 894 segments identical (895 after,
the open segment). The phone was left on WebSocket (first audio 1.78 s against 2.45 s on WebRTC in
the same session); Diagnostics switches it.

**WebRTC transport, build 12 (10:01 UTC), evidence of the failure:** the server's RTVI mirroring and
client-message unwrapping work (the log shows `client:sync`, `client:address:lord`, `client:amen`
arriving as RTVI client messages). The app connected in its persisted quiet state, entered prayer on
its own address rule, and returned to quiet after amen; in quiet on WebRTC the app stops its audio
unit, so the server heard nothing afterwards (no transcripts at all in 02a, 07b, 09) and the wake
phrase cannot lift it by voice. WebRTC floor sync therefore stays unproven on device until the app
keeps its microphone running in quiet on that transport (app work, build 13); WebSocket is the
deployed transport.

Per-case tables (rendered from the run directories):

### `20261014T092120Z-phone-full-phone-7` (phone mode)

| Case | Result | First audio (s) | Amen to end (s) | Judge | Failed checks / note |
|---|---|---|---|---|---|
| 01-prayer-address-amen | fail |  |  |  | amen_to_end; pauses_never_end_prayer |
| 01b-prayer-lets-pray | **pass** |  | 1.21 |  |  |
| 02a-scripture-psalm23 | **pass** | 2.22 |  | 5 |  |
| 02b-scripture-romans8-suffering | **pass** | 1.78 |  | 5 |  |
| 03-word-from-god | **pass** | 1.35 |  | 3 |  |
| 04-interpretations | **pass** | 1.94 |  | 3 |  |
| 05-project-checkin | **pass** | 1.41 |  | 5 |  |
| 06-work-request | **pass** | 1.93 |  | 5 |  |
| 07a-backchannel | **pass** |  |  | 5 |  |
| 07b-barge-in | fail |  |  | 1 | judge — judge: The assistant ignored the user's specific question about the meaning of the phrase and instead provided a recitation of the verse and unrela |
| 07c-bridge | **pass** | 1.68 |  |  |  |
| 08-devotional-interruption | fail |  |  | 1 | judge — judge: The assistant did not stop the reading at the interruption, did not answer the question about the green pastures, and continued reading the  |
| 09-quiet-wake | **pass** |  |  |  |  |

**10 of 13 passed.** First-audio latency over cases 2 to 5: p50 1.78 s, p95 2.22 s, max 2.22 s (n=5).

### `20261014T093529Z-room-full-room-7` (room mode)

| Case | Result | First audio (s) | Amen to end (s) | Judge | Failed checks / note |
|---|---|---|---|---|---|
| 01-prayer-address-amen | **pass** |  | 1.31 |  |  |
| 01b-prayer-lets-pray | **pass** |  | 1.23 |  |  |
| 02a-scripture-psalm23 | **pass** | 1.60 |  | 5 |  |
| 02b-scripture-romans8-suffering | **pass** | 1.77 |  | 5 |  |
| 03-word-from-god | **pass** | 1.70 |  | 3 |  |
| 04-interpretations | **pass** | 1.64 |  | 3 |  |
| 05-project-checkin | **pass** | 2.95 |  | 5 |  |
| 06-work-request | **pass** | 1.51 |  | 5 |  |
| 07a-backchannel | **pass** |  |  | 5 |  |
| 07b-barge-in | **pass** |  |  | 5 |  |
| 07c-bridge | **pass** | 1.79 |  |  |  |
| 08-devotional-interruption | **pass** |  |  | 5 |  |
| 09-quiet-wake | **pass** |  |  |  |  |

**13 of 13 passed.** First-audio latency over cases 2 to 5: p50 1.70 s, p95 2.95 s, max 2.95 s (n=5).

### `20261014T094833Z-file-full-file-7` (file mode)

| Case | Result | First audio (s) | Amen to end (s) | Judge | Failed checks / note |
|---|---|---|---|---|---|
| 01-prayer-address-amen | **pass** |  | 0.98 |  |  |
| 01b-prayer-lets-pray | **pass** |  | 0.96 |  |  |
| 02a-scripture-psalm23 | **pass** | 1.53 |  | 5 |  |
| 02b-scripture-romans8-suffering | **pass** | 1.44 |  | 5 |  |
| 03-word-from-god | **pass** | 1.34 |  | 3 |  |
| 04-interpretations | **pass** | 1.36 |  | 5 |  |
| 05-project-checkin | **pass** | 2.59 |  | 5 |  |
| 06-work-request | **pass** | 1.39 |  | 5 |  |
| 07a-backchannel | **pass** |  |  | 5 |  |
| 07b-barge-in | **pass** |  |  | 5 |  |
| 07c-bridge | **pass** | 1.51 |  |  |  |
| 08-devotional-interruption | **pass** |  |  | 5 |  |
| 09-quiet-wake | **pass** |  |  |  |  |

**13 of 13 passed.** First-audio latency over cases 2 to 5: p50 1.44 s, p95 2.59 s, max 2.59 s (n=5).

### `20261014T100115Z-phone-phone-webrtc-1` (phone mode)

| Case | Result | First audio (s) | Amen to end (s) | Judge | Failed checks / note |
|---|---|---|---|---|---|
| 01-prayer-address-amen | fail |  |  |  | mode_sequence; amen_to_end |
| 01b-prayer-lets-pray | fail |  |  |  | mode_sequence; amen_to_end |
| 02a-scripture-psalm23 | fail |  |  | 1 | first_audio_latency; reply_contains:shepherd; reply_contains:green pastures; reply_contains:still waters; reply_contains:valley; reply_contains:goodness; judge — judge: The assistant provided no response, failing to read, summarize, or refuse the request. |
| 07b-barge-in | fail |  |  | 1 | judge — judge: The assistant provided no response, ignoring the user's request entirely. |
| 09-quiet-wake | fail |  |  |  | mode_sequence; reply_contains_any |

**0 of 5 passed.**

#### Rerun of the phone misses

### `20261014T100838Z-phone-phone-fix9` (phone mode)

| Case | Result | First audio (s) | Amen to end (s) | Judge | Failed checks / note |
|---|---|---|---|---|---|
| 01-prayer-address-amen | **pass** |  | 1.44 |  |  |
| 01b-prayer-lets-pray | **pass** |  | 1.13 |  |  |
| 07b-barge-in | **pass** |  |  | 5 |  |
| 08-devotional-interruption | **pass** |  |  | 5 |  |

**4 of 4 passed.**


## Layers 2 and 3: the conversation-core suite, campaign before Flows (2026-10-14 07:54 to 08:33 UTC)

Thirteen cases, one pass each per layer, against the systemd-managed talk tier (`voice-talk.service`
active), owners warmed first. Phone and room runs went through `scripts/phone-suite.sh` (isolated
intake; each published its two fixture prayers there; production untouched).

| Layer | Passed | First-audio p50 / p95 (cases 2 to 5) | Notes |
|---|---|---|---|
| Digital injection (`file`) | **13 of 13** | 1.35 s / 2.54 s | amen-to-end 0.95 to 0.98 s |
| Room loop (speakers → Spark-B mic → talk tier) | **13 of 13** | 1.70 s / 2.92 s | amen-to-end 1.26 to 1.47 s |
| Phone loop (speakers → iPhone → talk tier → phone speaker → Spark-B mic) | **11 of 13** | 1.76 s / 2.12 s | amen-to-end 1.26 to 1.44 s (bound 1.8 s, uplink); see the two misses below |

Phone-layer misses:

* **01 prayer by address, `bot_silent`**: the prayer itself was silent (`no_bot_audio_while_prayer`
  passed; amen-to-end 1.26 s) but the room microphone recorded 0.40 s of sound 4.4 s after the amen,
  with no `bot-started-speaking` event: not bot speech. Origin unknown (phone UI sound or room noise);
  the check counts every room-energy span in the case window.
* **07c bridge, first audio 2.08 s** against the 2.0 s bound: the uplink adds about 0.45 s on the
  phone path (the room layer measured 1.56 s for the same case). The suite now carries a 2.5 s
  phone-layer allowance for this case, recorded in the matrix amendments; it was added after this
  run, so the run is reported as it scored.

Per-case tables (rendered from the run directories):

### `20261014T075445Z-phone-full-phone-6` (phone mode)

| Case | Result | First audio (s) | Amen to end (s) | Judge | Failed checks / note |
|---|---|---|---|---|---|
| 01-prayer-address-amen | fail |  | 1.26 |  | bot_silent |
| 01b-prayer-lets-pray | **pass** |  | 1.10 |  |  |
| 02a-scripture-psalm23 | **pass** | 1.76 |  | 5 |  |
| 02b-scripture-romans8-suffering | **pass** | 2.12 |  | 5 |  |
| 03-word-from-god | **pass** | 1.67 |  | 3 |  |
| 04-interpretations | **pass** | 2.07 |  | 5 |  |
| 05-project-checkin | **pass** | 1.67 |  | 5 |  |
| 06-work-request | **pass** | 1.95 |  | 5 |  |
| 07a-backchannel | **pass** |  |  | 5 |  |
| 07b-barge-in | **pass** |  |  | 5 |  |
| 07c-bridge | fail | 2.08 |  |  | first_audio_latency |
| 08-devotional-interruption | **pass** |  |  | 5 |  |
| 09-quiet-wake | **pass** |  |  |  |  |

**11 of 13 passed.** First-audio latency over cases 2 to 5: p50 1.76 s, p95 2.12 s, max 2.12 s (n=5).

### `20261014T080748Z-room-full-room-6` (room mode)

| Case | Result | First audio (s) | Amen to end (s) | Judge | Failed checks / note |
|---|---|---|---|---|---|
| 01-prayer-address-amen | **pass** |  | 1.21 |  |  |
| 01b-prayer-lets-pray | **pass** |  | 1.43 |  |  |
| 02a-scripture-psalm23 | **pass** | 1.70 |  | 5 |  |
| 02b-scripture-romans8-suffering | **pass** | 1.77 |  | 5 |  |
| 03-word-from-god | **pass** | 1.70 |  | 3 |  |
| 04-interpretations | **pass** | 1.51 |  | 3 |  |
| 05-project-checkin | **pass** | 2.92 |  | 5 |  |
| 06-work-request | **pass** | 1.46 |  | 5 |  |
| 07a-backchannel | **pass** |  |  | 5 |  |
| 07b-barge-in | **pass** |  |  | 5 |  |
| 07c-bridge | **pass** | 1.49 |  |  |  |
| 08-devotional-interruption | **pass** |  |  | 3 |  |
| 09-quiet-wake | **pass** |  |  |  |  |

**13 of 13 passed.** First-audio latency over cases 2 to 5: p50 1.70 s, p95 2.92 s, max 2.92 s (n=5).

### `20261014T082047Z-file-full-file-6` (file mode)

| Case | Result | First audio (s) | Amen to end (s) | Judge | Failed checks / note |
|---|---|---|---|---|---|
| 01-prayer-address-amen | **pass** |  | 0.98 |  |  |
| 01b-prayer-lets-pray | **pass** |  | 0.99 |  |  |
| 02a-scripture-psalm23 | **pass** | 1.35 |  | 5 |  |
| 02b-scripture-romans8-suffering | **pass** | 1.45 |  | 5 |  |
| 03-word-from-god | **pass** | 1.35 |  | 3 |  |
| 04-interpretations | **pass** | 1.33 |  | 3 |  |
| 05-project-checkin | **pass** | 2.54 |  | 5 |  |
| 06-work-request | **pass** | 2.10 |  | 5 |  |
| 07a-backchannel | **pass** |  |  | 5 |  |
| 07b-barge-in | **pass** |  |  | 5 |  |
| 07c-bridge | **pass** | 1.46 |  |  |  |
| 08-devotional-interruption | **pass** |  |  | 5 |  |
| 09-quiet-wake | **pass** |  |  |  |  |

**13 of 13 passed.** First-audio latency over cases 2 to 5: p50 1.35 s, p95 2.54 s, max 2.54 s (n=5).

Earlier full campaigns the same night, kept for the record: `*-full-*-4` (phone 8/13, room 9/13,
file 7/13, before the admin-hold mode event, the per-session reading state and the context scrubber
were right) and `*-full-*-5` (phone 12/13, room 10/13, file 12/13, before the backchannel window was
put on the VAD clock and stacked pause phrases were consumed).


## Prayer capture (phone, isolated intake)

* 2026-10-14 00:49 UTC acoustic trial (`phone-trial.sh`, build 8): capture hash-matched the phone's
  segments, isolated intake transcript WER **0.0**, alignment 0.812.
* Build 12 (iOS agent, 2026-10-14 03:00 to 05:00 UTC): four fixture prayers published by the isolated
  intake with text equal to the reference; the 450 build-8 segments identical (size and SHA-256)
  after every install; `last_buffer_age_s` never above 0.1 s with the WebRTC audio unit running.
* Final campaign: every wrapped run published its two fixture prayers to the **isolated** intake
  (`phone-suite/<stamp>/intake-publications.txt`), and the production store stayed at 76
  publications, latest 2026-10-13 16:21 UTC.

## Infrastructure and process events (recovered; not counted against cases)

| When (UTC) | Event | Recovery |
|---|---|---|
| 10-14 02:43 | Spark-A refused every LLM: `insufficient_memory_for_envelope` (desktop apps hold ~40 GiB) | `SPARK_INFERENCE_CODING_ADMISSION_GIB=72`, owner restarted |
| 02:46 | Cohere circuit `startup_failed/owner_shutdown`; old release could not reset one model | spark-inference PR #4 (per-model reset, half-open, foreign-GPU allowlist) merged and deployed on both Sparks |
| 03:29 | `host_memory_pressure` with Qwen and Cohere both on Spark-A | LLM moved to Spark-B's owner via 28111 |
| 03:39 | voicesim judge pointed at Spark-A, cold-started Qwen there and evicted Cohere | judge moved to Spark-B (`judge_url`) |
| 05:10, 05:45 | **The phone, on the production backend, captured fixture prayers from the room during a room run and a phone run and published four synthetic prayers to the production prayer store.** The ingest has no retraction path. | Removed by store surgery with a backup, `rows.json` and receipt under `~/.local/rlm-adk/stores/prayer_pipeline/removed-synthetic-20261014/` (desktop), plus the voice-portal capture ledger rows; ingest and skill service restarted. Rule and tooling since: `scripts/phone-suite.sh` wraps every acoustic run (isolated intake + backend, app relaunched with `RLMADK_BACKEND_URL`), voicesim refuses acoustic runs outside it, and data-pipelines PR #59 (`17a1f3a`, released to the desktop ingest) adds the missing retraction path: `POST /recordings/{id}/disposition` with `action: retract` removes a publication with a reason and keeps the recording, artifacts and audio. |
| 05:48 | Phone session answered "One moment." with no tool call: bridge phrases had accumulated in the assistant context and the model imitated them | `ContextScrubber` before the assistant aggregator; prompt rule |
| 09:04 | First Flows build: the owner's Qwen chat template returned HTTP 400 `System message must be at the beginning` for the node task messages Flows appends mid-context, so no turn answered | node state folded into the system instruction (`LLMUpdateSettingsFrame`), no task messages; Flows initialized at pipeline start (frames queued before the task ran were lost) |
| 12:05 | Live use after the campaign: both owners had idled out (~80 min); each turn's request started a 200 s Qwen load that the next interruption cancelled (`model_start_cancelled` ×8 on Spark-B); the bot only spoke bridge phrases for nine minutes, and the app's audio engine stopped at 12:12 with a failed watchdog restart | owners warmed; `voice-talk-warm.timer` every 10 min; bridge follow-up notice at 8 s; app relaunched (PR #18) |
| 06:46 | Per-turn transcription (tried to stop "Psalm twenty \| three" splitting) deadlocked: Pipecat's stop strategy waits for a transcript before ending the turn | reverted to per-segment STT; VAD stop 0.8 s; prompt rule for pieces |

## Known limits and omissions

* **Phone barge-in with a soft interjection.** Once during the night (06:12 UTC) "Hold on." spoken
  while the phone's own speaker played never reached the talk tier (no VAD event, no transcript);
  in the later phone runs it did and case 8 passed. The app's voice processing can suppress a quiet
  overlap; treat it as intermittent until the app ducks instead of suppressing.
  **Live use 2026-10-14 17:01 UTC** (session `websocket-d33b013b89`): no VAD start in 390 s of bot
  speech while the user said "Stop. Stop.", "I have a question. Please pause." and more. The phone's
  capture shows the voice arrived loudly; the phone was on a Bluetooth headset (8 kHz) and Silero at
  16 kHz rejected the narrowband, double-talk speech. Fixed in the talk tier's VAD
  (`talk/vad.py`): replay of that capture goes from 0 to 8 detected interjection spans with no
  start on bot-only audio. See `ios-client.md` § "Double talk during playback".
* **Word tool latency on a new prayer.** Themes are cached per prayer; the first call for a prayer
  not yet themed took 59 s on the phone session (01:49 UTC); subsequent calls 0.3 s. The bridge
  phrase covers the gap, but a spoken "this will take a moment" is better; not done.
* **WebRTC on the phone**: closed with build 13 (5 of 5, see above). WebSocket remains the
  transport the phone is left on because it measured lower first-audio latency; both are proven.
* **Repeats.** Each case ran once per layer in the final campaign (plus the development runs listed
  in the run directory); p95 values are therefore over cases, not repeats.
* **Work request.** The allocator is inactive, so every work request is saved as an operation and
  spoken as refused and not done ("Saved as operation …, but the allocator refused it"); the
  acknowledgement path with an operation id is proven, the dispatch is not.
* `voice-talk.service` was enabled before the final campaign (the development runs earlier that night
  ran the same command under `setsid nohup`).

#### Build 13 phone runs (12:42 to 12:50 UTC)

### `20261014T124621Z-phone-phone-webrtc-2` (phone mode)

| Case | Result | First audio (s) | Amen to end (s) | Judge | Failed checks / note |
|---|---|---|---|---|---|
| 01-prayer-address-amen | **pass** |  | 1.25 |  |  |
| 01b-prayer-lets-pray | **pass** |  | 1.18 |  |  |
| 02a-scripture-psalm23 | **pass** | 2.45 |  | 5 |  |
| 07b-barge-in | **pass** |  |  | 3 |  |
| 09-quiet-wake | **pass** |  |  |  |  |

**5 of 5 passed.** First-audio latency over cases 2 to 5: p50 2.45 s, p95 2.45 s, max 2.45 s (n=1).

### `20261014T124245Z-phone-phone-debug1` (phone mode)

| Case | Result | First audio (s) | Amen to end (s) | Judge | Failed checks / note |
|---|---|---|---|---|---|
| 02a-scripture-psalm23 | **pass** | 1.78 |  | 5 |  |
| 09-quiet-wake | **pass** |  |  |  |  |

**2 of 2 passed.** First-audio latency over cases 2 to 5: p50 1.78 s, p95 1.78 s, max 1.78 s (n=1).
