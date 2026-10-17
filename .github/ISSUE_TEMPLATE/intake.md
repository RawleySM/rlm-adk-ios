---
name: Intake ticket
about: Work the Intake resident accepted from a voice-portal ticket and did not finish in its delivering turn
title: "[intake] <ticket id>: <title>"
labels: intake
assignees: ''
---

<!--
Opened by the Intake resident for an accepted bug or feature that is not finished within the turn
that delivers it (docs/voice-portal/pipecat-rebuild/intake-issues-log.md, "Tracking on GitHub").
Add `bug` for a bug, `capability-gap` when the voice assistant could not meet the request
(ticket origin `auto_escalation`), or `feature` for any other feature request. The delivering PR
says "Fixes #<this issue>", and the ticket's status reply names this issue's URL.
-->

## Intake ticket id

`bug-YYYYMMDD-NN` (the intake issues log; `GET http://127.0.0.1:8765/issues/<ticket id>` on the desktop)

## Rawley's words

> verbatim, as the ticket's `text` records them

## Origin

- [ ] voice (he asked the assistant to send it to Intake: `conversation`)
- [ ] auto-escalation (the assistant could not meet the request: `auto_escalation`; its reply was: "...")
- [ ] Bug chip (`bug_chip`)

## Acceptance

What is true when this is done (the spoken behaviour, the screen, the data), checked how.

## Status

- `accepted` on <date>, status reply <message id>
- PR: #<n>
- `done` / `failed`: <date>, naming the merge commit and the build or deploy
