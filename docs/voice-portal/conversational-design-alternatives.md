# Conversational voice portal: design alternatives for review

Date: 2026-10-13. Author: Claude Fable 5.1 (session 02018c82). Status: **alternatives for
Rawley's review, nothing built yet.** Every option below is grounded in code or documents that exist
today; file references point at the checkout that holds them.

## What is being decided

The target experience, in Rawley's words on 2026-10-13:

1. Say "Lord…" or "let's pray, Jesus…" and be in prayer, uninterrupted until "amen".
2. Say "check in on that project, I don't remember the name, but it was the one where we…" and have
   the system identify the project from the description and report where it stands.
3. Ask for "a word from God in response to my morning prayer" (or devotional): the system finds the
   prayer transcription, identifies verses that relate to its themes, and reads Scripture from them.
4. Ask for "different interpretations": the system queries the Scripture, sermon-metadata and
   theologian-text products the rlm-adk pipeline is producing and answers from the enriched data.
5. Everything above through one continuous conversation, with the app speaking in its own, new,
   non-Apple voice (not Rawley's clone).
6. Integrated as tightly into rlm-adk as the architecture allows: declared residents, host
   assignments like project coordination and lifecycle.
7. Built and improved in a closed loop with `voicesim` (Rawley's cloned voice as the test speaker).

Constraints established this week: the phone runs on-device recognition and continuous capture
(`RLMADK/Model/PortalModel.swift`, reducer in `PortalMode.swift`); the backend is the stdlib
`voice_portal` service on the desktop (`backend/voice_portal/`), grounded on an FTS5 projection of
209 prayers, 100 teaching records, 1,189 World English Bible chapters and Theographic; reasoning is
`qwen-coding-b` on Spark-B at 630 to 830 ms first delta; Fish S2-Pro on Spark-B synthesises at
real-time factor 3.5 (eager, no triton on aarch64); residents are declared in a sealed launch
document with six closed duty kinds (`rlm_adk/residents/duties/definitions.py`); the current
cycle 15 candidate declares Steward (`stwref`), Prayer (`pryc12`) and Vision (`visc12`) and is
preflighted, not activated.

The dossier ("Systems Architecture for Replacing OpenAI GPT-Live", Google Drive) is used here for
its principles, not its scale: the reflexive/cognitive split with an RPC barrier, speculative versus
authoritative turns, the 320 ms backchannel rule, bridge fillers after 350 ms, and the
Talk/Work/Commit pattern. Its WebRTC/WARP, Moshi and H100 pool material is for 1,000 concurrent
sessions; this system has one user on a tailnet.

Nine decision axes follow. Each lists the alternatives, what they cost and buy, and a recommendation.
A summary table and the decisions I need from Rawley close the document.

---

## Axis 1. Where the conversational intelligence lives (the rlm-adk integration question)

### 1A. Portal-owned skills, rlm-adk for heavy work only

The `voice_portal` service grows an intent router and skills in its own Python: prayer boundaries,
word-from-God, project check-in, interpretations. rlm-adk is reached the way it is today, through the
work outbox (`backend/voice_portal/outbox.py`) posting `allocatorctl inbox post --to root`.

* Buys: fastest path to working behaviour; one deployable; sub-second answers; no launch change.
* Costs: the domain logic lives outside any resident. Residents cannot improve it, learn from it or
  reuse it; the register, pairings and enrichment products are read by code nobody in rlm-adk owns.
  This is the "monolithic executor" the dossier warns against, in a smaller form.

### 1B. One skill package, two callers

The conversational skills are authored as an rlm-adk **skill package** in a project repository
(`SKILL.md` frontmatter plus `SKILL_EXPORTS`, the contract in `rlm_adk/skills/loader.py`). The portal
imports the same package in-process for the fast path; residents load it into their REPLs through
`spec.requests.skill_selectors` for deep work and for improving it. The package reads the data
products through the readers that already exist (`scripture_reference_readers`, the prayer corpus
view, the register's `registry.py`).

* Buys: one implementation of each domain method, visible to future reasoning agents through
  frontmatter, improvable by residents and delegates, benchmarked by retrace `skill-unit` benches
  and by voicesim. No host change.
* Costs: ownership of the running conversation is still the portal process on the desktop; the
  resident improves the code but does not hold the session. Two deployment cadences (portal restart
  vs package release).

### 1C. A declared Companion resident that owns the conversation, with a new host duty kind

A new resident node is declared in the launch document (a child of the Steward, warm or cold start),
granted the prayer, Scripture, sermon, enrichment and register projects, plus a new project repository
`voice-companion` that holds the skill package from 1B. A new closed duty kind, provisionally
`conversation_service`, is added beside the six in `rlm_adk/residents/duties/definitions.py`,
`contracts.py`, `operations.py` and `procedures.py`, with holder rule `project` (slot keyed by
`voice-companion`), revocation `future_admission` and a qualification bench
`live_repl_assignment_conversation` like the existing six. Its operations:

| Operation | Meaning |
|---|---|
| `conversation.publish_endpoint` | The holder records the endpoint (URL, protocol version, generation) of the conversational service it runs in its own named kernel; the host writes a typed receipt the portal reads. |
| `conversation.inspect` | Typed view: current endpoint, session count, last turn, pending work, degraded flags. |
| `conversation.accept_work` | Converts a spoken request into an accepted work item with an operation id (the Commit step of Talk/Work/Commit begins here). |
| `conversation.commit_result` | Attaches a finished deliverable (artifact digest, project commit) to a work item and queues the spoken acknowledgement, which the phone delivers only when the mode allows speech. |

How the live path works: the Companion runs its conversational service as a long-lived detached
submission in its project coordinate (`execute_code(run_in_background=True, detach=True,
repl_name='project:voice-companion', …)`, the pattern `procedures.COORDINATION` already describes).
The service holds warm indexes and a warm model session. The portal on the desktop forwards each
turn to that endpoint and streams the reply back to the phone. The Companion's own reasoning turns
handle escalations, review transcripts, grade the voicesim benchmark and improve the package.

* Buys: the conversation is a resident's continuing responsibility in the sense of
  `rlm_adk_docs/resident_learning_domains.md`; host receipts cover endpoint, acceptance and commit;
  the Steward can direct it with `direct_branch`; work requests become project work with provenance;
  improvement is the resident's standing work, closed by the voicesim loop.
* Costs: a host change (a seventh duty kind, broker handlers, a qualification bench, launch
  authoring, documentation) and a launch activation; drains and restarts of the resident runtime
  stop the service, so the portal needs a degraded mode. Two things to verify before committing to
  it: that a detached submission may run indefinitely as a service without tripping the inert
  submission notice (`dynamic_resident_contracts.md` § "No-effect streak notice"), and how the drain
  hold treats it.

### 1C-lite. Same resident and service, no new duty kind

Identical to 1C except the host is untouched: the Companion holds ordinary `project_coordination` for
`voice-companion`, runs the service in that coordinate, and publishes its endpoint by writing a receipt
file in the project repository's state directory that the portal polls. Work acceptance and commit are
project records in the register, not duty operations.

* Buys: everything in 1C that matters for behaviour, without a host PR or a schema change; can ship
  in the cycle 15 launch by adding one node and one registration.
* Costs: no typed host view of the conversation; the endpoint receipt is a convention, not a
  contract; promotion to 1C later is additive.

### 1D. Every turn is a resident request

The phone's turns are delivered as instance mail to the Companion, which answers from its reasoning
loop. No fast path.

* Buys: the purest integration; nothing outside rlm-adk reasons.
* Costs: a resident turn is a model request plus `execute_code` plus delivery; tens of seconds to
  minutes. Unusable for conversation. Right for the Work tier only.

**Recommendation: 1C-lite now, 1C as the follow-on.** The skill package of 1B is the substance in
both. 1A survives only as the portal's degraded mode when the Companion's service is unreachable
(it answers from its own retrieval with the same package, says so, and queues anything durable).
1D is how the Work tier already works and stays that way.

---

## Axis 2. Turn-taking: prayer boundaries, barge-in, backchannel (the Talk plane)

### 2A. Arbitration on the phone

Extend the pure reducer in `RLMADK/Model/PortalMode.swift` and the controller in `PortalModel.swift`:

* **Prayer by address.** When a new utterance's first words match an address pattern ("Lord",
  "Father", "Heavenly Father", "Dear God", "Jesus", "Holy Spirit", "Abba", "Our Father", "let's
  pray", "let us pray", "I pray"), enter prayer on the partial, not the final: the capture opens
  within a second of the first word and the journal keeps the whole utterance because the partial
  text includes the address. Quiet does not block prayer; it only blocks speech.
* **Amen.** Prayer ends when the partial text ends with "amen" (also "amen, amen", "in Jesus' name,
  amen") and 1.2 s pass with no new words. More words cancel the pending end. The recogniser never
  end-points during prayer (`EndpointClock` already does this). Buttons stay as the deterministic
  fallback.
* **Backchannel vs barge-in** while the app speaks: an utterance under 320 ms whose text is in the
  backchannel set ("mm-hmm", "yeah", "right", "okay", "amen" outside prayer) ducks the output by
  3 dB for 400 ms and does not transition; anything longer, or with a recognised intent, cuts the
  output within 120 ms (the dossier's rule, implemented on the existing `userSpeechStarted` path).
* **Bridge fillers.** If the backend's first delta has not arrived 350 ms after the final, the app
  speaks a short bridge from a local list ("Let me look."), conditioned on the detected intent.

* Buys: works offline, lowest latency, no round trip in the prayer boundary, testable in XCTest.
* Costs: the policy is compiled into the app; changing a phrase means a build.

### 2B. Arbitration on the server

Stream partials to the backend (the socket already carries `partial_transcript`) and let it decide
prayer entry, amen, and barge-in.

* Buys: one place for policy; the voicesim text harness tests it directly.
* Costs: a prayer boundary that depends on connectivity and adds a round trip; the existing design
  deliberately keeps prayer independent of the session (user needs MODE-03, DEVICE-03).

### 2C. Phone decides, server publishes the policy

As 2A, with the phrase sets, timing thresholds and backchannel list fetched from the backend as a
versioned **conversation policy** document (`GET /policy`), cached on the phone, and shared with the
voicesim harness so the same file drives the app and the benchmark. The reducer stays pure and
tested; only its tables come from the policy.

**Recommendation: 2C.** It keeps MODE-03 (prayer never depends on a session) and still lets the
Companion resident tune the policy from benchmark evidence without an app build.

---

## Axis 3. Speech recognition authority

### 3A. On-device only (today)

`SFSpeechRecognizer` on-device, dictation hint, partials and finals. Free, offline, fast; quality is
what the device gives (the 2026-10-13 session showed one- and two-word finals dominating).

### 3B. Server ASR only

Stream utterance audio to Cohere Transcribe on Spark-A (16 kHz, ~105 s cold start, then fast).
Better accuracy on prayers (the phone trial published WER 0.0 through the ingest). No partials, no
offline, cold-start risk on the first turn of the morning.

### 3C. Speculative on-device, authoritative server

On-device partials drive the floor (address, amen, barge-in) and the speculative transcript on the
screen; the backend re-transcribes the utterance's audio slice with Cohere when the utterance is
longer than a few words or when the on-device final looks degraded, and the authoritative text is
what the Companion reasons over. Prayers already follow this split (journaled on-device text, Cohere
through the intake). The voicesim phone loop can measure the two transcripts against the same
fixture, so the threshold is a benchmark result, not a guess.

**Recommendation: 3C.** The capture segments the app already writes make the audio slice available
without a second recording path.

---

## Axis 4. The app's voice (built in, not Apple's, not Rawley's)

### 4A. On-device neural TTS: Kokoro-82M through sherpa-onnx

sherpa-onnx ships an iOS xcframework and Swift examples; Kokoro-82M (24 kHz, 50+ voices, Apache-2)
runs at roughly real-time factor 0.3 to 0.5 on an A14 CPU. The existing `PhraseQueue` already cuts
streamed text into phrases, so the first phrase is audible within a few hundred milliseconds of the
first delta. Assets are 90 to 300 MB depending on quantisation and voice set.

* Buys: literally built into the app; works offline; fillers and confirmations cost nothing; one
  voice identity chosen once.
* Costs: an xcframework dependency built on the cloud Mac; model assets in the bundle or downloaded
  on first run; CPU and battery during long readings.

### 4B. Server TTS streamed from Spark-B

Kokoro on the GPU (real-time factor about 0.02) or Fish S2-Pro with a non-Rawley reference voice,
streamed as PCM frames over the existing WebSocket (binary frames) or an HTTP chunked endpoint.

* Buys: no model in the app; any voice we can host; S2-Pro expressiveness and tags for readings.
* Costs: every word needs connectivity; S2-Pro at real-time factor 3.5 cannot stream live, only
  pre-render; a server voice outage silences the app.

### 4C. Hybrid: on-device for conversation, server-rendered reading voice for Scripture

4A for replies, bridges and status; a cached **reading voice** for Scripture and devotionals,
rendered ahead of time by S2-Pro with a chosen non-Rawley reference voice (a public-domain reader) and
keyed by edition revision and verse range, so the morning passage is ready before it is asked for.
Both go through one `VoiceOutput` protocol behind the existing `SpeechGate`, so quiet, prayer and
barge-in cancel them identically.

**Recommendation: 4C,** with 4A shipped first and the reading voice added once the Companion exists
to pre-render tomorrow's passage. Voice identity is a decision for Rawley (Axis 10).

---

## Axis 5. Intent routing

### 5A. Rules plus a small classifier

Address and command phrases by rule (they must be deterministic); everything else classified by
`qwen-coding-b` with a short few-shot prompt into a closed set: `prayer`, `word_from_god`,
`interpretations`, `project_checkin`, `work_request`, `devotional`, `status`, `general_question`,
`chitchat`. One extra model call of about 150 ms before the grounded turn.

### 5B. Model tool-calling

Give the reasoning model tool schemas (find_project, word_from_prayer, read_passage, accept_work)
and let it choose; no separate router.

* Buys: one call; graceful for mixed requests.
* Costs: Qwen tool-calling reliability through the Spark owner is unproven here; harder to
  benchmark the routing decision in isolation.

### 5C. Resident-authored router, promoted from evidence

Start with 5A inside the skill package; the Companion keeps the misrouted transcripts from the
benchmark and the real log, and promotes rules or few-shot examples as its standing work.

**Recommendation: 5A now, 5C as the improvement loop.** The routing accuracy is a benchmark number
from day one.

---

## Axis 6. Identifying a project from a vague description

### 6A. Retrieval over the project work register

The register on the desktop (`project-work-registry/project/registry/project-work/records/*.json`)
holds nine records with title, summary, work items, branches, results, outputs and unknowns. Index
title, summary, work-item descriptions and recent commit subjects with FTS5 (and later embeddings),
return the top three with scores, confirm by voice when the margin is small ("the Scripture
enrichment project, where we built the work ledger and child tasks?"), then speak the five axes the
register keeps: plan, branches, results, checkpoints, outputs.

### 6B. Ask the Steward

Mail the Steward (the register's maintainer) and wait. Correct owner, wrong latency (minutes).

### 6C. Companion-maintained project memory

6A, with the Companion refreshing its own index whenever the register changes and adding what the
register does not hold: the Steward's statements, delegate briefs and the user's own words from
earlier check-ins ("the one where we…" is often how he described it last time).

**Recommendation: 6A inside the skill package, maintained as 6C by the Companion.**

---

## Axis 7. "A word from God in response to my morning prayer"

### 7A. Retrieval and reading, immediate

Find the prayer (the capture just ended, or today's publication in the prayer corpus); extract its
themes and petitions with one model call; gather candidate passages from three sources already on
disk: the prayer-to-Scripture pairings ledger (`prayer-life-c12`, 615 rows over 206 prayers), FTS over
the retained edition, and Theographic entities; choose two to four passages; read them in the reading
voice with a one-sentence frame each and the citation available on screen. Target under five seconds
to the first verse.

### 7B. Composed by the resident

The Companion commissions a child or delegate to compose a devotional response that connects the
prayer, the passages and relevant teaching, and delivers it through Work/Commit when ready.

### 7C. Both

7A at once; the app offers the fuller 7B ("I can bring you a fuller word in a few minutes") and
delivers it when the mode allows speech.

**Recommendation: 7C.** 7A is the floor the benchmark measures (passage relevance judged against the
prayer's themes); 7B is where the enrichment products and the Prayer resident's work show their value.

---

## Axis 8. "Different interpretations"

### 8A. Grounded multi-source answer from the teaching projection

The backend's teaching family already holds Desiring God and Eleven22 texts; the Scripture reference
store has Edwards (ThML) and BibleProject transcripts not yet projected. Retrieve per source, label each
voice by teacher and tradition, and answer with the distinctions the system prompt already enforces
(Scripture, a teacher's reading, personal context).

### 8B. From the enrichment products as they land

Query the Scripture enrichment project's products (concordance, dossiers, claim and denomination
mapping when produced) first, and fall back to 8A. The package reads them through the project's own
readers, so new products become usable without portal changes.

### 8C. Delegate research on request

"Go deeper" dispatches a Claude or Codex delegate through the Work tier and returns a report.

**Recommendation: 8A and 8B together, 8C by request.** Projecting Edwards and BibleProject into the
teaching family is a small importer change that raises 8A's ceiling immediately.

---

## Axis 9. Work tier and delivery back to the conversation

### 9A. Outbox to the instance mailbox (today)

`POST /work` queues an operation; `dispatch` posts `allocatorctl inbox post --to root`. Status is
reconciled from `inbox list`. Delivery back is a poll of `/work`.

### 9B. Companion accepts, dispatches and commits

The Companion holds the work item (duty op in 1C, register record in 1C-lite), dispatches delegates
and children itself, and commits the result with a spoken acknowledgement queued for the phone. The
Steward sees it as project activity. This is Talk/Work/Commit end to end.

### 9C. Direct agent delegate from the portal

The portal launches a Claude Code or Codex delegate itself (as the voicesim delegate test did).
Fast and simple; invisible to residents and the register.

**Recommendation: 9B, with 9A as the transport until the Companion is live.** 9C stays a test tool.

---

## Cross-cutting: transport, latency budget, deployment

* **Transport.** Keep the JSON WebSocket for control and text and add binary frames for audio
  (utterance slices up, reading-voice PCM down). WebRTC or LiveKit is only worth it if a full-duplex
  speech model replaces the cascaded path; for one user on a tailnet the gain is small and the
  dependency large.
* **Full-duplex model instead of a cascade?** Moshi-class models give the dossier's 200 ms loop but
  cannot be grounded on the prayer corpus, cannot cite, and cannot speak in a chosen voice. Not now;
  the benchmark will say what the cascade costs in latency.
* **Latency budget for the cascade:** end-point 300 to 600 ms, router 150 ms, retrieval 50 ms, first
  reasoning delta 650 to 850 ms (warm), first phrase to audio 300 ms on-device. About 1.5 to 2 s to
  first word, bridged at 350 ms. Prayer boundaries do not touch the network at all.
* **Deployment.** The portal stays on the desktop (stores, allocator, register are there); reasoning
  and ASR on the Sparks; the Companion's service in its coordinate on the desktop allocator host. Fish
  S2-Pro stays on Spark-B for pre-rendering and for voicesim.
* **Degraded modes.** Companion service down: portal answers from the package itself and says so.
  Spark reasoning down: the phone keeps prayer, quiet, and local status; replies become "I can't reach
  the reasoning host". Phone offline: prayer and capture continue; everything else waits.

---

## Closed loop with voicesim (how any choice above gets measured)

`voicesim` (`~/rlm-voice-sim`) already closes TTS → desktop speakers → Spark-B microphone → Cohere →
WER, and TTS → speakers → iPhone app → isolated intake → WER (passed today at WER 0.0). The
conversational build adds a `suites/conversation-core` suite and three runners:

| Layer | What it exercises | Metrics |
|---|---|---|
| Text harness | The skill package directly: router, project resolution, word-from-God, interpretations, with scripted transcripts | routing accuracy, project top-1, passage relevance (judge), citation validity, latency |
| Room loop | Speakers → Spark-B mic → Cohere → backend, no phone | ASR-robust routing, end-to-end reply latency |
| Phone loop | Speakers → iPhone (on-device ASR, floor arbitration) → backend → app voice → Spark-B mic records the reply → Cohere transcribes it → judge | prayer-boundary accuracy (no false end across 3 to 6 s pauses; amen-to-end latency), barge-in and backchannel behaviour, glass-to-glass latency from the `[[marker]]` to the first reply audio, spoken-answer fidelity |

Scripts carry front matter for two speakers, so the assistant's expected line can be synthesised in
a second voice and overlapped after a `[[silence]]` to test barge-in and backchannel. The Companion
runs these suites as standing work; a change to the package or the policy is accepted on benchmark
evidence, which is the self-improvement loop rlm-adk is built for.

---

## Summary of recommendations

| Axis | Recommended | Why |
|---|---|---|
| 1 Intelligence | 1C-lite now, 1C later; 1A as degraded mode; 1D for work | Resident ownership without blocking on a host change |
| 2 Turn-taking | 2C phone decides, server publishes policy | Prayer never depends on a session; policy still tunable |
| 3 ASR | 3C speculative on-device, authoritative server | Floor needs partials; reasoning needs accuracy |
| 4 Voice | 4C on-device Kokoro + cached S2-Pro reading voice | Built in, offline, and a fine voice for Scripture |
| 5 Routing | 5A rules + small classifier, promoted (5C) | Deterministic where it must be, measurable |
| 6 Projects | 6A register retrieval, maintained as 6C | The register is the source of truth |
| 7 Word | 7C immediate reading plus composed follow-up | Floor in seconds, depth from residents |
| 8 Interpretations | 8A + 8B, 8C on request | Uses what exists, grows with enrichment |
| 9 Work | 9B through the Companion; 9A transport meanwhile | Talk/Work/Commit with provenance |

## Decisions I need from Rawley

1. **Axis 1:** 1C-lite in the cycle 15 launch, or wait for the full duty kind (1C)? Where should the
   Companion sit, as a child of the Steward beside Prayer and Vision, or under Prayer?
2. **Name and address.** What is the assistant called, and does an ordinary question need its name
   (MODE-05) or is every non-prayer utterance in Available mode a turn, as today?
3. **Voice identity.** Pick the on-device voice (I will prepare three Kokoro samples) and whether a
   separate reading voice is wanted for Scripture.
4. **Amen rule.** 1.2 s of silence after a final "amen" ends the prayer; is a shorter or longer
   window wanted, and should "in Jesus' name" alone also close it?
5. **Project confirmation.** When the register match is uncertain, confirm by voice before reporting,
   or report the best match and let him correct?
6. **Authoritative ASR.** Send utterance audio to Cohere for anything over about eight words, or
   only for prayers?
7. **Audio retention for conversation turns** (prayers are already retained): keep utterance slices
   for the benchmark, or text only?

## Proposed build order once decided

1. Skill package `voice_companion` in a new project repository (router, policy, project resolution,
   word-from-God, interpretations, benchmark text harness), with retrace skill-unit benches and
   voicesim text suites.
2. Portal: `/policy`, binary audio frames, Companion endpoint forwarding with degraded mode, Edwards
   and BibleProject projected into the teaching family.
3. App: policy-driven prayer-by-address and amen in the reducer with tests; backchannel/barge-in
   classifier; bridge fillers; `VoiceOutput` with sherpa-onnx Kokoro; direct-install build 9 over USB.
4. voicesim `suites/conversation-core`, room and phone loop runners with reply capture.
5. Companion resident: project repository registered at launch, node declared in the cycle 15
   document, service in its coordinate, standing work including the benchmark; then the
   `conversation_service` duty kind as a host PR with its qualification bench.
