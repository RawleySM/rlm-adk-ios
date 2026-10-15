# Intake issues log (bug reports from the Bug chip)

Rawley reports a bug by voice: he taps **Bug** on the portal page, the chip reads **Bug
Reporting...**, he describes the problem (spoken, or typed into the input field), and taps it again.
While the chip is on, the assistant says nothing and answers nothing, but his voice is still recorded
and transcribed. The talk tier (Spark-A) posts what it heard, with the context of the moment, to
the skill service on the desktop. The skill service records it as a ticket in this log and triages it.

A resident will later watch this log: the planned Intake resident (`intake-resident-proposal.md`), or
the Researcher resident. That is not built yet. Nothing posts mail to a resident and nothing wakes
one; the log only accumulates tickets.

## Where it is

| | |
|---|---|
| Log | desktop `~/.local/rlm-adk/stores/intake/issues.jsonl` (skill service config `issues_log`; empty means `<state root>/intake/issues.jsonl`, which is what tests and isolated backends use) |
| Lock | `issues.jsonl.lock` beside it (advisory `flock`, held for each scan-and-append) |
| Writer | the skill service, `backend/voice_portal/issues.py` |
| Read API | `GET /issues[?status=open&limit=50]` (newest first), `GET /issues/<ticket_id>` (the folded ticket) |
| Write API | `POST /issues` (the talk tier; idempotent on `report_id`) |

## Format

JSON Lines, one event per line, append-only, `fsync`ed. Every event has `schema: "intake-issue.v1"`,
`event`, `ticket_id` and `at` (Unix seconds). A ticket is the fold of its events in file order;
a torn or unknown line is skipped.

| Event | Written | Fields |
|---|---|---|
| `reported` | at once, when the talk tier posts the report | `report_id`, `ticket` (the whole ticket below, with `triage.state: "pending"` and a provisional keyword triage) |
| `triaged` | when the model call returns (seconds; a cold owner takes minutes), or from the keyword rules when it fails | `triage` (replaces the ticket's triage; `state: "done"`) |
| `status` | reserved for the consumer | `status` (`open` → whatever the consumer defines, e.g. `claimed`, `resolved`) |

A consumer appends its own events under the same `ticket_id` (and takes the lock); it never
rewrites lines.

### The ticket

```json
{
  "schema": "intake-issue.v1",
  "ticket_id": "bug-20261015-03",
  "report_id": "<talk tier uuid>",
  "recorded_at": 1792051200.0, "recorded_at_iso": "2026-10-15T07:20:00+00:00",
  "recorded_by": "voice-portal skill service",
  "surface": "voice_portal", "reporter": "operator", "status": "open",
  "text": "The report's words: the server transcript segments joined (typed lines included).",
  "transcript": {
    "segments": [{"t": 1792051190.2, "text": "...", "source": "voice|typed"}],
    "device_segments": [{"text": "the phone's own recognizer finals"}],
    "asr": "cohere-transcribe", "started_at": 0, "ended_at": 0, "ended_by": "client|admin|disconnect"
  },
  "source": {"session_id": "ws-…", "client": "ios", "app_build": "17", "transport": "websocket", "audio_route": "<AVAudioSession route>", "install_id": "…"},
  "context": {
    "mode_before": "available|quiet|prayer", "node": "devotional", "bot_speaking": true, "pending_turn": false,
    "passage": {"reference": "Romans 12", "reading": true, "spoken_count": 4, "verse_count": 21},
    "recent_turns": [{"role": "user|assistant", "text": "…"}],
    "recent_floor_events": [{"t": 0, "mode": "…"}]
  },
  "triage": {
    "kind": "bug|feature_request|question|feedback",
    "title": "≤ 80 characters", "summary": "…", "observed": "…", "expected": "…", "steps": ["…"],
    "component": "voice_input|voice_output|conversation|scripture_pane|prayer|app_ui|connectivity|backend|other",
    "severity": "critical|major|minor|cosmetic",
    "flags": ["blocks_use", "data_loss", "regression", "intermittent", "reproducible", "wrong_content"],
    "needs_detail": false, "detail_questions": ["…"],
    "disposition": "ready|needs_detail|request|no_report",
    "priority": "P1|P2|P3|P4",
    "route": "intake",
    "state": "pending|done", "source": "provisional_keywords|reasoning|fallback_keywords|no_words",
    "model": "qwen-coding-b", "errors": [], "elapsed_ms": 0, "usage": {}
  }
}
```

## Triage: flag, sort, disposition

One strict-JSON call to the skill service's reasoning owner (`inference_base_url`, thinking off,
`response_format` schema `issue_triage`; one plain retry when the owner rejects the schema), with the
report's words and its context (open passage and whether it was being read, the mode, the last
conversation turns, the app build and audio route). The model **flags** (`severity`, `flags`) and
**sorts** (`kind`, `component`); off-list values fall back to the keyword rules. The host then
derives, deterministically:

* **disposition**: `no_report` when fewer than three words were said (the press is kept: its
  context still says what was happening); `needs_detail` when the model says an engineer would have
  to ask (its `detail_questions` say what); `ready` for a bug; `request` for a feature request,
  question or feedback.
* **priority**, the sort key for a consumer: P1 for critical or `blocks_use` / `data_loss`, P2 major,
  P3 minor, P4 cosmetic or `no_report`.
* **route**: `intake` for every ticket until the consumer exists and decides otherwise.

When the owner is down the keyword rules triage the ticket (`source: fallback_keywords`, the error
in `errors`). A ticket still pending when the skill service starts is triaged again.
