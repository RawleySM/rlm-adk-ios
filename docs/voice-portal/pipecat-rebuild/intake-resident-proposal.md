# Proposal: a declared intake resident for voice-portal requests (next cycle)

Prepared 2026-10-14 for the cycle after 14d. Read-only review of rlm-adk `origin/main` at d6a08710
(assignment v2: PRs #1318 to #1320; instance mailbox: PR #1317, docs #1334) and the unactivated
cycle-15 bundle on the desktop (`~/.config/rlm-adk/launch/cycle15/`, still `rlm-launch-v1`).
Nothing here changes rlm-adk; it proposes authoring for the next launch document and a matching
change in the voice-portal skill service.

## 1. Problem

The voice portal's work requests (use case 6) post operator mail. Today they go to `root`, which
is the Steward: a resident whose standing work is long-running and whose scope is not "answer
Rawley's questions". On the installed 2026-10-08 release the post lands in the old run-level root
inbox and waits for the Steward's own tool boundary; it sat undelivered for the whole 17-minute run
on 2026-10-14. On a current release it would still interrupt the Steward's work with mail it should
not be handling. Rawley's direction: do not distract residents doing long-running work or projects;
route requests to a single resident that answers from the global registries when it can, and
hands the rest to an agent delegate.

## 2. What the landed assignment registry says about this

The typed duty registry (`rlm_adk/residents/duties/contracts.py`, generated reference
`rlm_adk_docs/host_assignment_reference.md`) is a closed union of six kinds:

| Kind | Holder rule | What the host decides for the holder |
|---|---|---|
| `capacity_governance` | root only | project capacity target, branch holds, slot directives, request pause/resume, future coordinator assignment |
| `project_coordination` | one slot per configured project | who may open that project's next branch |
| `project_registration` | root only | who may enroll configured repositories |
| `model_routing_control` | root primary, scoped non-root delegate | gateway pin and delegate selections |
| `installed_release_activation` | harness-project resident | who may queue an installation request |
| `project_promotion_execution` | one slot per selected project | who may queue a head/base promotion |

Operator intake is deliberately **not** a duty. The intent-first dossier briefly proposed a
`run_inbox` kind ("the resident that receives operator requests addressed to no resident",
8c6a4649) and withdrew it the same day in favour of the instance mailbox (0d36c515): every post
names its recipients, there is no unaddressed post, "root is an explicit recipient, not an inbox
duty", and mail is a baseline interface of every continuing resident. The duty extension review
(`proposals/host_responsibility_catalog/DUTY_EXTENSION_REVIEW.md`) gives the admission test a new
kind would have to pass: a generic host-owned decision an authored method cannot establish, a
separately variable holder relationship distinct from grants and baseline interfaces, an owner
acceptance point, pending-work semantics, and separable evidence. An intake resident fails that
test on purpose: which resident answers Rawley is an authoring choice (the launch names the
recipient id; the skill service posts `--to` it), not a host decision at an acceptance point.

So the proposal is a **declared resident with grants and a mission**, with **no new duty kind**
and no duty slot held. What constitutes its assignment is exactly what the launch docs call
grants and baseline: `spec.requests.project_ids` for read access, `spec.instructions` for the
mission, mail delivery by address, and the delegate route it may use.

## 3. The resident

Working name **Intake** (package key minted by the builder, for example
`intake_<id>`; give it an explicit six-character id so the skill service can be configured with it
before activation, say `intk01`). Declared as a child of the root in the v2 launch tree, `start:
"cold"` (a new resident in a new instance must form; there is no package to warm from),
`hold_until: "parent_running"` so it exists as soon as the Steward runs and does not wait on a
published package of its own.

### 3.1 Mission (spec.instructions, enduring)

1. You answer Rawley's requests that arrive as instance mail (`from: operator`). Each message
   begins `voice-portal operation_id=<id> kind=<kind>` followed by the request text; some are
   spoken through a phone and transcribed, so read them charitably.
2. Reply with `send_message("operator", text, in_reply_to=<message_id>)`. Every request gets a
   first reply within your current turn: either the answer, or an acceptance that says what you
   will do and that it is not done yet. Never report work as done without naming the artifact
   (a file path, a register record id, a commit, a delegate report id).
3. Answer from the global registries when the question is about status: the project work
   register (`project-work-registry`, read through its README and `project_work.reader`:
   plans, branches, results, checkpoints, outputs per project), the granted project
   repositories' authored work records, `inspect_resident` for a resident in your lineage, and
   `read_signals` for recent activity notices. Quote record ids and dates. Do not guess.
4. When the request needs digging or producing (research, a comparison, a document, a code
   change), hand it to an agent delegate from your REPL: `llm_query(prompt, agent="codex" |
   "claude_code" | "grok", autonomy=..., cwd=<granted repository>)`. Put the whole task, the
   operation id and the expected deliverable path in the prompt. When the delegate returns,
   check its report (success, files changed, commits) and reply to the operator with the
   artifact or the failure. Prefer `readonly` autonomy for questions, `scoped` for products.
5. Do not start projects, open branches, change capacity, enroll repositories or create
   residents. If a request needs that, reply that it needs the Steward or Rawley, and say why;
   do not forward the mail yourself.
6. Keep a request ledger in your workspace (one JSON line per operation id: received, replied,
   delegate ids, artifacts) and publish nothing else as a package unless a method is reused.
7. Between messages, wait on `resident_wait` for mail; do not poll, do not run background work.

### 3.2 Steering (launch-specific)

Where the register and repositories live on this host, the delegate kinds that are ready on this
gateway policy, and the one-line format the skill service expects back (a leading `status:` word
from `answered | accepted | failed | needs_steward`, then prose).

### 3.3 Requests and grants

```json
{
  "id": "intk01",
  "start": "cold",
  "hold_until": "parent_running",
  "spec": {
    "name": "Intake",
    "instructions": "<section 3.1>",
    "context": {"data": {}, "manifest": {}},
    "requests": {
      "source_grant_ids": [],
      "project_ids": [
        "project-work-registry",
        "steward-reference", "prayer-life-c12", "scripture-c12",
        "sermons-c12", "discipleship-vision-c12", "scripture-enrichment-c15"
      ],
      "skill_selectors": []
    }
  },
  "steering": "<section 3.2>",
  "children": []
}
```

Project ids must be within the parent's selected access (the Steward already requests all seven
in cycle 15), so preflight accepts the child. The grants give read access and a checkout; they
confer no duty (no coordination slot, no promotion slot), which is the intended shape: Intake
reads registers and repositories, and delegates do their work in the delegate's own cwd inside
those checkouts under Intake's git identity, so returned-work commits are attributed to Intake.

### 3.4 Host capabilities to assign, and which not to

| Capability | Assign? | Reason |
|---|---|---|
| Instance mail (`send_message`, `read_messages`, mail frames, message wakes) | baseline, nothing to declare | The whole mechanism; the skill service posts `--to intk01` and reads `inbox list --to-operator` |
| Project read access via `spec.requests.project_ids` | yes, all seven | Status answers come from the register and authored work records |
| `inspect_resident`, `read_signals`, `resident_wait` | baseline | Lineage-scoped resident state and activity; idle waits |
| Agent delegation (`llm_query(agent=...)`) | baseline, needs ready delegate routes | The "dig further" path; gateway policy cycle14g has claude_code, codex and grok ready |
| Finite children (`llm_query` without `agent`) | baseline | Cheap summarisation of a register read before replying |
| `capacity_governance` | no (root only) | Intake must not change targets or holds |
| `project_coordination` slots | no | Opening branches is project work, not intake |
| `project_registration` | no (root only) | Enrollment stays with the Steward |
| `model_routing_control` delegate slot | no | Nothing in the mission needs route changes |
| `installed_release_activation` | no | Harness installation is not a voice request |
| `project_promotion_execution` | no | Merges stay with project holders; Intake reports, delegates produce |
| SRV cloud jobs (`submit_job`) | no, keep `controls.cloud_job` unset | Spend boundary; a delegate is enough |

Assignments block of the launch (`resident.assignments`, mode `initialize`): all six kinds named,
`capacity_governance` and `project_registration` enabled with holder `stwref`, coordination and
promotion disabled or configured as today without Intake, routing and activation disabled. Intake
appears nowhere in `adoptions`.

## 4. Voice-portal side (rlm-adk-ios, skill service)

1. Config: `work_recipient: "intk01"` (and a fallback refusal when unset; never default to root).
2. `outbox.dispatch`: `allocatorctl inbox post --to <recipient> --file <directive>`; parse
   `posted message_id=<id> to=[...]`; record `message_id`.
3. `outbox.reconcile`: `inbox list` per-recipient status (`mail_delivered`) advances
   `directive_posted` to `delivered`.
4. New `outbox.poll_replies`: `inbox list --to-operator`, match `in_reply_to == message_id`,
   store the reply text, map the leading status word to `acknowledged | artifact_available |
   failed | needs_steward`; the `request_work` tool speaks the reply on the next check-in and the
   talk tier's late-result rule applies (respect current mode and turn).
5. Case 6 assertions gain two layers: delivered within one Intake tool boundary, and a reply
   threaded on the message id within the case window for a status question; a research request
   asserts `accepted` first and `artifact_available` later with a named artifact.

Prerequisite: the desktop runs a release with the mailbox (`allocatorctl update` to current
main; the installed release's `inbox post` has no `--to`).

## 5. Launch generator change

In `make_launch_cycle15.py` (or its successor for the v2 document): append the node of section
3.3 to the root's `children`, add the Intake mission and steering strings beside the existing
`REGISTER_BULLET` texts, and keep `grants.project_registration` as is (the seven repositories are
already registered at launch). Then `allocatorctl preflight --launch` must show the child
resolved with its seven project requests inside the Steward's access. The v2 cutover itself
(instance mode `new`, fresh resident roots, `format: rlm-launch-v2`, `assignments.initialize`)
is described in `rlm_adk_docs/assignment_v2_runtime_handoff.md` and is a precondition the
cycle-15 bundle does not yet meet.

## 6. Open choices for Rawley

- Depth: child of the Steward (proposed; the Steward gets `operator_mail` activity notices when
  Intake receives mail, which is useful visibility) or a second root-level sibling (not possible:
  the tree has one root).
- Whether Intake may forward a request to a project resident by mail when it is clearly that
  resident's domain, or always goes through a delegate (proposed: delegate only, to honour "do not
  distract").
- The reply status vocabulary (four words proposed) and whether the phone should speak delegate
  progress or only the final artifact.
