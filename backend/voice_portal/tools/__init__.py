"""Read-only skill-service tools for the Pipecat talk tier (the HTTP RPC barrier).

Each tool returns JSON with a `spoken` string (short, spoken-style, no markdown, no bracket
citations) beside its structured fields. Endpoints: `GET /tools/scripture`, `GET /tools/word`,
`GET /tools/interpretations`, `GET /tools/project`, `POST /tools/work` and `GET /policy`.
See docs/voice-portal/pipecat-rebuild/skill-service.md.
"""
