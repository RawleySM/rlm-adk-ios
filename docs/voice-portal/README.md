# Voice portal requirements and architecture

These documents capture the October 12, 2026 requirements and architecture research for Rawley's
personal voice portal. Native SwiftUI/iOS is the intended front end, using this repository and
the reported Apple Developer Program purchase. The backend research compares ways to organize
the existing pipeline products and connect them to durable tasks, projects, and deliverables.

- [Top candidates](top-candidates.md): the consolidated shortlist, recommended combination,
  cost implications, corrected research claims, and the tests that would change the rankings.
- [User needs and requirements](user-needs-and-requirements.md): voice interactions, continuous
  recording, prayer and quiet/wake behavior, proactive reminders, project workflows, native-device
  requirements, and backend knowledge organization.
- [Architecture research prompt](architecture-research-prompt.md): a self-contained prompt for
  investigating the current interfaces and comparing front-end and backend options.
- [Recent live-voice tooling research prompt](live-voice-landscape-research-prompt.md): a focused
  review of recent open-source and hybrid voice stacks, using OpenAI live voice as the performance
  reference and total operating cost as a significant selection factor.
- [Architecture research](architecture-research.md): source inventory, native audio considerations,
  Theographic and retrieval comparisons, proposed components and interfaces, and a phased proof plan.

- [Prototype plan](prototype-plan.md): the selected first slice, source contracts, host interfaces and
  acceptance checks. [Fable review](prototype-fable-review.md) is preserved with the Codex adjudication.
- [Prototype runbook](prototype-runbook.md): how to deploy and run the desktop backend, the client, the
  cloud Mac, and the signing/device situation.
- [Prototype acceptance](prototype-acceptance.md): what was built and exercised, built but unverified,
  or blocked, with evidence paths and simulator screenshots.

- [Conversational design alternatives](conversational-design-alternatives.md) (2026-10-13, for review):
  nine decision axes for the fully conversational portal (where the intelligence lives in rlm-adk, turn-taking
  and prayer boundaries, ASR authority, the app's own voice, routing, project identification, word-from-God,
  interpretations, the work tier), recommendations, the voicesim closed loop, and the decisions still open.

- [Live voice stack alternatives](live-voice-stack-alternatives.md) (2026-10-13, for review): the dossier's
  three open-source Talk-tier architectures (Pipecat, LiveKit server + Agents, the Kyutai Moshi/Unmute family)
  weighed against this system's requirements and hardware, with a scored matrix, a recommendation and the
  spikes to run before committing.

- [Gemini research report archive](research/2026-10-12-gemini/README.md): seven completed reports,
  source lists, all twelve independent prompts, and links/status for the remaining studies.

The research distinguishes existing source products and simulator evidence from proposed
integrations and device behavior still needing proof. Its recommendation remains provisional
until the native audio and retrieval evaluations are completed.
