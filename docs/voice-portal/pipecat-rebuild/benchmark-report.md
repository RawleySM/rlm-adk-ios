# Pipecat rebuild: benchmark report

Measurements against `acceptance-matrix.md` (with its 2026-10-14 amendments). Every number comes
from a run directory on Spark-A under `~/.local/state/voicesim/runs/talk-suites/conversation-core/runs/`
(talk tier), the harness receipts under `~/.local/state/voice-talk/harness/` (skill service), the
wrapper evidence under `~/.local/state/voicesim/phone-suite/` (isolated intake publications), and the
phone-trial run `~/.local/state/voicesim/phone-trial/runs/20261014T004931Z` (prayer capture). Tables
are rendered by `rlm-voice-sim/scripts/benchmark_tables.py <run_dir>`.

## Versions under test (final campaign, 2026-10-14 07:04 to 07:45 UTC)

| Piece | Version |
|---|---|
| Talk tier | rlm-adk-ios branch `claude/talk-tier`, `talk/` at `d9c10ab` (Pipecat 1.12.0, Python 3.12.3, Spark-A `~/srv/voice-talk`), voice Kokoro `bm_george`; config: VAD stop 0.8 s, Smart Turn v3 fallback 1.5 s, bridge 0.2 s, amen silence 1.2 s, STT gate 120 RMS |
| Skill service | rlm-adk-ios `8dee970d6f47c1b3e36e89d2ae23437ab854b79c` on the desktop (`/healthz` reports it); projection with Edwards and BibleProject |
| spark-inference | `7e98c9d58fb5d8997dd35b1feae3450546148aa9` on Spark-A (28080, Cohere `cohere-transcribe-03-2026`) and Spark-B (`qwen-coding-b` through 28111) |
| voicesim | rlm-voice-sim `7da4af5`+ (`voicesim talk`, modes text/file/room/phone, `scripts/phone-suite.sh`), fixtures voice `rawley-prayer` (S2-Pro, seed 7), 21 fixtures incl. `wake`/`quiet` controls |
| iPhone app | build 12 (`0.2.0 (12)`, commit `6d60653`, PR #12 merged as `0e793ce`) on the iPhone 12 `00008101-000C35463A32001E`, iOS 26.6.2 (23G90), WebSocket transport, backend production except during wrapped runs |

## Layer 1: text harness against the skill service

`backend/scripts/skill_harness.py` against `http://100.90.105.30:8765`, receipt
`skill-harness-20261014T070402Z.json`: **22 of 22 checks passed** (held-out project descriptions
top-1 5 of 5; Psalm 23 verbatim WEB; Romans 8:28-30; word tool 0.3 s warm with 3 passages, frames one
sentence, every passage cited; interpretations 3 teachers with traditions; policy version
`1a77aaff19de1ba3`, 17 address phrases, `amen_silence_s` 1.2).

RESULTS_PLACEHOLDER

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
* The `voice-talk.service` unit was enabled at the end of the campaign; before that the server ran
  under `setsid nohup` with the same command.
