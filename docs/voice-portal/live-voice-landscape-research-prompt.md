# Research Prompt for Recent Open Source Live Voice Tooling

Prepared October 12, 2026. Copy the standalone prompt below into a research session. Also provide
`user-needs-and-requirements.md` and the existing architecture study when available. This prompt
requests research and a comparison plan, not provider experiments or deployment.

---

Research the **recent open-source and open-weight landscape for live conversational voice** for my
private, single-user RLM-ADK iPhone application. Produce a current, source-backed shortlist and a
cost-aware recommendation. Live voice is a foundational part of the application. The required
experience is a natural conversation with interruption, corrections, interviews, and context
switching while an independent backend performs durable work.

## Performance reference and selection priority

Use **OpenAI's live-voice API as my selected performance gold standard**. Identify the exact current
GPT-Live and/or Realtime API, model, connection mode and date used as the reference. Evaluate
naturalness, response latency, simultaneous listening/speaking, interruption, contextual continuity,
and backend delegation against that experience. This is my product benchmark, not an assertion that
an independent comparison has already proved OpenAI superior to every candidate.

**Cost is a significant driver of the ultimate choice.** Recommend an affordable design that meets
the required conversational experience. OpenAI can remain the benchmark or a selective option even
if another stack is chosen for routine use. If a cheaper candidate loses essential quality, explain
that loss and compare hybrid arrangements. Do not choose a low headline price while omitting the
compute, integration, maintenance or backend costs that make it usable.

I have ChatGPT Pro. Treat subscription usage and application API billing as separate budgets; do
not assume Pro supplies API credits. Verify applicable billing and funding arrangements from current
official documentation without inspecting credentials or changing my account. Do not assume either
a universal prepaid-only requirement or that subscription access authorizes arbitrary API usage.

## Application context and requirements

- The first user is Rawley. The native SwiftUI/Xcode workspace is **RawleySM/rlm-adk-ios**. I purchased
  the $99 Apple Developer Program membership. Verify device/OS and signing readiness separately.
  The existing scaffold and unsigned simulator workflow do not prove device audio capability.
- My morning routine begins with a verse and a few-minute devotional. I can interrupt to ask for
  historical context, theological comparisons, personal application, or a different activity.
  Preserve the passage, edition, actual playback position and relevant conversation context.
- Speech must support free-flowing interviews and revisions, such as drafting a testimony about my
  relationship with my son Creighton, filling missing details and reviewing a storyboard on the phone.
- Recording is intended to continue throughout normal operation, including explicit quiet. Recording,
  transcription, live conversational inference and server-side execution are separate lifetimes.
- Explicit **quiet** suppresses all speech until explicit **wake** or the chosen assistant name.
  Calendar events and important work do not override it. Ordinary conversation finishing does not
  automatically enter quiet. The exact words are not yet chosen.
- **Prayer capture** records a prayer addressed to God. Long pauses never trigger a response or
  automatic prayer termination. An explicit control ends it; save and wait for my next instruction.
- When available, initial proactive speech covers calendar preparation/departure and project phases
  needing input. Routine project status is on request. Background work continues in prayer and quiet.
- Heavy research or project requests become durable action items. Acknowledge the actual recorded or
  accepted state, survive the voice session ending, and return real artifacts through the portal.
- RLM-ADK reasoning/execution and project state must remain independently usable. Scripture, prayer,
  sermon and life-context pipelines already exist. Theographic is a biblical knowledge candidate;
  its data role is distinct from selecting the live-voice stack.
- Most interactions should work without screen attention. Display storyboards and outputs when useful.
  A speech provider's vision support must not be assumed to cover the artifact review path.

## Freshness and evidence requirements

1. Establish the actual research date. Search for releases, papers, model weights and substantial
   capability changes in the **last 90 days**, expand discovery to **180 days**, and include older
   actively relevant baselines where useful. Do not exclude a strong older model solely because it
   lacks a recent release; label its age and current maintenance evidence.
2. Go beyond a fixed list of familiar projects. Use primary repository release notes, model cards,
   publisher documentation, papers and maintained SDK documentation. Read the supporting material.
   Search snippets, popularity and a recent generic commit are not proof of a recent voice capability.
3. Record repository/model identity, release/tag or commit, publication/release date, access date,
   current maintenance/archival status and what actually changed. Distinguish a paper/demo from
   downloadable weights, a maintained inference implementation, and a usable native integration.
4. Separate claims from independent measurements and proposed tests. Record hardware, codec, model
   precision, network and workload behind latency/cost claims; do not compare incompatible numbers.
5. Verify licenses separately for code, weights, codecs and dependencies. Distinguish open source,
   open weights and hosted-only components. Preserve applicable source terms and existing safeguards;
   do not introduce an unrelated security-hardening or LLM-isolation architecture.

## Candidate layers and discovery starting points

Compare layers and complete arrangements rather than treating every project as an interchangeable
voice model. Verify these starting points at research time and discover newer alternatives:

- **Voice frameworks, transports and turn orchestration:** [LiveKit Agents](https://github.com/livekit/agents),
  [Pipecat](https://github.com/pipecat-ai/pipecat), their current native iOS/Swift client paths, and other
  maintained realtime transports. Separate the open-source framework/server from any paid hosted
  service or model dependency. Framework selection may complement OpenAI rather than replace it.
- **Speech-to-speech and full-duplex models:** [Kyutai Moshi](https://github.com/kyutai-labs/moshi),
  [NVIDIA PersonaPlex](https://github.com/NVIDIA/personaplex), current relevant Qwen/GLM/other speech
  model releases, and newly discovered candidates. Verify whether each supports simultaneous input
  and output, stable interruption, persona control, transcripts and external reasoning/tool handoffs.
- **Composable local or hybrid stacks:** recent streaming STT, VAD/turn detection, text reasoning and
  streaming TTS components. Include maintained Whisper-family and neural TTS implementations where
  competitive. Do not mistake chunked transcription or audio output for a complete live conversation.
- **OpenAI reference and lower-cost hosted comparators:** refresh official capabilities, models,
  pricing and session accounting. Keep this comparison distinct from the open-source classification.

Inspect this repository and the narrow existing RLM-ADK interface needed for connection. Verify the
actual available local server hardware, architecture and software compatibility through read-only
evidence where accessible. Do not assume a consumer x86 CUDA example runs unchanged on the available
Spark/ARM host, or that a server model can run on the iPhone. Keep the native audio path under app
control regardless of inference location.

## Compare the complete conversational experience

For each credible stack, evaluate:

1. Streaming and full-duplex behavior; time to first audible response; conversational latency
   distribution; interruption stop time; overlap handling; endpointing; short acknowledgements;
   long pauses; voice expressiveness; and recognition of Scripture names and personal names.
2. Whether it can meet prayer silence and explicit quiet/wake through deterministic app behavior,
   and how it avoids turning captured ambient speech into an unintended instruction. Preserve the
   requested continuous recording behavior.
3. Back-end delegation while conversation continues; tools or external-service integration; transcript
   access; carrying a correction into ongoing work; cancellation and stale-result behavior; and
   preserving durable operation/project identity when the voice connection closes.
4. Swift/iOS client integration, audio formats, WebRTC/WebSocket or other transport, echo management,
   speaker/Bluetooth routes, screen lock, interruptions, offline intervals and reconnects. Distinguish
   documented platform support from behavior proven on an actual iPhone.
5. Hardware/VRAM/RAM needs, CPU/GPU architecture, quantization quality, codec dependencies, serving
   and startup latency, concurrency with existing workloads, power and sustained operation. Separate
   a browser demo from a service that can support this application.
6. The work needed to achieve the required experience: turn orchestration, tool bridge, durable
   state, capture/upload, artifact review and observability. Prefer existing owners and adapters over
   replacing working RLM-ADK machinery merely because a voice framework offers a job abstraction.

## Cost analysis and session lifecycle

Refresh prices on the research date. The October 12 starting evidence is OpenAI's published
`gpt-live-1` rate of $0.05 per active minute; it is a dated reference, not a future guaranteed rate.
GPT-Live bills active-session silence and backend waits, and microphone muting does not close the
session. Verify each alternative's distinct accounting instead of applying that rule to every API.
Use [model/pricing documentation](https://developers.openai.com/api/docs/pricing),
[voice usage accounting](https://developers.openai.com/api/docs/guides/voice-latency-cost), and the
[subscription/API pricing distinction](https://learn.chatgpt.com/docs/pricing).

Show transparent formulas and a 30-day monthly comparison for 30 minutes, 2 hours and 8 hours of
active voice sessions per day, plus a 24-hour session sensitivity case. Separate speaking/listening
time from session-open time. Use stated turn lengths, cache assumptions and context growth for
token-priced services; do not invent a per-minute equivalence from token rates alone.

Separately account for continuous local recording, transcription cadence, storage, voice reasoning,
RLM-ADK backend/tool use, network/hosted transport, GPU hosting or local power, hardware acquisition
if needed, competing workload capacity, implementation and ongoing maintenance. Existing hardware
may reduce cash outlay but does not establish zero operating or opportunity cost. Compare recurrent
cash costs and engineering effort separately instead of hiding them in an arbitrary score.

Compare always-open paid voice with locally persistent capture plus paid sessions when useful,
self-hosted voice, and hybrid routing. Explain how wake, allowed calendar/phase prompts, interrupted
devotionals and resumable interviews still work when sessions close. Identify any quality, startup
latency, offline or reliability penalty. Cost savings must not silently remove continuous capture,
quiet/prayer behavior, or proactive work. A monthly target budget is not supplied; show sensitivity
and break-even assumptions rather than imposing an invented cap.

## Required deliverables

Produce:

1. A dated landscape map and shortlist with release/commit evidence, component roles, licenses,
   hardware/native-client compatibility, capabilities and maturity. Include noteworthy recent
   entrants and explain why outdated or unsuitable candidates were excluded.
2. A comparison against the exact OpenAI performance reference. Clearly separate documented claims,
   comparable published measurements, unknowns and proposed tests. Define a proposed quality floor
   from the user journeys; do not fabricate achieved latency or quality scores.
3. Complete-stack options: OpenAI-based; open-source framework plus selected models; self-hosted
   speech-to-speech; composable STT/reasoning/TTS; and hybrid where justified. Give implementation
   effort and a workload-based total-cost table with sensitivity/break-even analysis.
4. A recommendation, nearest alternative, material compromises and the conditions that change the
   choice. Explain which parts of OpenAI's experience alternatives can match and what still needs
   proof. Cost should materially influence the conclusion while preserving the required experience.
5. A voice-session/capture/engagement/backend-state diagram and the minimum interfaces needed for
   native iOS and RLM-ADK. Preserve stable job/context/artifact identity across provider sessions.
6. A bounded, reproducible evaluation plan: interrupted devotional; spoken correction during backend
   work; long prayer silence; explicit quiet with a due reminder; wake; a testimony interview; a
   voice session ending after task acceptance; and network/audio interruption. Specify measurements,
   source/audio selections and what success proves. Propose benchmarks; do not run paid API calls,
   record new audio, occupy serving hardware or start long-running trials under this research request.
7. An annotated primary-source list with dates, and unresolved choices such as phone/OS, preferred
   voice, latency tolerance, active-use hours, budget and available compute. Continue research with
   explicit nonblocking assumptions rather than stopping for optional preferences.

Follow applicable AGENTS.md instructions and the retired-capability list. This is a private project.
Write the requested research artifacts, but do not deploy, alter source pipelines, purchase credits,
start cloud runners, publish products or change account/billing settings as part of the review.
