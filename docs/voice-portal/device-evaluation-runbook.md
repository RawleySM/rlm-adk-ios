# Physical iPhone evaluation runbook: `scripts/device_eval.py`

Status 2026-10-13: signed WebDriverAgent installed; physical UI navigation, typing/dismissal,
Quiet restore, long-pause prayer capture, real isolated intake/STT, and runner restart exercised.
See [acceptance and issues](device-evaluation-acceptance.md) for exact proof and limits, and
[the reviewed plan](device-evaluation-plan.md) for alternatives and decisions.

The harness is a set of separate commands that root composes. Each writes
`<out>/<command>-receipt.json` (schema `rlm-adk-ios.device-eval.receipt/1`: UTC and monotonic
timestamps, git commit, hashes of every harness and root script, arguments, every action, every
named check with its failure reason, evidence files with sha256) and exits 0 pass, 1 fail,
2 blocked, 3 crash, 4 pending, 130 cancelled (the receipt is written in every case).
Blocked means a precondition outside the run's control: locked phone, open prayer, alert, missing
PipeWire node. The general smoke scenario never submits text or touches prayer/backend settings. The separate
physical-prayer scenario intentionally changes the backend and taps Begin/End, then restores it.
All process cleanup targets only run-owned PIDs; the main app is never killed or reinstalled.

## Prerequisites root provides

- WDA runner installed and running on the phone; its port reachable locally. The session
  script forwards it to `http://127.0.0.1:18100`; always pass `--wda` as in the examples. Root's `scripts/wda_session.py` owns that lifecycle (userspace
  RSD tunnel, DVT launch of the runner, activation of the existing main app with PID verification,
  port forward and bounded lifetime); `pymobiledevice3 usbmux forward 8100 8100` is the plain alternative. This harness uses
  plain HTTP so it runs under the system Python without the toolchain venv. Apple's "Enable UI
  Automation" passcode prompt on first use is a physical blocker root handles, not this harness.
- Phone capture files pulled by root's `scripts/pull_device_capture.py` (house arrest over USB,
  closed segments plus `captures.json`); the `phone` and `audio score` commands read that copy.
- The app (build 8, `com.rawleystanhope.rlmadk`) already running on the phone. The harness requires that existing process and never requests a new one. Runner bootstrap
  activates it with `kill_existing=False` and verifies the same PID before and after.
- Phone unlocked and awake for UI commands (auto-lock off during the window, or run while awake).
- `ssh desktop` and `ssh -l rawleysm spark-media` working non-interactively from Spark-A, with
  `pw-dump`, `pw-play` (desktop) and `pw-record` (Spark-B) in the remote user's PATH.
- Local: Python 3.12+, `ffmpeg`/`ffprobe` for `fixture excerpt`. No numpy needed.

## Commands

```bash
H="python3 scripts/device_eval.py"
WDA=http://127.0.0.1:18100
OUT=/home/rawleysm/.local/state/rlm-adk-ios-prototype-supervision/device-eval-runs/$(date -u +%Y%m%dT%H%M%SZ)

# 1. Phone state without a session (optional, from a pulled Documents/mode.json)
$H phone mode --mode-json pulled/mode.json --out $OUT/mode        # exit 2 if a prayer is open

# 2. WDA preflight: /status, lock state, app process state, prayer/quiet labels, screenshot
$H preflight --wda "$WDA" --out $OUT/preflight

# 3. Attach session (no bundleId, shouldTerminateApp=false, forceAppLaunch=false), activate if backgrounded
$H session attach --wda "$WDA" --out $OUT/attach            # prints the session id; receipt proves state before/after

# 4. UI smoke: Portal -> typedInput (keyboard appears) -> keyboardDone (keyboard gone) ->
#    Diagnostics tab (navigation bar + backendURLField present, untouched) -> Portal -> quiet
#    toggled and restored. Types nothing unless --type-text is given; never submits.
$H ui smoke --wda "$WDA" --session-id <SID> --out $OUT/smoke [--type-text "hello"] [--no-quiet-toggle]

# 5. Release the session
$H session release --wda "$WDA" --session-id <SID> --out $OUT/release

# 6. Reuse the accepted 120-second normalized fixture; preserve opening silence and all pauses.
#    Its original/derived hashes and normalization are in normalization-first120s.json.
F=/home/rawleysm/.local/state/rlm-adk-ios-prototype-supervision/device-eval-fixtures
FIXTURE=$F/prayer-july13-first120s-normalized.wav

# 7. Desktop plays it once while Spark-B records the room (finite, run-owned processes)
$H audio play-record --fixture $FIXTURE --out $OUT/room \
   [--stream-volume 0.5] [--pre-roll 2] [--tail 3] [--readiness-timeout 15]

# 8. Pull the closed segments of the window (root: scripts/pull_device_capture.py --since/--until
#    writes pulled/captures.json, pulled/<id>/NNNNNNNN.wav and pulled/pull-receipt.json), then score.
#    --pull-receipt supplies manifest, directory, capture id and the index window (e.g. 185:186)
#    and cross-checks the manifest hash; --window FIRST:LAST sets the window explicitly.
$H phone verify-segments --pull-receipt pulled/pull-receipt.json --out $OUT/segments
$H audio score --source $FIXTURE --room $OUT/room/room.wav \
   --pull-receipt pulled/pull-receipt.json --out $OUT/score \
   [--hypothesis phone_journal=journal.txt] [--hypothesis pipeline=stt.txt] \
   [--phrase "feel your presence"] [--reference-text ref.txt [--reference-verified]]

# ASR on a pulled or room WAV: the deployed local endpoint accepted 16 kHz mono PCM and rejected
# 48 kHz. Resample a copy for it (provider unchanged); keep the original for scoring.
ffmpeg -nostdin -i room.wav -ac 1 -ar 16000 -c:a pcm_s16le room-16k.wav
```

Order for the first increment (plan, "Root adjudication"): 6 then 7 then 8 with the phone's
**ambient** capture proves the acoustic path with no UI automation at all; 2 to 5 prove attach,
navigation, keyboard and quiet with evidence; the prayer scenario is a separate command below against an isolated real intake.

## What each command actually proves, and what it cannot

- **preflight / attach**: the receipt records the `/session` payload (no `bundleId`), the app
  state before activate (must already be 2 to 4) and after (must be 4), the lock state, any alert
  text, source XML and a PNG. If the app is not running the command fails and does nothing else.
  Attach does not prove the audio owner kept running; that is read from the phone's own
  `recent-log.txt` / snapshot by root (a "launched;" line after the attach time would mean a relaunch).
- **ui smoke**: every claim is backed by WDA element attributes and source XML saved per step
  (`01-portal` to `07-quiet-restored`). The keyboard check looks for `XCUIElementTypeKeyboard` in
  the source, the same evidence `RLMADKUITests` uses on the simulator. The prayer button is only
  read; "End prayer" blocks the scenario. The quiet button is tapped twice and must end on its
  original label. Draft cleanup, keyboard dismissal and Quiet restoration run independently in
  `finally`, including after a failed screenshot or navigation action. Existing drafts block typing
  and are preserved when not typing. A failed restore is recorded for explicit recovery.
- **fixture excerpt**: `[offset, offset+duration)` decoded by ffmpeg to 16-bit mono at 48 kHz with
  `volume=-6dB` (boost refused without `--allow-boost`, duration capped at 300 s), wrapped in 1 s
  silence and, with `--markers`, three 100 ms 1 kHz bursts at -20 dBFS before and after. The
  provenance JSON next to the WAV carries the Drive id/URL/hash (checked against
  `drive-provenance.json`), ffprobe output, exact offsets, layout and output hash. Transcripts are
  recorded as `candidate_unverified`; neither an old corrected transcript nor new ASR is automatically a verbatim reference for
  the selected excerpt. Verify the words before enabling WER.
- **audio play-record**: resolves the sink and source by `node.name` in `pw-dump` (never by id),
  copies the fixture to a run-owned `/tmp/device-eval-<run>` directory on the desktop and checks
  its hash, starts `pw-record --target <source> --rate 48000 --channels 1 --format s16` on Spark-B
  as `rawleysm` inside `XDG_RUNTIME_DIR=/run/user/1000` with a per-run unique
  `application.name` (`rlm-adk-device-eval-record-<run_id>`), learns its PID through a reader
  thread (a stalled SSH cannot block the harness; no PID within 10 s kills the local ssh child and
  fails), and polls `pw-dump` until a running `Stream/Input/Audio` node with exactly that name
  exists (bounded; no readiness means no playback and the PID is stopped). Both remote children
  run under `setsid timeout --signal=INT`: the recorder's lifetime is capped at pre-roll + fixture
  + tail + 30 s and playback at fixture + 15 s, so a dead SSH session or a dead supervisor cannot
  leave either running. Then pre-roll, `pw-play --target <sink> --volume 0.5` (global volume
  untouched), tail, `kill -INT <pid>` so the WAV header is finalized (TERM then local
  kill only on timeout), copy back with hash check, duration plausibility check, and removal of
  exactly this run's remote files. **A passing receipt here is transport-only**: files moved,
  hashes matched, processes exited 0. It says nothing about whether sound left the speakers or
  reached either microphone; that is `audio score`'s job, and the room recording is independent
  evidence only of what the Spark-B microphone heard, not the sound at the iPhone and not a
  calibrated level.
- **audio score**: per recording: format, duration, peak, clipping ratio, RMS, and percentiles of
  the 100 ms frame-RMS distribution (`frame_p10_dbfs`, `frame_p90_dbfs`, their range, the fraction
  of frames at least 10 dB above p10). These are statistics of each recording, not a measured SNR
  and not speech/noise labels. A silent or flat room (p90-p10 range under 6 dB) fails
  `room_has_signal` and no alignment is attempted. Alignment is envelope cross-correlation
  (100 ms coarse, 20 ms fine) searched over non-negative lags only; `lag_s` is set only when the
  peak reaches 0.5, otherwise it is null and the rejected candidate is reported separately, so an
  invalid correlation is never read as latency. Phone segments are verified strictly before
  concatenation, either the whole capture from index 0 or a `--window FIRST:LAST` of closed,
  contiguous, hash-verified segments at their original indexes (the manifest is never reindexed;
  pulled files outside the window are listed as `outside_window_files`). Apple voice processing
  changes the phone envelope, so `phone_alignment_credible` can fail with intact capture; the
  receipt says so. Text: `wer` only with `--reference-verified`; otherwise the same edit distance
  is reported as `candidate_error_ratio` against the unverified candidate transcript (not a WER,
  not bounded by 1, no approval gate, just labelled); `--phrase` gives exact normalized hits.
- **Receipts** carry `git_commit` plus `code` (sha256 of every harness file, a combined digest,
  and git's modified/untracked view of them), because HEAD alone hid uncommitted code in the first
  live proof.

## First live observation (root, 2026-10-13, 70 s prayer replay)

Transport passed: fixture copied, recorder ready, playback exited 0, room WAV copied with matching
hash. Acoustics failed: source-in-room peak correlation 0.17 with a nonsensical negative lag, room
p90-p10 range 4.34 dB. Root found the desktop's ALSA hardware PCM attenuated to -48.39 dB while
PipeWire reported software volume above 100%. Lessons encoded above: transport success is reported
separately from acoustic evidence, negative lags are not searched and incredible correlations
carry no lag, and the hardware mixer path is outside this harness (it changes nothing on the
desktop). The fixture may now take an explicit `--gain-db` boost with `--allow-boost` up to
+24 dB for calibration; the output is refused if any sample clips, and the default stays -6 dB.
Whether the room recording contains the played phrases is a separate check (ASR on the 16 kHz
copy, `--phrase`), not implied by any correlation.

## Physical prayer trial: `scripts/device_prayer_eval.py` (verified 2026-10-13)

Root ran the two-minute trial against a separately configured real backend and intake:
capture `prayer-1791915065-b5bfcd40`, 144.5 s, three hash-matched segments, transcript
`01a53898e0ef496eacab22a3b08cdb9d` published by the isolated intake; original backend and Quiet
restored, app PID 1403 unchanged throughout. Baseline used: WDA 16.15.0 forwarded on port
**18100** (`scripts/wda_session.py`), desktop sink `alsa_output.usb-1130_USB_AUDIO-00.analog-stereo`
(USB analog profile), first **120 seconds** of the older Drive prayer separately processed with
ffmpeg `loudnorm=I=-18:TP=-3:LRA=11` (measured -20.79 LUFS, -3 dBTP), stream volume 0.5.
The preceding 45-second calibration was user-confirmed at these settings. All natural pauses
remain; only the explicit End tap ends the prayer. Source-in-room envelope correlation was
0.6762, source-in-phone 0.5629, room-in-phone 0.8716. All met the timing threshold; none proves
perceptual quality or exact transcription. The pipeline introduced additional short phrases;
see the acceptance issue log. The successful physical trial preceded the final cancellation and
restoration corrections; those failure paths are verified by focused regression tests.

```bash
cd scripts && python3 device_prayer_eval.py --session-id <SID> --install-id <install> \
   --test-backend http://<isolated-backend> --fixture <normalized.wav> --out $OUT/prayer \
   [--wda http://127.0.0.1:18100] [--publish-timeout 120] [--volume 0.5]
```

Behaviour: `restore-state.json` in `--out` records the original backend URL and Quiet flag
before any mutation and marks prayer ownership the moment Begin is tapped (ownership means no
prayer was open before the tap, so a failed snapshot cannot lose it). Restoration of the owned
prayer, the backend field and Quiet are independent steps; a failure in one is a failed check and
never skips the others. SIGTERM and Ctrl-C run the same restoration and still write the receipt
(exit 130, `cancelled: true`). Publication is awaited for `--publish-timeout`; `real_intake_finalized`
and `real_intake_published` are separate checks and an unsettled wait gives outcome **pending**
(exit 4), which is not acceptance; query the intake later. A pre-existing open prayer stops the
trial before any mutation.

## Recovery

- A crash still writes the receipt; the `reasons` list has the exception.
- `audio play-record` cleanup runs in `finally`: INT to the recorded PID, copy, remove. If ssh
  itself hung, the remote `pw-record` PID is in the receipt (`pw_record_started.pid`) and the remote
  directory is `/tmp/device-eval-<run_id>.*` on both hosts; remove only those.
- A stale WDA session: `session release --session-id <SID>`; WDA also replaces it on the next attach.
- Quiet left toggled: tap Quiet/Wake once on the phone; the receipt's `mode_labels_final` shows
  which label was left.

## Deterministic regression coverage

Final local run: **157 tests passed** (`python3 -m unittest discover -s tests -t .` from
`backend/`). The final real-phone smoke separately verified placeholder handling and draft cleanup.

- `backend/tests/test_device_eval.py` exercises the orchestration against fake transports;
  `backend/tests/test_wda_lifecycle.py` protects existing-process activation. Attach sends no `bundleId` and both launch flags
  false; refuses an app that is not running; preflight blocks on lock and on an open prayer; smoke
  passes with evidence and restores quiet, types nothing by default, never taps send/prayer/backend,
  blocks on an alert without accepting it, fails when the keyboard does not appear; alignment finds
  a 1.5 s offset across 48 kHz/44.1 kHz; a flat room is rejected; clipping is counted; segment
  accounting passes exact files and fails hash, order, format and open-segment cases; play/record
  orders record, readiness, play, INT, copy, cleanup, aborts without playback when readiness never
  comes or the recorder dies early, blocks on a missing sink, never uses pkill; real ffmpeg excerpt
  layout and guards; CLI exit codes and receipts.
- A 45 s excerpt from the real Drive recording (offset 60 s, -6 dB, markers) derived to /tmp with
  provenance; the original's sha256 unchanged. Scored against a simulated delayed copy to exercise
  the alignment path on natural speech (see the summary in the session report).

## Runner bootstrap and restart

Use the existing `device-tools-py313` environment on Spark-A (Python 3.13.15,
pymobiledevice3 11.29.0). Build/sign the separate runner with `wda-runner.yml`; pin WDA16.15.0
and commit e7e2665a00633dc174d7fc579f7e606bc9ba782a. Install only the signed runner IPA using
InstallationProxy. Do not uninstall or replace RLMADK for harness updates.

```bash
TOOLS=/home/rawleysm/.local/state/rlm-adk-ios-prototype-supervision/device-tools-py313/bin
PHONE=00008101-000C35463A32001E
$TOOLS/pymobiledevice3 mounter auto-mount --udid "$PHONE"
$TOOLS/python scripts/wda_session.py --udid "$PHONE" --seconds 900 --startup-seconds 300
```

Wait for the JSON `ready:true` result before making UI requests. The main app must already be
running. On this iOS26.6.2 phone, direct runner startup can exit with XCTest Code10300:
`Failed to background test runner within 30.0s.` The launcher activates the already-running
main app two seconds after DVT runner launch, verifies the original PID, and then waits for
WDA readiness. This worked without a second passcode prompt. It never requests termination of
the main app. Existing runners are not owned and are not stopped by an attaching invocation.
An owned runner and forward are stopped on Ctrl-C/SIGTERM/deadline, with PID absence checked.

Apple can still require device-side authorization after restart or expiry; this run does not
prove permanent passcode autonomy. A locked phone or new authorization prompt is a precise
precondition failure, not a reason to reset the phone or disable its passcode.

## Isolated real intake setup

For the prayer test, configure a fresh `VOICE_PORTAL_STATE` root and a fresh
`PRAYER_INGEST_STORE_ROOT`. Run the installed prayer-ingest release's Python with `-I -m
prayer_ingest serve`, `PRAYER_INGEST_HOST=127.0.0.1`, `PRAYER_INGEST_PORT=18091`, and the new
store root. Use its real ASR service; do not start Drive imports or historical workers.
The backend `config.json` sets `listen_host=0.0.0.0`, `listen_port=18765`,
`prayer_intake_url=http://127.0.0.1:18091`, and `prayer_store` to the new intake database.
Run `python3 -m voice_portal serve` with this worktree's backend on PYTHONPATH. The fixture
backend and the production fixed-byte test-run API are unsuitable for acoustic capture.

Wrap each owned process in `timeout --signal=INT --kill-after=15 900`, record both PIDs,
check intake `/healthz` and backend `/status`, and actively supervise. The phone uses the
Spark-A address on port18765 for this bounded trial. After publication, preserve artifacts,
verify restoration to the original backend, stop only the two owned processes, and check both
ports are closed. This is actual isolated pipeline publication; it does not add the replay to
the user's production canonical prayer corpus.
