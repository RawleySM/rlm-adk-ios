# Steward and Researcher runtime — operating handoff

The installed allocator is on **desktop**, not Spark-A. This handoff describes
instance `research-ab-20261014`, admitted on 2026-10-14. Read
[LIVE_BRINGUP.md](LIVE_BRINGUP.md) for observed trial results and unresolved limits.

## Roles and direct steering

- Steward `stwlab` holds capacity governance only. Target one, ceiling two, no
  automatic increase. It does not select research projects or relay your mail.
- Researcher `rsch01` holds all six declared project-coordination slots and owns
  the research opportunity/experiment queue.
- Native experiment coordinators own their exact isolated trial through stop,
  delegate settlement, store stability checks, allocator restoration and outcome
  delivery. Their fresh finite monitor children assess accepted scopes at each
  checkpoint. The tested topology runs separately from its coordinator.
- Ordinary compaction threshold is 150,000 tokens. Retained sessions survive the
  installed allocator's stop/restore; REPL variables and processes do not.

Direct mail, from a shell that can SSH to desktop:

```bash
ssh desktop '~/srv/rlm-adk/bin/allocatorctl inbox post --to rsch01 "Your research direction here"'
ssh desktop '~/srv/rlm-adk/bin/allocatorctl inbox post --to stwlab "Your resource direction here"'
ssh desktop '~/srv/rlm-adk/bin/allocatorctl inbox list --to-operator'
ssh desktop '~/srv/rlm-adk/bin/allocatorctl status --json'
```

The installed authoring document on desktop is
`/home/rawley-stanhope/.config/rlm-adk/launch/research-ab-20261014/launch.author.json`.
The research repository is
`/home/rawley-stanhope/.local/rlm-adk/projects/runtime-improvement`.
Researcher records request contracts and returned outcomes under
`research/attempts/`; shared-host samples are under `research/resources/`.
Do not edit installed release trees or silently resume an operator pause.

## Observe the right proof level

The campaign starts with A: Steward with Prayer and Vision siblings; B: Steward
with Prayer as its child and Vision under Prayer. Both use frozen source inputs,
selected warm packages and one shared output project. Package reuse proves
selection and exercised reads, not complete semantic acceptance of the products.
Use committed artifacts, original monitor assessments and the coordinator's
reasoning to judge grounding, ambition, usefulness and remaining gaps. A directory,
commit, invocation or settled receipt alone does not prove a useful outcome.

Subscription usage snapshots are account-wide, not trial costs. Preserve missing
coverage. Do not add cached input to total input, or reasoning to provider output
when already included. Shared GB10 process memory and unified-memory samples are
not attributable trial VRAM; NVIDIA total memory may be unavailable.

## Monitoring boundaries learned during bring-up

A held initial checkpoint may precede resident admission and have no effective
assignments. After activation, a checkpoint can discover new eligible scopes and
propose additions while its current batch still covers the previously accepted
set. `native.policy.merge_assignments` adds the proposed defaults at a validated
continuation; the next checkpoint must assess them. Do not mistake that ordinary
transition for missing current reports or invent reports for future assignments.
Actual blockers, rejected commits and failed/missing reports for accepted scopes
remain failures requiring an explicit judgment.

A declared project or repository effect alone does not qualify native project
coverage. The trial Steward opens its real `project:trial-ab-output` coordinate
using the existing duty and capacity machinery. The three residents and that
project fit the original four-scope measurement. Additional real scopes require
applicable qualification; do not omit them to fit the old measurement.

An external coding agent attending this campaign must arrange fixed time-based
checks every 60 seconds through actual convergence, then no more than 300 seconds
apart. Native review cadence is separately qualified at 300 seconds. Do not end
attendance with a live trial unless another owner has explicitly accepted the
obligation, or the exact trial is stopped and settlement verified. Activity does
not postpone the next check. Never infer settlement from a pending stop request.

## Complete request and warm-install preparation

The packet limit applies to the complete monitor prompt, including objective, scope instructions, source references and previous report. Check that complete request, not only the serialized evidence. The concise objective remains useful, but PR #1345 changes the default to 32,000 characters and preserves complete evidence records. The old 8,000-character qualification and ab-prep-003 requests do not qualify this changed source and limit. Prepare fresh requests only after the new exact-source qualification completes; sizing or replay alone is not live proof. Preserve the pinned qualification's actual source, policy and collection limits.

A new warm trial needs the selected bare skills repository and refs, private installation `skills/config.json`, and its referenced validation profile manifest/source bundle under `skills/validation/`, in addition to frozen data and fresh resident/session/output roots. Copy known-good installation metadata through ordinary preparation, then run the full selected-source preflight. A successful source-tree copy alone does not establish warm-start readiness.


## Packet repair installed and allocator resumed

Installed PR #1345 / main `e4664bb8c0d52a7de3e3648a384fedd61ea0dbf8` as release `4427b6b202f6309781907078433caa09d13aca751c249b0b2e77aef822ad98cd`. Route readiness passed with no override. Allocator is active at PID 4056421, run `rlm-20261014T214809-4056421`; explicit `allocatorctl resume` found no remaining hold. Both residents resumed generation 17 in their original sessions; formation epoch remains 1. Data, skill packages, project registry and launch are retained. Installed packet/coordinator bytes equal the tested source and clean prepared candidate. No isolated lease is open.

Qualification 12 is **REFUSED**, not qualified. Two two-monitor cycles completed (78.67 and 62.64 seconds), delivered four original reports, and arranged/fired actual wakes. Those reports identify attributed commits, clean worktrees and concrete evidence/counterevidence while preserving unknown token coverage. The first expanded four-monitor cycle triggered successful compaction at the ordinary 150,000-token setting; compaction took roughly 129 seconds and the invocation then exceeded its existing review authority. Native cleanup completed. No new isolated trial was launched by this qualification. This timing failure is distinct from the repaired record-destruction bug; neither source GREEN nor the successful partial qualification proves the full A/B campaign.

Researcher was notified through `mail-cd76daed264c426d8f5ee8248db009ab`. The allocator is resumed, but the prior campaign launch hold remains: old ab-prep-003 requests and qualification 11 pin stale source and 8,000-character limits. A replacement exact-source 32,000-character qualification must pass before fresh A/B requests launch. Do not silently raise compaction or checkpoint deadlines to hide this failure. Original evidence is on desktop under `~/.local/share/rlm-adk/native-supervision-20261014/measurement-12/`; local deployment and validation summaries are in `evidence/native-packet-repair-deploy.json` and `evidence/native-packet-repair-validation.json`.
