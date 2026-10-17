#!/usr/bin/env python3
"""Live text harness for the skill service (proof layer 1 of the acceptance matrix).

Queries the deployed skill service over HTTP, one block per use case, and writes a JSON receipt
with pass/fail per check and the raw responses (abbreviated). No model mocks: the word and
interpretation tools call the real owner.

    python3 backend/scripts/skill_harness.py --base http://100.90.105.30:8765 --out /tmp/harness.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.parse
import urllib.request

PSALM_23_WEB_OPENING = "Yahweh is my shepherd"
HELD_OUT_PROJECTS = [
    ("the library with the reference grammar and resolver for enriching scripture with prayer and teaching", "scripture-enrichment-c15"),
    ("the register of all our repositories with need, products, consumers, evidence and disposition", "project-work-registry"),
    ("the inference owner on the sparks that serves qwen and cohere with circuits and admission", "spark-inference"),
    ("the story corpus and the h7 audio devotional for the discipleship vision", "discipleship-vision-c12"),
    ("the pipelines repo with the apple ingestion gateway and the prayer ingest", "data-pipelines"),
]


def get(base: str, path: str, params: dict | None = None, timeout: float = 480.0) -> tuple[int, dict, float]:
    url = base.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), time.monotonic() - started
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode("utf-8", "replace")[:300]}, time.monotonic() - started


def post(base: str, path: str, payload: dict, timeout: float = 120.0) -> tuple[int, dict, float]:
    req = urllib.request.Request(base.rstrip("/") + path, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), time.monotonic() - started
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode("utf-8", "replace")[:300]}, time.monotonic() - started


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://100.90.105.30:8765")
    ap.add_argument("--out", required=True)
    ap.add_argument("--post-work", action="store_true", help="also POST a real work request (files an intake ticket, mailed only in post mode)")
    args = ap.parse_args()
    base = args.base
    checks: list[dict] = []
    raw: dict = {}

    def check(case: str, name: str, ok: bool, **detail):
        checks.append({"case": case, "name": name, "ok": bool(ok), **detail})

    # health
    status, health, _ = get(base, "/healthz")
    check("infra", "healthz", status == 200 and health.get("ok"), source_commit=health.get("source_commit"))

    # 2. Scripture verbatim
    status, psalm, t = get(base, "/tools/scripture", {"reference": "Psalm 23"})
    raw["scripture_psalm23"] = {k: psalm.get(k) for k in ("available", "coverage", "cite_ids")} | {"spoken_head": (psalm.get("spoken") or "")[:120], "verses": len(psalm.get("verses") or [])}
    check("2", "psalm23_available", status == 200 and psalm.get("available"), seconds=round(t, 2))
    check("2", "psalm23_verbatim_web", PSALM_23_WEB_OPENING in (psalm.get("spoken") or ""), head=(psalm.get("spoken") or "")[:60])
    check("2", "psalm23_six_verses", len(psalm.get("verses") or []) == 6, verses=len(psalm.get("verses") or []))
    check("2", "psalm23_citations", bool(psalm.get("cite_ids")))
    status, rom, t = get(base, "/tools/scripture", {"reference": "Romans 8:28-30"})
    check("2", "romans8_28_30", status == 200 and rom.get("available") and len(rom.get("verses") or []) == 3, verses=len(rom.get("verses") or []))
    status, spoken_ref, _ = get(base, "/tools/scripture", {"reference": "John chapter three verse sixteen"})
    check("2", "spoken_reference_form", spoken_ref.get("available") and any(v.get("verse") == 16 for v in spoken_ref.get("verses") or []))
    status, missing, _ = get(base, "/tools/scripture", {"reference": "Genesis 51"})
    check("2", "unretained_reports_unavailable", status == 200 and missing.get("available") is False and bool(missing.get("spoken")))

    # 3. Word from God
    status, word, t = get(base, "/tools/word", {"which": "latest"})
    passages = word.get("passages") or []
    raw["word"] = {"prayer": word.get("prayer"), "themes": word.get("themes"), "passages": [{k: p.get(k) for k in ("reference", "frame", "source")} for p in passages], "seconds": round(t, 1)}
    check("3", "word_has_prayer", status == 200 and bool((word.get("prayer") or {}).get("id")), seconds=round(t, 1))
    check("3", "word_2_to_4_passages", 2 <= len(passages) <= 4, count=len(passages))
    check("3", "word_frames_one_sentence", all(len(re.findall(r"[.!?](\s|$)", (p.get("frame") or "").strip())) <= 1 for p in passages))
    check("3", "word_passages_cited", all(p.get("cite_ids") for p in passages))
    check("3", "word_themes", 3 <= len(word.get("themes") or []) <= 8, themes=word.get("themes"))

    # 4. Interpretations
    status, interp, t = get(base, "/tools/interpretations", {"reference": "Romans 8:28", "topic": "suffering"})
    voices = interp.get("voices") or []
    raw["interpretations"] = {"voices": [{k: v.get(k) for k in ("teacher", "tradition", "title")} for v in voices], "distinctions": json.dumps(interp.get("distinctions"))[:200], "seconds": round(t, 1)}
    check("4", "interp_two_teachers", len({v.get("teacher") for v in voices}) >= 2, teachers=sorted({v.get("teacher") for v in voices}))
    check("4", "interp_traditions_labelled", all(v.get("tradition") for v in voices))
    check("4", "interp_distinctions_note", bool(interp.get("distinctions")))
    check("4", "interp_cited", all(v.get("cite_id") for v in voices))

    # 5. Project check-in
    hits = 0
    per = []
    for description, expected in HELD_OUT_PROJECTS:
        status, proj, t = get(base, "/tools/project", {"description": description})
        top = (proj.get("candidates") or [{}])[0].get("project_id")
        hits += top == expected
        per.append({"description": description[:60], "expected": expected, "top1": top, "confident": proj.get("confident"), "seconds": round(t, 2)})
    raw["project"] = per
    check("5", "project_top1_4_of_5", hits >= 4, hits=hits)
    status, vague, _ = get(base, "/tools/project", {"description": "the scripture project"})
    check("5", "project_uncertain_asks", vague.get("confident") is False and "?" in (vague.get("spoken") or ""), spoken=(vague.get("spoken") or "")[:120])
    status, confirmed, _ = get(base, "/tools/project", {"description": "the scripture project", "confirm": "scripture-overlay-c8"})
    rep = (confirmed.get("spoken") or "").lower()
    check("5", "project_report_axes", confirmed.get("report") is not None and all(w in rep for w in ("disposition:", "need:", "products:", "consumers:", "evidence:")), spoken=(confirmed.get("spoken") or "")[:160])

    # 6. Request sent to Intake (optional: files a real intake ticket)
    if args.post_work:
        status, work, _ = post(base, "/tools/work", {"request_text": "Research how Jonathan Edwards and John Piper differ on the affections in prayer.", "kind": "message"})
        raw["work"] = work
        check("6", "work_ticket_id", status in (200, 201) and bool(work.get("ticket_id")), status=work.get("status"))
        check("6", "work_never_done", work.get("completed") is False and "done" not in (work.get("spoken") or "").lower(), spoken=(work.get("spoken") or "")[:160])
    else:
        status, worklist, _ = get(base, "/work")
        check("6", "work_endpoint_reachable", status == 200, operations=len(worklist.get("operations") or []))

    # policy
    status, policy, _ = get(base, "/policy")
    check("policy", "policy_amen_silence", status == 200 and abs(float(policy.get("amen_silence_s", 0)) - 1.2) < 1e-6, version=policy.get("version"))

    ok = all(c["ok"] for c in checks)
    receipt = {"base": base, "at": dt.datetime.now(dt.UTC).isoformat(), "ok": ok, "checks": checks, "raw": raw}
    with open(args.out, "w") as f:
        json.dump(receipt, f, indent=2)
    for c in checks:
        print(f"{'PASS' if c['ok'] else 'FAIL'} [{c['case']}] {c['name']} " + json.dumps({k: v for k, v in c.items() if k not in ('case', 'name', 'ok')})[:140])
    print("overall", "PASS" if ok else "FAIL", args.out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
