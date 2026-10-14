# Pipecat rebuild: benchmark report

Measurements against `acceptance-matrix.md`. Every number here comes from a run directory on
Spark-A under `~/.local/state/voicesim/runs/talk-suites/conversation-core/runs/` (talk tier), the
harness receipts under `~/.local/state/voice-talk/harness/` (skill service), and the phone-trial runs
under `~/.local/state/voicesim/phone-trial/runs/`. Infrastructure failures are listed separately
and were repeated after recovery, as the matrix requires. This file is appended as runs land; the
final section says what passed, what failed, and what was left out.

## Versions under test

| Piece | Version |
|---|---|
| Talk tier | rlm-adk-ios branch `claude/talk-tier`, `talk/` (Pipecat 1.12.0, Python 3.12.3 on Spark-A), voice Kokoro `bm_george` (kokoro-onnx v1.0) |
| Skill service | rlm-adk-ios `ee5916706b6c7e8d3624c1cc4efaf05d5d3d6275` (PR #11) deployed on the desktop 2026-10-14 03:40 UTC; projection with Edwards and BibleProject imported (693 documents, 28,416 chunks) |
| spark-inference | `7e98c9d58fb5d8997dd35b1feae3450546148aa9` (PR #4) on Spark-A (28080, Cohere) and Spark-B (18300 / forward 28111, `qwen-coding-b`) |
| voicesim | rlm-voice-sim `6d8caad` (`voicesim talk`), fixtures built 2026-10-14 03:18 UTC with voice `rawley-prayer` (S2-Pro, seed 7) |
| iPhone app | build 8 on the iPhone 12 (`00008101-000C35463A32001E`, iOS 26.6.2) until the Pipecat-client build lands; see the phone section |

## Layer 1: text harness against the skill service

`backend/scripts/skill_harness.py` against the live service `http://100.90.105.30:8765`. Receipt `skill-harness-20261014T035512Z.json`: **22 of 22
checks passed.**

| Case | Checks | Result |
|---|---|---|
| 2 Scripture | Psalm 23 available, verbatim WEB opening ("A Psalm by David. Yahweh is my shepherd; I shall lack nothing"), six verses, citations; Romans 8:28-30 three verses; "John chapter three verse sixteen" resolves; Genesis 51 reported unavailable with a spoken reason | pass |
| 3 Word from God | latest prayer found (the 2026-10-13 12:18 capture), 3 passages, one-sentence frames, every passage cited, 3 to 8 themes; 0.2 s warm (themes cached per prayer; first call ~7.6 s, cold owner ~245 s) | pass |
| 4 Interpretations | three teachers (John Piper, Jonathan Edwards, The Church of Eleven22), traditions labelled, distinctions note, every voice cited | pass |
| 5 Project | held-out descriptions top-1 **5 of 5**; "the scripture project" asks a confirmation question; confirmed report names plan, branches, results, checkpoints, outputs | pass |
| 6 Work | `/work` reachable (2 operations recorded); a real POST was exercised by the skill agent: operation `aaa1555f` saved, allocator refused "no live run" and the spoken text says it is not done | pass (allocator inactive by design) |
| policy | `/policy` version `8a353ffb765871ab`, `amen_silence_s` 1.2 | pass |

## Layer 2a: digital injection (fixture streamed into the talk tier, no room)

Run `20261014T034405Z-file-file4` (after the owner moves described under infrastructure):

| Case | Result | Measures |
|---|---|---|
| 01 prayer by address | **pass** | entered prayer on "Lord"; 3.0 s and 4.5 s pauses did not end it; bot audio 0.0 s; amen-to-end **1.16 s** |
| 01b let's pray, mid-sentence amen | **pass** | "amen to that, but I am not finished" did not end it; final amen ended it at **1.16 s**; bot audio 0.0 s |
| 02a read Psalm 23 | **pass** | first audio **1.87 s** after the utterance; all five phrases present; judge 5/5 (verbatim WEB) |
| 07c bridge | **pass** | bridge phrase spoken; first audio before the utterance's scripted end (Smart Turn ended the turn at the comma of a two-clause question, see notes) |
| 09 quiet / wake | fail in this run: a suite-runner defect (the trigger watched for events only after the step finished streaming; the quiet transition had already happened at 3.5 s) | fixed in voicesim `6d8caad`; rerun below |

Full-suite runs (all twelve cases, file then room) are appended below when they finish.

## Layer 2b: room loop (desktop speakers → Spark-B microphone → Cohere → talk tier)

Run `20261014T035210Z-room-room2`:

| Case | Result | Measures |
|---|---|---|
| 01 prayer by address | **pass** | prayer entered from the room microphone; pauses survived; bot audio 0.0 s; amen-to-end within 1.5 s |
| 02a read Psalm 23 | **pass** | first audio under 3.0 s; all phrases; judge pass |

## Layer 3: phone loop

Pending the Pipecat-client build (iOS agent). The prayer capture path through the native app was
proven on 2026-10-13 20:49 EDT (run `20261014T004931Z`): capture hash-matched, isolated intake
transcript WER 0.0, alignment 0.812.

## Infrastructure events during the runs (recovered, not counted against cases)

| When (EDT) | Event | Recovery |
|---|---|---|
| 22:43 | Spark-A refused every LLM: `insufficient_memory_for_envelope` (79.9 GiB available vs the 80 GiB default gate; desktop apps hold ~40 GiB) | `SPARK_INFERENCE_CODING_ADMISSION_GIB=72` in owner.env, owner restarted |
| 22:46 | Cohere circuit `startup_failed/owner_shutdown` from that restart; the old release could not reset one model without unloading all | unload, reset, reload; spark-inference PR #4 (per-model reset, owner_shutdown half-open, foreign-GPU allowlist) merged and deployed on both Sparks |
| 23:11 | Cohere circuit `request_aborted_or_backend_failed` (a disconnected client) | `sparkctl reset cohere-transcribe` |
| 23:29 | `host_memory_pressure` with Qwen and Cohere both resident on Spark-A (20 GiB free) | LLM moved to Spark-B's owner (`qwen-coding-b` via 28111); Spark-A keeps Cohere |
| 23:39 | voicesim's judge was still pointed at Spark-A and cold-started Qwen there, evicting Cohere mid-run | judge moved to Spark-B (`judge_url` config field) |
| 23:50 | first room run streamed no audio: pw-record writes raw samples to a pipe, not WAV | live-mic reader handles both |

## Notes

* Barge-in timing is measured from the end of the 320 ms backchannel window, as recorded in the
  matrix before the runs: the dossier's own rule makes a sub-200 ms cut from onset incompatible with
  tolerating "mm-hmm".
* 07c: Smart Turn v3 ended the turn at the comma of "Which of my prayers mentions my son, and what
  Scripture fits it?" on the synthetic voice; the bridge then played over the second clause. A
  prosody-aware stop or a longer `stop_secs` for long questions is a tuning item, not a floor defect.
