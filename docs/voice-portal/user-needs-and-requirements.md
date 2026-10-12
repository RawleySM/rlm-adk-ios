# RLM-ADK Voice Portal User Needs and Requirements

Rawley wants a personal application that connects Scripture, devotional teaching, prayer, life context, and practical responsibilities to real work carried out by RLM-ADK. Voice is the primary interface. The application should help him spend less time looking at a screen while directing projects, receiving timely reminders, and reviewing their outputs.

These requirements come from the October 12, 2026 conversation and the subsequent clarifications about native iOS development, backend organization, foundational live voice, and cost. They describe desired product behavior, not a claim that the current backend or phone already supports it. The latest explicit choices govern the modes and proactive behavior below.

## User and intended outcomes

The first version serves Rawley alone. Outputs may be shared outside this application; a broader user community is outside the initial scope.

The application should help Rawley:

- Begin the morning with a verse and a devotional lasting a few minutes.
- Ask questions, pray, reflect, and change activities through natural speech.
- Apply Scripture to actual relationships, experiences, and challenges.
- Develop ideas arising from prayer into research, art, testimony, ministry, or other projects.
- Delegate everyday tasks and receive practical support for responsibilities he finds difficult to organize.
- Direct and track RLM-ADK work that produces inspectable deliverables, including products that live outside the portal.
- Preserve conversational and project continuity across interruptions and sessions.

The backend carries out the substantial work. The portal holds the conversation, communicates task and project state, and presents the current material when visual review is useful.

## Native iPhone development and device operation

Rawley reports purchasing the **$99 Apple Developer Program membership**. He has a Swift/Xcode development repository at [RawleySM/rlm-adk-ios](https://github.com/RawleySM/rlm-adk-ios). Native installation on his iPhone is the preferred direction, addressing microphone and application-lifecycle fragility he experienced with the Chrome-based RLM dashboard.

**DEVICE-01 — Use the existing native workspace.** Investigate and extend the SwiftUI/Xcode foundation in that repository. Compare alternatives where useful, but evaluate native iOS as the primary front end rather than leaving the intended phone platform unspecified.

**DEVICE-02 — Prove actual device audio behavior.** Research and verify microphone permission, background audio configuration, locked-screen capture, playback, Bluetooth routing, interruptions, recovery, and power use on the intended iPhone. Membership supports signing and device distribution; it does not confer unrestricted microphone access or guarantee uninterrupted recording. The purchase is user-reported; membership activation, signing configuration, and successful device installation have not been verified in this research.

**DEVICE-03 — Own recording independently of a live agent session.** Continuous recording, quiet/wake, prayer boundaries, saved audio/transcript segments, and interrupted-playback position must survive voice-service reconnection. If the operating system interrupts capture, represent the gap and recover appropriately instead of reporting continuous recording that did not occur.

The inspected repository has a SwiftUI scaffold and a successful unsigned simulator workflow. Those establish an existing development starting point, not working microphone capture or signed-device operation. See the architecture research report for pinned source evidence and the device proof plan.

## Backend consolidation and knowledge organization

Existing pipelines have accumulated prayer, life-context, teaching, Scripture, and other source data. Rawley wants research into how to organize and use those products; a consolidated application knowledge/retrieval architecture has not been selected.

**DATA-01 — Build on existing source products.** Inventory source identities, revisions, text locators, update/change contracts, and available reader packages. Preserve source-owned acquisition pipelines and place derived knowledge, interpretation, retrieval indexes, and application state in the appropriate consuming project. A proposed knowledge service should not force a replacement of working ingestion.

**DATA-02 — Compare backend approaches against user journeys.** Evaluate relational and hybrid text/vector retrieval, a typed knowledge graph, Microsoft GraphRAG, and a temporal personal-context graph where relevant. Compare their incremental-update behavior, citations, corrections, theological distinctions, latency, resource use, and operating effort. Keep operational tasks, reminders, and project phases durable independently of a search index.

**DATA-03 — Evaluate Theographic explicitly.** The intended candidate is **Theographic**, the biblical entity and relationship dataset. Determine how its native identifiers, passage references, people, places, events, and relationships can augment Scripture context. It is a knowledge source/schema, not by itself the application's graph database, retrieval algorithm, or task manager. The current Scripture source catalog already represents it; verify the retained source before proposing new ingestion, and validate semantic joins rather than assuming catalog presence proves integration.

**DATA-04 — Make personal and interpretive links inspectable.** Connections among prayers, relationships, life events, teaching, Scripture, parts mapping, and projects should retain the source/revision and evidence supporting them. Preserve user corrections and distinguish a source assertion, a teacher's reading, a personal reflection, and a proposed cross-domain connection. Keep exact records, dates, and project outcomes queryable without relying solely on similarity search.

## Core voice experience

Live voice is a central product requirement. The app must support the natural, free-flowing interaction Rawley experienced in this requirements conversation: interrupting, adding details, changing activities, and interviewing about a story while the backend carries work forward. Choosing the live-voice API and its surrounding tooling is therefore a foundational architecture decision.

**VOICE-01 — Speak naturally.** The user can ask questions, provide details, correct an interpretation, direct a project, and reprioritize work without navigating menus for ordinary operations.

**VOICE-02 — Interrupt and change context.** During Scripture or devotional playback, speech stops the playback and lets the user choose the next activity. The system remembers the passage, what was actually heard, and the interrupted activity. It resumes when directed; an interruption does not require automatic resumption.

**VOICE-03 — Carry the relevant context.** A request such as “explain the historical context of that verse” can refer to the passage and keywords just heard. A transition from Scripture to prayer to testimony carries the relevant thread into the project.

**VOICE-04 — Switch the visible view when needed.** A spoken request to review a storyboard, inspect a project, or play a finished video brings up the appropriate material. A switch to a project does not imply that an unproduced storyboard or deliverable is already available.

**VOICE-05 — Stay usable without looking.** Ordinary navigation, playback control, status queries, project selection, prioritization, and revision requests should work by voice. Visual review remains available for frames, videos, documents, and other artifacts.

**VOICE-06 — Provide a live conversational interface.** Support responsive streaming speech, natural turn taking, interruption, and continuous conversational context during an exchange. Evaluate simultaneous listening and speaking, partial utterances, spoken corrections, and the transition from conversation into backend work. A sequence of record/upload/respond forms is insufficient for the intended experience.

**VOICE-07 — Use OpenAI live voice as the performance gold standard.** OpenAI's live-voice API is Rawley's chosen reference for conversational naturalness, responsiveness, interruption handling, and delegation to the backend. Evaluate the current GPT-Live and Realtime offerings explicitly and identify the exact model/API used as the benchmark. This sets the product's target experience; it is not a claim that a head-to-head comparison of every alternative has already established a universal winner.

**VOICE-08 — Keep voice and execution independently selectable.** The live-voice frontend must connect to RLM-ADK and its local or hosted reasoning/services without requiring the voice provider to become the durable task, project, or knowledge owner. Compare recent open-source frameworks, speech models, and hybrid stacks against the OpenAI reference while preserving the user's mode and continuity requirements.

## Voice cost and subscription expectations

Cost is a significant driver of the ultimate voice choice. Preserve the desired conversational quality while evaluating affordable daily operation, including local/open-source and hybrid arrangements. The reference experience does not predetermine the selected provider.

**COST-01 — Compare total ongoing cost against the required experience.** Show costs for realistic active-conversation, prayer, quiet, and available-idle workloads. Compare the lowest-cost credible options that satisfy the required interaction quality, and explain the experience lost by cheaper compromises. A monthly budget is not yet specified.

**COST-02 — Separate Pro subscription use from API billing.** Rawley has ChatGPT Pro and is concerned about heavy voice usage. Treat Pro/ChatGPT usage and application API usage as separate budgets; do not assume Pro includes an API allowance. Verify the applicable API billing/credit arrangement rather than assuming subscription credits can fund it. OpenAI's current documentation separates API pricing from subscription usage. [OpenAI subscription and API pricing distinction](https://learn.chatgpt.com/docs/pricing).

**COST-03 — Separate recording from paid voice-session lifetime.** Continuous recording must not automatically imply an always-open paid voice session. Investigate how session closure/reconnection, local capture, wake handling, and proactive calendar/phase prompts can coexist with natural voice. GPT-Live currently bills active session duration, including silence and backend waits; muting alone does not end billing. [OpenAI voice cost accounting](https://developers.openai.com/api/docs/guides/voice-latency-cost).

**COST-04 — Make usage and ownership costs inspectable.** Track voice session duration, backend model/tool use, transcription, storage, hosted compute, and local compute assumptions separately. Open-source code or open weights do not imply zero operating cost. Include hardware compatibility, power, implementation and maintenance effort, and any required hosted dependencies in the comparison.

## Recording and spoken engagement

Continuous recording, permission to speak, prayer capture, and background execution are separate concerns. The desired behavior is continuous recording throughout normal operation, including quiet mode. Retention duration and whether the stored record includes raw audio, transcripts, or both remain to be decided.

| State | Recording | Spoken behavior | Backend work |
| --- | --- | --- | --- |
| Available between exchanges | Continues | May respond to the user and initiate the allowed proactive prompts | Continues |
| Active conversation | Continues | Natural turn taking and interruption; avoids talking over the user | Continues |
| Prayer capture | Continues, with the prayer identified as a distinct input | No agent responses or proactive spoken interruptions, including during long pauses | Continues |
| Explicit quiet | Continues | No spoken output until the explicit wake command | Continues |

**MODE-01 — Explicit quiet persists.** Saying quiet, or using an equivalent control, suppresses spoken engagement until the user explicitly wakes the assistant. A timer, important calendar event, or project event does not override quiet.

**MODE-02 — Wake restores availability.** The wake word or assistant name can initiate an exchange and, when explicitly quiet, restore permission for spoken engagement. Exact words are unset. When the user finishes an ordinary exchange, the assistant can return to being available between exchanges. It does not enter explicit quiet merely because an answer ended or there was silence.

**MODE-03 — Prayer is addressed to God.** Prayer capture is distinct from engaging the agent. Long pauses must not trigger a response, a question, a timeout-based conclusion, or a mode change. An explicit control ends the prayer input.

**MODE-04 — Save and wait after prayer.** Finishing prayer saves it and waits for the user to ask what is next. It does not automatically begin commentary, reflection, an interview, or a creative project.

**MODE-05 — Availability matters.** The user wants spoken engagement to fit the setting, including whether he is speaking with someone else. Explicit quiet is a firm user-controlled state. How any additional availability inference should work requires research; it must not replace the explicit quiet/wake rule.

The handling of silent notifications and haptics during quiet or prayer remains open. Platform limitations on continuous recording, background execution, microphone competition, locked screens, and other apps must be researched rather than assumed away.

## Proactive behavior and schedule integration

**PROACTIVE-01 — Initial spoken prompts have a defined scope.** Outside explicit quiet and prayer capture, begin with calendar preparation and departure reminders, and project phase changes that need the user's attention.

**PROACTIVE-02 — Ordinary status is on request.** The user can ask where a project stands and hear what is complete, what is pending, what needs his input, and which outputs are available. Routine status narration is not automatically delivered at the start of every session. A phase requiring attention is the explicit proactive exception.

**PROACTIVE-03 — Keep working when silent.** Recording and background execution continue during quiet and prayer. Deferred spoken events must remain represented so they can be handled when engagement is allowed. Handling of stale or missed reminders needs an explicit design.

**PROACTIVE-04 — Integrate practical planning with the schedule.** Reminders should use relevant commitments, preparation needs, and travel or departure context. The user also wants appointment booking and ongoing responsibility tracking. Current calendar sources and write paths must be verified in the existing ecosystem.

**PROACTIVE-05 — Actively gather useful details.** The backend should search available context for information needed to advance delegated work. Routine task fulfillment should not stall because quiet mode or an abstract privacy assumption is mistaken for a request to stop background work.

The user initially requested notification when heavier research returns. His later choices narrow unsolicited spoken updates to calendar needs and project phases needing attention. Whether every completion should cause an informational notification, or only a result ready for requested review or another action, remains an open delivery choice.

## Scripture and devotional conversation

**SCRIPTURE-01 — Morning routine.** A greeting such as “good morning, what does the Lord have to say to me?” starts a verse and devotional lasting a few minutes. Existing John Piper devotional or sermon text is an intended source. Translation, reading plan, selection rules, and the exact devotional feed remain open.

**SCRIPTURE-02 — Explain and compare.** The user can request historical context, leading interpretations, and the points of disagreement among traditions, including Calvinist and Arminian readings of a particular passage.

**SCRIPTURE-03 — Apply to life context.** The user can ask for an example from his life or for a verse or prayer suited to the context or challenge of a transition in his day.

**SCRIPTURE-04 — Make the grounds understandable.** Answers should distinguish Scripture, a teacher's interpretation, personal-context application, and exploratory connections. Source detail should be available without requiring a long spoken bibliography for every exchange.

Intended context includes the prayer corpus, broader life context, Scripture, and an expanding collection of sermon and teaching transcripts. The user named John Piper and Joby Martin / The Church of Eleven22. Confirm the precise Piper feed or collection during source discovery; the spoken name alone is not a verified source identifier.

Existing sermon and YouTube ingestion should be reused where applicable. Its established ingestion convention is text and metadata; acquiring source videos is unnecessary for this portal. That source-ingestion convention does not limit the creation of new testimony videos.

## Research and tracked execution

**RESEARCH-01 — Start substantial research through speech.** The user may ask for work connecting seemingly unrelated fields, such as Scripture, relativity, and Penrose's twistor theory. This is a desired research workflow; those connections are not established scientific or theological conclusions.

**RESEARCH-02 — Acknowledge a real action item.** The immediate response confirms that a research task has actually been recorded or accepted for execution. It does not need to improvise an immediate substantive answer to a heavy research request.

**RESEARCH-03 — Return a usable result.** Research yields an inspectable report or other artifact with sources, assumptions, and the limits of any speculative connection. Its completion and review state are available through the portal.

**PROJECT-01 — Create and manage durable projects.** Planning conversations, interview answers, revisions, decisions, priorities, execution state, and outputs persist across sessions. The user can select and prioritize concurrent projects by voice.

**PROJECT-02 — Track real outcomes.** An accepted job, a running process, and an available deliverable are distinct states. The user must be able to inspect the actual output. Failures and work needing his input should be visible rather than described as completion.

**PROJECT-03 — Support deliverables elsewhere.** A project can produce a separate website, service, published devotional, book, research report, or media artifact. The portal remains a place for planning and review even when the final product has its own interface and location.

## Testimony and creative projects

The desired sequence is context gathering, an initial story draft, an interview, storyboard development where appropriate, production, and review.

**CREATIVE-01 — Look back first.** Gather relevant prayers and life context around a relationship or event before asking the user to reconstruct everything. An example is a testimony about how God has moved in his relationship with his son, Creighton.

**CREATIVE-02 — Interview to fill gaps.** Use a natural conversation to obtain missing details and clarify the meaning of the story. Incorporate the user's corrections into the draft.

**CREATIVE-03 — Review the storyboard together.** Display frames on the phone while discussing them by voice, and accept changes to scenes, narration, and sequence. Keep prayer-derived facts and creative visualization distinguishable.

**CREATIVE-04 — Continue the established production process.** Inspect and connect to existing RLM-ADK media workflows before proposing a replacement. Research current interfaces and output handoffs; do not assume prior pipeline work is already connected to this portal.

**CREATIVE-05 — Deliver actual media.** The user can consume the resulting art, story, or testimony video on his phone and share outputs through separate channels.

## Other project and task types

**LIFE-01 — Everyday delegation.** Support tasks smaller than projects, including tax-related work, finding a car service provider, arranging appointments, and maintaining a record of oil changes. The exact execution authority and standing preferences for purchases, filings, and bookings remain to be established; routine steps should use existing applicable backend authority.

**LIFE-02 — Recurring responsibilities.** Use available evidence to answer questions such as when the last oil change occurred and to identify what needs attention next. Unknown or conflicting records should prompt a focused question when needed.

**PARTS-01 — Ongoing personal parts mapping.** Track parts the user identifies, their roles and relationships, and reflections over time. The user wants to integrate Dick Schwartz's IFS vocabulary with his own Christian framing of Jesus working through him. Present this as his intended personal framework; do not treat it as the definition of standard IFS or as an established clinical conclusion.

**MINISTRY-01 — Develop a Holy Disturbance into a project.** Conversations can develop into a ministry or standalone product. An example is supporting veterans and connecting them to groups or experiences important to the user. Exact names, intended audience, and commitments should be clarified within that project before execution.

Parts mapping, a Christian IFS book, original devotionals, ministry services, and testimony are examples of the same general project capability. They do not each require a separate hard-coded top-level application.

## Experience and operational expectations

The interface should have clear prayer and quiet controls, an understandable recording state, and a simple way to view the current artifact or project. Ordinary operations should require little screen attention. Project selection and priority changes should also be available by voice.

The live-voice API is a foundational component, with OpenAI as the user-selected performance benchmark and cost as a significant driver of the ultimate choice. Voice may be provided by an external service while reasoning and execution use local or hosted models. The user is open to local inference, OpenAI, Claude, or another suitable backend; provider selection remains unresolved. The separate live-voice landscape research prompt compares recent open-source and hybrid options against this experience.

Research must cover unreliable connectivity, application restarts, recording interruptions, simultaneous phone audio, deferred reminders, and long-running jobs. Identify what must remain durable outside any individual voice session and how the user learns about incomplete work.

This is a private deployment. Preserve existing safeguards and applicable repository rules. Do not add security hardening, LLM isolation layers, or related architectural restrictions without explicit prior permission and a concrete failure case. Honor the retired-capability list in AGENTS.md.

## Decisions for architecture research

1. Native iOS implementation in RawleySM/rlm-adk-ios: verify membership activation and signing readiness, intended iPhone/iOS version, background and locked-screen recording, other apps, calls, and power use. Evaluate a companion only if device evidence requires it.
2. Stored recording form, retention, transcription cadence, and how recorded material becomes usable life context.
3. Wake and quiet phrases, prayer boundaries, and the difference between an explicit command, an exploratory idea, and speech captured from the surrounding setting.
4. Additional availability inference, silent notifications, and handling of deferred or stale alerts.
5. Backend consolidation: existing task/project/media/calendar contracts; source-backed retrieval and knowledge organization; Theographic adapters and semantic joins; and evidence-based comparison of relational/hybrid retrieval, Neo4j, Microsoft GraphRAG, and optional temporal graph tooling.
6. Standing task preferences and how existing execution authority applies to delegated practical work.
7. Scripture and devotional source choices and spoken versus inspectable source presentation.
8. A proposed first complete user journey and a phased scope that preserves the eventual vision.
9. Live-voice API and tooling selection: compare recent open-source and hybrid options against the OpenAI performance reference, establish an acceptable quality floor, and identify the cost/latency tradeoffs.
10. Voice usage and monthly budget assumptions; separate Pro subscription usage from API billing, and compare paid session lifetime with continuous local recording and background work.

## Scenario acceptance criteria

| Scenario | Desired observable behavior |
| --- | --- |
| Morning devotional interrupted by a question | Playback stops; the answer refers to the actual passage heard; the prior reading position is recoverable by request. |
| Prayer contains a long pause | Recording continues without an agent response or automatic prayer termination. |
| Prayer ends | The prayer is saved; the system waits for the next user instruction without automatic reflection. |
| A calendar reminder becomes due during quiet | No speech occurs; recording and background work continue; the deferred event has a known state. |
| The user says wake after explicit quiet | Spoken engagement becomes available again; the user's request can be handled with preserved context. |
| The user finishes an ordinary exchange | The assistant remains available for the allowed proactive events; it does not silently enter explicit quiet. |
| A project phase needs attention while available | A concise spoken prompt explains the phase and the required input. Routine status remains queryable. |
| A heavy research request is accepted | A real action item is acknowledged and remains inspectable after the voice session ends. |
| The user begins a testimony | Existing context informs a first draft; interview answers and spoken revisions carry forward into storyboard and production work. |
| A backend project finishes | The actual deliverable can be opened or played, including when it lives outside the portal. |
| A connection or session ends during work | Previously accepted tasks and saved project state remain recoverable; uncertain recording or execution state is represented. |
| A corrected prayer or repaired source arrives | Relevant derived context is updated using the source revision/change contract; prior evidence and the correction remain distinguishable. |
| Theographic supplies context for a verse | The entity/passage link resolves to retained source evidence and the appropriate Scripture edition; unresolved mappings are visible. |
| A simulator build succeeds | Report simulator success separately from signed-device installation and microphone/background proof. |
| A live-voice alternative is recommended | Its interruption, naturalness, latency, backend handoff, and mode behavior are compared against the OpenAI reference; limitations and total cost are explicit. |
| The app is recording during a long quiet or prayer interval | Capture and saved mode state persist; any paid voice-session lifetime and cost are separately represented rather than inferred from microphone activity. |
| The platform cannot meet continuous phone recording | The research identifies the exact limitation and proposes explicit alternatives without claiming the requirement has been satisfied. |
