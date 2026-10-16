# Voice portal prototype: runbook

How to run the consumer backend on the desktop and the iPhone client, what each part depends on,
and how to stop it. Companion documents: the [plan](prototype-plan.md), the preserved
[Fable review](prototype-fable-review.md) and the [acceptance report](prototype-acceptance.md).

## Components

| Part | Where | What it owns |
| --- | --- | --- |
| `backend/voice_portal` (Python 3.12, stdlib only) | desktop `rawley-stanhope`, deployed copy at `~/srv/voice-portal/backend`, state root `~/.local/rlm-adk/voice_portal` | SQLite FTS5 projection of the retained sources, streaming turns against the local inference owner, prayer-intake relay, durable work outbox, HTTP + WebSocket on port 8765 |
| `scripture_reference_readers` | desktop `~/srv/voice-portal/vendor` (pinned copy of `RawleySM/data-pipelines` commit `4e8a5d93aa73774599045e13f2906ed7a34422e3`; `vendor/PIN.txt`) | Reads the retained Scripture store, verifies body hashes, yields WEB verse atoms |
| `RLMADK` (SwiftUI, iOS 17) | this repository, built unsigned for the simulator on GitHub macOS runners | One `AVAudioEngine` capture owner with local WAV segments, on-device `SFSpeechRecognizer`, `AVSpeechSynthesizer` behind the quiet/prayer/barge-in gate, persisted mode, prayer uploads, work list |

Nothing in this prototype starts, resumes or updates the RLM-ADK allocator. The backend only runs
`allocatorctl status --json`, `allocatorctl inbox post --file PATH` and `allocatorctl inbox list --all`.

## Backend on the desktop

Configuration lives in `~/.local/rlm-adk/voice_portal/config.json` and is shown verbatim by
`GET /config` and summarised by `GET /status`. The deployed values on 2026-10-13:

```json
{
  "listen_host": "0.0.0.0", "listen_port": 8765,
  "prayer_store": "/home/rawley-stanhope/.local/rlm-adk/mirrors/prayer/prayer_ingest.sqlite3",
  "youtube_store": "/home/rawley-stanhope/.local/rlm-adk/stores/life_context/sources/youtube/catalog.sqlite3",
  "scripture_store": "/home/rawley-stanhope/.local/rlm-adk/stores/life_context/sources/scripture_reference",
  "reader_path": "/home/rawley-stanhope/srv/voice-portal/vendor",
  "inference_base_url": "http://127.0.0.1:28111/v1", "inference_status_url": "http://127.0.0.1:28111/status",
  "inference_model": "qwen-coding-b",
  "prayer_intake_url": "http://127.0.0.1:8091",
  "allocatorctl": "/home/rawley-stanhope/srv/rlm-adk/bin/allocatorctl"
}
```

`inference_model` is `qwen-coding-b` rather than the plan's preferred `spark-small` because Spark-B
cannot start the `spark-small` engine: its owner logs `Docker GET failed: HTTP 404` from
`GET /images/<profile.image>/json` (the image is not present on `spark-12b7`), and Spark-A reports
`insufficient_memory_for_envelope`. See the acceptance report. Thinking is disabled through
`inference_extra_body` (`chat_template_kwargs.enable_thinking=false`); `reasoning_content` deltas,
if any, are counted but never spoken or shown.

Deploy and run (from this checkout, on Spark-A or any host with SSH to `desktop`):

```bash
# code + pinned reader
rsync -a --delete --exclude __pycache__ --exclude SOURCE_COMMIT backend/ desktop:~/srv/voice-portal/backend/
rsync -a --delete --exclude __pycache__ --exclude .venv \
  /home/rawleysm/data-pipelines/packages/scripture_reference_readers/scripture_reference_readers desktop:~/srv/voice-portal/vendor/
ssh desktop "echo $(git rev-parse HEAD) > ~/srv/voice-portal/backend/SOURCE_COMMIT"   # fingerprint shown by /healthz

# on the desktop
cd ~/srv/voice-portal/backend
./run-backend import prayer teaching reference_teaching scripture theographic   # idempotent; ~25 s for everything (reference_teaching: Edwards ThML + BibleProject transcripts, ~13 s)
./run-backend start        # nohup, pidfile ~/.local/rlm-adk/voice_portal/run/backend.pid
./run-backend status       # pid + /healthz (shows source_commit from SOURCE_COMMIT)
./run-backend logs 50
./run-backend stop
```

Source stores are opened `mode=ro` with `PRAGMA query_only=ON`. The consumer database
`voice_portal.sqlite3`, captures, artifacts, outbox directive files and evidence receipts live under
the state root, never in Git.

### HTTP and WebSocket surface

| Route | Purpose |
| --- | --- |
| `GET /healthz`, `/status`, `/config`, `/sources` | liveness, owner readiness, intake health, allocator state (read-only), counts, latest import receipts |
| `GET /search?q=&family=&limit=` | focus-first FTS5 search; reports `focus_terms`, `plan`, per-hit `strategy` |
| `GET /citations/{cite_id}` | version-qualified citation: `doc_id:revision12:chunk`; `verified` recomputes the body hash |
| `GET /documents/{family}/{source_doc_id}/revisions` | revision history of one source record |
| `GET /devotional?passage=PSA.23&date=` | verse segments with cite ids, one teaching excerpt, Theographic people/places |
| `GET /theographic/passage?osis=Gen.12.1,…` | bounded Theographic join with unresolved keys listed |
| `POST /turns` | one grounded turn without a socket (smoke/evidence) |
| `GET /ws` | voice session: `hello`, `partial_transcript` (acknowledged only), `final_transcript` (one model call per turn id), `cancel`, `mode_changed`, `ping`; server sends `session`, `turn_accepted`, `assistant_text_delta`, `assistant_done` (citations, timing, usage, scope), `assistant_cancelled`, `duplicate_turn`, `error`. Binary frames are refused: no PCM uplink. |
| `POST /work`, `GET /work/{id}`, `POST /work/{id}/dispatch|reconcile|source-packet`, `GET /work/{id}/artifacts/{aid}` | durable outbox over `allocatorctl inbox` |
| `POST /prayer/captures/{cid}/segments/{n}` (raw audio, `X-Audio-SHA256`), `POST …/finalize`, `POST …/discard`, `GET /prayer/captures/{cid}` | relay to the existing prayer intake with verified digests/lengths |
| `POST /prayer/test-runs`, `GET /prayer/test-runs/{id}` | the intake's own sanctioned test-run mechanism (never published to the corpus) |

### Retrieval behaviour

Natural requests mix request-frame words ("which prayer of mine speaks about…", "what Scripture
fits…") with the subject. `retrieval.focus_terms` drops frame vocabulary and stopwords; `search`
runs `focus_all` (every subject word), then `focus_any`, then the broad request, each stage only
filling what the previous left open. Turns gather two chunks per family (Scripture, teaching,
prayer) after the passage actually heard, which is supplied at the revision heard even if a later
import made it historical. The system prompt tells the model the evidence is a retrieved excerpt
set, so it must say "the retrieved excerpts do not include that" rather than claim corpus-wide
absence.

### Work outbox states

`queued_locally → posting → directive_posted | backend_unavailable | delivery_unknown`, plus
`artifact_available` (only with a result reference that names a posted directive of this operation)
and `failed`. Dispatch is an atomic claim (`BEGIN IMMEDIATE` + conditional update + attempt row);
only the winner writes its per-attempt directive file `outbox/<op>-attempt<N>.txt`, then runs the
CLI. A `posting` row left by a crash is recovered as `delivery_unknown` at service start and is never
replayed; reconciliation uses `inbox list --all` and only proves non-delivery when the earlier
attempt's run id is in the listing. Source packets are `source_packet` artifacts written to unique
files and are never promoted to a result.

## iPhone client

The app connects to `RLMADK_BACKEND_URL` (launch environment) or the URL saved in Diagnostics
(default `http://100.90.105.30:8765`, the desktop's tailnet address). Mode state (`quiet`, activity,
devotional passage/revision/position), capture manifests and the local work list are JSON files in
the app's Documents directory; segment WAVs are `Documents/captures/<captureId>/NNNNNNNN.wav`.

Voice command phrases (configuration in `PortalModel`; buttons are the deterministic fallback):

| Action | Phrases |
| --- | --- |
| Quiet | "quiet please", "be quiet", "go quiet" |
| Wake | "wake up", "portal wake" |
| Begin prayer | "begin prayer", "let us pray", "let's pray" |
| End prayer | "end prayer", "amen end prayer" |

Capture files are retained; nothing deletes them automatically.

### Cloud Mac

Every push runs `.github/workflows/cloud-mac.yml`: backend unit tests on Ubuntu; on macOS the
unsigned simulator build, the XCTest suite plus the keyboard navigation XCUITest, a fixture backend
plus fake reasoning owner on the runner, launch, screenshots of the Portal (before/after a typed CI
demo turn), Work and Diagnostics tabs, and the remote inspection exercise (snapshot, image, tab
select, keyboard dismiss, manifest sync, capture file copy) reported by the simulator client. Full `xcodebuild` logs are uploaded. For hands-on work dispatch the session
workflow on this branch (the helper defaults to `main`):

```bash
gh workflow run cloud-mac-session.yml -R RawleySM/rlm-adk-ios --ref codex/voice-portal-prototype -f minutes=150
scripts/cloud-mac ssh 'export PATH=/opt/homebrew/bin:$PATH; xcodegen generate; …'
scripts/cloud-mac stop
```

The runner cannot reach the desktop over the tailnet directly (`tag:ci-mac` has no such grant). For
the real-backend simulator run a reverse tunnel from Spark-A was used:
`ssh -N -R 18765:100.90.105.30:8765 runner@rlm-adk-ios-mac.tail0ec4a5.ts.net`, with the app
launched as `SIMCTL_CHILD_RLMADK_BACKEND_URL=http://127.0.0.1:18765`.

## Recovering locally retained captures (issue #4)

Prayer segments upload as they close; End prayer finalizes the ordered recording. Ambient segments
remain local. Upload and finalization share one in-flight owner per capture. If a segment-close,
End prayer, reconnect or Retry callback arrives during a network request, it asks the owner to
re-read the pending manifest afterward instead of sending a duplicate request. Different captures
can progress independently. A failed pass releases ownership; it does not spin retrying. The
regression tests hold actual URLSession responses across these callback races. Files live in the app container: `Documents/captures/<captureId>/NNNNNNNN.wav`
(roughly 60-second segments, with shorter segments at capture boundaries),
`Documents/captures.json` (per-segment `startedAt`/`endedAt`, bytes, sha256, gaps) and
`Documents/mode.json`. Builds that include `UIFileSharingEnabled` and
`LSSupportsOpeningDocumentsInPlace` expose that directory as **Files > On My iPhone > RLM-ADK** and
in Finder's file sharing.

1. Copy the current app's Documents through the paired developer connection or existing file sharing
   before changing installation channels when accessible. Use the direct private installation route
   below for updates of the same bundle id; TestFlight is a fallback. Do **not** uninstall: deleting
   the app deletes the container and every capture with it.
2. In Files, open On My iPhone > RLM-ADK, select `captures.json` and the `captures/` folder, and
   copy (share/AirDrop/Finder copy) to the Mac or desktop. Copy; do not move or delete.
3. Hash the copied WAVs (`sha256sum`) and compare with `captures.json` before anything else.
4. Decide coverage from evidence, not counts: use a segment's `startedAt`/`endedAt`, its WAV header
   (sample rate, data length) and recorded gaps to select candidate files. These timestamps are writer
   start/close observations, not sample-accurate wall-clock alignment across interruptions. Listening
   establishes what the retained audio contains.
5. Transcribe only the segments that evidence places in the window of interest, through the existing
   batch transcription route; never upload the whole ambient capture as prayers, and never finalize
   ambient audio as a prayer recording without listening.

Proof boundary: this update makes the bytes reachable; it does not fix why a prayer capture can
close with zero segments, and it does not establish that any ambient segment contains the prayer.

## Seeing what the phone sees: inspection, keyboard, remote recovery

Builds from this change report app-owned state to the backend and accept a small set of remote
commands over the existing text/control WebSocket. This is not screen mirroring: the keyboard,
permission alerts, Control Center and other apps are outside what the app can render; the
snapshot carries keyboard visible/frame and the focused field so a reviewer knows what covers
the UI. A backgrounded or suspended app sends nothing, and the backend reports that honestly.

Keyboard: the typed-input and backend-URL fields have a Done button above the keyboard, dismiss
on Send/submit, on interactive scroll, on any tab change, and on the Begin prayer / Quiet / Stop
buttons; a `dismiss_keyboard` command resigns the first responder remotely.

Each install has a stable id (`Diagnostics` shows the first eight characters; `ios-…`). The
phone sends a bounded snapshot on connect, tab change, mode change, capture events, keyboard
show/hide, return to foreground and every 30 s while in the foreground: tab, connection, mode,
audio owner state, whether the engine is actually running and the age of the last audio buffer,
the last write error, recognizer state, keyboard frame/focus, typed/partial/last text, the tail of
the assistant text, capture summaries and the last 40 log lines.

```bash
B=http://100.90.105.30:8765
curl -s $B/devices | python3 -m json.tool                       # known phones, freshness, connected
curl -s $B/devices/$ID/snapshot | python3 -m json.tool          # freshness: live | connected_background | stale | offline | no_snapshot
curl -s $B/devices/$ID | python3 -m json.tool                   # snapshot + recent commands + latest image/manifest
curl -s -X POST $B/devices/$ID/commands -H 'Content-Type: application/json' -d '{"kind":"snapshot"}'
curl -s -X POST $B/devices/$ID/commands -H 'Content-Type: application/json' -d '{"kind":"image"}'
curl -s -o phone.jpg -D - $B/devices/$ID/images/latest           # app-rendered window; X-Image-Age-S header
curl -s -X POST $B/devices/$ID/commands -H 'Content-Type: application/json' -d '{"kind":"dismiss_keyboard"}'
curl -s -X POST $B/devices/$ID/commands -H 'Content-Type: application/json' -d '{"kind":"select_tab","args":{"tab":"diagnostics"}}'
curl -s -X POST $B/devices/$ID/commands -H 'Content-Type: application/json' -d '{"kind":"sync_manifest"}'
curl -s $B/devices/$ID/manifest | python3 -m json.tool          # every capture, segment identities, orphan WAVs
curl -s -X POST $B/devices/$ID/commands -H 'Content-Type: application/json' \
  -d '{"kind":"copy_capture_files","args":{"capture_id":"ambient-1791887853-9d265072","indexes":[48,49,50]}}'
curl -s $B/devices/$ID/commands/$COMMAND_ID                      # queued | delivered | applied | failed | unsupported
curl -s $B/devices/$ID/captures/ambient-1791887853-9d265072       # copied files (bytes, sha256, path) + ASR lines
```

Command states mean exactly this: `queued` is stored and not yet on the phone (offline, or the
socket write failed, which also marks the device offline); `delivered` was actually written to a
live socket; `applied`, `failed` and `unsupported` are the phone's own report, keyed by command
id. Queue acceptance is never execution. A command still without a result when the phone says
hello again (queued, or delivered on a socket that died) is pushed again; the phone keeps a
bounded persisted cache of results it already reported and answers a re-pushed command from it
without running it twice. `connected` comes from the service's live socket registry (a restart
forgets every connection); `connected_background` means the socket is up but the app last said it
left the foreground, so its state is not live.

Recovery transfers: `copy_capture_files` sends the named closed segment files of one capture
(every closed file on disk for that capture when `indexes` is omitted, including files no manifest
lists; a capture known only by its directory works too), bytes unchanged, read and re-hashed off
the UI thread, verified by digest and size on the backend, into
`<state root>/evidence/devices/<install id>/captures/<capture id>/`. The file a writer is still
appending to is never named or copied as complete. The result is `applied` only when every named
file was verified; otherwise `failed` with the exact copied, failed and missing indexes. Identical
re-sends are idempotent; different bytes for an already stored index are refused. These copies are
evidence for listening and dating, kept out of the prayer intake. `sync_manifest` uploads every
manifest with segment identities and lists closed WAVs on disk that no manifest knows (orphans).
During prayer, the on-device provisional ASR text is appended to
`Documents/prayer-asr/<capture>.jsonl` with capture id and sequence BEFORE any control phrase acts
(a line ending in "end prayer" is kept as recognized). Each recognition request is bound, for its
whole life, to the capture it was opened under: Begin prayer and End prayer end the accepting
request (no more audio is appended to it; its final is still delivered under the owner it was
opened with, however late it settles) and open the next request at once under the new owner, so
no audio is lost between the two, a prayer final can never become a conversational turn or land in
a later prayer, and a conversation final is never journaled as prayer. Control phrases in a final
act only when its owner is still the open prayer (or it is a conversation final outside prayer);
words from an ended prayer or from a conversation request that was open before Begin prayer never
end or alter the current prayer. A relaunch with an open prayer rebinds the recognizer to that
capture before any request opens. No time window is involved. Journal write failures are shown on
the Portal tab and in the snapshot, and lines sync on reconnect. The app log
is durable (`Documents/recent-log.txt`, bounded) and reloaded on launch.

Honest capture status: a prayer that settles with zero segments is `Failed: no audio captured`,
never "Saved locally", and retained manifests from earlier builds in that state are relabelled on
launch (nothing deleted). During prayer the app checks every second whether audio actually reaches
the recorder (engine running, a buffer in the last two seconds); the warning states what was
observed and when, clears itself when buffers resume, and never claims the prayer is lost. A
failed restart after an interruption, route change, media reset or return to foreground sets the
owner to Failed instead of pretending to record.

CI exercises all of this against the fixture backend with the real simulator client
(`scripts/ci_remote_inspection.py`, artifact `remote-inspection.json`, `simulator-remote-image.jpg`)
including a byte-for-byte copy of a seeded capture file, a portal-to-work tab transition observed
in the snapshots, and the image matched to its command id; the keyboard navigation XCUITest
requires the software keyboard (the hardware keyboard is disabled before the simulator boots).
Simulator evidence only; the phone must receive an upgrade through the direct private installation
route below (or the retained TestFlight fallback), without uninstalling.

## Signing, TestFlight and device

Verified on 2026-10-13 through the existing authenticated Chrome session (no password or two-factor
entry was needed): the Individual Apple Developer Program membership is active, **Team ID
`4PJBKY2WQR`**, renewal 2027-10-12. App Store Connect first-use terms and App Store Connect API
access were accepted/approved in the same session.

Assets created on 2026-10-13 (none should be recreated; all are retained outside the runner):

| Asset | Identity | Where |
| --- | --- | --- |
| App Store Connect API team key `rlm-adk-ios-ci` (App Manager) | Key ID `Z9XH3R3RV2`, Issuer `192ea9f0-7f02-4fa4-9cac-051d567feced` | `.p8` in `~/.local/state/rlm-adk-ios-prototype-supervision/asc-key/` on Spark-A (one-time download); GitHub secrets `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_PRIVATE_KEY` |
| Bundle ID `com.rawleystanhope.rlmadk` | `87XJJ9FG5S` | registered via the API |
| Apple Distribution certificate | `AUBJ7C9S7Y`, expires 2027-10-13 | CSR made on the cloud Mac; `.p12` backup in the same private directory; secrets `DIST_P12_BASE64`, `DIST_P12_PASSWORD` |
| App Store provisioning profile `rlm-adk-ios appstore` | `6SW4NCM292`, UUID `8548aa2f-24a2-4bc2-9ada-37e7707c484d` | `.mobileprovision` in the private directory; secret `APPSTORE_PROFILE_BASE64` |
| App Store Connect app record "RLM-ADK Voice Portal" | app `6822129536`, SKU `rlmadk-voice-portal`, en-US, Full Access | created in App Store Connect |
| Development certificate "Created via API" | `67R2TGBV29` | created by Xcode's first automatic-signing attempt; unused |

Repository variable `APPLE_TEAM_ID=4PJBKY2WQR`. The small API client used for the registrations is
`asc.py` next to the key (stdlib plus `openssl` for the ES256 JWT); the repo-owned upload script is
`scripts/asc_build_upload.py`.

### What works and what does not (Xcode 26.6 on the GitHub runner)

- `xcodebuild archive` with **automatic** signing fails: the team has no registered device, so Xcode
  cannot create an iOS App Development profile ("Your team has no devices from which to generate a
  provisioning profile").
- `xcodebuild archive` with **manual** signing (`CODE_SIGN_STYLE=Manual`, `Apple Distribution`,
  profile `rlm-adk-ios appstore`) succeeds once the identity is in a dedicated keychain with
  `set-key-partition-list`; signing from the login keychain hung codesign on the headless runner.
- `xcodebuild -exportArchive` with `destination=export` produces a signed IPA (`codesign --verify
  --deep --strict` passes). With `destination=upload` it fails: `lookupGenericSettingsForSubmission
  returned an error` / `No Accounts with App Store Connect Access` for the API key.
- `xcrun altool --upload-app` fails on Xcode 26.6 (`Defaults.properties` missing);
  `iTMSTransporter` reports it is now part of the Transporter app, which is not installed.
- The App Store Connect **Build Upload API** (`buildUploads` / `buildUploadFiles`, OpenAPI 4.5.1) is
  available to this team; `scripts/asc_build_upload.py` implements it (receipt written after every
  step, resumable by upload/file id, never re-sends a build number that already has a build, and a
  resumed `PROCESSING` upload is polled to its real final state). It was exercised for real on
  2026-10-13: build 3 was reserved, transferred (one 582,394-byte part, HTTP 200), committed
  (`COMPLETE`) and taken to `PROCESSING` by Apple, which then **failed validation** with `90022`
  (missing 120x120 icon) and `90713` (missing `CFBundleIconName`). The fix is the asset catalog
  `RLMADK/Assets.xcassets/AppIcon.appiconset` (generated opaque 1024 px icon) plus
  `ASSETCATALOG_COMPILER_APPICON_NAME=AppIcon` and a top-level `CFBundleIconName`; build 3 is spent.
  Build 4 (0.2.0) went through the same path and processed to `VALID` / `READY_FOR_BETA_TESTING`
  (build `3d99fc47-22f2-407a-8bef-9bbb369a5a8d`). The internal TestFlight group "Prototype
  internal" (`93dc08e3-…`) holds the account holder only; install on the phone through the
  TestFlight app signed in as `rawley.stanhope@gmail.com`.

Manual upload from a machine that holds the key and a signed IPA:

```bash
python3 scripts/asc_build_upload.py --ipa build/export/RLMADK.ipa --app-id 6822129536 \
  --key-id Z9XH3R3RV2 --issuer-id 192ea9f0-7f02-4fa4-9cac-051d567feced \
  --key-path ~/.local/state/rlm-adk-ios-prototype-supervision/asc-key/AuthKey_Z9XH3R3RV2.p8 \
  --receipt upload-receipt.json            # add --build-upload-id ID to resume a record
```

`testflight.yml` runs the same archive/export/upload on a standard macOS runner from the GitHub
secrets. It is only dispatchable once the workflow file exists on the default branch (the first
dispatch attempt returned 404 for that reason), so until the PR merges the exercised path is the
cloud-Mac session plus this script.

Version and build come from the build settings (`MARKETING_VERSION`, `CURRENT_PROJECT_VERSION`), wired
into the generated Info.plist by XcodeGen; `testflight.yml` sets the build number from its input or
the run number.

### Device

- TestFlight internal testing is now the **fallback** route (no device UDID needed). The last
  TestFlight build is `0.2.0 (6)`, `VALID` / `IN_BETA_TESTING`; its installation on the phone is
  unverified. The default private route is the direct signed install below.
- `iphone-12` is on the tailnet (state `-`); the only owned Mac with a device path (`macbook-rawley`,
  macOS 12.7.6) cannot run current Xcode, but it can pair with the phone over USB for
  `pymobiledevice3` and for reading the UDID.
- Physical-device voice behaviour (microphone, on-device recognition, Bluetooth, lock screen,
  interruptions) remains unverified until a build is installed and exercised on the phone.
  Build numbers are one shared sequence across both routes; the next is > 6.

## Direct private installation (Ad Hoc / release-testing, no TestFlight)

This app is permanently private to Rawley's phone, so the default delivery minimises Apple's part:
Apple still issues the certificate, the registered device and the provisioning profile, and its
certificate check runs at first launch, but there is no upload, processing, review or TestFlight.
Signing a build and installing it on the phone are two separate steps.

One-time device setup completed on 2026-10-13: the registered iPhone 12 is paired to Spark-A USB,
Developer Mode is enabled, and Ad Hoc profile `83XW724QDD` reuses certificate `AUBJ7C9S7Y`.
Direct Build 7 was installed without uninstalling; 86 completed audio segments retained their
exact size and hash. Remote diagnostics and still screenshots were exercised while native WAV
files continued growing. The HTTPS package is hosted but OTA installation has not been exercised.
The setup steps below are a reference, not a request to repeat enrollment:

1. The phone's UDID. Cable the phone to the MacBook or Spark once (unlock it and accept "Trust This
   Computer", the computer-pairing trust, which is unrelated to any "trust developer" setting):
   `pymobiledevice3 usbmux list` prints the UDID and `ProductVersion`.
2. Register the device to team `4PJBKY2WQR` (App Store Connect API `devices`, platform IOS) and
   create an **Ad Hoc** profile for bundle `87XJJ9FG5S` / `com.rawleystanhope.rlmadk` with the
   existing Apple Distribution certificate `AUBJ7C9S7Y` and that device (`profiles`, type
   `IOS_APP_ADHOC`). Download it and store it base64 as the GitHub secret `ADHOC_PROFILE_BASE64`.
   The profile expires with the certificate (2027-10-13 at the latest); every build's receipt
   shows the exact expiry, and an expired profile stops the app from launching.
3. Enable **Developer Mode** on the phone (Settings > Privacy & Security > Developer Mode, restart).
   Apple's "Distributing your app to registered devices" says it is required to run an iOS app
   installed from an IPA. Observe the phone's actual prompts; nothing here promises their absence.

Build and package (`.github/workflows/direct-install.yml`, manual dispatch; it is dispatchable once
the file is on `main`):

```bash
gh workflow run direct-install.yml -R RawleySM/rlm-adk-ios \
  -f build_number=7 -f device_udid=<UDID> -f base_url=https://<our host>/ios/build7
```

- `build_number` is required and must advance the shared sequence (TestFlight used 6).
- The run decodes the profile with `security cms`, and `scripts/direct_install.py validate-profile`
  rejects anything that is not an Ad Hoc profile for this team and bundle that lists the exact
  UDID (enterprise, App Store, development or expired profiles, or a missing secret, fail the run
  before signing; nothing is ever substituted).
- The export method is read from the runner's `xcodebuild -help` (`release-testing`, the current
  name for Ad Hoc; `ad-hoc` as fallback) and the export options plist is written for that method
  and the validated profile name. The existing Apple Distribution identity signs the archive.
- The **exported** IPA is inspected, not the archive: its `embedded.mobileprovision` is decoded
  and validated again, `codesign --verify --deep --strict` must pass, and `codesign -dvvv` plus the
  entitlements must agree with the validated profile (identifier, team, Apple Distribution
  authority, `application-identifier`, no `get-task-allow`, no `beta-reports-active`). Only then
  is `direct-install-receipt.json` written: commit, export method, bundle id, version, build,
  IPA sha256 and size, signing authorities and cdhash, profile name, UUID, expiry, days left,
  certificate fingerprints and the device list the build installs on.
- `scripts/direct_install.py package` writes `manifest.plist`, `index.html` and the sha256 sidecar
  for the given HTTPS base URL, taking the identity from the package itself. No hosting is set up
  by the run; the artifact `direct-install-<build>` holds the IPA, receipt, manifest, page and logs.

Installing on the phone (acceptance step, separate from the build):

- Paired route: from the Mac helper with the phone cabled (or on a Wi-Fi pairing created over the
  cable), `pymobiledevice3 apps install RLMADK.ipa`. This is an upgrade of the same bundle id under
  the same team; do **not** uninstall first, which would delete the retained captures.
- Link route: open the build's page on the phone and tap "Install on this iPhone"; the
  `itms-services` link needs a trusted HTTPS manifest. It is a tap on the phone (plus Apple's
  confirmation), never a silent push. First exercised with build 18 on 2026-10-15.
  The pages are on Spark-A, `~/.local/state/rlm-adk-ios-prototype-supervision/private-delivery`,
  served tailnet-only by Tailscale Serve at `https://rlm-adk-spark.tail0ec4a5.ts.net:8443/`.
  Publish a downloaded `direct-install-<N>` artifact with
  `python3 scripts/direct_install.py publish --ipa RLMADK.ipa --receipt direct-install-receipt.json
  --root <delivery folder> --root-url https://rlm-adk-spark.tail0ec4a5.ts.net:8443 --notes "<one line>"`:
  it writes `build<N>/` (IPA, manifest, page, sha256, receipt), then `latest.json` (atomically, last)
  and the root `index.html`.
- Update banner (build 19 and later): the app reads that `latest.json` when it comes to the
  foreground and every 5 minutes while open (`RLMADK/Model/AppUpdate.swift`; override with
  `RLMADK_UPDATE_URL`). A newer build for this bundle shows "Update available: build N" with Install
  (opens the `itms-services` link) and a dismiss; the device snapshot reports `update_offer`.
  The banner stays hidden while `latest.json` names the installed build, so a fresh install looks
  unchanged. From build 20, Diagnostics → App shows the installed build and the last check
  ("checked 08:34 · latest build 19 · up to date", or why it failed); the snapshot reports it
  as `update_check`.
- Conversation pane (build 21 and later): the reply is labelled with the talk model's name, the way
  the transcript is labelled "You:". The talk server sends `{"type": "assistant", "name": ...}` when
  the app syncs (config `llm_display_name`, else `llm_model`); the app keeps the last name. The
  Devotional disclosure is gone from the pane.
- Install hand-off (build 21 and later): iOS 26 queues an `itms-services` install opened from the
  running app and leaves it "Waiting…" until the app leaves the foreground (build 20 stalled this
  way on 2026-10-16: installcoordinationd waited for a termination assertion and installed the moment
  the app process ended). So once Apple's prompt closes, the banner shows "Installing" and the app
  steps to the Home Screen at once (UIKit's private `suspend`), where the icon shows progress. Nothing
  on the banner is tappable meanwhile (build 22: a ✕ tapped one second after the prompt cancelled the
  step aside and the install waited 25 minutes until the app was closed). No prompt within 4 s offers
  the install page in Safari; coming back on the old build (prompt cancelled) offers Install again. The first launch of a newer build shows a green
  "Updated to build N (from M)" with the notes. Phases are logged (`update install: build N <phase>`)
  and reported in the snapshot as `update_install` / `updated_notice`.
- First launch: the phone must be online for Apple's certificate check (PPQ); Developer Mode must
  be on. Then verify retention, not assume it: before installing, run `sync_manifest` and note the
  captures' bytes and sha256 from `GET /devices/<id>/manifest`; after installing and launching,
  run it again and compare files and bytes. The install id can be compared only if the previous
  build already reported one (build 6 did; build 5 and earlier did not), so a new install id on
  the first migration is not by itself proof of data loss.
- Local tests for the utility run on Linux in the backend test job (`tests/test_direct_install.py`);
  real CMS decoding, signing and export are exercised only by the Mac workflow with a real profile.
