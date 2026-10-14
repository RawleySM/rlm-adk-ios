# Pipecat rebuild: runbook

How the rebuilt portal is deployed, warmed, tested and recovered. Owners and thresholds are in
`acceptance-matrix.md`; the wire protocol in `talk-protocol.md`; the skill endpoints in
`skill-service.md`.

## Pieces and where they run

| Piece | Host | Start / status |
|---|---|---|
| Talk tier (Pipecat) | Spark-A, `~/srv/voice-talk`, source `talk/` in this repo | `systemctl --user enable --now voice-talk.service` (unit in `talk/voice-talk.service`; `ExecStartPre` warms the owners, `TimeoutStartSec` 600 s because a cold Cohere takes ~110 s and a cold LLM ~200 s). Status: `curl http://127.0.0.1:7860/status`. Logs: `~/.local/state/voice-talk/logs/`, per-session JSONL under `~/.local/state/voice-talk/sessions/`. |
| Skill service (voice_portal) | desktop, `~/srv/voice-portal/backend`, state `~/.local/rlm-adk/voice_portal` | `python3 -m voice_portal serve` under setsid/nohup (no unit yet). `curl http://100.90.105.30:8765/healthz`. |
| Cohere STT | Spark-A `spark-inference.service` (28080) | `curl 127.0.0.1:28080/status | jq .model_admission` |
| LLM (`qwen-coding-b`) | Spark-B owner (18300) through Spark-A's forward 127.0.0.1:28111 | `curl 127.0.0.1:28111/status | jq .model_admission` |
| Fish S2-Pro (voicesim speaker) | Spark-B `fish-speech-s2pro.service`, tunnel 18085 on Spark-A and desktop | `voicesim server status` |
| Isolated prayer intake (tests only) | Spark-A port 18091, started by `rlm-voice-sim/scripts/phone-trial.sh` | never the production store |
| iPhone 12 | Spark-A USB; direct-install builds from `.github/workflows/direct-install.yml` | `pymobiledevice3 usbmux list` |

## Deploy the talk tier

```bash
# on Spark-A
rsync -a --delete <checkout>/talk/talk/ ~/srv/voice-talk/talk/ && rsync -a <checkout>/talk/tools/ ~/srv/voice-talk/tools/
cp <checkout>/talk/voice-talk.service ~/.config/systemd/user/ && systemctl --user daemon-reload
systemctl --user restart voice-talk.service && sleep 5 && curl -s http://127.0.0.1:7860/status
```

Conversation states are Pipecat Flows nodes (`talk/flows.py`: available, conversation, prayer, devotional, project, quiet); each node's state text rides in the LLM system instruction because the Spark-B owner's chat template rejects a system message after the first (`System message must be at the beginning`). Flows is initialized when the pipeline starts (`StartFrame` at the floor), not on client connect.

Configuration: `~/.config/voice-talk/config.json` (`TalkConfig`); the floor policy (address, amen,
quiet and wake phrases, thresholds), the voice (`bm_george`), owners and the skill service URL live
there. `VOICE_TALK_<FIELD>` overrides a field for an experiment.

## Deploy the skill service

```bash
# on the desktop, from a checkout at the commit to deploy
rsync -a --delete backend/voice_portal/ ~/srv/voice-portal/backend/voice_portal/
git rev-parse HEAD > ~/srv/voice-portal/backend/SOURCE_COMMIT
pid=$(ss -Hltnp 'sport = :8765' | grep -oP 'pid=\K[0-9]+'); kill $pid; sleep 2
cd ~/srv/voice-portal/backend && setsid nohup python3 -m voice_portal serve > ~/.local/rlm-adk/voice_portal/logs/serve.log 2>&1 &
curl -s http://127.0.0.1:8765/healthz
```

Imports (`python3 -m voice_portal import <family>`) run with the server stopped; see `skill-service.md`.

## Keep the owners warm (the 2026-10-14 12:05 UTC outage)

The Spark-B owner unloads Qwen after roughly 80 minutes idle and Spark-A's owner does the same with
Cohere. A cold Qwen takes about 200 s to start and a cold Cohere about 110 s. On 2026-10-14 the
phone's first request after the campaign hit a cold Qwen; every turn's request started a model load
that the next utterance's interruption cancelled (`model_start_cancelled` in the Spark-B owner log,
eight times in seven minutes), so the bot only ever spoke bridge phrases for nine minutes. Two
things now stand against that:

* `voice-talk-warm.timer` (unit files in `talk/`, installed next to `voice-talk.service`) runs
  `tools/warm.py` every 10 minutes, so neither owner idles out; its log is
  `~/.local/state/voice-talk/logs/warm.log`.
* The bridge speaks a follow-up (`bridge_followup_phrase`, 8 s after the turn with no reply) so a
  slow owner is announced instead of silent; a listener who waits lets the load finish.

Symptom to recognise: user transcripts arrive, LLM TTFB of 50 s or more in the talk log, and the
Spark-B owner log shows `model_start_cancelled`. Remedy: run `tools/warm.py` once in the
foreground (it waits out the cold start) and check the timer is active.

## Warm before any proof run

```bash
~/srv/voice-talk/.venv/bin/python ~/srv/voice-talk/tools/warm.py        # Cohere (~107 s cold) and the LLM (~200 s cold)
```

Cohere is a single-request model: a client that disconnects mid-request opens its circuit
(`request_aborted_or_backend_failed`, recovery `operator_reset`). The owners now run spark-inference
`7e98c9d`, so one model can be reset without unloading the others:

```bash
R=~/.local/share/spark-inference/releases/$(git -C ~/spark-inference rev-parse origin/main)
$R/tools/spark_local_llm/sparkctl reset cohere-transcribe          # Spark-A
ssh spark-media '~/.local/share/spark-inference/releases/7e98c9d58fb5d8997dd35b1feae3450546148aa9/tools/spark_local_llm/sparkctl reset qwen-coding'
```

Memory on Spark-A: desktop applications hold about 40 GiB, so Qwen and Cohere cannot both stay
resident there without `host_memory_pressure` refusals. Keep the LLM on Spark-B. Anything that asks
Spark-A's owner for Qwen (a misconfigured judge did this on 2026-10-13) evicts Cohere to make room.

## Run the proofs

**Every run that puts sound in the room (`--mode room`, `--mode phone`) goes through
`scripts/phone-suite.sh`.** The phone's own prayer-by-address capture publishes whatever prayer it
hears, hold or no hold; on 2026-10-14 two room runs and one phone run put four fixture prayers into
the production prayer store before this wrapper existed (removed the same night, receipt under
`~/.local/rlm-adk/stores/prayer_pipeline/removed-synthetic-20261014/` on the desktop). The wrapper
starts an isolated prayer intake (:18091) and an isolated voice-portal backend (:18765) on Spark-A,
relaunches the app with `RLMADK_BACKEND_URL` pointing at them, runs the suite, and relaunches the
app on the production backend afterwards; its evidence directory lists what the isolated intake
published.

```bash
cd ~/rlm-voice-sim
voicesim talk build suites/conversation-core                      # fixtures in the cloned voice (once per script change)
voicesim talk run suites/conversation-core --mode file             # digital injection, every case (no sound in the room)
scripts/phone-suite.sh suites/conversation-core --mode room        # speakers → Spark-B mic → talk tier (phone held quiet, isolated intake)
scripts/phone-suite.sh suites/conversation-core --mode phone       # speakers → iPhone → talk tier → phone speaker → Spark-B mic
voicesim talk report conversation-core                             # markdown over every run
voicesim talk probe --text "Read me Psalm twenty three." --out /tmp/p   # one turn
```

Run directories: `~/.local/state/voicesim/runs/talk-suites/conversation-core/runs/<stamp>-<mode>-<label>/<case>/`
with `talk-receipt.json` (every message and the bot audio's onset and spans), `bot.wav`, `result.json`.
Phone mode (`voicesim/talkphone.py`) opens no socket: it follows the phone's session in the talk
tier's session JSONL (modes, transcriptions, bot speech) and records Spark-B's microphone; fixture
windows are blanked and `bot.wav` holds only the phone's reply spans (a transcriber given the whole
recording invents text for the silence). It plays the `wake` control fixture first because the phone
re-asserts its own quiet on every reconnect. `scripts/phone-trial.sh` remains the prayer-capture
proof (capture hash, isolated intake WER).

File and room runs hold every other live session quiet through `POST /admin/hold` (and release at
the end); the room shares its air with the phone, which otherwise answers every fixture through its
speaker. `GET /admin/sessions` shows who is connected and in which mode.

## Recovery checklist (in this order, before blaming the pipeline)

1. `curl 127.0.0.1:28080/status` and `curl 127.0.0.1:28111/status`: every model `available: true`; a
   circuit → `sparkctl reset <model>`; `inhibited: true` → read `inhibit_reason` (a fence needs
   `sparkctl reset all` with nothing loaded).
2. `tools/warm.py` → both `ok: true`.
3. `curl http://100.90.105.30:8765/healthz` and `/tools/scripture?reference=Psalm%2023`.
4. `curl 127.0.0.1:7860/status` → `ready`; restart the unit if not.
5. Phone: `audio_owner_state` and `engine_running` from `GET /devices/<install>/snapshot` on the skill
   service; a dead engine is relaunched with `pymobiledevice3 developer dvt launch com.rawleystanhope.rlmadk --kill-existing`
   (device tools: `~/.local/state/rlm-adk-ios-prototype-supervision/device-tools-py313/bin`). The
   phone's talk session: `curl 127.0.0.1:7860/admin/sessions`; if it is `quiet` outside a test, say
   "wake up" or `POST /admin/sessions/<id>/mode {"mode":"available"}`.
6. A stale hold (`hold.active: true` with no run going): `curl -X POST 127.0.0.1:7860/admin/release`.
7. Then rerun the case; record the infrastructure failure in the benchmark report rather than counting
   it against the case.
