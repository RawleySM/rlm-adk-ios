# Physical iPhone UI and acoustic evaluation plan

Date: 2026-10-13. Owner: Codex; requested independent reviewer: Claude Code `fable`.
Status: Fable reviewed; root adjudicated 2026-10-13. Implement the revised design below.

## Outcome and current evidence

Rawley wants autonomous physical-device debugging and end-to-end evaluation: actual taps,
typing, navigation, recording, publication, and evidence without asking him to speak, tap,
read logs, or send screenshots. UI commands inside the app supplement but do not prove UI
reachability. Keep the native AVAudioEngine app and direct private signed delivery.

Build 8 is installed on iPhone 12, iOS 26.6.2, UDID `00008101-000C35463A32001E`, bundle
`com.rawleystanhope.rlmadk`, team `4PJBKY2WQR`. Spark-A USB pairing, Developer Mode, DVT
screenshots, launch/stop, InstallationProxy and container reads work. No WDA runner is installed.
The app has stable accessibility identifiers for prayerButton, quietButton, typedInput,
sendButton, keyboardDone, backendURLField and keyboardDoneDiagnostics. Do not modify the app
unless actual evaluation needs a missing identifier or reveals a bug.

All three physical endpoints are within three feet:
- `ssh desktop`, user rawley-stanhope: PipeWire speaker node
  `alsa_output.usb-1130_USB_AUDIO-00.iec958-stereo`, current volume 0.44. SSH commands need
  `XDG_RUNTIME_DIR=/run/user/1000` to address the existing audio session.
- Spark-B `ssh -l rawleysm spark-media`: Razer Seiren Mini microphone, active in that user's
  PipeWire session. The default mediaops SSH user has a separate dummy audio graph; do not
  change permissions or restart another user's graph. Resolve source by node name, not unstable ID.
- Spark-A owns the USB iPhone and coordinates the test.

## Minimal proposed architecture

1. Separate, signed WebDriverAgent runner for the registered device. Pin an upstream release
   and commit, build on the existing GitHub macOS workflow infrastructure, retain source,
   signing and artifact receipts. Register only the runner's additional bundle/profile as needed;
   reuse signing identity where supported. Do not assume an Ad Hoc app profile fits an XCTest
   runner: prove entitlements and launch. Never uninstall the main app. No TestFlight/public upload.
2. Start WDA from Spark-A through existing pymobiledevice3 developer/testmanager services and
   connect over USB. Prefer direct WDA HTTP plus a small Python client to a full Appium server
   unless Appium demonstrably removes fragile lifecycle code. Read UI hierarchy; prefer stable
   identifiers and labels over coordinates. A command returning success is not UI acceptance.
3. Repository-owned Python CLI for preflight, runner lifecycle, UI scenarios, acoustic probe,
   artifact collection and report. Reuse existing backend device snapshots/manifests and real
   intake contracts to verify effects; do not emulate an app action through diagnostics and call
   that a UI test. Document reproducible setup and recovery.
4. SSH-controlled desktop playback and Spark-B recording using the existing PipeWire nodes.
   No new always-running service. Use only child processes owned by this run, finite durations,
   cleanup on error, monotonic local time and signal-based synchronization. No reboot/uninstall
   to recover. Do not recreate the deleted heartbeat automation.

## Acoustic method: three recordings, then reverse direction

Keep the source waveform and exact words as ground truth. Capture a second waveform with
Spark-B's microphone as the independent room reference, and retrieve the third waveform
from the phone's retained capture. This disambiguates source/playback silence, room/noise,
phone capture loss, upload corruption, and ASR errors. The external mic does not prove exactly
what reached the phone's microphone and is not a calibrated SPL meter.

First run a short calibration: background noise, a low-volume time-coded chirp sequence,
and a short intelligible speech fixture. Start at the existing speaker level with conservative
per-stream gain; do not set device volume to maximum. Save source/sink metadata and levels.
Gate on audible-in-reference energy, clipping, marker detectability and channel/sample format.
No acoustic proof if the independent mic did not hear the fixture. Use distinctive start/end
markers, detected from audio correlation, so cross-host wall-clock skew is not latency evidence.

Then use a fixture containing spoken run identity, several known clauses, a long intentional
pause and a final clause. Operate Begin/End Prayer using WDA. Verify all clauses and the final
word were captured, the pause did not auto-end prayer, and the app stayed silent. Retrieve
manifest, original phone WAV, independent room WAV, pipeline receipt and transcript. Preserve
real prayer/ambient files. Clearly identify test captures and prefer a separate real-service test
store when supported; do not silently mix synthetic prayer into the user's canonical corpus.
Do not substitute host WAV upload for the iPhone acoustic capture route.

Measurements: waveform duration and non-silent coverage; clipping ratio; detected marker/phrase
times; missing known active-speech spans (intentional silence excluded); level relative to the
room reference; normalized word error rate and exact sentinel-clause coverage. Use meaningful
fixture thresholds after calibration; report measured quantities and failures individually,
not a made-up aggregate quality score. Signal correlation is not a perceptual quality metric.
User steering: use older prayer recordings stored on Google Drive as the speech source,
not generated speech. Discover real Drive metadata and retain file ID, name, hash and any
existing transcript. Download a local original without modifying Drive. Create separately
identified playback excerpts preserving natural pauses; document exact source offsets. Existing
transcripts are reference candidates, not automatically verbatim ground truth: verify the scored
excerpt before using word-error thresholds. Add calibration markers only to the derived copy.

Reverse test: while desktop is silent, trigger a known app playback action and record iPhone
speaker output on Spark-B. Correlate to an expected waveform where available; otherwise use
independent transcript/content and observed onset/offset. This can measure audible output,
end-of-input to first audio, stop/quiet behavior and unintended repeated output. Later run
scripted interruptions from the desktop speaker and measure whether the phone stops talking
and captures the interrupt. Endpointing, echo, barge-in and voice naturalness are separate
dimensions; infrastructure passing does not mean conversational quality is solved. Current live
conversation provider returns `503 inference_inhibited`; do not override that owner's state.

## Initial physical acceptance

- Actual WDA menu navigation, typing/dismissal/Diagnostics reachability; screen and UI evidence.
- Bounded acoustic calibration with source/reference/phone provenance where possible.
- Begin prayer -> controlled room speech with long pause -> End prayer -> retained original
  audio -> actual intake/transcript, or precise stage failure and preserved artifacts.
- Quiet toggled through UI; microphone buffers and saved native WAV continue during inspection.
- Runner reconnect/relaunch exercised without deleting app data.
- Finite run receipt including app/runner version, source commit, device identity, fixture hash,
  timestamps, actual action sequence, screenshots, files/hashes, metrics, pass/fail/blocked.
- Preserve active user prayer; prerequisites must detect it and refuse that scenario, not end it.
- On completion stop this run's playback/recording and WDA test session as appropriate; leave
  the app usable and restore prior quiet/tab/backend state when changed.

## Alternatives Fable must compare

Direct WDA with pymobiledevice3 versus Appium with preinstalled WDA; custom signed XCTest
suite; app-owned remote commands only; raw HID. For audio compare physical acoustic loop,
direct file injection, and human voice fixtures. Give smallest credible choice and fallback,
concrete compatibility/signing risks, synchronization validity, data retention and runner audio
interference. Beta is eligible. No security/privacy hardening, unrelated architecture or model
replacement in this update.

## Work order

Fable independent plan review -> root adjudication and revised plan -> implement smallest
approved design -> focused local tests and CI -> signed runner -> physical UI and acoustic
acceptance -> repair actual failures -> PR/merge and durable runbook/evidence. Root supervises
foreground work and records phase transitions in the existing supervision state.json.

## Root adjudication after Fable review

Reviewed with Claude Code fable, observed claude-fable-5-1. The raw review is retained in the
supervision record. Its launch lacked read/network grants outside the worktree; root verified
the installed pymobiledevice3 11.29.0 source and current upstream Appium documentation directly.
Adopt direct WDA; explicitly disable forceAppLaunch and shouldTerminateApp; check lock state;
use separate development runner signing; report speech/noise levels per recording and use
envelope/spectral alignment only for timing. Verify ordered segment hashes, formats and frames;
report phone-journal and pipeline ASR separately. All audio child processes are finite and owned.
Build pinned WDA directly; a separate custom XCTest signing probe adds another build. Reuse the
existing development certificate if its key is available, and register a runner profile.
Current Appium supports Linux real devices via RemoteXPC, so remains a valid fallback. Installed
pymobiledevice3 includes HID services, but no verified touch path exists here. Use its actual
APIs, not the review's unverified CLI names. Reuse supported userspace device transport.

The user's Google Drive fixture instruction supersedes the review's synthetic baseline. Use
older natural prayer audio and preserve source provenance and pauses. Do not synthesize run-ID
speech. WER requires a verified reference; otherwise report ASR agreement and confirmed phrases.
Apple voice processing can suppress chirps; known speech envelopes and optional markers in the
derived copy locate onset/end. Spark-B room recording is independent, not a calibrated proxy
for the sound at the iPhone microphone.

The production pipeline test-run API requires byte identity with its fixed fixture WAV; it
cannot certify an acoustic rerecording. Do not misuse its identity or discard personal prayer.
First prove ambient acoustic capture. For Begin/End prayer and real intake/STT use a separate
instance of the existing intake/backend with run-owned data roots if necessary; no pipeline
code changes or fake intake. Restore the phone's original backend and verify reconnection.

First increment: actual UI attach/navigation/keyboard/quiet, preserved native capture, and
older-prayer acoustic source/reference/phone evidence. Prayer UI plus isolated real intake/STT
follows if existing deployment configuration permits it. Report incomplete stages separately.
Reverse playback can use devotional Play; conversation currently has inference_inhibited and
must not override that owner's state. Lock/passcode prompts are precise physical blockers.
