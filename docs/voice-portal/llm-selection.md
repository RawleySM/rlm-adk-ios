# Which LLM behind the talk tier, and why: baseline, candidates, expected gains

Written 2026-10-14 after the Pipecat rebuild shipped. Answers: how was the current model chosen,
what does the community run under Pipecat when self-hosting, and what would a different model (or
pair of models) buy us. Numbers marked **measured** come from our own Sparks and logs; the rest are
cited and were not reproduced here.

## 1. How the current model was chosen

By availability, not by voice criteria. The talk tier calls `qwen-coding-b`, which is the
spark-inference owner's coding profile: `ukisai/Swift-Qwen3.8-27B-NVFP4`, a community
"reasoning-efficient" fine-tune of Alibaba's Qwen3.8-27B (dense, Gated DeltaNet hybrid), quantised
NVFP4/FP8 (about 22 GB), served by vLLM in the owner's pinned aarch64 image on GB10. It was resident
on both Sparks for coding work since 2026-09-29, exposed an OpenAI-compatible endpoint with tool
calling, and let thinking be switched off per request. I took it, moved it to Spark-B when Spark-A
ran out of memory, and tuned prompts around it. No alternative was evaluated.

What it does well in our runs: tool routing across seven tools (cases 2 to 8 pass by injection and
in the room), verbatim quoting when handed verse text, and sensible devotional answers. What it costs
us is speed, and that is structural: a dense 27B reads all of its weights for every token, and GB10's
273 GB/s memory bus caps that at roughly 10 tokens/s.

## 2. Measured baseline (Spark-B owner, warm, 2026-10-14)

| Metric | Value | Source |
|---|---|---|
| First token, short prompt, direct to the owner | 0.18 to 0.27 s | **measured** (streamed `chat/completions`) |
| First token inside the talk pipeline (prompt + tools + history) | p50 0.71 s, p90 1.55 s, p95 1.87 s (n=205) | **measured** (talk log TTFB) |
| Full LLM response in the pipeline | p50 1.9 s, p90 7.5 s | **measured** |
| Decode rate | about 9 tokens/s | **measured** (90 chunks in 9.8 s) |
| Cold start after idle unload | 200 to 214 s | **measured** (12:05 UTC outage; `warm.py`) |
| Weights / KV | 22 GB / fp8 fixed cache, 262k context | owner profile |

The decode rate is the number that shapes the experience. Exact-speech tools (readings, the word,
the work receipt) bypass the LLM, so Scripture is unaffected; but an interpretations answer or a
project report of 200 to 300 tokens streams over 20 to 30 s. The TTS keeps up sentence by sentence,
so the user hears a slow talker, not silence. First-audio latency is already dominated by
endpointing (VAD 0.8 s plus Smart Turn) rather than by the model.

## 3. What the Pipecat community runs when self-hosting

* **PhoneLLM Alpha 1** (Daily / Pipecat, 2026): a full-parameter fine-tune of NVIDIA
  **Nemotron 3 Nano 30B-A3B** (hybrid Mamba-Transformer MoE, 3.5B active) trained for multi-turn
  tool calling with thinking off, i.e. exactly the voice-turn job. Official NVFP4 checkpoint
  (19.4 GB) with a vLLM recipe; BSD-2 over the Nemotron open license; English only; synthetic
  customer-service training data; vendor-reported PhoneBench 72.0 (BF16) / 72.0 (NVFP4 + BF16 KV),
  P95 first token under 100 ms on a B200. Sources: the model cards
  (https://huggingface.co/pipecat-ai/phonellm-alpha-1,
  https://huggingface.co/pipecat-ai/phonellm-alpha-1-nvfp4), the launch post
  (https://www.daily.co/blog/announcing-pipecat-phonellm-alpha-1/) and the benchmark page
  (https://www.pipecat.ai/benchmarks/phonebench-alpha-1).
* **Nemotron 3 Nano / 3.5 Lightning 30B-A3B** (NVIDIA): the base family; community DGX Spark runs
  report 57 to 75 tokens/s for Nano NVFP4 and about 108 to 117 tokens/s for 3.5 Lightning NVFP4
  with speculative decoding (https://ai-muninn.com/en/blog/dgx-spark-nemotron-3-nano-w4a16-74-toks,
  https://forums.developer.nvidia.com/t/benchmark-nvidia-nvidia-nemotron-3-5-lightning-30b-a3b-nvfp4/379875,
  https://blog.kubesimplify.com/nemotron-3-5-lightning-on-dgx-spark). 3.5 Lightning (2026-08-11,
  OpenMDW 1.1) has no published tool-calling evaluation that I found.
* **gpt-oss-20b / 120b** (OpenAI, MXFP4): 120b decodes at about 59 tokens/s on a Spark under vLLM
  (about 60 GB of weights; six vLLM workarounds documented at
  https://ai-muninn.com/en/blog/part2-gpt-oss-120b-serve-script); 20b about 70 tokens/s single
  stream under SGLang (https://www.lmsys.org/blog/2025-11-03-gpt-oss-on-nvidia-dgx-spark/). Strong
  tool calling by reputation; harmony chat format needs care with system messages.
* **Qwen3 MoE (30B-A3B class)**: commonly suggested in guides for fast local tool calling; I found no
  GB10 vLLM numbers for it. Qwen3.8 itself ships only the 27B dense and a 2.4T flagship
  (https://github.com/QwenLM/Qwen3.8).
* General guidance in the voice-agent literature: LLM first token 200 to 700 ms in a cascaded
  pipeline, and "a fast small model beats a slow smart one for anything conversational"
  (https://www.coval.ai/blog/voice-ai-models-2026/,
  https://www.marktechpost.com/2026/08/30/lowest-latency-inference-apis-for-voice-and-realtime-agents-a-time-to-first-token-ttft-first-benchmark/).

Function-calling leaderboards (BFCL v3/v4 aggregators) put Qwen and GLM families near the top of
open weights, but versions and aggregators disagree and none of them score PhoneLLM or Nemotron
Nano; treat them as weak evidence for our tool set
(https://llm-stats.com/benchmarks/bfcl, https://presenc.ai/research/open-weight-function-calling-tool-use-2026).

## 4. What a change could buy us

| Candidate | Fit on GB10 next to Cohere (Spark-A) or fish-speech (Spark-B) | Expected decode | Expected first token | Tool calling | Risks |
|---|---|---|---|---|---|
| **PhoneLLM Alpha 1 NVFP4** | 19.4 GB weights; fits on **Spark-A beside Cohere** (64 GB available now), removing the Spark-B hop | 6 to 8× ours (57 to 75 tok/s class) | lower than ours at equal prompt (3.5B active; prefill also cheaper) | purpose-trained for multi-turn tool calls without thinking | alpha; English only; customer-service register may colour devotional tone; vendor-only benchmarks; NVFP4 validated on B200 only, GB10 untested by the vendor (same Blackwell NVFP4 path our Qwen build already uses); needs `trust_remote_code`, Mamba cache flags, vLLM ≥ 0.27 |
| **Nemotron 3.5 Lightning 30B-A3B NVFP4** | same footprint class | highest (about 110 tok/s with speculative decoding) | lowest | unknown; no published tool-call eval | newest; drafter choice differs between NVIDIA docs |
| **gpt-oss-120b MXFP4** | 60 GB: Spark-B only, and only if fish-speech moves; not beside Cohere | about 60 tok/s | good | strong by reputation | memory; harmony/system-message quirks; six vLLM patches on Spark |
| **gpt-oss-20b MXFP4** | about 13 GB; fits anywhere | about 70 tok/s | lowest | adequate; weaker than 120b | quality ceiling for interpretations and project reports |
| **Keep Qwen3.8-27B (current)** | 22 GB | about 9 tok/s | 0.2 to 0.7 s | proven on our suite (13/13 file and room) | slow long answers; 200 s cold start; shared with coding work |

Concrete gains I expect from PhoneLLM or Nemotron on our suite: interpretations and project
check-in answers finishing in 3 to 5 s instead of 20 to 30 s; the word tool's first call for a
new prayer (59 s today, mostly LLM theming on the same owner) dropping to under 10 s; the cold
start, when it happens, shrinking with the smaller weights; and the talk LLM moving to Spark-A so
the voice path no longer depends on Spark-B and the forward. First-audio latency changes little,
because endpointing dominates it. Judge scores may move either way: the current model's answers are
good, and a 3.5B-active model can be terser or less careful with Scripture distinctions. That is
what the A/B is for.

**On a two-model combination.** A small router model plus a large content model is the usual
remedy for a slow large model. With a 30B-A3B class model already fast enough for both roles, the
combination adds a hop, two warm sets and a routing failure mode for little gain; I recommend one
model for the talk turn and the skill service's own calls. The place where a second, bigger model
earns its keep is behind the RPC barrier (the word tool's theming, interpretations synthesis) if the
fast model's answers prove shallower there; the skill service can point those calls at a different
owner profile without touching the audio path.

## 5. How to decide: the A/B we can run with what we have

The conversation-core suite plus the skill harness is a complete evaluation for this choice: 13
cases with verbatim ground truth, a judge, timing endpoints and the same fixtures for every model.

1. Add an owner profile for the candidate (`spark_inference/config.py` `Profile`: HF repo and
   revision, served id, port, admission GiB, vLLM flags from the vendor recipe), release
   spark-inference, and start it on Spark-A under its own served id beside Cohere.
2. Point the talk tier at it with `VOICE_TALK_LLM_BASE_URL` / `VOICE_TALK_LLM_MODEL` (config
   fields, no code) and run `voicesim talk run suites/conversation-core --mode file --label <model>`
   three times each for the current model and the candidate; then one room run each through
   `scripts/phone-suite.sh`.
3. Compare per layer: pass counts, judge scores per case (2b, 3, 4, 5, 8 are the quality-sensitive
   ones), first-audio p50/p95, LLM TTFB and full-response p50/p90 from the talk log, and the word
   tool's first-call latency. Keep whichever wins on quality at equal or better latency; a tie goes
   to the faster model.
4. If the candidate wins, retire the Spark-B forward for the talk tier and keep Qwen3.8-27B as the
   coding profile only.

Nothing here changes rlm-adk; the owner profile and the talk config are the only moving parts.

## 6. A/B run: PhoneLLM Alpha 1 NVFP4 against Swift-Qwen3.8-27B (2026-10-14)

### Setup

* Candidate owner profile `phonellm` on Spark-A (spark-inference PR #5, release 85e8fc3): the
  vendor's vLLM 0.27.1 recipe, which is the vLLM version inside the owner's default image; engine
  source pinned to snapshot 61f182c2; bf16 KV, Mamba `align` cache, FlashInfer attention and
  FLASHINFER_CUTLASS NVFP4 MoE, `--max-num-seqs 4` with four admitted requests so a barge-in cancel
  is a verified per-request abort rather than a circuit; `--gpu-memory-utilization 0.22`.
  Qualified by hand first (`docker run` on port 28199): loads on SM121, TTFT 0.106 s, about 52
  tokens/s single stream, complete tool calls in 0.7 to 0.9 s. Through the owner the cold start is
  about 225 s (torch.compile and CUDA-graph capture dominate; Qwen's is about 200 s), so the
  keep-warm timer matters as much as before.
* Talk tier config for the candidate arm: `llm_base_url` 127.0.0.1:28080/v1, `llm_model phonellm`,
  `llm_temperature 0` (the vendor's tested setting; the baseline arm keeps its 0.4). Everything
  else, including the system prompt, tools, Flows nodes, voice, VAD and endpointing, is identical.
  Both PhoneLLM and Cohere are resident on Spark-A's owner, so the candidate arm has no Spark-B hop.
* Judge: `qwen-coding-b` on Spark-B for both arms (unchanged from the campaign).
* Protocol: three file-mode runs of conversation-core per arm (`--label llm-qwen-N` /
  `llm-phonellm-N`), then one room run per arm through `scripts/phone-suite.sh`. Per-turn LLM
  timings come from the talk tier's session JSONL via `rlm-voice-sim/scripts/llm_turn_timings.py`
  (user turn end to first LLM text; response start to first tool call; tool result to next first
  text; full response).

### Finding: PhoneLLM routes "read me Psalm 23" to the tool only about half the time

In candidate run 1, case 02a failed because the model answered the reading request with text
instead of calling `read_scripture` (once as a garbled call literal,
`<|tool_call>call:read_scripture{reference:...}`, spoken as text; otherwise as Psalm 23 recited from
memory, two or three verses). Cases 07a, 07b and 08 then score low for the same reason: the
"reading" they interrupt is the model's recital, not the tool's, so the reading tracker and
`continue_reading` have nothing to resume. Replaying the talk tier's exact context (system prompt,
seven tool schemas, temperature 0) against the owner eight times gave 2 to 5 tool calls out of 8 for
"Read me Psalm twenty three." while "check in on that project" and "interpretations of Romans 8:28"
routed 8/8 every time. Identical requests at temperature 0 give different answers, so the engine is
not deterministic at this near-tie; a sweep of engine variants on the same eight replays did not
change the rate:

| Engine variant | tool calls / 8 (Psalm 23) | other two prompts |
|---|---|---|
| vendor recipe (owner) | 2 | 8/8, 8/8 |
| `--no-enable-prefix-caching` | 4 | 8/8, 8/8 |
| plus `--no-async-scheduling` | 5 | 8/8, 8/8 |
| `--moe-backend marlin` | 5 | 8/8, 8/8 |

Prompt variants (an explicit "never recite Scripture from memory" rule in the system prompt; a
stronger `read_scripture` description) moved the rate to 3/8 and 6/8, within the noise. Enabling
thinking makes the model spend the whole token budget in `reasoning_content` and speak nothing.
Qwen3.8-27B called the tool in every one of its 39 case runs. This is a model-level preference
(customer-service training; a famous passage the model "knows"), amplified by non-deterministic
numerics; the fix that does not depend on the model is a rule in the floor that routes an explicit
"read me <reference>" straight to `read_scripture` (as the pause, continue and wake phrases already
are), which would also remove 1.5 to 2 s of tool-call emission from the Qwen path.

### Results

Pass counts and judge scores per run (file mode, 13 cases; judge 1 to 5):

| Run | Pass | Failures | Judge 02a / 02b / 03 / 04 / 05 / 06 / 07a / 07b / 08 |
|---|---|---|---|
| qwen-1 | 12/13 | 03 (judge 1: no passages) | 5 / 5 / 1 / 3 / 5 / 5 / 5 / 5 / 5 |
| qwen-2 | 13/13 | | 5 / 5 / 5 / 3 / 5 / 5 / 5 / 5 / 5 |
| qwen-3 | 13/13 | | 5 / 5 / 5 / 5 / 5 / 5 / 5 / 5 / 5 |
| phonellm-1 | 11/13 | 02a (recital), 07a (recital) | 1 / 5 / 5 / 3 / 5 / 5 / 1 / 3 / 3 |
| phonellm-2 | 10/13 | 02a (recital), 05 (asked for the description it was given), 07a | 3 / 5 / 5 / 3 / 1 / 5 / 1 / 5 / 5 |
| phonellm-3 | 8/13 | 02a, 05, 06 (asked "what should I call it?" instead of calling request_work), 07a, 08 | 3 / 5 / 5 / 3 / 1 / 1 / 1 / 3 / 1 |

Totals: Qwen 38/39, PhoneLLM 29/39. Every PhoneLLM failure is a routing failure, not a wrong
answer: it recites Psalm 23 instead of calling `read_scripture` (02a, and the floor cases 07a/07b/08
that interrupt that reading), and in runs 2 and 3 it slipped into a customer-service slot-filling
register for the two open-ended requests ("What is the description of the project you'd like to
check in on?" after being given it; "I'll start a research project on that. What should I call it?"
without calling the tool). Judge scores where it did route are equal to Qwen's (02b, 03, 04 and 06
at 5/5/3/5).

Per-turn LLM timings (seconds; p50 over the run's LLM turns):

| Run | first text | tool-call emission | post-tool first text | full response | word tool first text |
|---|---|---|---|---|---|
| qwen-1 | 3.53 | 1.97 | 1.47 | 3.85 | 24.4 |
| qwen-2 | 2.96 | 1.57 | 1.13 | 1.86 | 11.6 |
| qwen-3 | 2.70 | 1.63 | 1.20 | 1.89 | 8.9 |
| phonellm-1 | 0.24 | 1.00 | 2.81 | 0.89 | 0.7 (tool call; theming stays on Qwen) |
| phonellm-2 | 0.24 | 0.93 | 3.08 | 0.73 | 0.7 (same) |
| phonellm-3 | 0.24 | 0.95 | 2.89 | 0.79 | 0.7 (same) |

First audio (bridge phrase, endpointing-bound) is unchanged: p50 1.38 to 1.50 s in every run; amen
to end 0.95 to 0.98 s in every run. PhoneLLM's post-tool first text is slower (2.5 to 4.0 s against
1.1 to 1.6 s): the second request carries the tool result (several hundred to a few thousand
tokens) and this model's prefill on GB10 is not faster than Qwen's per token.

First-audio latency over cases 2 to 5 (benchmark_tables.py): Qwen p50 1.38 to 1.50 s, p95 2.50 to
2.64 s; PhoneLLM p50 1.46 to 1.61 s, p95 2.63 to 2.70 s.

Room layer (one run per arm through `scripts/phone-suite.sh`, isolated intake and backend, phone
on the desk, Spark-B microphone): Qwen 11/13 (`llm-qwen-room`, 15:01 UTC) and PhoneLLM 11/13
(`llm-phonellm-room`, 15:19 UTC). Both arms missed the same two cases for acoustic reasons that do
not involve the model: in 02b the room playback reached Cohere as "What is Roman Greek or about
Scott?" (PhoneLLM asked for clarification; in the Qwen run no speech reached the server at all), and
in 05 the long project description split at its mid-sentence pause into two transcripts, so the
bridge phrase came 3.0 to 3.1 s after the end of the whole utterance (threshold 2.0 s). Every other
case passed in both arms, including 02a for PhoneLLM in that run (the coin landed on the tool).
PhoneLLM's room-run LLM timings match its file runs (first text p50 0.25 s, tool call 1.00 s,
full response 0.81 s); Qwen's too (2.81 / 1.61 / 1.66 s). The room layer therefore confirms the
latency picture and does not change the routing picture; two isolated intake publications per run,
none in production.

### Decision

**Keep Swift-Qwen3.8-27B (`qwen-coding-b`) as the talk LLM.** PhoneLLM Alpha 1 is 6 to 12 times
faster on the LLM leg (first text 0.24 s against 2.7 to 3.5 s; full response 0.7 to 0.9 s against
1.9 to 3.9 s) but drops 10 of 39 case runs on tool routing that Qwen gets right 39 of 39 times, and
the misses are the ones that matter most here: exact Scripture and the two agentic requests. The
talk tier was switched back to `qwen-coding-b` at 14:58 UTC and again after the PhoneLLM room run at 15:36 UTC; the `phonellm` owner profile stays in
spark-inference for the next alpha and for the mitigations below.

What would make a fast MoE model viable on this suite, in order of expected value:

1. Route explicit reading requests by rule in the floor (book name plus chapter, "read me",
   "read"), calling `read_scripture` directly as pause, continue and wake phrases already are. That
   removes the near-tie that PhoneLLM loses half the time and 1.5 to 2 s of tool-call emission
   from the Qwen path. It changes no model.
2. Re-test PhoneLLM's next release (the card calls this an alpha trained on synthetic customer
   service data) and Nemotron 3.5 Lightning (`spark-small`, already a profile) with the same three
   replays times eight before any suite run: a model must route "read me Psalm 23", the long project
   description and the work request 8/8 at temperature 0 to be worth a run.
3. If a fast model is adopted, keep the judge and the word tool's theming on Qwen until the fast
   model's long-answer quality is scored separately.

Evidence: run directories `~/.local/state/voicesim/runs/talk-suites/conversation-core/runs/
20261014T13*-file-llm-qwen-{1,2,3}` and `20261014T1{359,434,446}*-file-llm-phonellm-{1,2,3}`;
engine sweep tallies in `~/.local/state/phonellm/sweep.log`; manual qualification logs in
`~/.local/state/phonellm/manual*.log`; the talk config for each arm in
`~/.local/state/voicesim/llm-ab/config.{qwen,phonellm}.json` with `switch-llm.sh`.

