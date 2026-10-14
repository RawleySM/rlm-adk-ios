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
