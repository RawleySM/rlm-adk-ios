# Pipecat rebuild: benchmark report

Measurements against `acceptance-matrix.md` (with its 2026-10-14 amendments). Every number comes
from a run directory on Spark-A under `~/.local/state/voicesim/runs/talk-suites/conversation-core/runs/`
(talk tier), the harness receipts under `~/.local/state/voice-talk/harness/` (skill service), the
wrapper evidence under `~/.local/state/voicesim/phone-suite/` (isolated intake publications), and the
phone-trial run `~/.local/state/voicesim/phone-trial/runs/20261014T004931Z` (prayer capture). Tables
are rendered by `rlm-voice-sim/scripts/benchmark_tables.py <run_dir>`.

## Versions under test (final campaign, 2026-10-14 07:54 to 08:33 UTC)

| Piece | Version |
|---|---|
| Talk tier | rlm-adk-ios branch `claude/talk-tier` (PR #13), `talk/` at `a790d3b` (Pipecat 1.12.0, Python 3.12.3, Spark-A `~/srv/voice-talk`), voice Kokoro `bm_george`; config: VAD stop 0.8 s, Smart Turn v3 fallback 1.2 s, bridge 0.2 s, amen silence 1.2 s, STT gate 120 RMS |
| Skill service | rlm-adk-ios `8dee970d6f47c1b3e36e89d2ae23437ab854b79c` on the desktop (`/healthz` reports it); projection with Edwards and BibleProject |
| spark-inference | `7e98c9d58fb5d8997dd35b1feae3450546148aa9` on Spark-A (28080, Cohere `cohere-transcribe-03-2026`) and Spark-B (`qwen-coding-b` through 28111) |
| voicesim | rlm-voice-sim `26540cb` (`voicesim talk`, modes text/file/room/phone, `scripts/phone-suite.sh`), fixtures voice `rawley-prayer` (S2-Pro, seed 7), 21 fixtures incl. `wake`/`quiet` controls |
| iPhone app | build 12 (`0.2.0 (12)`, commit `6d60653`, PR #12 merged as `0e793ce`) on the iPhone 12 `00008101-000C35463A32001E`, iOS 26.6.2 (23G90), WebSocket transport, backend production except during wrapped runs |

## Layer 1: text harness against the skill service

`backend/scripts/skill_harness.py` against `http://100.90.105.30:8765`, receipt
`skill-harness-20261014T070402Z.json`: **22 of 22 checks passed** (held-out project descriptions
top-1 5 of 5; Psalm 23 verbatim WEB; Romans 8:28-30; word tool 0.3 s warm with 3 passages, frames one
sentence, every passage cited; interpretations 3 teachers with traditions; policy version
`1a77aaff19de1ba3`, 17 address phrases, `amen_silence_s` 1.2).

## Layers 2 and 3: the conversation-core suite, final campaign

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
| 05:10, 05:45 | **The phone, on the production backend, captured fixture prayers from the room during a room run and a phone run and published four synthetic prayers to the production prayer store.** The ingest has no retraction path. | Removed by store surgery with a backup, `rows.json` and receipt under `~/.local/rlm-adk/stores/prayer_pipeline/removed-synthetic-20261014/` (desktop), plus the voice-portal capture ledger rows; ingest and skill service restarted. Rule and tooling since: `scripts/phone-suite.sh` wraps every acoustic run (isolated intake + backend, app relaunched with `RLMADK_BACKEND_URL`). |
| 05:48 | Phone session answered "One moment." with no tool call: bridge phrases had accumulated in the assistant context and the model imitated them | `ContextScrubber` before the assistant aggregator; prompt rule |
| 06:46 | Per-turn transcription (tried to stop "Psalm twenty \| three" splitting) deadlocked: Pipecat's stop strategy waits for a transcript before ending the turn | reverted to per-segment STT; VAD stop 0.8 s; prompt rule for pieces |

## Known limits and omissions

* **Phone barge-in with a soft interjection.** "Hold on." spoken while the phone's own speaker plays
  never reached the talk tier (no VAD event, no transcript) in the phone layer; a louder, longer
  interruption ("Wait, stop there. What does the valley of the shadow mean?") does. The app's voice
  processing suppresses the quieter overlap. Case 8 therefore cannot pass on the phone layer with
  this build; it passes by injection and in the room. App-side fix (echo-cancellation gain / duck
  instead of suppress) is listed under next work.
* **Word tool latency on a new prayer.** Themes are cached per prayer; the first call for a prayer
  not yet themed took 59 s on the phone session (01:49 UTC); subsequent calls 0.3 s. The bridge
  phrase covers the gap, but a spoken "this will take a moment" is better; not done.
* **WebRTC floor sync on the phone** was added server-side (RTVI server-message mirror and
  client-message unwrapping) but the phone runs used the WebSocket transport; WebRTC floor sync on
  device is unverified.
* **Repeats.** Each case ran once per layer in the final campaign (plus the development runs listed
  in the run directory); p95 values are therefore over cases, not repeats.
* **Work request.** The allocator is inactive, so every work request is saved as an operation and
  spoken as refused and not done ("Saved as operation …, but the allocator refused it"); the
  acknowledgement path with an operation id is proven, the dispatch is not.
* `voice-talk.service` was enabled before the final campaign (the development runs earlier that night
  ran the same command under `setsid nohup`).
