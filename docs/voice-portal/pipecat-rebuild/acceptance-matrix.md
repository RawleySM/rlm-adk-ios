# Pipecat rebuild: acceptance matrix

Written 2026-10-13 before any run, as the goal requires: owners verified, timing endpoints, numeric
thresholds, repeats and scoring fixed in advance. Measurements land in `benchmark-report.md` beside
this file; this file does not change to fit results.

## Verified owners (read on 2026-10-13 ~21:50 EDT)

| Component | Host | Evidence |
|---|---|---|
| Prayer intake (production) | **desktop**, `rlm-prayer-ingest.service`, 127.0.0.1:8091 | `systemctl --user` on desktop; store `~/.local/rlm-adk/stores/prayer_pipeline/prayer_ingest.sqlite3` |
| Prayer intake (isolated, for tests) | **Spark-A**, release `~/srv/prayer-ingest/releases/8e0e8df0…`, port 18091, store `~/.local/state/voicesim/phone-trial/isolated-intake-store` | phone trial 2026-10-13 |
| Prayer ASR (Cohere Transcribe) and LLMs (`qwen-coding`, `qwen-coding-flash-next`, `spark-small`) | **Spark-A**, `spark-inference.service`, 127.0.0.1:28080 (`/v1/models`, `/v1/chat/completions`, `/upstream/cohere-transcribe/v1/audio/transcriptions`) | `curl /v1/models` |
| Second reasoning owner (`qwen-coding-b`) | **Spark-B**, reached from the desktop at 127.0.0.1:28111 (`rlm-spark-forwards.service`) | desktop portal config |
| Fish S2-Pro (voicesim test speaker) | **Spark-B**, `fish-speech-s2pro.service`, 127.0.0.1:18085; tunnels on Spark-A and desktop | voicesim |
| Room microphone (Razer Seiren) | **Spark-B**, PipeWire source, user `rawleysm` | voicesim |
| Room speakers (USB audio) | **desktop**, PipeWire sink `alsa_output.usb-1130_USB_AUDIO-00.analog-stereo`, per-stream volume 0.5 | voicesim |
| Voice portal backend (today) | **desktop**, `python3 -m voice_portal serve` from `~/srv/voice-portal/backend`, 0.0.0.0:8765, no unit | `ss -ltnp` |
| Data products read by the skill service | **desktop**: prayer store, `life_context/sources/*` (29 products incl. `prayer_corpus`, `scripture_reference`, `youtube`, `sermon_transcripts`, `testimony_ledger`, `petition_thread`, `supplication_readings`), project work register at `~/.local/rlm-adk/projects/project-work-registry/project/registry/project-work/records/*.json`, prayer-life-c12 pairings ledger | `ls` on desktop |
| Talk tier (new) | **Spark-A**, `~/srv/voice-talk`, Pipecat user service | this rebuild |
| Skill service (new) | **desktop** beside the data, evolving `voice_portal` | this rebuild |
| iPhone 12 | UDID `00008101-000C35463A32001E`, build 8 installed, on Spark-A USB; direct-install builds via `.github/workflows/direct-install.yml` | phone trial |

Correction to the goal text: the production intake is on the desktop, not Spark-A, since the
2026-10-13 ingestion cutover; the isolated test intake used for proofs runs on Spark-A. voicesim
runs from Spark-A and uses Spark-B's microphone and the desktop's speakers.

## Timing endpoints

All acoustic events are measured in **room-microphone time** (Spark-B recording), aligned to the
fixture timeline through the fixture's `[[marker]]` burst (voicesim `alignment.lag_s`). Pipeline
events (turn start, TTS start, interruption) are logged with monotonic timestamps and mapped into
room time with the offset estimated from the first bot-audio onset of each run; both raw clocks are
retained in the run directory.

| Quantity | Start | End |
|---|---|---|
| First-audio latency | end of the user utterance's last speech sample in the fixture (manifest segment end, mapped to room time) | first room-mic sample where bot audio exceeds the noise floor by 6 dB |
| Barge-in latency | the end of the backchannel window: onset of the interrupting user speech plus 320 ms (fixture segment start, room time, plus `backchannel_max_s`). The dossier's own rule classifies speech shorter than 320 ms as a backchannel, so no system can both tolerate "mm-hmm" and cut within 200 ms of onset; the cut is measured from the moment the speech is known to be a turn | last room-mic sample of bot audio above floor + 6 dB |
| Amen-to-end latency | end of the "amen" segment in the fixture | `prayer_ended` event (app journal time, or Pipecat mode transition) |
| Bridge-phrase latency | end-of-turn detection (`UserStoppedSpeakingFrame`: VAD stop 0.7 s plus Smart Turn, or its 1.5 s fallback), amended 2026-10-14 after the first full runs: endpointing is a fixed cost no bridge can precede | onset of the bridge phrase audio |

## Thresholds, repeats and scoring

"Near zero" WER is **at most 1 error per 32 reference words (WER ≤ 0.031)** on the intake
publication, the same bar the 2026-10-13 phone trial met at 0.0. Exact Scripture is scored as
text equality in the harness and as WER against the retained edition text in the acoustic loops;
semantic replies are scored by a rubric judge (`qwen-coding` with the rubric in the suite) on a
1 to 5 scale plus deterministic checks. Each acoustic case runs **3 repeats**; a case passes when
every repeat meets every pass condition. The text harness runs once with fixed seeds.

| # | Case | Pass conditions |
|---|---|---|
| 1 | Prayer by address | prayer mode entered before the end of the address utterance (on a partial); 0 prayer ends during the 3.0 s and 4.5 s pauses across all repeats; **no bot audio** during prayer (room-mic energy in the prayer window stays within 3 dB of the pre-roll floor); amen-to-end ≤ 1.5 s (phone layer ≤ 1.8 s: the phone's uplink adds about 0.45 s before the talk tier hears anything, measured 2026-10-14 as 1.64 to 1.73 s against 1.16 s by injection); capture published through the isolated intake with WER ≤ 0.031 |
| 2 | Scripture reading | harness: spoken text equals the retained edition verses, citations resolve; acoustic: WER ≤ 0.05 of the reply transcript against the edition text; voice is the configured Kokoro voice (not Apple, not clone) |
| 3 | Word from God | the latest prayer is the one just published or the newest corpus row; 2 to 4 passages; at least one from the pairings ledger when the ledger has rows for that prayer; each frame ≤ 1 sentence; judge relevance ≥ 3/5 per passage against the prayer's themes; every passage cited |
| 4 | Interpretations | ≥ 2 distinct teachers or traditions named; every claim attributed; Scripture, teacher's reading and personal context labelled; judge ≥ 3/5; zero uncited quotations |
| 5 | Project check-in | top-1 correct on ≥ 4 of 5 held-out descriptions; when the top-2 score margin is below the configured ratio the reply is a confirmation question naming the best candidate; a confirmed report names plan, branches, results, checkpoints and outputs from the record |
| 6 | Work request | `allocatorctl inbox post` succeeds with a message id (or the refusal is spoken verbatim); the spoken acknowledgement contains the operation id; status never claims completion |
| 7 | Floor | backchannel: bot audio continues ≥ 1.0 s after "mm-hmm" with ≥ 3 dB duck for ≥ 0.3 s (the utterance must be under 320 ms of voiced speech); barge-in: bot audio ends ≤ 200 ms after the 320 ms backchannel window closes (≤ 520 ms after sentence onset); bridge: when first reply audio would exceed 350 ms a bridge phrase starts ≤ 600 ms after end-of-turn detection, and first audio of any kind ≤ 2.0 s after the utterance end (case 07c; amended 2026-10-14, see the endpoint above) |
| 8 | Devotional interruption | reading stops ≤ 200 ms after question onset; answer mentions the passage (judge ≥ 3/5 and the passage reference string present); "continue" resumes at or within one segment of the interrupted verse |
| Q | Quiet | after "quiet please": no bot audio for the window; capture segments keep growing; `wake up` restores speech within one turn |
| L | Latency (across cases 2 to 5) | first-audio p50 ≤ 2.0 s, p95 ≤ 3.0 s over all repeats |

## Proof layers

| Layer | Path | Counts when |
|---|---|---|
| Text harness | scripted transcripts → skill service HTTP (and the Pipecat tool functions in-process) | assertions pass; no model mock for the judge |
| Room loop | voicesim fixture → desktop speakers → Spark-B mic → Cohere (per utterance) → Pipecat (WebSocket transport fed by the recorded audio, or SmallWebRTC from a headless client) → bot audio captured → Cohere → score | thresholds above |
| Phone loop | voicesim fixture → desktop speakers → iPhone app (native capture + Pipecat client) → Pipecat → app plays the reply → Spark-B mic captures it → Cohere → score; prayer capture pulled over USB and published through the isolated intake | thresholds above, plus capture hash match |

The three Spark services, the isolated intake and the phone's audio engine are part of the system
under test; a run that fails because one of them was down is recorded as an infrastructure failure
and repeated after recovery, not counted against the case. Nothing synthetic enters the production
prayer store: every acoustic prayer goes to the isolated intake on Spark-A.

## Amendments after the first full runs (2026-10-14)

* Every acoustic run goes through `rlm-voice-sim/scripts/phone-suite.sh`, which points the phone at
  an isolated intake; the phone's own prayer capture otherwise publishes fixture prayers to the
  production store (it did, four times, before the wrapper; removed with a receipt).
* Bridge latency is measured from end-of-turn detection, and case 07c's first-audio bound is 2.0 s
  (endpointing 0.7 s + Smart Turn + bridge 0.2 s + TTS onset 0.35 s).
* Phone-layer amen-to-end bound 1.8 s (uplink latency).
* Cohere transcribes once per turn (not per VAD segment) so a mid-sentence pause no longer splits a
  number or a two-clause question.
