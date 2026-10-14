# Live voice stack: the dossier's three open-source architectures, weighed for this system

Date: 2026-10-13. Author: Claude Fable 5.1 (session 02018c82). Status: **for Rawley's review; nothing
chosen, nothing built.**

The GPT-Live dossier names four open-source ways to build the real-time half of a voice system:
the Pipecat asynchronous-tool pipeline (its Pattern 1), the LiveKit media server with its Agents
framework and the Observer-and-Worker room (Pattern 2, and the transport of its "sovereign stack"),
the Kyutai full-duplex family (Moshi and Mimi, its reflexive voice engine, with DuplexOmni as the
foreground/background variant in Pattern 3), and an event bus with a workflow engine (Pattern 4).
Pattern 4 is a back-end discipline rather than a voice stack, and it is what rlm-adk already is:
instance mail, `resident_wait` wakes, duty operations, the register and provenance receipts. So this
document weighs the other three as the **Talk tier**, with rlm-adk fixed as the Work and Commit
tiers behind an RPC barrier, as concluded in the previous discussion.

Facts below come from the projects' own repositories and documentation read on 2026-10-13 (sources
at the end). Where a page did not state something, that is said rather than guessed.

## What the stack must do here

| Requirement | Why it is non-negotiable |
|---|---|
| Prayer by address, uninterrupted until "amen", with capture that survives any outage | MODE-03 and DEVICE-03 in the user needs; the phone trial proved the native capture path today |
| Barge-in under about 150 ms, backchannel without cut-off, bridge phrases | The dossier's floor rules; the difference between a companion and a kiosk |
| A new, good voice that is not Apple's and not Rawley's clone | Stated on 2026-10-13 |
| Grounded answers with citations from the prayer, Scripture, teaching and register products | SCRIPTURE-04, DATA-04; the voice tier must call our skill service, not improvise |
| Durable work through rlm-adk residents and delegates, spoken back when the mode allows | Talk/Work/Commit; the Companion resident |
| Runs on what we own | Desktop: x86_64, 12 cores, 31 GB, GTX 970 with 4 GB (no modern GPU work). Spark-A and Spark-B: aarch64 GB10, 128 GB unified, CUDA 12.9, torch works, no triton. iPhone 12 (A14). |
| Measurable with voicesim without Rawley at the microphone | The closed loop is how the system improves |

## The hardware constraint in one line

Anything that needs a GPU runs on an aarch64 Spark. Anything that ships only as an x86_64 image is
either built from source for aarch64 or it does not run here.

---

## Option A. Pipecat (Daily; BSD-2)

**What it is.** A Python framework in which a conversation is a pipeline of frame processors:
transport in → VAD → STT → context → LLM → TTS → transport out, with interruption handling and
asynchronous function calling built in. Transports include SmallWebRTC (its own lightweight WebRTC,
server side and an iOS Swift package), WebSocket, Daily and LiveKit. Turn detection is Smart Turn v3,
an open model that runs locally with ONNX in about 65 ms and is the default user-turn stop strategy;
it needs Silero VAD enabled. Local services exist for Whisper, Moonshine and FunASR speech-to-text,
Kokoro, Piper and Pocket TTS, and Ollama; the OpenAI service points at any OpenAI-compatible
endpoint, which is how it reaches `spark-inference`. Pipecat Flows adds a state machine over the
pipeline. The iOS client SDK is Swift, BSD-2, version 1.3, built on the RTVI protocol, with Daily and
SmallWebRTC transport packages.

**How it would sit in our topology.**

```
iPhone (RLMADK)                         Spark-A                                   desktop
 native capture + prayer journal  ──►  prayer intake (today)                  Companion resident
 (unchanged; prayer never needs        Pipecat pipeline (user service):        + skill service
  the network)                           SmallWebRTC in ─ Silero ─ Smart Turn   (project lookup,
 Pipecat Swift client (SmallWebRTC) ─►   ─ STT (Cohere per utterance, or        pairings, grounded
   mic uplink + bot audio downlink        Kyutai STT if it builds) ─ context      answers)  ◄── RPC
   RTVI messages (mode, captions)        ─ LLM (spark-inference, OpenAI API)         ▲
                                         ─ prayer/floor processors (ours)            │ mail / duty ops
                                         ─ TTS (Kokoro on GB10, streamed)      rlm-adk residents,
                                         ─ async tools ──► skill service          delegates, register
```

**Dossier layers it provides.** Media gateway (SmallWebRTC, modest), floor arbitrator (VAD, Smart
Turn, interruption, speculative transcripts through RTVI), bridge fillers (a processor), the RPC
barrier (async tool calls that never block the pipeline). It does not provide a reflexive S2S model
and does not claim to; it is the cascade the dossier budgets at 720 to 1,500 ms.

**Pros.**

* Runs on aarch64: pure Python plus ONNX; Kokoro and Whisper-class models run on the GB10 the way
  fish-speech does. Nothing has to be ported.
* Per-step control is exactly what prayer needs: a processor that suppresses every bot frame while
  the mode is prayer and watches the transcript stream for the amen rule is a few dozen lines, and
  it is testable in Python with voicesim text and audio.
* Smart Turn is trained on real speech, so a thinking pause is less likely to end a turn than with a
  silence timer; that matters for a user who prays and reflects slowly.
* Async function calling is the dossier's RPC barrier out of the box: the pipeline keeps streaming
  while the skill service or a resident works; the result is injected back as context.
* Pipecat Flows gives prayer, devotional, project and quiet as explicit states with their own
  allowed transitions, which maps onto the app's reducer.
* Transport-agnostic: SmallWebRTC now, LiveKit later without rewriting the pipeline.
* The whole conversational system can be benchmarked without the phone: feed voicesim audio in
  through the WebSocket or local transport and capture the bot audio, so the room loop measures
  routing, latency and spoken fidelity directly.
* Smallest operational footprint of the three: one Python service, no media server, no Redis, no
  TURN for a tailnet.

**Cons.**

* Two audio paths on the phone. The app's `AudioOwner` owns the microphone for continuous capture
  and the prayer journal; the SmallWebRTC client expects to own it for the uplink. iOS has one audio
  session, and libwebrtc's audio unit applies its own echo cancellation, gain control and noise
  suppression, which would also alter the captured prayer audio if the capture were taken from the
  same path. The native capture must stay separate and the two must share the session without
  fighting. This is the main engineering risk and it needs a spike on the phone before anything else.
* Streaming speech-to-text is the weak link. Cohere Transcribe is file based, so STT is per
  utterance after the turn ends (fine for a cascade, not a live caption). Kyutai STT would give
  streaming text and semantic end-of-turn, but its Rust server is not published for aarch64.
* Python asyncio micro-stalls (the dossier's warning) are real but irrelevant at one session.
* The iOS SDK is young (version 1.3, a small repository); it does less for us than LiveKit's Swift
  client in background audio and interruption recovery, and the app already solves those natively.

**Licensing.** Framework and clients BSD-2. Smart Turn is open; Kokoro Apache-2; Silero VAD MIT.

---

## Option B. LiveKit server plus LiveKit Agents (Apache-2; model license on turn detection)

**What it is.** A Go WebRTC media server (self-hostable) and a Python agents framework whose
`AgentSession` manages STT, LLM, TTS, VAD, turn detection and room connection in one primitive.
Agents are participants in a room; the phone is another participant. The Swift client SDK is mature
(`client-sdk-swift`, `components-swift`, a SwiftUI starter). Plugins are mostly cloud providers plus
LiveKit Inference; the OpenAI plugin takes a base URL, so `spark-inference` works; local STT and TTS
need small custom plugin classes (the base classes are public). Turn detection is a transformer
model that runs on CPU under 500 MB; its weights are under the LiveKit Model License, which forbids
use "on a standalone basis or with any frameworks other than LiveKit Agents". Everything can be
self-hosted; the cloud control plane is closed source.

**How it would sit in our topology.**

```
iPhone (RLMADK)                      desktop (x86, no GPU needed)         Spark-A / Spark-B
 native capture + prayer journal      LiveKit server (Go)                  STT (Cohere) and TTS (Kokoro)
 LiveKit Swift client:  ──────────►   room: phone, Host agent,  ◄────────  reached by the Host agent's
   audio track up/down, data channel         Companion observer               custom plugins
                                      Host agent (Agents, Python):           LLM: spark-inference
                                        AgentSession, turn detector,
                                        tools ──► skill service (RPC)
                                      Companion resident = silent worker:
                                        subscribes to transcript + data
                                        channel, accepts work, publishes
                                        {task_id, status} back to Host
```

**Dossier layers it provides.** Media gateway (a real one: SFU, jitter, AEC via libwebrtc on the
client), floor arbitrator (VAD plus the turn model, interruptions), the Observer-and-Worker topology
(the dossier's Pattern 2 is literally LiveKit's), data channels for Commit messages, and the room as
a natural home for a second client later (Mac, car, a second phone).

**Pros.**

* The best iOS client of the three: audio session handling, background audio, route changes,
  reconnection and interruption recovery are solved and widely deployed. This is the fragility
  DEVICE-02 lists.
* Observer and Worker is exactly the Companion's role: a second participant that never publishes
  audio, reads the authoritative transcript, and returns compact results over the data channel. The
  host speaks them when the mode allows.
* Multi-client for free: the same room serves a desktop or Mac client later; a room per day of
  prayer is also a clean session boundary.
* The media server needs no GPU and runs on the desktop next to the Companion and the register.
* Turn detection quality is strong and runs on CPU.

**Cons.**

* Most machinery of the three: a media server, API keys, optionally Redis and TURN, an agents
  worker, and custom plugins for every local model (none of our STT or TTS has a shipped plugin).
* `AgentSession` wants to answer. Prayer means suppressing the agent's reply, its filler and its
  turn-taking for minutes while keeping the room open; this is possible through interruption controls
  and custom nodes, but it works against the framework's grain where Pipecat works with it.
* The turn detector's license binds it to LiveKit Agents. Acceptable if LiveKit is the choice,
  but it is the one component that cannot move with us.
* Same two-audio-path question on the phone as Pipecat: native capture for prayer, WebRTC track for
  conversation. The LiveKit client gives more control over the audio session than SmallWebRTC, which
  helps.
* Cloud pull: the documentation and plugins lean toward LiveKit Cloud and hosted inference; staying
  self-hosted is supported but is the road less documented.

**Licensing.** Server and framework Apache-2; Swift SDK Apache-2; turn detection models under the
LiveKit Model License (framework-bound).

---

## Option C. The Kyutai family: Moshi full duplex, with Unmute as its cascade

**What Moshi is.** A 7B full-duplex speech-text model over the Mimi codec (24 kHz, 12.5 Hz frames,
about 1.1 kbps) that listens and speaks at once, with an inner text monologue. Theoretical latency
160 ms, about 200 ms measured on an L4. Code MIT (Python) and Apache-2 (Rust); weights CC-BY 4.0.
PyTorch needs about 24 GB of GPU memory (no quantisation in that path); MLX runs on Apple silicon;
Rust/Candle serves int8 and bf16. Two released voices, Moshiko and Moshika, both synthetic. The
repository documents no tool use, no grounding and no language list.

**What Unmute is.** Kyutai's modular answer for people who need a real LLM: Kyutai STT (1B
English/French with a 0.5 s delay and **semantic voice activity detection** that predicts whether
the speaker has finished from content and intonation) → any OpenAI-compatible LLM → Kyutai TTS 1.6B
(about 450 to 750 ms), served by a Rust `moshi-server` over WebSockets. MIT. Voices come from a
voice repository (a donation project); the character file selects one. Tool calling is not built in
("wrap vLLM in a custom server"). The documented requirements are a CUDA GPU with 16 GB, **x86_64
only, "aarch64 is not planned"**, Linux; the frontend is a web app, there is no iOS client.

**How it would sit in our topology.** Moshi: on a Spark (aarch64 PyTorch is the only path that is
known to run here, as fish-speech does), fed by a WebRTC gateway we would have to build, since
Moshi ships a web client and bare-bones CLIs without echo cancellation. Unmute: the Rust server
would have to be built from source for aarch64 with CUDA on sm_121, which nobody has published;
otherwise it has no GPU to run on here.

**Dossier layers it provides.** Moshi is the reflexive voice engine itself, the one layer the other
two options do not have, with the floor behaviour (hesitation awareness, self-interruption) inside
the model. Unmute provides the best open streaming STT and semantic end-of-turn in the cascade
class, and a strong TTS.

**Pros.**

* Moshi is the only option with the dossier's 200 ms loop and native backchanneling.
* Kyutai STT's semantic VAD is the right primitive for a user who pauses to think: it predicts
  completion from meaning and intonation, not from silence.
* Kyutai TTS gives a high-quality non-clone voice from a curated repository, streaming, with
  acceptable latency; it could serve as the app voice even if nothing else from this family is used.
* MIT and CC-BY: the most permissive set.

**Cons.**

* Moshi cannot do the job as specified: no grounding on the prayer corpus, no citations, no tool
  calls, no choice of voice beyond two synthetic ones, no documented way to make it read a passage
  verbatim; prayer mode could only be approximated by muting its output. Fine-tuning a 7B full-duplex
  model is a research project, not a build.
* Unmute's platform is x86_64 with a GPU. Our x86_64 machine has a 4 GB GTX 970 and our GPUs are
  aarch64. Running it means an unsupported from-source build of a Rust CUDA server on a new compute
  capability, and a client we would write ourselves.
* Neither has an iOS client; neither has the floor machinery for a separate native prayer capture.
* The Kyutai TTS weights' licence was not stated on the page read; it would need checking before it
  carries the app's voice.

**Licensing.** Moshi code MIT/Apache-2, weights CC-BY 4.0; Unmute MIT; Kyutai STT weights CC-BY 4.0;
Kyutai TTS weights not confirmed.

---

## Baseline for comparison: what we have today (phone-native cascade)

On-device recognition and capture, the reducer's floor rules, the backend's grounded turn over a
JSON WebSocket, Apple's synthesiser. It needs no server media plane and no new dependency, and the
prayer path already passed the physical trial. It lacks echo cancellation between the app's own
speech and the microphone, a trained end-of-turn model, a good voice, and a way to benchmark the
conversation without the phone.

---

## Scoring

Five is best. Each score is argued in the sections above; the weights are mine and are the first
thing to challenge.

| Criterion (weight) | Phone-native | A. Pipecat | B. LiveKit | C. Kyutai |
|---|---|---|---|---|
| Prayer semantics under our control (5) | 5 | 5 | 3 | 1 (Moshi) / 3 (Unmute) |
| Turn-taking quality: barge-in, backchannel, pauses (4) | 2 | 4 | 4 | 5 |
| Grounding and tool integration with the skill service and rlm-adk (5) | 4 | 5 | 4 | 1 / 3 |
| Voice: quality and freedom to choose a new voice (4) | 1 | 4 | 4 | 3 / 5 |
| Fits our hardware without porting (5) | 5 | 5 | 5 | 2 / 1 |
| Outage behaviour: prayer and capture survive (4) | 5 | 4 | 4 | 2 |
| iOS client maturity (3) | 4 | 2 | 5 | 0 |
| Benchmarkable with voicesim without the phone (4) | 2 | 5 | 3 | 3 |
| Operating effort for one user (3) | 5 | 4 | 2 | 1 |
| Licence freedom (2) | 5 | 5 | 3 | 5 |
| Dossier alignment: layers provided (2) | 1 | 3 | 4 | 5 / 3 |
| **Weighted total (max 205)** | **150** | **181** | **153** | **84 / 115** |

Reading the numbers: Pipecat wins because it is the only option that scores at the top on the three
heaviest criteria at once (prayer control, grounding, hardware) while fixing what the phone-native
path lacks. LiveKit ties the baseline: it buys client robustness and the room model at the cost of
machinery and of fighting the framework in prayer. Kyutai loses on hardware and on the job itself,
but two of its components are worth taking on their own.

## Recommendation

1. **Adopt Pipecat as the Talk tier,** running as a user service on Spark-A beside the prayer intake
   and `spark-inference`. SmallWebRTC transport to the phone; Silero plus Smart Turn v3; Cohere per
   utterance for STT; Kokoro on the GB10 for TTS, streamed; the OpenAI-compatible service pointed at
   `spark-inference`; our own processors for prayer-by-address, amen, backchannel ducking and bridge
   phrases; Pipecat Flows for the modes; async tools to the skill service; durable work to the
   Companion through rlm-adk mail and, later, duty operations.
2. **Keep the native capture and prayer journal exactly as they are.** Prayer must not depend on a
   session. The first spike is the audio-session coexistence on the phone: native tap plus
   SmallWebRTC uplink, measured with the voicesim phone loop (does the prayer capture still hash
   match and score WER 0.0 while the conversational uplink is live, and does the app's own speech
   leak back into the uplink without echo cancellation?). If coexistence fails, fall back to one
   path: the app sends its captured PCM over the WebSocket itself and Pipecat's WebSocket transport
   receives it; we lose libwebrtc's echo cancellation and must duck or pause the microphone while the
   bot speaks.
3. **Take two Kyutai components on their merits, as spikes, not commitments:** build `moshi-server`
   for aarch64 CUDA on a Spark to get streaming STT with semantic end-of-turn (if it builds, it
   replaces per-utterance Cohere in the Talk tier; Cohere stays authoritative for prayers), and
   evaluate Kyutai TTS against Kokoro as the app's voice once its weight licence is confirmed.
4. **Keep LiveKit as the documented alternative** if, after the Pipecat spike, client robustness or a
   second client becomes the deciding factor. Pipecat's LiveKit transport makes that a transport
   change, not a rewrite.
5. **Do not adopt Moshi.** Revisit when a full-duplex model with tool use, grounding and voice choice
   exists and runs on aarch64.

## Spikes before committing (each measured with voicesim, each a day or two)

| Spike | Question | Pass condition |
|---|---|---|
| Phone audio coexistence | Can the native capture and the SmallWebRTC uplink share the audio session? | Prayer capture hash-matches and scores WER 0.0 while the uplink is live; bot speech does not re-enter the uplink above the noise floor |
| Pipecat on Spark-A | Smart Turn, Silero, Kokoro and the OpenAI service against `spark-inference`, SmallWebRTC server | A scripted voicesim room loop answers a grounded question with first bot audio under 2.0 s and barge-in under 200 ms |
| Prayer processors | Address and amen rules as Pipecat processors plus Flows states | The pauses fixture (3.0 s and 4.5 s) never ends prayer; amen-to-end under 1.5 s; no bot frame leaves the pipeline during prayer |
| Kyutai STT on aarch64 | Does `moshi-server` build and run with CUDA on the GB10? | Streaming transcript with semantic end-of-turn on the pauses fixture; if not buildable in a day, drop it |
| Voice selection | Kokoro voices and, licence permitting, Kyutai TTS voices, rendered for the same passage | Rawley picks one by ear; render time under real time on the GB10 |

## Sources

* Pipecat: [repository](https://github.com/pipecat-ai/pipecat), [supported services](https://docs.pipecat.ai/server/services/supported-services), [Smart Turn](https://docs.pipecat.ai/server/utilities/smart-turn), [user turn strategies](https://docs.pipecat.ai/server/utilities/turn-management/user-turn-strategies), [iOS SDK](https://github.com/pipecat-ai/pipecat-client-ios), [iOS SmallWebRTC transport](https://docs.pipecat.ai/client/ios/transports/small-webrtc).
* LiveKit: [agents repository](https://github.com/livekit/agents), [turn detector plugin](https://github.com/livekit/agents/tree/main/livekit-plugins/livekit-plugins-turn-detector), [LiveKit Model License](https://huggingface.co/livekit/turn-detector/blob/main/LICENSE), [LiveKit vs Pipecat field guide (vendor)](https://livekit.com/field-guides/guide/livekit-vs-pipecat).
* Kyutai: [Moshi](https://github.com/kyutai-labs/moshi), [Unmute](https://github.com/kyutai-labs/unmute), [delayed streams modeling: STT and TTS](https://github.com/kyutai-labs/delayed-streams-modeling), [Kyutai STT page](https://kyutai.org/stt).
* Third-party comparisons read for context, not relied on for facts: [Forasoft](https://www.forasoft.com/blog/article/pipecat-vs-livekit-agents), [channel.tel](https://www.channel.tel/blog/pipecat-vs-livekit-voice-framework-decision).
* On-device voice (for the fallback): [sherpa-onnx Swift package](https://swiftpackageregistry.com/uakihir0/sherpa-onnx-spm), [Kokoro Core ML iPhone figures](https://huggingface.co/mattmireles/kokoro-coreml).
* Hardware read directly on 2026-10-13: desktop `nvidia-smi` (GeForce GTX 970, 4,096 MiB), Spark-B `nvidia-smi` (GB10) and `uname -m` (aarch64).
