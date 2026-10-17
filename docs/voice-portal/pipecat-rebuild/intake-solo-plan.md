# Plan: Intake as the only resident, connected to the voice portal

Prepared 2026-10-17. Supersedes the three-resident topology in `intake-delegation-plan.md` (Steward,
Researcher and Intake together) and the reset candidate's three-resident document. Rawley's direction:
launch the next allocator instance with **only the Intake resident**, and connect the voice portal so
that **messages, teaching requests and new project proposals** reach it from him, alongside the bug
and feature tickets the Bug chip already files.

Facts below were read from rlm-adk `origin/main` 4916fc33, rlm-adk-ios `origin/main` 703780c, the
desktop allocator (`allocatorctl status`, release 71625b49) and the desktop skill service config.

## 1. What already works, and what does not

| Piece | State |
|---|---|
| Capture | The Bug chip records Rawley's words verbatim (the assistant is silent), the talk tier posts `POST /issues`, the skill service triages and logs the ticket (`intake-issues-log.md`). |
| Ticket to mail | Built. `intake_mail_mode: "stage"` writes the mail and the exact `allocatorctl inbox post --to intk01 --file ...`; `"post"` sends it and polls replies. The overnight 2026-10-16 run posted 9 tickets and recorded Intake's replies. |
| Mailbox (installed release) | `inbox post --to <id\|key\|root> [--reply-to] [--priority]`; `inbox list --to-operator --json [--in-reply-to]` (schema `rlm_adk.inbox_list.v1`) returns threaded replies **and** unthreaded resident messages. No cursor: every list is the whole history, so the reader dedupes by `message_id`. |
| Replies to the phone | **Missing.** Replies live only in the issues log. The app has no ticket view, unknown message types are dropped, the talk tier never speaks a late result, and unthreaded mail is never read. |
| `request_work` voice tool | **Unsafe.** It posts live to `work_recipient` (`intk01`) and bypasses the stage gate; replies are never read; `outbox.reconcile` calls an `inbox list --all` the CLI no longer has. |
| Triage kinds | `bug`, `feature_request`, `question`, `feedback` only. A teaching or a proposal is mis-sorted as feedback. |
| Intake overnight | Accepted every ticket, delivered none: each ledger entry reads `blocked_on: generation-1 project attachment ... not forced or replaced` (a stale cross-generation `rlm-adk-ios` attachment). It also spent its turns on coordinate-hold acceptance steering. The staleness clean-up (separate workstream) fixes the attachment in host code. |

## 2. Topology: one root resident

New instance `intake-solo-20261017`, one resident `intk01` (package `intake_intk01`), `start: "cold"`
(no published package), root of the tree with `children: []`. A single-resident tree is valid; duty
holder rules are positional, not name-based (`rlm_adk/residents/duties/definitions.py`,
`duties/launch.py`). As the root, Intake must hold `capacity_governance` and `project_registration`,
which is exactly what turns a project proposal into a real project.

| Section | Value |
|---|---|
| Limits | 1 total, 1 active, 1 child |
| Projects requested | `rlm-adk-ios` (coordination slot, holder `intk01`), `project-work-registry` (read; the work register) |
| Registration grants | `rlm-adk-ios`, `project-work-registry` at `register_at: "launch"`; a runtime **proposal pool** `intk-p01` ... `intk-p05` at `~/.local/rlm-adk/projects/intake-proposals/p0N` (paths need not exist yet) |
| Coordination | `rlm-adk-ios` held by `intk01`; each pool key configured with `holder: null` until registered |
| Capacity | ceiling 3, initial target 1 |
| Disabled kinds | model routing control, installed release activation, project promotion execution (its delegate merges with `gh`, as before) |
| Gateway | the desktop's installed policy; delegate kind `claude_code` for product work |

## 3. The three request kinds, end to end

| Kind | Captured as | Intake does | Reply (leading word) | Durable artifact |
|---|---|---|---|---|
| Bug / feature | `bug`, `feature_request` | app scope: opens the `rlm-adk-ios` coordinate, one Claude Code delegate per ticket, PR, checks, merge, build or deploy | `accepted`, then `done` / `failed` | PR, merge commit, build and install link |
| Message / question | `message` (was `question`, `feedback`) | answers from the work register, the granted repositories and its ledger; quotes record ids and dates | `answered` | its ledger line |
| Teaching | `teaching` | restates what it learned and where it applies, records it in its own package (`SKILL.md` and `references/teachings.md`, helpers if it is a method), publishes and adopts the new version; product rules go into that repository's docs by delegate | `learned` | package version `intake_intk01/vN` |
| Project proposal | `project_proposal` | writes a project brief (outcome, first step, sources, risks, how it stays small) as a register record draft, and asks once for a go-ahead; on "go" it creates the repository at the next pool path (`git init`, `gh repo create RawleySM/<name> --private`), `register_project`, assigns itself coordinator, opens the first branch | `proposed`, then `started` | brief, project id, repository, first commit |

The go-ahead is deliberate: the portfolio review retired 27 pointers that accumulated from projects
nobody chose. A proposal costs one brief; a project costs a repository, a registration that can never
be reused and capacity.

Intake may also send Rawley **unthreaded** mail, only to report the outcome of work he asked for or a
blocker that needs him; never status chatter.

## 4. Changes, by component

### Skill service (`backend/voice_portal/`)
1. Triage kinds `bug | feature_request | message | teaching | project_proposal` (prompt, `KINDS`,
   keyword fallback). A spoken first word overrides the model: "teach ..." / "remember ..." ->
   teaching, "project ..." / "proposal ..." -> project_proposal, "bug ..." -> bug. Non-bug kinds get
   disposition `request` with no `needs_detail` gate.
2. Mail text per kind with the reply vocabulary of section 3 (and no hard-coded Spark-A address).
3. One reply reader: a single `inbox list --to-operator --json` per poll, dedupe by `message_id`,
   thread on `in_reply_to` into the ticket's `status` events, record unthreaded mail as `inbound`
   events; store the instance id on `mailed`, skip ids from other instances.
4. `GET /intake/feed?since=<t>` (replies and inbound, newest last) and a pushed `intake_message`
   event on the phone's WebSocket.
5. `/tools/work` goes through `issues.record_report` (kind hint), so the stage/post gate covers every
   path; `outbox.dispatch` / `reconcile` retire.

### Talk tier (`talk/talk/`)
- `request_work` becomes `send_to_intake(kind)`, filing Rawley's verbatim last words, not the model's
  paraphrase; it says "Sent to Intake." and nothing more.
- `client_bug` keeps working; it carries an optional `kind` hint for a future chip.
- **Automatic escalation** (added 2026-10-17, Rawley: "if I ask for something and the voice assistant
  can't meet it, it should automatically go to the resident ... I want that properly tracked and
  registered as such a fix"): the prompt tells the model to call `send_to_intake` itself for anything
  its tools cannot do, and `talk/escalation.py` files his words with origin `auto_escalation` when
  the model says it cannot anyway (`talk-protocol.md`, "Automatic escalation"). The mail tells Intake
  the request was auto-escalated and that a tracked fix is expected.

### App (`RLMADK/`)
- Handle `intake_message`: an `Intake: <text>` line in the conversation pane, mirroring `You:` and
  `<model>:`, and the latest replies on the Bug card.
- The single Issue button and an Inbox tab are **ticket `bug-20261016-03`**, left to Intake as its
  first delivered app change, so the system proves itself on Rawley's own request.

### Launch authoring (desktop `~/.config/rlm-adk/launch/intake-solo-20261017/`)
- Generator `make_intake_solo_launch.py` -> `launch.author.json`, schema-validated against the
  installed release, then `allocatorctl preflight --launch`.
- Mission: section 3 per kind; no Researcher escalation (topology-scope tickets reply `needs_detail`
  naming why it is RLM-ADK work); root powers used only for proposals Rawley approved; reply first in
  the delivering turn; wait on `resident_wait` between messages.
- Steering: paths, the delivery procedure (from the earlier launch notes), the pool keys, the reply
  vocabulary. No acceptance-test steering.

### Config (desktop `~/.local/rlm-adk/voice_portal/config.json`)
- `intake_recipient` and `work_recipient` stay `intk01`; `intake_mail_mode` moves to `"post"` only
  after the launch is active.

## 5. Order

1. Staleness clean-up (running separately): host fix for the stale attachment, batched
   `project-retire`, carry-forward `allocatorctl update`, service stopped.
2. This branch: skill service, talk tier and app changes with tests; CI green; deploy the skill
   service and talk tier; ship the app build from the branch.
3. Launch document, schema check, preflight.
4. `pause` (hold), `relaunch --launch <doc>`, verify `instance=new:intake-solo-20261017` and the seal;
   retire the 25 superseded project pointers in one batch (26 and 27 only with Rawley's yes).
5. Skill service to `post` mode; re-mail the open tickets (`bug-20261016-03`, `-05`, `-06`,
   `bug-20261015-02`).
6. Release the hold (`resume`) and supervise: checks every 60 s until convergence, never more than
   300 s apart afterwards, with model-call and token deltas (thinking included) for Intake and its
   delegates.

### Tracking (GitHub + the work register)
- An accepted bug or feature that Intake does not finish within its delivering turn gets a GitHub
  issue in `RawleySM/rlm-adk-ios` from `.github/ISSUE_TEMPLATE/intake.md`, labels `intake` plus
  `bug`, or `capability-gap` (the assistant could not meet the request), or `feature`; the
  delivering PR says `Fixes #<n>`; the ticket's status events carry the issue URL; the portal's
  Inbox (`bug-20261016-03`) will show them (`intake-issues-log.md`, "Tracking on GitHub"). Intake
  opens issues as it accepts work; nothing is backfilled.
- The work register record `rlm-adk-ios` names that issue list as the repository's backlog and the
  Intake resident as the consumer of voice requests.

## 6. Acceptance

- A spoken message gets a threaded `answered` reply shown as an `Intake:` line on the phone.
- A teaching produces a published `intake_intk01` version that contains it, named in the reply.
- A proposal (`bug-20261016-04`, mapping parts in prayer, is the natural first one) gets a `proposed`
  brief; after "go", a registered pool project with a repository and a first commit.
- `bug-20261016-06` (reading 2 Corinthians) is fixed by Intake's delegate: PR merged, talk tier
  deployed, `done` naming PR and commit.

## 7. Host gaps (generic follow-ups, not blockers)

1. Runtime projects need keys declared at launch (`duties/registration.py`); no "allowed root" form,
   so pool ids are placeholders and the pool size is fixed for the instance.
2. Project ids are never reusable installation-wide (`continuous/project_registry.py`).
3. `inbox list` has no `--since`/`--limit`, and `inbox post` has no `--json` receipt.
