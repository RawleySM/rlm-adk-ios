# Plan: build 19 delivered by the Intake resident through a Claude Code delegate

Prepared 2026-10-15. Planning only; nothing here has been changed on the desktop or in rlm-adk.
Goal set by Rawley: instead of Claude (this session) building the in-app update banner, show the
native path end to end. His bug report `bug-20261015-01` ("In-app update banner for Tailscale link")
arrives at a declared **Intake** resident, which hands the change to an **agent delegate** (Claude
Code), which produces build 19 and publishes it. Significant topological changes go instead to the
**Researcher** resident for a supervised live test.

Sources: rlm-adk at `origin/main` 48df8d54 (paths below are rlm-adk paths unless noted), the desktop
allocator read through `allocatorctl status --json`, and the proposals in this directory. Facts are
cited; inferences are marked.

## 0. Updates 2026-10-16 (decisions taken and what changed since the plan)

- **Decisions.** Rawley chose one launch with Steward, Researcher and Intake together, Claude Code
  as Intake's delegate, and lifted the two-resident limit. Resident limits are fixed for an instance
  in its registry policy (`rlm_adk/residents/provisioning.py`, `RegistryPolicy`: "Everything here is
  fixed for the instance: storage, the launch root, limits, root grants ..."; a resume with new
  limits is refused as "resident application policy conflict"), so the launch is a **new instance**,
  `voice-intake-20261016`, with all three residents cold (neither had a published package). Steward
  and Researcher keep Codex's minimal-launch specifications verbatim except one Researcher sentence,
  changed on Rawley's instruction: "Start the supplied A/B campaign automatically once admission and
  the explicitly prepared launch prerequisites are ready." became "Start the supplied A/B campaign
  only when Rawley directs it through the mail room; until then it stays held." Everything else
  specific to this launch is in steering. Because the instance is new, Intake holds a
  `project_coordination` slot for `rlm-adk-ios` (registered at launch), opens that coordinate for each
  ticket and dispatches its delegate from it; capacity target 2 within ceiling 3; limits 3/3/2. Merges:
  Intake's delegate may merge its own rlm-adk-ios pull request once every required check passes.
  Authoring: desktop `~/.config/rlm-adk/launch/voice-intake-20261016/` (`launch.author.json`, its
  generator `make_voice_intake_instance.py`).
- **Host items landed.** `inbox list --json` and `--in-reply-to` (rlm-adk #1352), the delegate `cwd`
  doc fix (#1348), and dossiers for the outside-repository delegate notice (#1350) and partial quiesce
  (#1351). The allocator is updated to main `8c0a94c9`, reviewed beforehand (no blockers; the Researcher
  needs a fresh measured qualification after #1346/#1354).
- **Partial quiesce, in progress (Codex, branch `codex/coordinate-trial-holds`, not merged).** A native
  experiment will run under a lease with no production hold: continuous residents (Steward, Intake)
  keep running; each experiment occupies one of the Researcher's project coordinates until cleanup;
  the Steward holds or releases it with `hold_branch` / `release_branch` under capacity governance;
  the Researcher pauses and resumes its own experiment (`pause_live_test_experiment`,
  `resume_live_test_experiment`); a held trial stays held until every hold is released. No new duty
  kind. This removes the risk in section 4 that a Researcher experiment pauses Intake, once installed
  by a carry-forward `allocatorctl update`. Until then the old behaviour holds. Note for that work:
  #1354 makes its plan's 60-second interval impossible (review budget must leave 6 s; measured review
  latency is 129-147 s) and conflicts textually in `request_model.py`.
- **Voice portal.** The ticket-to-mail bridge and reply polling are built (`issues.mail_ticket`,
  `poll_replies`, `POST /issues/<id>/mail`, `POST /issues/replies`; config `intake_recipient`,
  `intake_mail_dispositions`, `intake_poll_s`), and the work outbox posts with `--to work_recipient`
  and parses the mailbox receipt.

## 1. Where things stand (observed)

| Item | State | Evidence |
|---|---|---|
| Installed allocator | Release of merge **e4664bb8**, one PR (#1346, native live-test coordinator context) behind main; no schema boundary in between, so a plain carry-forward `update` applies | `readlink ~/srv/rlm-adk/current`; `git log e4664bb8..origin/main`; `rlm_adk_docs/host_release_cone.md` L147, L323-325 |
| Service | Stopped cleanly 2026-10-15 06:04 EDT; no hold or pause | `allocatorctl status --json` |
| Active launch | `rlm-launch-v2`, deployment `research-ab-mailroom`, instance `research-ab-20261014`: `stwlab` (Steward, resourcing only, holds `capacity_governance`) and `rsch01` (Researcher, holds all six coordination slots); limits 2 residents, 1 child, capacity ceiling 2 | status; `minimal-research-launch/LAUNCH_READINESS.md` |
| Mailbox | Installed `inbox post` takes `--to <id>` (required, repeatable) and may name a declared resident not yet created | installed `inbox post --help`; `rlm_adk/host/cli.py` L2659-2685 |
| Delegate routes | Last receipt `ready`: claude_code (claude-opus-5-5, Max subscription OAuth), codex (gpt-6.1-sol), grok (grok-4.7-build) | `<root>/route-readiness/20261015T014804Z-update-60bd4d69.json` |
| Intake | Proposal merged here (PR #23); never declared in any launch; the minimal launch says "Intake is deferred" | `intake-resident-proposal.md`; `MINIMAL_STEWARD_RESEARCHER_LAUNCH.md` L61-63 |
| Researcher | Running as the minimal launch; A/B campaign held after native qualification 12 was refused; broad proposal superseded | `minimal-research-launch/OPERATING_HANDOFF.md` L85-89, `LIVE_BRINGUP.md` L3-4 |
| Ticket | `bug-20261015-01` in the intake issues log, triaged feature_request / app_ui / P3 / needs_detail; nothing reads the log | desktop `~/.local/rlm-adk/stores/intake/issues.jsonl`; `intake-issues-log.md` |
| Desktop tools | `claude` 2.1.289, `codex` 0.161.0, `grok`; `gh` logged in as RawleySM; desktop reaches Spark-A over SSH; no rlm-adk-ios checkout | checked 2026-10-15 |

## 2. Answers to the questions

**Is allocatorctl outdated?** Barely. The desktop runs e4664bb8; main adds only #1346 (native live-test
coordinator context). Everything Intake needs (instance mailbox #1317, assignment v2 #1318-#1320,
declared children) is installed. Updating is still worth doing before the Researcher's next live
test, because #1346 touches that code: `allocatorctl pause` (to keep residents parked), then
`allocatorctl update --repo <clean checkout> --commit 48df8d54dab6391637e44f50312ada67fc61d13c`
(`host_release_cone.md` L753-757), which probes every delegate route before stopping the service.

**Does Intake need a duty?** No; the earlier decision stands and current main supports it.
- The duty registry is a closed list of six kinds (`rlm_adk/residents/duties/contracts.py` L17-24).
  Operator intake was withdrawn as a duty in favour of the instance mailbox
  (`proposals/operator_mailbox/PLAN.md` L33; `rlm_adk_docs/projects/README.md` L66).
- "Watch the issues log and triage" fails the admission test
  (`proposals/host_responsibility_catalog/DUTY_EXTENSION_REVIEW.md` L39-64): it is a domain selection
  rule authored code can implement, so it belongs in Intake's mission and skills.
- "May start a live test" is already a baseline operation: `livetest.*` calls are routed with a
  binding-identity check and no duty check (`rlm_adk/orchestrator.py` L1543-1566). Gating it would be
  the "invented restriction on an adequate baseline operation" the review rejects (L47-48).
- What Intake does need is an **assignment it already qualifies for**: a `project_coordination` slot
  for the repository it changes, here `rlm-adk-ios` (not the `voice-requests` repo of the original
  proposal, whose deliverable was a markdown note). A slot is optional (delegates can work from a
  workshop) but it makes Intake's branches and delegates visible in capacity accounting
  (`rlm_adk_docs/projects/project_branches.md` L80-82, L119-121, L272).

**What would I change in rlm-adk main?** Only generic host mechanics; none is required for a first
demonstration.
1. `allocatorctl inbox list --json` (with an `in_reply_to` filter): today the list is text only
   (`rlm_adk/host/cli.py` L2686-2692), so the voice-portal service would have to parse replies.
2. A notice (not a refusal) when a delegate's `cwd` lies outside every granted repository: today
   nothing checks it (`rlm_adk/agent_delegate/policy.py` L295-305) and commits outside granted
   repositories are not recorded (`rlm_adk_docs/agent_delegate/agent_delegation.md` L223-254).
3. Fix the stale line that calls the delegate `cwd` "a disposable temp directory"
   (`agent_delegation.md` L796).
4. Later, a partial quiesce: a Researcher experiment pauses the whole production service, Intake
   included, under one lease (`cli.py` L2700-2716). Larger design work; not needed for build 19.

**Does it change if Intake or the Researcher implements it, or if Intake runs a live test?** Your
reading matches what the host supports, with one boundary made explicit:
- **Intake, through a delegate:** changes to the app, the talk tier, the skill service or docs, which
  are proven by their own tests, CI and device checks. Intake calls
  `llm_query(prompt, agent="claude_code", cwd=<worktree of rlm-adk-ios>)`; delegates run at `full`
  autonomy by default (`policy.py` L319-330), use the host's `gh` credentials, commit as
  `resident <id>` (`agent_delegation.md` L767-774), and return files changed, commits and a report
  (`agent_delegation.md` L130-195). These changes are not RLM-ADK topology and need no RLM-ADK live
  test.
- **Researcher, through a live test:** changes to RLM-ADK topology itself (the resident tree, duties
  and slots, launch documents, resident instructions or steering, model and gateway routing, host
  code). Any resident may call `start_live_test_experiment` (`repl/background_kernel.py`
  L2157-2159), but a separate coordinator unit runs the trial and production is paused for its
  duration (`rlm_adk_docs/native_live_test_supervision.md` L44-63, L77-119). The Researcher owns
  that because it runs one experiment at a time and its mission is evaluation, not deployment.
- **Should Intake run live tests itself?** No. If Intake started them too, two residents would
  compete for the single production lease, and Intake would pause itself.
- **The threshold**, as a rule Intake can apply: a request is Intake's when the change stays inside a
  product repository (rlm-adk-ios today) and its acceptance is that repository's tests plus a device
  or service check. It goes to the Researcher when it would change anything listed under
  "Researcher" above, or when its acceptance needs a running RLM-ADK instance. The handoff is mail:
  Intake sends `send_message("rsch01", <ticket and why>)` and replies to the operator `status:
  escalated`. Both proposals currently forbid this (Intake "does not forward the mail yourself";
  the minimal Researcher takes direction only from Rawley), so it is a steering change in the launch
  document, not host code.

## 3. What has to be done

**D. Decisions for Rawley**
- D1. Topology. Intake cannot join the running instance: its limits (2 residents, 1 child) and its
  set of coordination slots are fixed (`launch_config_preparation.md` L120-121). Options: (a) a new
  instance with `stwlab`, `rsch01` (warm starts from their packages) and `intk01` (cold); or
  (b) keep the Researcher lab as it is and run a separate instance for Steward plus Intake. Either
  way the held A/B campaign stays held unless you say otherwise.
- D2. Delegate kind: Claude Code (your preference) instead of the proposal's codex preference.
- D3. Merge authority: may Intake's delegate merge its own rlm-adk-ios PR after checks pass, so the
  build reaches you with no click? (Without it, every change waits for a merge by you or a session.)

**Phase 1: host (rlm-adk), optional before the demonstration**
- Carry-forward update to 48df8d54 (paused first).
- PRs for items 1-3 of "What would I change in rlm-adk main?" above.

**Phase 2: voice portal (rlm-adk-ios backend)**
- Ticket-to-mail bridge: after triage, the skill service posts each `ready`, `request` or
  `needs_detail` ticket to the configured recipient (`work_recipient`, refusing when unset):
  `allocatorctl inbox post --to intk01 --file <ticket text>` with first line
  `voice-portal ticket_id=<id> kind=<kind> priority=<p>`, then the words, the triage and the context.
  Plain text, never JSON: a JSON body of shape `{"kind":"job_request"}` creates cloud job demand
  (`rlm_adk/residents/jobs.py` L581-610). The post runs on the desktop as the service user.
- Reply polling: `inbox list --to-operator`, matched on `in_reply_to`, appended to the issues log as
  the reserved `status` event (`accepted`, `answered`, `escalated`, `failed`, with any artifact).
- The work outbox gains `--to` (`backend/voice_portal/outbox.py` L156 still posts without it).

**Phase 3: launch authoring (desktop, `~/.config/rlm-adk/launch/…`)**
- Clone rlm-adk-ios to the projects area and register it (`grants.project_registration`,
  `register_at: launch`; the host never clones, `projects/project_enrollment.md` L17-22).
- Intake node `intk01`, cold, under the Steward; `requests.project_ids` rlm-adk-ios and
  project-work-registry; one `project_coordination` slot for rlm-adk-ios; limits raised (3 residents,
  2 children) and the capacity target one higher.
- Intake mission, adapted from the proposal: wake on mail; for an app-scope ticket, create a branch
  in a worktree, delegate with `agent="claude_code"`, require CI green, run the direct-install build
  (`gh workflow run direct-install.yml`), publish it to Spark-A's delivery folder with
  `scripts/direct_install.py package` and a `latest.json`, merge if D3 allows, reply with the build
  number, PR, commit and install link; for a topology-scope ticket, escalate to `rsch01` (section 2).
  Fix the proposal's known defects: readonly delegates asked to write files (R-06), the stale
  "no coordination slot" sentence, target 7 and holder `stwref`.
- Researcher steering: accept escalations from `intk01` and answer the operator with the experiment
  plan.
- `allocatorctl preflight --launch <doc>` (read-only, spends a few tokens on route probes), then
  `allocatorctl relaunch --launch <doc>`.

**Phase 4: the build-19 run (supervised)**
- Post `bug-20261015-01` with answers to its two detail questions: show the banner only when a newer
  build is published; the app reads one fixed address
  (`https://rlm-adk-spark.tail0ec4a5.ts.net:8443/latest.json`) that names the latest build and its
  install link.
- Supervise as a live run: checks every 60 seconds until progress is evident, never more than 300
  seconds apart, with model-call and token deltas (thinking included) for Intake and its delegate.
- Acceptance: CI green with a simulator screenshot of the banner; build 19 on the install page with
  `latest.json`; Intake's threaded reply naming PR, commit, build and link. Build 18 has no banner,
  so you install 19 from the link once; the banner itself is proven when the next build (or a test
  `latest.json` naming a higher number) is published.

## 4. Risks

- Native live tests are not yet accepted on installed source with real providers
  (`native_live_test_supervision_qualification.md` L5-6, L89-90); the Researcher's campaign is held.
  Escalated tickets would queue until that is resolved.
- A Researcher experiment pauses Intake too (one production lease).
- Delegates at `full` autonomy use the host's own `gh` and SSH credentials; the mission, not the
  host, is what limits them to rlm-adk-ios and the delivery folder.
- Intake replies and ticket status depend on parsing `inbox list` text until item 1 lands.
