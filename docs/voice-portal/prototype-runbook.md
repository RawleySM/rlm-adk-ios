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
./run-backend import prayer teaching scripture theographic   # idempotent; ~10 s for everything
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
unsigned simulator build, the XCTest suite, a fixture backend plus fake reasoning owner on the
runner, launch, and screenshots of the Portal (before/after a typed CI demo turn), Work and
Diagnostics tabs. Full `xcodebuild` logs are uploaded. For hands-on work dispatch the session
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

## Signing and device

- KeePassXC entry `Apple Developer + App Store Connect - Individual - Enrollment 9Z9523324Y`
  (username `rawley.stanhope@gmail.com`, URL `https://developer.apple.com/account`), metadata only.
- No Team ID or App Store Connect API key exists in the repository or on the hosts; the project builds
  with `CODE_SIGNING_ALLOWED=NO` for the simulator only.
- The only owned Mac with a physical device path (`macbook-rawley`, via `mac-actions --target rawley
  system status`) runs macOS 12.7.6 and cannot run current Xcode; the GitHub runner cannot attach a
  physical phone. `iphone-12` is on the tailnet (state `-`).
- Checking membership activation requires an interactive Apple ID sign-in with two-factor approval on
  Rawley's device; that was not attempted unattended.
