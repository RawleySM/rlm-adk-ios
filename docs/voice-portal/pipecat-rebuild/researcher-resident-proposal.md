# Proposal: Researcher, a continuing owner of recursive improvement

**Current preferred next-cycle option:** [Minimal Steward + Researcher launch](MINIMAL_STEWARD_RESEARCHER_LAUNCH.md).
That later proposal declares only those two outer residents, assigns every outer
project-coordination slot to Researcher and evaluates the existing domain topology
inside trials. In that current option Rawley messages each resident directly and
Steward manages resources only; the older research-management split below is historical. This broader document and its JSON companions retain the earlier
Intake-inclusive option; do not use those fragments unchanged for the minimal launch.

Prepared 2026-10-14 for the next allocator launch. **Proposal only: no deployment,
runtime mutation, project enrollment or new host implementation.** Source baseline:
RLM-ADK merged implementation at `d6a0871058fe491a7f06d1b906ccd3a281724d36`
(native supervision PRs [1338](https://github.com/RawleySM/rlm-adk/pull/1338) and
[1339](https://github.com/RawleySM/rlm-adk/pull/1339)). Source inspection used the
launcher-modules checkout. Statements about the installed allocator are dated
observations from this conversation, not a promise about its next state.

Companions:

- [Researcher node](researcher-resident.node.json): a typed resident-node proposal.
- [Launch assembly fragment](researcher-resident.launch-fragment.json): annotated
  additions to combine with the full launch, **not an executable launch file**.
- [Researcher open items](Researcher_Open_Items.md): foundations, owners and
  acceptance evidence for the next update and first experiment.
- [Intake proposal](intake-resident-proposal.md): the sibling resident's separate
  responsibility for Rawley's requests.

## 1. Purpose and the useful improvement loop

Researcher maintains a continuing, evidence-backed portfolio of changes that help
**Rawley Stanhope and the system glorify God and spread the Good News through more
ambitious, impactful and faithfully executed work**. The system includes Rawley's
attention, judgment, relationships, understanding and ability to act. Producing
more tokens, agents, commits or experiments does not itself accomplish that mission.

Researcher uses external coding-agent delegates to investigate opportunities,
develop candidates and prepare experiments. It then works through the portfolio
as the Steward prioritizes work and makes project capacity available. Independently
hosted native live-test coordinators run the accepted experiments through settlement.
Researcher evaluates the returned evidence, recommends adoption and learns from
both successful and unsuccessful attempts.

Useful examples include a topology that turns a vague goal into a tested reusable
method; model selection that improves difficult interpretation without making
Rawley repeatedly repair results; instructions that lead residents to finish and
deliver a substantial project; and host changes that remove a demonstrated obstacle
to these outcomes. These are proposed research directions, not observed successes.

This is recursive self-improvement when evaluated changes become reusable methods,
selected configurations or installed source, and subsequent work demonstrates the
benefit. An endlessly growing suggestion list is not that loop.

## 2. Topology and distinct ownership

```text
Continuous allocator, R_srv
  Steward — root resident; portfolio priority and project capacity governor
    Intake — residency depth 1; operator requests and voice-requests project
    Researcher — residency depth 1; improvement portfolio and sponsoring project
    Existing domain residents — retained unless separately changed

Independent experiment execution, R_coord
  native d0 coordinator — retained session/REPL, finite review invocations
    native d1df1 ... d1dfN — fresh finite monitors at each checkpoint

Isolated tested topology, R_trial
  its own root/residents, projects, capacity, stores and recursive activity
```

Researcher and Intake are siblings under the Steward. An experiment coordinator
is not another continuing child resident of Researcher. It is d0 within its own
execution, even when Researcher initiated it at residency depth 1. Its monitors
are native finite children, not external `agent_delegate` jobs. Depth labels do
not replace run, session, dispatch, resident and project identity.

| Actor | Owns | Does not acquire merely by taking this role |
|---|---|---|
| Rawley | Mission, priorities, judgments requiring his experience and discernment | An obligation to manually review every routine research action |
| Steward | Portfolio priority, interruption windows, production capacity, branch directives and assigned registration duties | Automatic semantic acceptance of an experiment |
| Researcher | Opportunity portfolio, delegate briefs, candidate contracts, outer project attachment, initiation, outcome reconciliation and adoption recommendations | Capacity governance, deployment or merge authority |
| External delegates | Bounded investigation, source changes, tests, launch-contract preparation and original reports | Native lifecycle ownership just because they authored the experiment |
| Native coordinator model | Interpreting monitor reports and global evidence; experiment decisions | A right to alter the experiment's accepted objective or judge success mechanically |
| Coordinator process and existing owners | Real wakes, validation, exact trial lifecycle, deadline enforcement, settlement and restoration | Research prioritization or human benefit judgments |
| Finite native monitors | Assessment of their current resident/project packets | Overall stop/continue authority |
| Configured promotion/activation holders | Accepting exact promotion or installation requests through their existing typed duties | Ownership inferred from a resident's name or mission |

Source contracts: [native supervision](https://github.com/RawleySM/rlm-adk/blob/d6a0871058fe491a7f06d1b906ccd3a281724d36/rlm_adk_docs/native_live_test_supervision.md),
[project branches](https://github.com/RawleySM/rlm-adk/blob/d6a0871058fe491a7f06d1b906ccd3a281724d36/rlm_adk_docs/projects/project_branches.md).

## 3. Minimum launch assignment

Propose name **Researcher**, explicit resident ID `rsch01`, package key
`researcher_rsch01`, cold formation and `hold_until: parent_running`. Check the
ID and package namespace before activation. Later cycles can warm from an exact
published Researcher package; no such package is claimed here.

Start with one authored project, **`runtime-improvement`**, and assign Researcher
its `project_coordination` slot. The Steward continues to govern capacity and
configured project registration. Researcher requests ordinary access to the
research project, the harness project and `project-work-registry`. The proposed
harness ID `rlm-adk` must be resolved against the real registry; do not create a
duplicate identity for the same repository. Both parent and child need the
appropriate access. Add other domain repositories or evidence sources only when
their actual paths and intended research are identified.

| Assignment/interface | Initial choice | Reason |
|---|---|---|
| `project_coordination(runtime-improvement)` | Researcher | A real capacity-consuming outer coordinate for research work and experiment sponsorship |
| Capacity governance and project registration | Steward, preserving intended root assignments | Researcher requests capacity and resources rather than manufacturing them |
| Native experiment start/inspect/outcome exports | Existing native broker/skill interfaces | An accepted native caller can initiate; no new research duty is required |
| Instance mail, signals, waits and delegate calls | Existing baseline interfaces and available routes | Portfolio handoffs, outcome reconciliation and capable delegated work |
| `model_routing_control` | No new Researcher slot initially | Candidate model policies belong to trial inputs; production route adoption is separate |
| `project_promotion_execution` | No new Researcher slot initially | Recommend to the configured holder; preserve exact source/PR qualification |
| `installed_release_activation` | No new Researcher slot initially | Recommend to the configured holder; independent installation execution is still required |

Research judgment, opportunity ranking and experimental methods belong in authored
project code and resident skills. They do not justify another host duty kind.
The existing six-kind ledger already describes the variable host relationships
needed here. See the [generated duty reference](https://github.com/RawleySM/rlm-adk/blob/d6a0871058fe491a7f06d1b906ccd3a281724d36/rlm_adk_docs/host_assignment_reference.md).

The JSON fragment is intentionally an **assembly instruction**, matching the
Intake package's convention. Its explanatory keys and capacity policy are not
launch-schema fields. Union project IDs, append the new slot to the existing
coordination adoption, and preserve all six kinds and all other slots. Compute
one whole-topology capacity target; do not copy Intake's target of seven and
independently increment it in several files. Do not change the target merely
because an additional resident exists: capacity counts admitted project branches.

Researcher can be admitted with no experiment capacity initially. It can establish
its portfolio and then wait for the Steward's first bounded directive. A capacity
refusal is a reason to wait for a signal, not to repeat submissions.

## 4. Authored portfolio and delegate deliverables

Use the existing project work register for project-level plans, outputs and
evidence references. Store research-specific records in the research repository;
do not add hypothesis columns to the host assignment ledger or a competing host
work registry. Suggested layout, to be created by the resident/delegate:

```text
README.md                          # how to work and use the existing register
research/opportunities/<id>.json    # current hypothesis and next decision
research/opportunities/<id>.md      # reasoned case, alternatives, counterevidence
research/attempts/<attempt-id>/     # immutable inputs, request/handle, evidence index
research/decisions/                 # acceptance, rejection, adoption and follow-up
research/methods/                   # reusable authored methods and evaluations
```

Suggested opportunity fields: ID, title, motivating observation, affected work or
person, proposed mechanism, baseline references, expected useful output, evidence
against the hypothesis, dependencies, candidate variants, effort/provider/disruption
estimate, Steward directive, current decision and next action. Each attempt adds
exact source commits, launch and policy digests, package/data versions, original
delegate outputs, sponsor coordinate, operation ID, accepted handle, coordinator
and trial run identities, evidence references and disposition. These are proposed
authored data, not new public host schemas.

Separate opportunity state (`proposed`, `prioritized`, `deferred`, `investigating`,
`evaluating`, `accepted`, `rejected`, `superseded`) from experiment facts (`pending`,
accepted/running, settled, cleanup pending, outcome consumed). An inconclusive
experiment can be cleanly settled while its opportunity still needs investigation.
Use atomic/versioned project writes and stable attempt IDs; reconcile saved handles
on reentry instead of relying on REPL memory. Lost start returns replay the same
operation ID with the same bytes. A changed candidate gets a new attempt.

Every delegate brief should request:

1. A source-backed diagnosis and an explanation of the useful outcome a change
   could enable, including credible alternatives and contrary evidence.
2. A bounded implementation or configuration change in a named worktree, with
   exact base/head identities. Use Retrace and required F6 for RLM-ADK source
   changes; tests must exercise the claimed behavior.
3. A baseline and candidate launch contract, selected routes, exact promoted
   package/data references, test workload, acceptance evidence and stop conditions.
4. A native request conforming to the existing closed contract, with original
   digested evidence references and an applicable monitoring qualification.
5. A report identifying what was read, changed and tested; unresolved assumptions;
   and evidence needed before the Researcher should recommend adoption.

Delegate results return to the Researcher's REPL. Researcher must inspect and
surface the important findings through tool responses for its own model to judge
them. A file's existence or a delegate's success label is insufficient. External
delegation uses the actual supported individual `llm_query(..., agent=...)` route;
this proposal does not invent batched external-delegate semantics.

## 5. What an experiment must establish

Before starting, state the hypothesis, useful deliverable, baseline, changed
variable(s), acceptance conditions and disconfirming evidence. Prefer one mechanism
per initial comparison, while allowing a justified bundled topology change when
its interaction is the hypothesis. Compare equivalent input snapshots and tasks;
record provider variability, cache/usage coverage, route fallbacks and warm-package
versions. A failed dependency is not evidence against an unrelated hypothesis.

Measure output correctness and completeness, usable artifacts or methods, work
Rawley can actually act on, repair effort, latency, operational reliability and
resource use. Record Rawley's feedback when a claim depends on his experience.
Use reference tasks and later held-out work to distinguish a reusable improvement
from prompt tuning to one trial. Avoid a universal convergence score or any
numeric proxy purporting to measure glorifying God or spiritual fruit.

Start with one actual-provider experiment at a time and a measured **300-second
native checkpoint interval**, within the existing 60–300-second contract. The
coordinator's ordinary compaction threshold is **150,000 tokens**. Requalify the
actual coordinator policy, environment, source and maximum expected monitor load;
qualification is not transferable merely because a model has the same name.
Retain unknown compaction latency when a path did not occur in qualification.
First live acceptance also needs the established external supervision procedure.

The coordinator receives the objective and acceptance/stop references in its
native request. Its finite monitor children receive bounded source-prepared
packets in their own `LlmRequest`s. Their original typed assessments return to
the coordinator REPL and must become model-visible before it authors the next
decision. Deterministic code validates and commits that decision, arms real wakes,
and invokes lifecycle owners. Researcher receives the eventual outcome; it should
not run a duplicate monitoring loop around an accepted independent coordinator.

Usage accounting includes research delegates, coordinator, monitors and trial
work, with separate run/store scopes and deduplicated original occurrences.
Cached input and reasoning counters can overlap other counters; unavailable
coverage remains unknown. Hosted API charges can be recorded where reported;
subscription usage snapshots describe capacity and must not be converted into
invented per-token dollar costs.

## 6. An experiment sponsored by a project, containing projects

There are **three different relationships**, not recursively shared capacity:

| Scope | Capacity and identity | Who closes it |
|---|---|---|
| Production sponsor | Researcher's `project:runtime-improvement`, one attachment against `R_srv`'s instance ledger | Original Researcher attachment owner through `release_project`, or existing retirement settlement |
| Independent coordinator | One experiment operation, its own `R_coord` and finite monitor work; not automatically a production project attachment | Coordinator driver and its existing recovery/lifecycle owners |
| Trial projects | Trial-local project pointers and attachments against the separate trial instance's configured capacity | Trial-local owners, then exact trial settlement |

A runtime project branch is not a Git branch. Multiple candidate Git worktrees
can exist under one research campaign, but they do not create additional runtime
entitlements. Conversely, a delegate's cwd does not enroll it in a project
coordinate: dispatch it from the accepted coordinate. Record both sponsor identity
and actual target repository/effects when changing harness code from the research
project.

The smallest initial policy is **one active attempt under the one sponsor project**.
One project ID cannot stand for several independent simultaneous outer attachments.
Do not allocate a new project identity for every retry. If future parallel
campaigns need independently controlled capacity, configure actual separate
projects/slots deliberately. The current slot universe is fixed per instance;
registering another repository does not enlarge it.

The trial uses private cloned worktrees, stores and package selections; its project
IDs may have the same labels as production but are scoped by instance, run and
canonical store. Record explicit mappings to source repositories and commits.
Promoted package/data reuse does not imply shared mutable working directories.
Include seed hashes and collection limits in the comparison.

**Current source does not make the independent experiment an outer attachment's
settlement dependency.** The native broker retains the actual caller's run/session,
tool/submission and REPL identity, but the request has no explicit sponsorship
capacity contract. Record the original attachment reference alongside the immutable
request; do not claim an authored record enforces lifecycle ownership. For initial
attended use, Researcher retains the outer attachment until the returned exact
outcome is settled, reconciles it after restore and only then releases it.

This leaves an important qualification requirement: an explicit branch close,
resident retirement or restart can end/recover the outer attachment while the
independent coordinator remains responsible for a trial. The smallest proposed
future host addition, **only if unattended capacity accounting requires it**, is
a reference from the existing attachment settlement mechanism to the existing
accepted experiment outcome. It would keep external obligations visible through
settlement and recovery, not introduce another assignment registry. Its acceptance,
close/cancel and retirement behavior must be specified and tested before claiming
automatic end-to-end sponsorship.

Holding the outer branch is not a trial stop command. There is currently no public
native cancel export beside start/inspect/outcome. A cancel directive needs a
qualified coordinator-owner path or an external operator. Do not substitute
`release_project` for stopping the trial or call recovery against a live owner.

Production pause preserves an attachment while stopping new production reasoning;
it also pauses Intake and the Steward. Use an agreed interruption window and report
that availability consequence. Begin with pause quiescence; stopping/restarting
the initiating runtime requires additional sponsorship recovery proof. New nested
experiments targeting the same allocator are not an initial use case: the existing
exclusive lease must remain authoritative.

## 7. Updating allocatorctl from a native run

The implemented activation duty already supplies a useful split:

1. The assigned activation holder submits `request_release_activation` with the
   exact main-line commit, expected installed release, qualification references
   and optional captured launch bytes. It receives a durable queued request.
2. An **independent external foreground process** consumes it through
   `allocatorctl update --repo <clean harness checkout> --resident-request <id>`.
   Existing installation locking, route checks, drain, selection and startup apply.
3. The successor observes the actual activation disposition and performs the
   required production acceptance checks. Queued, installed, started and useful
   are separate outcomes.

The native request does **not** start the installer. Running the installer from
the REPL or delegate whose allocator it drains risks waiting on the caller's own
submission and losing the installer on service stop. Do not bypass the documented
foreground updater with ad hoc background jobs or manual release-pointer changes.
For the next cycle, a coding agent/operator can consume the prepared request.
Fully unattended improvement needs a small, explicitly owned independent consumer
handoff, using the existing request journal and updater; its hosting, claim,
completion and restart semantics remain open.

**Current activation scope is narrower than arbitrary topology adoption.** A
captured launch must resume the same instance and retain its assignments. Adding
a cold child may fit that contract only if the complete current-source preflight
accepts it; changing the configured slot universe or moving to a new instance does
not fit. The first V1-to-V2 launch therefore uses operator/coding-agent authored
`update --launch`, preserving data and published skills with new resident roots.
It cannot bootstrap itself through a duty absent from the installed V1 runtime.

An accepted live-test lease blocks a production update. Finish trial settlement
and restore/release the lease before requesting installation. Separate completed
evidence from any still-running trial, and arrange a fresh baseline if another
actor has changed the installation. Forward repair uses the documented updater;
do not restore old release pointers by hand.

Sources: [activation acceptance](https://github.com/RawleySM/rlm-adk/blob/d6a0871058fe491a7f06d1b906ccd3a281724d36/rlm_adk/residents/duties/activation/owner.py),
[activation consumer](https://github.com/RawleySM/rlm-adk/blob/d6a0871058fe491a7f06d1b906ccd3a281724d36/rlm_adk/host/activation_requests.py),
[V2 handoff](https://github.com/RawleySM/rlm-adk/blob/d6a0871058fe491a7f06d1b906ccd3a281724d36/rlm_adk_docs/assignment_v2_runtime_handoff.md).

## 8. From demonstrated improvement to production adoption

Researcher authors a decision packet: hypothesis and original result, comparison
and counterevidence, exact candidate identities, reproducibility limits, expected
human benefit, required changes and proposed post-adoption observations. The
Steward chooses when it is worth adopting within Rawley's standing priorities;
Rawley supplies judgments that only his actual experience can establish.

| Improvement | Promotion/adoption path | Evidence that closes the loop |
|---|---|---|
| Harness or authored project code | Exact tested head/base/PR to configured `project_promotion_execution` holder; normal fetched-main helper and applicable approval gate; harness code then needs activation | Tests and actual trial, factual merge commit, installed digest where relevant, production behavior |
| Resident skill/method | Existing publication/validation and explicit selection of exact package in the intended resident installation | Package bytes and publication receipt, successful adoption and execution on later work |
| Instructions or topology | Versioned authoring and explicit successor launch; evaluate whether same-instance retain fits, otherwise use the operator launch route | Sealed selection, actual admitted roster/assignments, delivered instructions and intended behavior |
| Model/delegate routing | Exact trial policy evaluated first; assigned routing holder or selected gateway-policy launch performs compatible production adoption | Actual bound provider/model/purpose and output behavior; queued rebind is insufficient |
| Data pipeline/tooling | Authored project/package promotion plus preserved input/product contracts | Reproducible outputs, installed/imported callable and useful subsequent consumption |

`project_promotion_execution` is a repository PR merge operation, not generic
promotion of packages, launches or model policy. A trial's private published
package is not automatically available to production. Keep its source history,
immutable bytes, validator inputs and receipts; use supported publication/adoption
operations for the destination. Do not copy a receipt and claim that the destination
validated or adopted it. Resolve this transfer path before planning to promote
trial-authored skills.

Treat candidate drift explicitly: a changed head/base, package, prompt, model
policy or launch may invalidate the relevant qualification. Preserve unaffected
evidence, but recheck the changed behavior before adoption. After adoption, compare
ordinary work with the promised result and record regressions. A well-supported
negative result is valuable learning; successful cleanup alone is not a positive
research result.

## 9. Smallest next-cycle sequence and proof level

1. Resolve the current allocator owner and interruption window; preserve the old
   instance, data, project work and published skill history.
2. Assemble one V2 launch containing the intended full roster, including Intake
   and Researcher as siblings. Create the real research repository, map actual
   project pointers and assign its single coordination slot. Validate the whole
   document and prepared package selections with the candidate release.
3. Admit Researcher in **portfolio preparation mode**. It can prepare opportunities
   while live initiation remains gated by the open items; this is a useful launch
   without claiming autonomous improvement is complete.
4. Close installed-protocol, actual-provider supervision and sponsor-lifecycle
   acceptance. Run one bounded baseline/candidate experiment, with an independent
   native coordinator and the existing first-trial external supervision.
5. Reconcile the original outcome, settle/release the sponsor coordinate, present
   the decision packet and exercise one appropriate promotion/adoption path.
6. Verify the benefit on subsequent work and promote reusable research methods.

The merged implementation has deterministic native-supervision evidence and a
successful five-cycle actual-provider **no-trial** timing qualification; it does
not yet establish this proposed Researcher workflow in the installed service.
At this conversation's allocator check, the selected launch was still V1 and
another actor had started it. No new trial or update is authorized merely by
creating these proposal files. See the [qualification report](https://github.com/RawleySM/rlm-adk/blob/d6a0871058fe491a7f06d1b906ccd3a281724d36/rlm_adk_docs/native_live_test_supervision_qualification.md)
and the open-items acceptance matrix before describing the next cycle as proven.

## 10. Source and validation notes

Additional narrow source anchors:

- [Closed native experiment request](https://github.com/RawleySM/rlm-adk/blob/d6a0871058fe491a7f06d1b906ccd3a281724d36/rlm_adk/live_testing/native/request_model.py): input references, qualified cadence and identical shared lifecycle-owner bytes.
- [Native broker](https://github.com/RawleySM/rlm-adk/blob/d6a0871058fe491a7f06d1b906ccd3a281724d36/rlm_adk/live_testing/native/broker.py) and [execution binding](https://github.com/RawleySM/rlm-adk/blob/d6a0871058fe491a7f06d1b906ccd3a281724d36/rlm_adk/repl/execution_binding.py): actual caller attribution, not automatic external project membership.
- [Duty procedures](https://github.com/RawleySM/rlm-adk/blob/d6a0871058fe491a7f06d1b906ccd3a281724d36/rlm_adk/residents/duties/procedures.py): capacity, coordinate release, activation and promotion contracts.
- [Typed launch assignments](https://github.com/RawleySM/rlm-adk/blob/d6a0871058fe491a7f06d1b906ccd3a281724d36/rlm_adk/residents/duties/launch.py): exact slot universe and parent/holder eligibility.

The node and fragment's runtime-shaped entries are checked against those typed
models. That is schema-level validation, not a complete launch preflight, provider
readiness, admission, experimental execution or semantic acceptance. No source
changes, Retrace runs or live trial are needed to validate this documentation-only
proposal; future implementation assertions are listed separately.

Validation performed during authoring on 2026-10-14:

- Both sibling nodes parsed with the current `ResidentNode` model.
- Researcher registration entries, coordination configuration and adoption slot
  parsed with their existing typed models.
- A schema-only synthetic Steward roster containing both siblings passed the
  six-kind assignment and holder/access checks; it placed Steward at depth 0 and
  Intake and Researcher at depth 1. This did not reconstruct the production roster.
- Local companion links resolved. Only the four new proposal files were added
  to the iOS documentation directory; Intake documents were left unchanged.
