# Architecture Research Prompt for the RLM-ADK Voice Portal

Copy the prompt below into the research agent or session that will investigate the architectural options. It is self-contained. If available, also provide `user-needs-and-requirements.md` for the fuller requirements and scenario criteria.

---

Research architectural options for a private, single-user, voice-led mobile front end to my existing RLM-ADK ecosystem. Start by understanding the user needs and inspecting the existing system, then compare feasible designs. Produce an evidence-backed recommendation and a phased implementation proposal. The work requested in this prompt is research and design.

I am Rawley. My goal is to spend less time looking at a screen while connecting Scripture, prayer, life context, practical responsibilities, and creative or ministry ideas to real work performed by the backend. The portal is where I converse, plan, interview, direct, and review. Deliverables may live elsewhere, including separate websites, services, books, devotionals, research reports, and testimony videos.

## Settled development context and expanded research scope

Live voice is a foundational requirement, enabling natural interviews, interruptions, corrections, and transitions while the backend works. Use OpenAI's live-voice API as my selected performance gold standard, identifying the exact current GPT-Live or Realtime API/model used for comparison. Cost is a significant driver of the final choice, so compare recent open-source and hybrid alternatives that can meet the required experience. Treat this benchmark as my product target, not an already completed universal performance ranking. Use the separate `live-voice-landscape-research-prompt.md` for the focused tooling review, and incorporate its findings into this architecture comparison.

I have ChatGPT Pro, but application API billing must be evaluated separately from subscription usage. Do not assume my subscription provides API credits. Separate continuous device recording from the lifetime of a paid voice session, account for billed silence/backend waits where applicable, and compare total voice, reasoning, transcription, compute and maintenance costs.

I have purchased the $99 Apple Developer Program membership and have the Swift/Xcode repository [RawleySM/rlm-adk-ios](https://github.com/RawleySM/rlm-adk-ios). Native iPhone installation is the preferred front end because I experienced microphone and application-lifecycle fragility in the Chrome-based RLM dashboard. Inspect that repository's current scaffold, build workflow, signing readiness, and audio configuration. Treat the purchase as user-reported; verify activation and device signing separately. Paid membership does not remove iOS microphone permissions, background-audio requirements, or interruptions. Compare browser/hybrid options only where they illuminate the native decision or offer a useful supplementary interface.

The backend already has many data pipelines, but we have not selected a consolidated knowledge/retrieval structure for these user needs. Research both the front end and what the backend needs to evolve into. Explicitly evaluate **Theographic** as a biblical knowledge dataset/schema alongside backend choices such as relational/hybrid search, Neo4j with typed graph retrieval, Microsoft GraphRAG, and optional temporal graph tooling such as Graphiti. These candidates occupy different layers and can be combined. Do not mistake Theographic for a database or assume GraphRAG is a complete operational backend. The existing Scripture source catalog already contains Theographic; establish which retained assets and reader contracts are available, then assess adapters and semantic joins.

## User needs that the architecture must serve

1. **Morning Scripture and devotional.** I greet the app and hear a verse and a devotional lasting a few minutes, drawing on relevant teaching and my existing context. I can interrupt, ask a question, pray, or switch to another activity. Retain what passage and audio I actually heard and where playback stopped. Resume when I direct it.

2. **Natural conversation and contextual navigation.** I ask for historical context, comparisons of theological interpretations, application to my life, or a verse or prayer fitting a current challenge. Spoken commands can change the visible view and active project. Carry relevant conversational context without making me navigate a scripted sequence of forms.

3. **Continuous recording with independent speech control.** I want recording to continue during normal operation, including explicit quiet. Do not reinterpret this as wake-word detection alone. Research storage of raw audio, transcripts, and derived context; their retention is not yet decided.

4. **Explicit quiet and wake.** Quiet suppresses all spoken output until I explicitly use wake or the assistant's name. Background recording and backend work continue. Even an important calendar event does not override quiet. In ordinary available operation, finishing a conversational exchange does not automatically enter explicit quiet. The exact wake and quiet phrases are unset.

5. **Distinct prayer capture.** I am addressing God, not conversing with an agent. Prayer capture records and tolerates long pauses without responses, proactive spoken reminders, or automatic end-of-prayer detection from silence. I explicitly end the prayer input. It saves and waits for me to ask what is next; no automatic reflection or new project begins merely because prayer ended.

6. **Defined initial proactivity.** When available, proactively prompt about calendar preparation or departure and project phase changes that need my attention. Ordinary project status is spoken when I ask. Investigate deferred alerts and stale reminders. Silent notifications and haptics during quiet or prayer are an open choice. An earlier desire for result-ready notifications must be reconciled with this latest scope; do not assume every status change warrants speech.

7. **Practical executive-function support.** Actively gather details from available context and move delegated tasks forward. Examples include car-service research, appointments, oil-change history, tax-related work, and recurring obligations. Use schedule context. Quiet is not a pause on useful backend work. Identify the standing preferences and existing execution authority needed for actual bookings, filings, or spending; do not invent universal permissions or require repeated confirmation for already-authorized routine work.

8. **Heavy research becomes durable work.** For a substantial research request, acknowledge that a real action item has been recorded or accepted. Return an inspectable sourced report later. I may ask for exploratory links between Scripture and subjects such as relativity or Penrose's twistor theory. Distinguish established findings, theological interpretation, and speculation.

9. **Real project management.** Retain planning conversations, decisions, interview answers, priorities, phase state, failures, and deliverable references across sessions. Let me choose and reprioritize multiple projects by voice. An acknowledgment, running process, and actual completed output are distinct states.

10. **Testimony and creative production.** First look back through prayers and life context for a relationship or event, then draft an initial story and interview me to fill gaps. An example is a testimony about God moving in my relationship with my son, Creighton. Develop a storyboard for video; show frames while discussing them by voice; accept spoken revisions; then continue production and let me review real media on my phone. Keep prayer-derived facts distinct from creative visualization. Reuse existing production work where suitable.

11. **Broader project types.** The same backend can develop a veterans ministry or separate website, original devotionals, a book, or an ongoing personal parts map. The parts project uses IFS vocabulary and my Christian framing of Jesus working through me. This is my intended framework, not a claim about the standard IFS definition or clinical efficacy. Domain projects should be possible without hard-coding each as a separate application.

12. **Simple optional screens.** Most actions work by voice. Screens show the relevant passage, storyboard, output, or project view when useful. Explicit prayer and quiet controls and an understandable recording state should be easy to access.

Intended sources include Scripture, my prayer corpus, life context, John Piper teaching/devotional material, Joby Martin / The Church of Eleven22 sermons, and an expanding transcript collection. Confirm exact collections and source identities. Existing YouTube/sermon ingestion is text and metadata; source-video downloading is not part of this proposal. This does not limit generating new testimony media.

## Inspect the existing ecosystem before selecting an architecture

Use applicable AGENTS.md and current repository documentation. Relevant source-discovery starting points, if accessible, are:

- `/home/rawleysm/rlm-adk`
- `/home/rawleysm/data-pipelines`
- `/home/rawleysm/apple-ingestion-gateway`
- `RawleySM/rlm-adk-ios` on GitHub, or its verified local checkout
- `RawleySM/project-work-registry` for authored project/work records where applicable

Treat these as discovery locations, not proof of a current deployed interface. If another checkout or installed release is authoritative, identify it. Start with the current documentation index and inspect the narrow source needed to establish the available contracts.

Map the current interfaces for prayer intake and storage, life-context retrieval, sermon/source provenance, session continuity, model routing, action items, task execution, project phases, human-input requests, priorities, artifacts, calendar operations, and media production. Verify whether a durable job or project contract actually exists before proposing a new orchestration layer. Reuse existing native owners where appropriate.

Inspect the data-products catalog, published prayer corpus views, life-source reader change/generation semantics, Scripture reader manifests and text locators, YouTube observations, and Theographic source metadata. Explain how derived indexes consume new records, corrected text, removals, late payload repairs, and source revisions without losing citations or duplicating entities. Preserve domain ownership: acquisition stays in source pipelines, domain projections and retrieval belong to consuming projects, and generic execution remains in the RLM-ADK host.

Report source implementation, installed configuration, exercised behavior, and proposal-only capability separately. A successful test, receipt, saved record, model turn, or running resident does not prove a usable output or a completed user outcome. Use current source anchors and observed evidence for claims. If live state is inaccessible, identify that boundary and continue the design work from the evidence available.

This is a private deployment. Preserve existing safeguards. Do not add security hardening, LLM isolation layers, or related architectural restrictions without my explicit prior permission and a concrete failure case. Do not suppress useful background work based on abstract privacy assumptions. Honor the retired-capability list in AGENTS.md and do not use or restore those capabilities.

Keep this research read-only with respect to running services and external side effects. Write requested research artifacts, but do not deploy, start paid provider experiments, make real bookings, file documents, or publish outputs as part of investigating the architecture.

## Research and compare architectural options

Research current capabilities using primary sources: official platform and provider documentation, authoritative specifications, and relevant papers where needed. Actually read supporting pages, record access dates, and cite the exact pages. Technical claims must not rest on search snippets or promotional summaries.

Compare a small set of materially different designs, including these candidate dimensions where relevant:

- Native SwiftUI/iOS in the existing repository as the primary client, with browser/hybrid alternatives and a companion-device arrangement evaluated only where relevant to feasibility or supplemental review.
- OpenAI live voice as the performance reference; recent open-source live speech-to-speech with backend delegation; an STT-to-agent-to-TTS pipeline; and hybrid combinations. Measure whether alternatives preserve the required natural interaction rather than treating all speech interfaces as equivalent.
- Local versus hosted voice and reasoning, with the voice model selected independently from RLM-ADK execution where supported.
- A thin connection to existing backend task/project contracts versus the minimum additional adapter required where current contracts are insufficient.
- Relational storage with full-text/vector retrieval; a typed knowledge graph with Theographic as one source; Microsoft GraphRAG local/global/DRIFT retrieval; and a temporal personal-context graph. Compare combined designs and the value of each layer, not only an exclusive choice of product names.

The intended platform is iPhone/iOS and the existing workspace uses SwiftUI/Xcode. Verify the target device and OS version. Voice provider, deployment arrangement, knowledge-store technology, and any added queue design remain open. Do not silently drop requirements to fit a preferred stack.

For each credible design, cover:

1. Continuous capture while locked or backgrounded, microphone competition, phone calls and other audio, audio routing, wake handling, power use, storage, and operating-system restrictions. Separate technical capability from distribution or platform-policy conditions. If continuous phone recording is infeasible, state exactly why and offer alternatives with their user-experience consequences.
2. Natural interruption, turn taking, long prayer pauses, explicit quiet enforcement, and context switching. Explain how conversational behavior differs from capture and background execution.
3. Durable recording/session/project state, reconnects, offline intervals, interrupted uploads, recovery after app restarts, and preserving the reading or storyboard position.
4. Turning voice instructions into real accepted jobs, preserving job identity through retries, and preventing duplicate work without replacing working backend machinery unnecessarily. Explain when the app may truthfully acknowledge acceptance.
5. Background execution, phase events, requests for user input, deferred reminders, completion delivery, and artifact access across voice-session lifetimes.
6. Retrieval of Scripture, prayer, teaching, and life context; source-qualified citations; different theological viewpoints; and user corrections that persist into project work.
7. Calendar and responsibility tracking, existing native write paths, voice-directed prioritization, and the distinction between a personal task and a longer project.
8. Testimony/media handoffs and review, standalone product links, and what small interfaces are needed to support other project types.
9. Latency, ongoing resource use, operating cost, availability, implementation effort, and fit with the existing infrastructure. State workload assumptions and separate documented pricing from estimated monthly totals. Do not infer guaranteed phone behavior from a server-side API feature.
10. Backend organization: source records versus derived chunks/claims/entities/relations, Scripture edition and versification, personal entity resolution, exact temporal queries, theological viewpoint attribution, and correction/reindex semantics. Assess Theographic's coverage and limitations from retained assets and native documentation. Explain whether an SQL baseline, graph projection, community summaries, and temporal extraction each improve the actual user journeys enough to justify their complexity.

Evaluate architecture options against user outcomes, not the number of features a provider advertises. Identify any true incompatibilities and dependencies. Ask only questions that materially change the recommendation; carry nonblocking unknowns as explicit assumptions.

## Deliver the research in a reviewable form

Produce:

1. A source-backed inventory of the current integration points, with the evidence level of each capability.
2. A requirements traceability table mapping the needs above to existing support, proposed components, unresolved choices, and feasibility limits.
3. Separate front-end/voice and backend-organization comparison tables covering mobile capture feasibility, voice quality, interruption/mode behavior, backend reuse, durability, citations, updates, cost, and implementation effort. Distinguish Theographic's dataset role from graph engines, extraction frameworks, and retrieval algorithms.
4. A recommended architecture with the reasons it best serves the user, the nearest alternative, and the conditions that would change the recommendation.
5. A component/data-flow diagram and a separate interaction-state diagram. Represent recording, speaking permission, prayer capture, and job execution independently enough to explain the quiet/wake rules accurately.
6. Proposed interface boundaries and event semantics for capture, context selection, accepted tasks, project phases, user-input requests, artifact availability, reminders, quiet/wake, and reconnection. Reuse established contracts; mark any necessary new contract as a proposal.
7. A first complete user journey and phased implementation plan. Propose small feasibility proofs for continuous capture, voice interruption, a durable RLM-ADK handoff, quiet-aware reminders, and retrieving a real output. Describe what each proof would establish and what it would leave unverified; do not run the proofs under this research request.
8. Open decisions, concrete platform constraints, and an annotated source list with access dates. Keep material assumptions next to the recommendation they affect.

Use the following scenarios to assess the proposed designs: an interrupted devotional; a prayer with long silence; save-and-wait after prayer; a calendar timer firing during explicit quiet; waking again; a project phase needing input; a substantial research request surviving the end of a voice session; testimony context lookup followed by interview and storyboard revision; an externally hosted deliverable; and a network or app interruption without losing acknowledged work.

The recommendation should explain which design can fulfill the intended experience, what must be adapted in the existing ecosystem, and what remains to be proven before implementation or deployment.
