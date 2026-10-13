# Physical UI and audio evaluation acceptance — 2026-10-13

The actual iPhone, desktop USB speakers and Spark-B Razer microphone were exercised. The main
native app stayed Build 8 with PID 1403 across UI interaction, prayer capture and runner recovery.
This is a private developer harness; no TestFlight or App Store upload is part of this update.

## Method and independent review

Claude Code `fable` (observed claude-fable-5-1) reviewed direct WDA, Appium, custom XCTest,
app-owned commands and HID alternatives, then implemented the small Python HTTP/audio harness.
Root verified upstream/installed contracts and completed signing, USB lifecycle and physical
acceptance. Fable reviewed integration and fixed cancellation, publication-wait and restoration
issues. The plan review initially lacked external read/network tool grants; root independently
verified its recommendations instead of treating that review as source verification.

Direct WDA was selected. Appium remains a compatible fallback on Linux; app-owned commands are
supplemental observations, not proof that a real UI element can be tapped. Raw HID currently
requires a media/mirroring stream in this toolchain; it was not introduced into the microphone
path. Beta status does not disqualify a package.

## Installed runner

- WDA 16.15.0, upstream e7e2665a00633dc174d7fc579f7e606bc9ba782a.
- Development-signed bundle com.rawleystanhope.wda.xctrunner; main bundle unchanged.
- [Signed build 38439694310, attempt 2](https://github.com/RawleySM/rlm-adk-ios/actions/runs/38439694310).
- IPA SHA256 c8e70299744282c158ca697a5f7d02dd621e8943a0352517fcf7846266a51d50.
- iPhone 12, iOS 26.6.2, paired USB on Spark-A; Python 3.13.15/pymobiledevice3 11.29.0.
- Separate runner installed and executed; readiness and real UI actions were verified.

## Physical UI proof

Actual WDA actions opened the software keyboard, typed a draft, read it back, dismissed the
keyboard, navigated to Diagnostics and back, and toggled/restored Quiet. Draft text was removed
without submitting it. Screenshots and accessibility XML were saved at each transition.
Before/after main-app PID was 1403; fresh native buffers continued. Ambient gap count stayed at
seven existing historical gaps. This does not establish sample-perfect continuity across every
possible interruption, installation, Bluetooth route or phone lock.

One first-use iOS Enable UI Automation passcode prompt was handled by Rawley. Subsequent
restart testing reused that authorization without another prompt. A direct launch exposed
XCTest Code 10300, "Failed to background test runner within 30.0s." Device logs identify the
failure, not a guessed credential issue. DVT launch followed two seconds later by activating the
already-running main app allowed WDA to become ready immediately. PID equality is checked before
and after activation. The fixed launcher completed physical UI smoke and verified cleanup of
its owned runner PID 2092. A second launch (runner PID 2134) repeated the physical smoke with
automatic draft cleanup and also verified runner shutdown. It never terminates the microphone app.

This is consistent with the class of bootstrap failure documented in
[Appium issue 22636](https://github.com/appium/appium/issues/22636); the workaround above was
measured on this phone. Future lock/reboot/authorization expiry remains an OS precondition;
permanent passcode autonomy is not claimed.

## Accepted speaker baseline and source

Rawley selected desktop USB analog output and confirmed "That did it. Its perfect now" after
the 45-second calibration. Keep his global/hardware volume. Use PipeWire node
alsa_output.usb-1130_USB_AUDIO-00.analog-stereo and per-stream volume 0.5.
The original USB digital path had an independently attenuated ALSA hardware PCM at -48.39 dB;
software volume alone did not reveal the cause of the quiet replay.

The source is the existing July 13 prayer from
[Google Drive](https://drive.google.com/file/d/1snQyQLpEvXIgvWq5BL1sWkNBzXCmQxBN/view).
Original SHA256 c33df73c03a9696ac874e966383e2b8d3b8e5141f6021d337a88ec00b6de81d8
was unchanged. A separate first 120 s copy uses ffmpeg loudnorm I=-18,TP=-3,LRA=11,
48kHz mono PCM16; measured output -20.79 LUFS/-3 dBTP. Normalization changes amplitude, not pauses.
Opening silence, "Jesus", another pause, and later clauses are preserved. No synthetic speech
or injected file replaces what the iPhone microphone hears.

## Physical long-pause prayer proof

The actual UI began prayer before playback and ended only after the full 120 s excerpt plus tail.
The phone retained 144.5 s in three segments, zero gap entries and zero pending uploads:

- Capture: prayer-1791915065-b5bfcd40.
- Recording: 77f995da3f8c42c0bde0d8d7d3ef4e2a.
- Published transcript: 01a53898e0ef496eacab22a3b08cdb9d.
- All three phone files match both local-manifest and intake receipt hashes.
- Installed real intake release 8e0e8df07442f80c5f51504702bc16cf6e7d4d03 processed this in a
  fresh run-owned store with the real Cohere ASR endpoint. No production prayer was replaced,
  and this replay was not added to the production canonical corpus.
- During playback, native engine_running remained true and last-buffer age was 0–0.1 s.
- Original backend http://100.90.105.30:8765 and Quiet=false were restored. PID 1403 remained.
- Both run-owned intake/backend processes and ports 18091/18765 were verified stopped afterward.

| Measurement | Observed result | Meaning |
| --- | --- | --- |
| Source/room/phone duration | 120 / 128.4053 / 144.5 s | Capture covers long pauses and tail |
| Source-in-room envelope correlation |0.6762, lag 3.26 s | Timing alignment passed 0.5 threshold |
| Source-in-phone correlation |0.5629, lag 7.32 s | Timing alignment passed |
| Room-in-phone correlation |0.8716, lag 4.08 s | Independent recordings align |
| Phone clipped samples |0 of 6,936,000 | No full-scale phone samples in this run |
| Room clipped samples |2 of 6,163,456 | Isolated full-scale samples; below configured threshold |
| Confirmed phrases |"Jesus, I feel your presence"; "thank you for the gift" | Present in source/room ASR and pipeline transcript |

The earlier 45 s calibration had phone correlation 0.353 and is retained as a failed timing result;
it is not silently relabeled. The longer 120 s run passed. Correlation is a timing aid, not a
perceptual-quality score. ASR agreement is not human-verified WER, and no WER claim is made.

## Issues and remaining evaluation

**AUDIO-EVAL-001 — extra ASR phrases around sparse speech (open).** The source 120 s and room ASR
both start with "Jesus. Jesus, I feel your presence...". The phone pipeline transcript instead
includes extra "Thank you" utterances and "Um" before/between the expected phrases. Real capture,
hashes and publication passed, but exact transcription did not. Preserve all three waveforms,
source/room ASR and pipeline transcript for VAD/chunking/ASR comparison. Do not delete the
intentional pauses or call this proof of verbatim quality. Whether these additions come from
phone processing, segmentation or the ASR invocation is not yet established.

**AUDIO-EVAL-002 — XCTest restart Code 10300 (resolved for measured device).** DVT startup plus
activation of the existing main app restores the missed background transition, with PID equality
and real post-restart UI smoke. Preserve future failure logs; do not assume every iOS build is fixed.

**AUDIO-EVAL-003 — conversational output not yet accepted (open).** This increment proves UI,
input acoustic capture and prayer intake. It does not validate voice naturalness, full-duplex
echo, barge-in, or response latency. The live reasoning owner was inference_inhibited and was
not overridden. Reverse-direction phone-speaker trials and voice-provider work remain separate.

## Evidence locations and version limits

On Spark-A the evidence root is
/home/rawleysm/.local/state/rlm-adk-ios-prototype-supervision/.
The reproducible harness lives in this repository; audio/screenshots stay in the run directory.

- device-eval-live/ui-first/: preflight, attach, smoke, typing/readback/clear screenshots/receipts.
- device-eval-live/replay-usb-normalized/: 45 s calibration, independent ASRs and rejected phone timing.
- device-eval-live/prayer-e2e-01/: prayer-trial-receipt.json, acoustic/room.wav, phone/,
  phone-verify/, three-way-score/, intake-status.json, source-asr.json, room-asr.json,
  phone-restored.json and service-quiescence.json.
- The saved pipeline transcript is intake/processing/9670cef02283459b997e82fa895fdf74/transcript.md
  under prayer-e2e-01; the original retained phone audio remains on the phone as well.
- device-eval-live/runner-restart/: real UI tests after the runner fix and restored.json.
- device-eval-live/final-ui/: final automatic draft cleanup, keyboard dismissal and Quiet restore;
  passing smoke receipt and restored.json show empty draft and fresh native audio.
- wda-session-lifecycle-fixed.log and wda-session-final.log: two ready/restart/shutdown cycles.
- device-eval-live/final-owned-audio-quiescence.json: no owned audio streams on either remote host.
- device-eval-all-final-tests.log: all 157 local backend/harness tests passed in 63.750 seconds.
- device-eval-fixtures/drive-provenance.json and normalization-first 120 s.json: original/derived identity.
- fable-device-ui-audio-plan-review.md, fable-device-eval-final-review.txt and
  fable-device-eval-final-fix-result.txt: independent review and fixes.

Receipts include source-file hashes because the first live run used uncommitted implementation
on HEAD 022f25e. Final exception-path/cleanup corrections have separate deterministic regression
proof; the successful physical prayer run should not be described as having exercised those
later exceptional paths. Tests use fake transports where labeled; they are not physical proof.
