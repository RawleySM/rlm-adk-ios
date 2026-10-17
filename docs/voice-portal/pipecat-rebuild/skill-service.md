# Skill service: tool endpoints for the Pipecat talk tier

The skill service is the existing voice portal backend (`backend/voice_portal`, stdlib Python 3.12)
extended with read-only tool endpoints. The Pipecat pipeline on Spark-A calls them over HTTP (the
RPC barrier in `live-voice-stack-alternatives.md`); the phone and the voicesim harness fetch
`/policy`. It runs on the **desktop** beside the data: `python3 -m voice_portal serve` from
`~/srv/voice-portal/backend`, state root `~/.local/rlm-adk/voice_portal`, listening on
`0.0.0.0:8765` (tailnet `http://100.90.105.30:8765`). Acceptance cases are in
[acceptance-matrix.md](acceptance-matrix.md).

## Contract shared by every tool

- Every reply is JSON with a `spoken` string: short, spoken-style sentences, no markdown, no bracket
  citations. Citations travel in structured fields (`cite_ids`, `cite_id`), each a version-qualified
  chunk id that `GET /citations/<id>` resolves to the exact retained text.
- `200` carries a tool result, including "not available" results (`available: false` plus a spoken
  reason). A missing required input is `400` with `error` and a `spoken` question. `POST /tools/work`
  answers `201`.
- Nothing is invented: Scripture is the retained World English Bible text verbatim; teacher excerpts
  are verbatim spans of cited chunks; traditions come only from an explicit map; register reports say
  "none recorded" for empty arrays.
- Only the intake issues log (`POST /issues`, `POST /tools/work`, the Work tab's dispatch) is
  written outside the portal's own SQLite, and only its mail gate (`intake_mail_mode`) ever runs
  `allocatorctl inbox post`. The prayer store and the register are opened read-only; nothing is
  written to the production prayer store.

## Endpoints

### `GET /tools/scripture?reference=...`

Accepts written and spoken forms: `Psalm 23`, `Romans 8:28-30`, `John chapter three verse sixteen`,
`John three sixteen`, `Romans eight verses twenty eight through thirty`. Spoken number words become
digits (`tools/spoken.py`), then `references.explicit_references` parses and `resolve_reference`
grounds the reference in the projected chapter; verses are cut from the chapter body at the reader's
verse locators (`references.passage_verses`).

Returns `available`, `normalized`, `reference`, `spoken_reference`, `references` (one report per
reference, reading order), `title`, `edition`, `verses: [{osis, verse, text}]`, `cite_ids`,
`coverage` (`full` / `partial` / `none`), `spoken_notice` and `spoken`.

`spoken` is exactly the verse texts joined by single spaces, so the harness can compare it with the
edition (Psalm 23 verse 1 includes the edition's superscription "A Psalm by David."). Verses missing
from a partially covered range are named in `spoken_notice`, never in `spoken`. Unknown book,
chapter not retained, or invalid verse: `available: false`, `reason`, and a spoken reason.

### `GET /tools/word?which=latest|morning|devotional[&refresh=1]`

"A word from God in response to my morning prayer."

1. **Prayer.** `latest` takes the newer (by recorded time) of the newest `prayer_captures` row with
   status `published` (text read by its transcript id from the prayer store, else the projection)
   and the newest row of the prayer store's `prayer_corpus_v1` view (`publications` when the view is
   missing, the projection when the store is unreadable). A published capture wins a tie: it is the
   same prayer, relayed. `morning` takes the newest of those recorded between 04:00 and 12:00 local
   time (desktop zone America/Detroit). `devotional` answers the latest prayer and searches today's
   devotional chapter (config `devotional_passages`, the same rotation `/devotional` uses) first for
   verses that fit each theme.
2. **Themes.** One reasoning call to `config.inference_base_url` / `inference_model` with
   `inference_extra_body` (`chat_template_kwargs.enable_thinking=false`) plus a strict
   `response_format` JSON schema: 3 to 6 themes, each `{label, petition, terms}`. A `400` (schema
   rejected) is retried once without `response_format`; other failures fall back to keyword themes
   from the prayer's own richest sentences (`themes_source: fallback_keywords`, with the error kept in
   `themes_detail.errors`). Successful results are cached in `tool_cache` per model, prayer text
   SHA-256 and prompt version; `refresh=1` bypasses the cache.
3. **Passages.** In order: up to two rows of the published pairing ledger for this prayer (joined by
   `prayer_id` or `text_sha256`; `cited` before `echoed` before `editorial`; whole-chapter refs
   skipped; rows are used only when the `manifest.json` beside the ledger lists the file with a
   matching SHA-256 and row count, reported as `candidates.ledger_manifest`); then live echoes (retained verses that share at least two exact four-word windows with
   the prayer, the same signal the ledger records as an echo, with parallel passages that echo the
   same words skipped); then one passage per theme from Scripture FTS, narrowed inside the hit chunk
   to the 1 to 3 verses with the highest IDF-weighted overlap of distinct theme words (two shared
   words required when the theme has two usable words). Two to four passages, each capped at five
   verses, read verbatim with their `cite_ids`.
4. **Frames** are deterministic, one sentence each, built from the theme label or the ledger basis
   and the reference ("Your words in this prayer echo Luke 11, verses 2 to 3."). No model writes a
   claim about a passage.

Returns `prayer {id, recorded_at, recorded_at_local, basis, source, client_capture_id, text_sha256,
chars, excerpt}`, `themes`, `themes_source`, `themes_detail`, `passages: [{reference,
spoken_reference, frame, verses, cite_ids, source (pairings|retrieval|devotional), basis, theme,
annotation_id, ledger_ref, truncated, echo_windows, theographic {people, places}}]`, `candidates`
(counts), `elapsed_ms` and `spoken` (a lead naming the prayer's date, then each frame and its verses
in reading order).

### `GET /tools/interpretations?reference=...&topic=...[&limit=3]`

Readings from the teaching family. Retrieval stages, best first: the reference as an FTS phrase
together with the topic words, the reference phrase alone, all topic words, any topic word, then
four-word phrases of the verse's own wording. The first hit per teacher becomes a voice, so the
answer spreads across teachers rather than repeating the most voluminous one; a hit whose best
sentences only quote the passage is kept only as a fallback for that teacher. Excerpts are the one
or two sentences of the cited chunk that best match (verse citation in modern or Roman-numeral form,
topic words, sentence after a quotation), whitespace-collapsed but otherwise verbatim.

Teacher: the record's own name for it (ThML `DC.Creator`, the publisher's `authors` list), else the
publisher. Tradition map (explicit, owner-supplied; anything else is "tradition not recorded"):

| Teacher or publisher | Tradition |
|---|---|
| John Piper, Desiring God (publisher, only when no author is named) | Reformed Baptist (Christian Hedonism) |
| Joby Martin, The Church of Eleven22 (publisher) | Evangelical, non-denominational |
| Jonathan Edwards | Reformed/Puritan |
| BibleProject | Evangelical, literary-theological |

Returns `scripture {reference, verses, cite_ids, edition}`, `voices: [{teacher, teacher_basis,
publisher, tradition, tradition_basis, work, title, excerpt, excerpt_span, excerpt_quotes_passage,
cite_id, attribution, source_doc_id, date, url, strategy}]`, `distinct_teachers`,
`personal_context` (the best matching recorded prayer, labelled as personal context),
`distinctions {scripture, teacher_reading, personal_context}`, `stages` and `spoken` (the verse
when it is short, a sentence saying the readings are interpretations and not Scripture, one
attributed excerpt per voice, then the personal context).

### `GET /tools/project?description=...[&confirm=<project_id>]`

Reads the project work register v2 (`rlm-adk.project-work-record.v2`: one record per repository;
records of any other schema are skipped) and indexes every record in an in-memory SQLite FTS5 table
(porter stemming; fields and BM25 weights: project id 2.0 with `cN` also indexed as "cycle N", title
3.0, summary and owner 1.5, products and consumers 1.5, host pointer ids and successors 0.5,
unresolved, disposition reason, evidence basis and need statements 1.0). It ranks the records
against the description's content words (spoken numbers become digits; request words such as
"project", "the one where", "check" are dropped). Host pointer ids are indexed, so an old pointer
name ("the prayer life project from cycle twelve") finds the repository it points at.

Returns `candidates: [{project_id, title, score}]` (top 3), `confident`, `confirmed`, `ratio`,
`terms`, and when confident or confirmed a `report {project_id, title, summary, updated_at,
record_revision, owner, host_pointer_ids, axes {disposition, need, products, consumers, evidence},
unresolved, record_path}`. Confident means the top score is above 0.01 and at least
`thresholds.project_confidence_ratio` (policy, 1.5) times the runner-up's, or the only match.
`spoken` for a report: title and record date, then disposition (with successors and the reason's
first sentence), need (count and the latest operator statement), products (grouped as produces,
contributes to, reads), consumers (confirmed and hypothetical) and evidence (level and basis), in
that order. Not confident: `spoken` is "Do you mean <best>? The next closest is <second>." and
`confirm_project_id` names the best candidate for the follow-up call.

Held-out check (written from the v2 records before the scorer ran, `tests/test_skill_tools.py`):
5 of 5 top-1 correct on the fixture copy of the register at bd5c217.

### `POST /tools/work`: send a request to Intake

The talk tier's `send_to_intake` tool posts a report (`report_id`, `transcript` with Rawley's own
last words, `context`, `source`, `kind`, `origin: "conversation"`); its escalation safety net posts
the same report with `origin: "auto_escalation"` and `assistant_reply` (what the assistant said when
it could not meet the request; `talk-protocol.md`); the short form
`{request_text, kind?, operation_id?}` is accepted too (`origin: "tool"`; `operation_id` makes it
idempotent). Either way it is `issues.record_report` plus the background triage, exactly as
`POST /issues`, so the ticket is staged or mailed by the same gate (`intake-issues-log.md`); nothing
here posts to the allocator. `kind` is a hint (`message`, `teaching`, `project_proposal`, `bug`,
`feature_request`). Returns `201 {accepted, status: "recorded", ticket_id, report_id, created,
kind_hint, mail_mode, completed: false, spoken}`; no words is `400` with `status: "rejected"`.
`spoken` says only what is true of the mail:

- `post` mode: "Sent to Intake."
- `stage` mode: "Saved for Intake. Mail to Intake is on hold, so it has not been sent yet."
- no `intake_recipient`: "Saved in the intake log."; fewer than three words: "Saved in the intake
  log; it was too short to send to Intake."

Completion is never claimed; Intake's replies arrive on the ticket (`GET /intake/feed`).

The call returns as soon as the `reported` event is fsynced (one lock, one scan of the log): the
triage model call and the allocatorctl post run on background threads, so the spoken
acknowledgement is immediate. Measured 2026-10-17 against an isolated backend on Spark-A: see
`intake-issues-log.md` ("Latency").

### `POST /issues`, `GET /issues`, `GET /issues/<ticket_id>`

The intake issues log (`issues.py`, format in [intake-issues-log.md](intake-issues-log.md)). The
talk tier posts a Bug chip report `{report_id, transcript {segments, device_segments, asr},
started_at, ended_at, ended_by, source, context}`; the service appends a `reported` event at once
(`201 {ticket_id, report_id, created, title, triage_state: "pending", log}`; a repeated `report_id`
answers `200` with `created: false` and writes nothing) and triages it on a background thread (one
strict-JSON reasoning call, keyword rules on failure), appending a `triaged` event. `GET /issues`
lists tickets newest first (`status`, `limit`); `GET /issues/<id>` returns the folded ticket. The log
is the one write this service makes outside its own SQLite. The report may carry a `kind` hint, an
`origin` and, for an auto-escalation, the `assistant_reply`; `GET /issues` rows and `GET /intake/feed`
items carry the ticket's `origin`. `POST /issues/<id>/mail` stages (default) or sends the ticket's mail to
`intake_recipient`; `POST /issues/<id>/status` appends a status written by someone other than the
resident; `POST /issues/replies` reads the mailbox now and pushes what is new (see
intake-issues-log.md).

### `GET /intake/feed?since=<unix>&limit=50`

Intake's replies on tickets and its inbound (unthreaded) mail, newest last: `{items, now, log}`,
each item the `intake_message` the phone is pushed on `/ws` when it is recorded (`id`, `kind`
reply|inbound, `ticket_id`, `ticket_title`, `ticket_kind`, `status`, `text`, `display`,
`from_name`, `sent_at`, `at`, `in_reply_to`, `instance_id`, `mentions`). The app reads it on every
reconnect after the last `at` it applied.

### `GET /policy`

The conversation policy (design option 2C): `address_phrases`, `amen_phrases`, `amen_silence_s`
(1.2), `prayer_ends_on_silence` (false), `quiet_phrases` ("quiet please"), `wake_phrases` ("wake
up"), `backchannel_words`, `bridge_phrases`, `thresholds {backchannel_max_ms 320,
backchannel_duck_db 3, backchannel_duck_ms 400, barge_in_cut_ms 120, bridge_after_ms 350,
bridge_start_max_ms 600, project_confidence_ratio 1.5}`, plus `schema`, `version` (hash of the
effective policy; clients cache by it) and `source {path, present, overrides, rejected, error}`.
Defaults live in `tools/policy.py`; `<state root>/policy.json` overrides top-level keys (and keys
inside `thresholds`). An override of the wrong type or an unknown key is rejected and listed, never
applied; an unreadable file serves the defaults with `source.error` set.

## Data sources (desktop, read-only)

| Source | Path | Used by |
|---|---|---|
| Portal projection (FTS5 over prayers, teaching, WEB chapters; Theographic) | `~/.local/rlm-adk/voice_portal/voice_portal.sqlite3` | all tools |
| Prayer store (production, opened `mode=ro`) | `~/.local/rlm-adk/stores/prayer_pipeline/prayer_ingest.sqlite3` (`config.prayer_store`), view `prayer_corpus_v1`, table `publications` | word |
| Relay capture ledger | `prayer_captures` in the portal projection | word |
| Prayer-to-Scripture pairings (Prayer & Word published product `scripture_pairings_frozen_20261008`: 615 rows over 206 prayers, frozen, coverage to 2026-10-08; checked against its `manifest.json`) | `~/.local/rlm-adk/stores/prayer_and_word/published/scripture_pairings_frozen_20261008/pairings.jsonl` (`config.scripture_pairings_ledger`) | word |
| Project work register records (schema `rlm-adk.project-work-record.v2`, one per repository) | `~/.local/rlm-adk/projects/project-work-registry/project/registry/project-work/records/*.json` (`config.project_register_records`) | project |
| Teaching: Desiring God, Eleven22 | `~/.local/rlm-adk/stores/life_context/sources/youtube/catalog.sqlite3`, `source_document_current_v1` | interpretations |
| Teaching: Jonathan Edwards (CCEL ThML), BibleProject transcripts (PDF) | `~/.local/rlm-adk/stores/life_context/sources/scripture_reference/` through the pinned `scripture_reference_readers` (`~/srv/voice-portal/vendor`) | interpretations |
| Inference owner (themes) | `config.inference_base_url` (desktop forward `127.0.0.1:28111` to Spark-B, model `qwen-coding-b`) | word |
| Allocator CLI | `~/srv/rlm-adk/bin/allocatorctl inbox post --to` (mail gate, post mode only), `inbox list --to-operator --json` (reply reader), `status --json` | issues |

## The scripture_reference teaching adapter

`sources/reference_teaching.py`, imported with `python3 -m voice_portal import reference_teaching`
(part of the default import). It reads the store's current manifest through the readers
(`open_store`, `iter_assets`, `extract_text`, which verifies body hashes) and projects into the
**teaching** family under the membership prefix `scripture_reference/`, with its own receipt
(`teaching:scripture_reference`). The publisher-catalog adapter now scopes its membership to `http`
ids, so neither adapter's complete selection retires the other's records.

- `ccel:edwards` (7 ThML files): one document per leaf ThML division. Teacher from the head's
  `DC.Creator`, work from `DC.Title` (inside the collected "Works" volumes, the top-level division
  title). Index and title-page divisions skipped; divisions whose sampled 8-word shingles are at
  least 60 % already selected (the collected volumes reprint the single works) skipped, single works
  first. First import on the desktop: 495 documents, 90 reprints, 15 index divisions and 5 short
  divisions skipped.
- `bibleproject:transcript` (198 PDFs): one document per transcript, extracted by the readers'
  `pdftotext -layout` path; page-number lines dropped, one paragraph per speaker turn, title from the
  transcript's own heading. Teacher and publisher "BibleProject" (the transcripts name the speakers by
  first name only).
- Total 693 documents, 16.5 M characters, 28,416 chunks; about 13 s on the desktop.

Unchanged revisions now refresh their title and metadata in place, which is how existing Desiring
God records gained the publisher's `authors` list (and so "John Piper" as teacher) without new
revisions.

## Configuration (config.json keys added)

`scripture_pairings_ledger`, `project_register_records`, `reference_teaching_sources` (default
`["ccel:edwards", "bibleproject:transcript"]`), `tools_inference_timeout_s` (420: covers a cold
model start, about 245 s observed on Spark-B), `word_max_tokens` (700). Float values can now be
overridden from the environment like the other types.

The intake keys (`issues_log`, `intake_recipient`, `intake_mail_mode`, `intake_mail_dispositions`,
`intake_poll_s`) are described in `intake-issues-log.md`. `work_recipient` is no longer read: requests
from `POST /tools/work` and the Work tab are intake tickets mailed to `intake_recipient`.

## Left out, and known limits

- **Judge endpoint**: not built (`POST /tools/judge` was ruled out of scope).
- **Project scorer**: lexical only. A description that shares no rare word with the intended record
  can be confidently wrong.
- **Edwards excerpts**: when Edwards quotes the passage, the King James wording often differs from
  the WEB enough that quote detection misses it, so an Edwards voice can be his quotation of the
  passage rather than his comment on it.
- **Eleven22**: the records name no speaker, so the teacher is "The Church of Eleven22" (publisher);
  the Joby Martin label applies only when a record names him, and none does today.
- **Word**: the first call after the owner has been idle includes a cold model start (about four
  minutes observed); later calls for the same prayer revision use the cached themes (about 0.2 s).
  A `503` from the owner (`insufficient_memory_for_envelope` was seen once on 2026-10-13) yields
  keyword themes, which are weaker. The pairing ledger is frozen (coverage ends 2026-10-08);
  newer prayers get live echoes and theme retrieval instead of ledger rows.
- **Teaching retrieval in conversation turns**: Edwards and BibleProject are now part of the
  teaching family, so `/turns` and `/devotional` teaching evidence can come from them too.
- **Interpretations** use the first reference in the request only; other life-context products
  (sermon transcripts, testimony ledger, petition threads) are not consulted.
- No change to rlm-adk, no residents or duty kinds declared.
