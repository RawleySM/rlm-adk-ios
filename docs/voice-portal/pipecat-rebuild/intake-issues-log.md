# Intake issues log (what Rawley sends the Intake resident)

Rawley reaches the Intake resident from the voice portal in three ways, and every one becomes a
ticket in this log:

- **Bug chip.** He taps **Bug** on the portal page, the chip reads **Bug Reporting...**, he
  describes the problem (spoken, or typed into the input field), and taps it again. While the chip
  is on, the assistant says nothing and answers nothing, but his voice is still recorded and
  transcribed. The talk tier (Spark-A) posts what it heard, with the context of the moment, to the
  skill service on the desktop (`POST /issues`).
- **In conversation.** He asks the assistant to tell, ask, teach or send something to Intake, to
  remember something or to propose a project. The talk tier's `send_to_intake(kind)` tool files his
  own last words (never the model's paraphrase) with `POST /tools/work`, and the assistant says
  "Sent to Intake." and nothing more (`talk-protocol.md`).
- **Automatically.** He asks for something the assistant cannot do. The assistant is told to call
  `send_to_intake` itself instead of saying it cannot (`feature_request` for a missing capability or
  a change, `message` for a question its tools cannot answer, `bug` when something it should do
  failed); when it says it cannot anyway, the talk tier's safety net files his words the same way
  with origin `auto_escalation` and the assistant's reply, and says "I've passed that to Intake; it
  will reply on your phone." (`talk-protocol.md`, "Automatic escalation"). Rawley's words
  (2026-10-17): the voice assistant is for answering quickly; a larger feature he must wait for is
  tracked and registered as a fix.
- **Work tab.** A request recorded on the phone's Work tab is sent with `POST /work/<id>/dispatch`
  (`outbox.send_to_intake`).

All three go through `issues.record_report`, so the stage/post mail gate below covers every path;
nothing in the skill service posts to the allocator any other way (the old work outbox dispatch and
its `inbox list --all` reconciliation are retired). The skill service triages each ticket and mails
it to Intake (`intake-solo-plan.md`). Intake's replies come back on the ticket and are shown on the
phone as an `Intake: ...` line (and on the Bug card).

## Where it is

| | |
|---|---|
| Log | desktop `~/.local/rlm-adk/stores/intake/issues.jsonl` (skill service config `issues_log`; empty means `<state root>/intake/issues.jsonl`, which is what tests and isolated backends use) |
| Lock | `issues.jsonl.lock` beside it (advisory `flock`, held for each scan-and-append) |
| Writer | the skill service, `backend/voice_portal/issues.py` |
| Read API | `GET /issues[?status=open&limit=50]` (newest first), `GET /issues/<ticket_id>` (the folded ticket), `GET /intake/feed[?since=<unix>&limit=50]` (Intake's replies and inbound mail, newest last) |
| Write API | `POST /issues` (the talk tier's Bug chip; idempotent on `report_id`; optional `kind` hint); `POST /tools/work` (the talk tier's `send_to_intake`, or `{request_text, kind?, operation_id?}`); `POST /work/<id>/dispatch` (the Work tab); `POST /issues/<id>/mail {note?, force?}` (stage or send the ticket's mail); `POST /issues/<id>/status {status, text?, artifact?, by?}` (a status written by someone other than the resident); `POST /issues/replies` (read the mailbox now and push what is new) |
| Push | `{"type": "intake_message", ...}` on the phone's control WebSocket (`/ws`), one per new reply or inbound message (the feed item below) |

## Format

JSON Lines, one event per line, append-only, `fsync`ed. Every event has `schema: "intake-issue.v1"`,
`event`, `ticket_id` and `at` (Unix seconds); only `inbound` events carry `ticket_id: null`. A
ticket is the fold of its events in file order; a torn or unknown line is skipped.

| Event | Written | Fields |
|---|---|---|
| `reported` | at once, when a report or request is filed | `report_id`, `ticket` (the whole ticket below, with `triage.state: "pending"` and a provisional keyword triage) |
| `triaged` | when the model call returns (seconds; a cold owner takes minutes), or from the keyword rules when it fails | `triage` (replaces the ticket's triage; `state: "done"`) |
| `mail_staged` | after triage when mail is staged (`intake_mail_mode: "stage"`, the default) | `recipient`, `body` (the mail file), `command` (the exact `allocatorctl inbox post --to ... --file ...` that "post" mode would run), `note` |
| `mailed` | after triage when mail is sent (`intake_mail_mode: "post"`) | `message_id`, `recipient`, `instance_id` (the mailbox's sealed launch, read from `inbox list` right after the post; null when that listing failed), `note` |
| `status` | a reply from the resident threaded on the ticket's mail (read every `intake_poll_s`), or `POST /issues/<id>/status` | `status` (the reply's leading `status: <word>`, else `answered`), `text`, `reply_message_id`, `in_reply_to`, `from` (the mailbox's `{kind, resident_id, package_key, name}`, or the writer's name), `sent_at`, `instance_id`, `artifact` |
| `inbound` | mail from a resident to the operator that is not a reply to a ticket's mail (unthreaded, or a reply to something else) | `ticket_id: null`, `message_id`, `status` (its leading word, or null), `text`, `from`, `sent_at`, `in_reply_to`, `instance_id`, `mentions` (the ticket ids its text names) |

Status words: `accepted`, `answered`, `learned`, `proposed`, `started`, `escalated`, `needs_detail`,
`done`, `failed`, `declined`. A consumer appends its own events under the same `ticket_id` (and
takes the lock); it never rewrites lines.

### The ticket

```json
{
  "schema": "intake-issue.v1",
  "ticket_id": "bug-20261015-03",
  "report_id": "<talk tier uuid>",
  "recorded_at": 1792051200.0, "recorded_at_iso": "2026-10-15T07:20:00+00:00",
  "recorded_by": "voice-portal skill service",
  "surface": "voice_portal", "origin": "bug_chip|conversation|auto_escalation|work_tab|tool", "kind_hint": "teaching",
  "assistant_reply": "I can't set a timer from here. (auto_escalation only; null otherwise)",
  "reporter": "operator", "status": "open",
  "text": "The report's words: the server transcript segments joined (typed lines included).",
  "transcript": {
    "segments": [{"t": 1792051190.2, "text": "...", "source": "voice|typed|conversation|request"}],
    "device_segments": [{"text": "the phone's own recognizer finals"}],
    "asr": "cohere-transcribe", "started_at": 0, "ended_at": 0, "ended_by": "client|admin|disconnect|send_to_intake|tool|work_tab"
  },
  "source": {"session_id": "ws-…", "client": "ios", "app_build": "17", "transport": "websocket", "audio_route": "<AVAudioSession route>", "install_id": "…"},
  "context": {
    "mode_before": "available|quiet|prayer", "node": "devotional", "bot_speaking": true, "pending_turn": false,
    "passage": {"reference": "Romans 12", "reading": true, "spoken_count": 4, "verse_count": 21},
    "recent_turns": [{"role": "user|assistant", "text": "…"}],
    "recent_floor_events": [{"t": 0, "mode": "…"}]
  },
  "triage": {
    "kind": "bug|feature_request|message|teaching|project_proposal",
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

`origin` and `kind_hint` are absent on tickets recorded before 2026-10-17 (they came from the Bug
chip); those tickets may also carry the retired kinds `question` and `feedback`, which fold, list and
are re-mailed as messages. `assistant_reply` is recorded only by the talk tier's auto-escalation
(origin `auto_escalation`) and is quoted to the triage model and in the mail. Every ticket id is
`bug-YYYYMMDD-NN`, whatever its kind.

### Latency

`POST /issues` and `POST /tools/work` answer as soon as the `reported` event is on disk (one lock,
one scan of the log, one fsync); the triage call and the allocatorctl post run on background
threads. Measured 2026-10-17 on Spark-A against an isolated backend with a 1 s fake triage owner:
`POST /tools/work` median 5.9 ms, p90 6.5 ms, max 7.8 ms over 30 calls
(`tests/test_issues.py::test_tools_work_returns_before_triage_and_lists_the_origin` holds it under
0.9 s with the slow owner).

## Triage: flag, sort, disposition

One strict-JSON call to the skill service's reasoning owner (`inference_base_url`, thinking off,
`response_format` schema `issue_triage`; one plain retry when the owner rejects the schema), with the
report's words and its context (open passage and whether it was being read, the mode, the last
conversation turns, the app build and audio route, and the kind it was sent as). The model **flags**
(`severity`, `flags`) and **sorts** (`kind`, `component`); off-list values fall back to the keyword
rules.

The kind is decided in this order:

1. **Rawley's first spoken word** (fillers such as "um" and "okay" skipped): "teach ..." /
   "remember ..." is a `teaching`, "project ..." / "proposal ..." / "propose ..." a
   `project_proposal`, "bug ..." a `bug`.
2. **The caller's hint**: `send_to_intake(kind)`, a `kind` on `POST /issues` or `/tools/work`, the
   Bug chip's optional `client_bug` `kind`. Anything outside the five kinds is ignored.
3. **The model** (or the keyword rules when it fails: "remember that", "keep in mind" make a
   teaching; "new project", "start a project" a proposal; "I'd like", "please add" a feature request;
   otherwise a bug).

The host then derives, deterministically:

* **disposition**: `no_report` when fewer than three words were said (the press is kept: its
  context still says what was happening); for a bug, `needs_detail` when the model says an engineer
  would have to ask (its `detail_questions` say what), else `ready`; every other kind is a `request`
  (no `needs_detail` gate; any open questions still travel in the mail).
* **priority**, the sort key for a consumer: P1 for critical or `blocks_use` / `data_loss`, P2 major,
  P3 minor, P4 cosmetic or `no_report`.
* **route**: `intake` for every ticket.

When the owner is down the keyword rules triage the ticket (`source: fallback_keywords`, the error
in `errors`). A ticket still pending when the skill service starts is triaged again.

## Mail to the Intake resident

With `intake_recipient` set (production: `intk01`), a triaged ticket whose disposition is in
`intake_mail_dispositions` (`ready`, `request`, `needs_detail`) gets a plain-text mail
(`issues.mail_text`): a first line `voice-portal ticket_id=... kind=... priority=... disposition=...
component=...`, a line naming the kind, where it came from (Bug chip, conversation, the assistant's
own escalation, Work tab), his words, for an auto-escalated ticket the block "This request was
auto-escalated. The voice assistant could not meet this; its reply was: ..." and that a tracked fix
is expected, the triage, open questions, the context, `Full record: GET http://127.0.0.1:8765/issues/<id>`
(the resident runs on the desktop beside the skill service) and how to reply
(`send_message("operator", "status: <word> ...", in_reply_to=<the message id>)`) with the words for
its kind (`intake-solo-plan.md` section 3):

| Kind | Reply words | The reply names |
|---|---|---|
| `bug`, `feature_request` | `accepted`, then `done` / `failed` (or `needs_detail`, `declined`) | the PR, the merge commit, the build or deploy; what stopped it; the GitHub issue when the work outlives the turn (below) |
| `message` | `answered` (or `needs_detail`, `declined`) | the answer, with the record ids and dates it rests on |
| `teaching` | `learned` (or `needs_detail`, `declined`) | what was learned, where it applies, the package version that holds it |
| `project_proposal` | `proposed`, then `started` after his go-ahead (or `needs_detail`, `declined`) | the brief; then the project id, the repository and the first commit |

Plain text on purpose: a JSON body shaped `{"kind": "job_request"}` is read by the host as cloud-job
demand.

### Tracking on GitHub

An accepted bug or feature that is **not finished within Intake's delivering turn** gets a GitHub
issue in `RawleySM/rlm-adk-ios`, opened by Intake from the template
`.github/ISSUE_TEMPLATE/intake.md` (fields: intake ticket id, Rawley's words, origin, acceptance,
status) with the labels `intake` plus `bug`, or `capability-gap` when the voice assistant could not
meet the request (origin `auto_escalation`), or `feature` otherwise. The delivering PR says
`Fixes #<n>`, so the merge closes it; the ticket's `status` events carry the issue URL (Intake names
it in its reply); the voice portal's Inbox (ticket `bug-20261016-03`, to be built by Intake) will
show them. The backlog is therefore
<https://github.com/RawleySM/rlm-adk-ios/issues?q=label%3Aintake>, and the work register's
`rlm-adk-ios` record says so. Work finished within the turn needs no issue: the `done` reply with
the PR and merge commit is its record. Issues are opened by Intake as it accepts work, never
backfilled for tickets it has not taken.

- `intake_mail_mode: "stage"` (default while the runtime is not running): the mail is written under
  `<state root>/outbox/issues/` and a `mail_staged` event records it and the exact post command.
  Nothing reaches the allocator, and no replies are read. `send_to_intake` then says "Saved for
  Intake. Mail to Intake is on hold, so it has not been sent yet." A staged ticket is not posted by
  itself when the mode changes: `POST /issues/<id>/mail {"force": true}` sends it.
- `intake_mail_mode: "post"`: the command runs (`allocatorctl inbox post --to <recipient> --file
  <mail>`, receipt `posted message_id=<id> to=<recipient>`), a `mailed` event records the id and
  the mailbox instance, and a background thread reads the mailbox every `intake_poll_s`.

## Reading Intake's replies and mail

One `allocatorctl inbox list --to-operator --json` per poll (schema `rlm_adk.inbox_list.v1`:
`instance_id` and every message residents sent the operator in that instance, oldest first). The
listing has no cursor, so each message is recorded once by `message_id`:

- `in_reply_to` names a ticket's `mailed` message of the same instance (or a mail recorded before
  instance ids were kept: message ids are unique `mail-<uuid>`): a `status` event on that ticket,
  whose status becomes the reply's leading word.
- Anything else: an `inbound` event (`ticket_id: null`) with the ticket ids its text names. A mail of
  another instance is never threaded, so its replies arrive as inbound mail.

A refused or failed listing records nothing and is retried at the next poll. Every new reply and
inbound message is pushed to the phone (`intake_message`, below); a phone that was away catches up
with `GET /intake/feed?since=<the last at it applied>` when it reconnects.

### The phone's view (`GET /intake/feed`, `intake_message`)

```json
{"type": "intake_message", "id": "mail-5b1e…", "kind": "reply|inbound",
 "ticket_id": "bug-20261017-02", "ticket_title": "…", "ticket_kind": "teaching",
 "status": "learned", "text": "status: learned. …", "display": "…",
 "from_name": "Intake", "sent_at": "2026-10-17T09:00:00+00:00", "at": 1792224000.5,
 "in_reply_to": "mail-77…", "instance_id": "intake-solo-20261017", "mentions": []}
```

`display` is the text without its leading `status: <word>`. The feed is `{"items": [...], "now",
"log"}`, newest last; `since` filters on `at` (when the skill service recorded it), `limit` keeps the
newest. An operator's own `POST /issues/<id>/status` is not in the feed. The app shows the newest as
`Intake: <display>` in the conversation pane (status and ticket beneath it) and lists the latest
three replies about the Bug card's ticket on the card.
