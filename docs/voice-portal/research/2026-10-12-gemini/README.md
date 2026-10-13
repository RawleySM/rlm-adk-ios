# Gemini research reports — October 12, 2026

Twelve independently prompted studies cover the native iOS frontend, recent live-voice tooling and costs, and backend knowledge and work organization. This archive currently contains **seven completed reports**. The five remaining conversations are indexed below with their observed status; their report content will be added when available.

Each completed report includes the rendered HTML, extracted plain text, and the source links displayed by Gemini. Trailing whitespace in the exports is normalized for repository formatting. The original prompts for all twelve studies are included. Conversation links use stable URLs. The [machine-readable manifest](manifest.json) records archive time, status, and SHA-256 hashes for the saved report artifacts.

Start with the consolidated [Top candidates decision brief](../../top-candidates.md) for the ranked shortlist, selectively verified primary-source claims, research corrections, and proposed selection tests.

## Reports

| ID | Research angle | Observed status | Saved report | Sources | Gemini |
| --- | --- | --- | --- | --- | --- |
| [IOS-1](prompts/ios-1-audio.md) | Native iPhone continuous audio feasibility | Complete and archived | [HTML](reports/IOS-1.html) · [Text](reports/IOS-1.txt) | [Source list](reports/IOS-1.sources.md) (67) | [Conversation](https://gemini.google.com/app/e891f613a99e4ae3) |
| [IOS-2](prompts/ios-2-interaction.md) | Natural voice interaction and prayer mode design | Complete and archived | [HTML](reports/IOS-2.html) · [Text](reports/IOS-2.txt) | [Source list](reports/IOS-2.sources.md) (89) | [Conversation](https://gemini.google.com/app/8b7f6dc01612ad41) |
| [IOS-3](prompts/ios-3-integration.md) | Swift voice transport and RLM-ADK integration | Start accepted; awaiting research progress | Pending | Pending | [Conversation](https://gemini.google.com/app/76771051e7305275) |
| [IOS-4](prompts/ios-4-reliability.md) | Recording and voice continuity under daily failures | Complete and archived | [HTML](reports/IOS-4.html) · [Text](reports/IOS-4.txt) | [Source list](reports/IOS-4.sources.md) (59) | [Conversation](https://gemini.google.com/app/a2b2e43090f14582) |
| [VOICE-1](prompts/voice-1-models.md) | Recent open speech-to-speech and full-duplex models | Complete and archived | [HTML](reports/VOICE-1.html) · [Text](reports/VOICE-1.txt) | [Source list](reports/VOICE-1.sources.md) (97) | [Conversation](https://gemini.google.com/app/6828661b9fed492f) |
| [VOICE-2](prompts/voice-2-frameworks.md) | Recent voice frameworks and native transport ecosystem | Complete and archived | [HTML](reports/VOICE-2.html) · [Text](reports/VOICE-2.txt) | [Source list](reports/VOICE-2.sources.md) (81) | [Conversation](https://gemini.google.com/app/ce7831be9952e4fa) |
| [VOICE-3](prompts/voice-3-economics.md) | Daily voice economics and cost-sensitive architecture | Start accepted; awaiting research progress | Pending | Pending | [Conversation](https://gemini.google.com/app/249d5392685ebd1f) |
| [VOICE-4](prompts/voice-4-evaluation.md) | Evidence-based comparison of live voice alternatives | Researching | Pending | Pending | [Conversation](https://gemini.google.com/app/da4deb5d78eaa6c2) |
| [BACKEND-1](prompts/backend-1-retrieval.md) | Hybrid retrieval versus GraphRAG knowledge architecture | Researching | Pending | Pending | [Conversation](https://gemini.google.com/app/b3f65e23388a1008) |
| [BACKEND-2](prompts/backend-2-theographic.md) | Theographic Scripture schema and integration | Complete and archived | [HTML](reports/BACKEND-2.html) · [Text](reports/BACKEND-2.txt) | [Source list](reports/BACKEND-2.sources.md) (46) | [Conversation](https://gemini.google.com/app/8996b7b9e6b74ba3) |
| [BACKEND-3](prompts/backend-3-temporal.md) | Personal temporal context and source correction architecture | Complete and archived | [HTML](reports/BACKEND-3.html) · [Text](reports/BACKEND-3.txt) | [Source list](reports/BACKEND-3.sources.md) (110) | [Conversation](https://gemini.google.com/app/592486bb7d3a2cc8) |
| [BACKEND-4](prompts/backend-4-work.md) | Durable tasks projects and proactive schedule coordination | Start accepted; awaiting research progress | Pending | Pending | [Conversation](https://gemini.google.com/app/6bb0e2bbed973de8) |

## Reading and evidence status

The Text links are directly readable on GitHub. The HTML files preserve the rendered report layout and tables and can be opened in a browser after downloading or from the local checkout. Source-link counts describe the links displayed by Gemini; they do not measure research quality or independent verification.

These are generated research inputs. Their claims have not been independently fact-checked or adopted as architecture decisions. The [IOS-1 review notes](reports/IOS-1-review-notes.md) identify specific background-audio assertions that need source and device verification. The existing [requirements](../../user-needs-and-requirements.md) and [architecture research](../../architecture-research.md) remain the context for evaluating the reports.

## Research scope

- Native iOS: continuous recording feasibility, natural voice and prayer interaction, Swift transport integration, and failure recovery.
- Recent voice tooling: speech models, voice frameworks, daily operating costs, and comparable performance evidence. Prompts prioritize the last 90 days, with a 180-day fallback and labeled older baselines.
- Backend evolution: hybrid retrieval and GraphRAG, Theographic schema integration, temporal context and source corrections, and durable tasks and calendar coordination.

The prompts preserve explicit prayer and quiet/wake behavior, use OpenAI live voice as the user-selected performance reference, and treat cost as a significant final-choice driver. They distinguish Theographic as a biblical dataset/schema from the choice of database, retrieval strategy, or task execution system.

## Continuation

Add completed outputs to this dated archive using the same report IDs, retain the prompts and source lists, and update this index and manifest. Pending statuses are observations at collection time, not continuous monitoring. Earlier transient browser states and raw browser-session traces are not report outputs and are kept outside the repository archive.
