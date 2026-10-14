"""`GET /tools/project`: resolve a vague spoken description to a project work register record and
report its five axes (plan, active branches, accepted results, package checkpoints, outputs).

The register is read-only: one JSON record per project under `config.project_register_records`
(schema `rlm-adk.project-work-record.v1`, README beside the records). Each call indexes the records
in an in-memory SQLite FTS5 table (porter stemming, per-field BM25 weights) and ranks them against the
description's content words. The best match is reported only when it is clear: its score is at least
`thresholds.project_confidence_ratio` (policy, default 1.5) times the runner-up's, or it is the only
match, or the caller confirms a project id. Otherwise `spoken` asks to confirm the best candidate.
Empty arrays in a record mean "none recorded", not proven absence, and the report says so.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

from ..config import Config
from ..retrieval import content_terms
from .policy import load_policy
from .spoken import clean_spoken, spoken_date, words_to_numbers

# Words that describe the request ("the one where we built ...") rather than the project.
REQUEST_WORDS = {
    "project", "projects", "one", "ones", "thing", "things", "stuff", "check", "checking", "status", "going",
    "doing", "update", "updates", "were", "was", "built", "made", "make", "done", "us", "let", "know", "give",
    "report", "where", "are", "is", "been", "being", "last", "time", "started", "start", "remember", "called",
    "whatever", "something", "think", "how", "like", "just", "some", "all", "any", "also", "very", "really",
}
FIELDS = ("project_id", "title", "summary", "work", "branches", "notes")
FIELD_WEIGHTS = (2.0, 3.0, 1.5, 1.5, 0.5, 1.0)
MIN_SCORE = 0.01


def load_records(directory: str | Path) -> list[dict]:
    """Every `*.json` record in the register directory (sorted by file name); unreadable files skipped."""
    root = Path(directory).expanduser()
    records: list[dict] = []
    for path in sorted(root.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get("project_id"):
            record["_path"] = str(path)
            records.append(record)
    return records


def _words(value: str | None) -> str:
    return re.sub(r"[-_/.]+", " ", value or "")


def _id_words(project_id: str) -> str:
    words = _words(project_id)
    # "c12" also indexes as "cycle 12" so a spoken "cycle twelve" finds it.
    return words + " " + " ".join(f"cycle {m}" for m in re.findall(r"\bc(\d+)\b", words))


def record_fields(record: dict) -> dict[str, str]:
    """The text of each indexed field of one record."""
    baseline = record.get("baseline") or {}
    plan = record.get("current_plan") or {}
    owner = record.get("owner") or {}
    work = []
    for item in record.get("work_items") or []:
        work += [item.get("title") or "", ((item.get("plan") or {}).get("objective")) or "", item.get("next_action") or "", _words(item.get("work_id"))]
    branches = [f"{_words(b.get('work_id'))} {_words(b.get('git_ref'))}" for b in record.get("active_branches") or []]
    notes = list(record.get("unresolved") or []) + list(baseline.get("unknowns") or [])
    for output in record.get("outputs") or []:
        notes += [_words(output.get("output_id")), ((output.get("artifact") or {}).get("locator")) or "", ((output.get("deployment") or {}).get("target")) or ""]
    for result in record.get("accepted_results") or []:
        notes += [_words(result.get("result_id")), *(result.get("limitations") or [])]
    for checkpoint in record.get("package_checkpoints") or []:
        notes += [_words(checkpoint.get("package_key"))]
    return {
        "project_id": _id_words(record["project_id"]),
        "title": record.get("title") or "",
        "summary": " ".join(filter(None, [baseline.get("summary"), plan.get("objective"), owner.get("name")])),
        "work": " ".join(filter(None, work)),
        "branches": " ".join(branches),
        "notes": " ".join(filter(None, notes)),
    }


def query_terms(description: str) -> list[str]:
    """Content words of the description minus request vocabulary; spoken numbers become digits."""
    return [t for t in content_terms(words_to_numbers(description or "")) if t not in REQUEST_WORDS]


def rank(records: list[dict], description: str) -> list[dict]:
    """All records matching at least one query term, best first: `[{project_id, title, score}]`."""
    terms = query_terms(description)
    if not terms or not records:
        return []
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(f"CREATE VIRTUAL TABLE p USING fts5({', '.join(FIELDS)}, tokenize='porter unicode61')")
        for record in records:
            fields = record_fields(record)
            conn.execute(f"INSERT INTO p(rowid, {', '.join(FIELDS)}) VALUES (?,{','.join('?' for _ in FIELDS)})", (records.index(record), *(fields[f] for f in FIELDS)))
        match = " OR ".join('"' + term.replace('"', "") + '"' for term in terms[:24])
        rows = conn.execute(f"SELECT rowid, -bm25(p, {', '.join(str(w) for w in FIELD_WEIGHTS)}) AS score FROM p WHERE p MATCH ? ORDER BY score DESC", (match,)).fetchall()
    finally:
        conn.close()
    return [{"project_id": records[rowid]["project_id"], "title": records[rowid].get("title"), "score": round(score, 4)} for rowid, score in rows]


def _short_title(title: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", title or "").strip()


def _count_phrase(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def summarize(record: dict) -> dict:
    """The five axes of one record as structured items plus one brief spoken sentence each."""
    titles = {item.get("work_id"): item.get("title") for item in record.get("work_items") or []}
    plan = record.get("current_plan") or {}
    items = record.get("work_items") or []
    statuses = Counter(item.get("status") or "unknown" for item in items)
    plan_parts: list[str] = []
    if plan.get("state") == "recorded" and plan.get("objective"):
        plan_parts.append(f"the plan is {plan['objective'].rstrip('.')}")
    else:
        plan_parts.append("no overall plan is recorded")
    if items:
        plan_parts.append(_count_phrase(len(items), "work item") + " (" + ", ".join(f"{n} {s.replace('_', ' ')}" for s, n in statuses.most_common()) + ")")
        active = next((i for i in items if i.get("status") in ("running", "awaiting_review", "blocked")), None) or next((i for i in items if i.get("status") in ("queued", "planned")), None)
        if active:
            step = f"; next is {active.get('title')}"
            if active.get("next_action"):
                step += f": {active['next_action'].rstrip('.')}"
            plan_parts[-1] += step
    branches = record.get("active_branches") or []
    branch_text = "none recorded" if not branches else "; ".join(
        f"{b.get('git_ref')}" + (f" for {titles.get(b.get('work_id')) or b.get('work_id')}" if b.get("work_id") else "") + (", with uncommitted changes" if b.get("dirty") else (", clean" if b.get("dirty") is False else ""))
        for b in branches
    )
    results = record.get("accepted_results") or []
    result_states = Counter(r.get("state") or "unknown" for r in results)
    result_text = "none recorded" if not results else ", ".join(f"{n} {s}" for s, n in result_states.most_common()) + ": " + "; ".join(str(r.get("result_id")) for r in results[:3])
    checkpoints = record.get("package_checkpoints") or []
    checkpoint_text = "none recorded" if not checkpoints else "; ".join(
        f"package {c.get('package_key')}" + (f" published as {(c.get('published') or {}).get('tag')}" if (c.get("published") or {}).get("state") == "published" else "") for c in checkpoints[:3]
    )
    outputs = record.get("outputs") or []
    output_text = "none recorded" if not outputs else "; ".join(
        f"{o.get('output_id')}" + (f" deployed to {(o.get('deployment') or {}).get('target')}" if (o.get("deployment") or {}).get("state") == "observed_deployed" else f", {((o.get('deployment') or {}).get('state') or 'deployment unknown').replace('_', ' ')}") for o in outputs[:3]
    )
    axes = {
        "plan": {"state": plan.get("state"), "objective": plan.get("objective"), "work_items": [{k: i.get(k) for k in ("work_id", "title", "status", "next_action")} for i in items], "spoken": "Plan: " + "; ".join(plan_parts) + "."},
        "branches": {"items": [{k: b.get(k) for k in ("work_id", "git_ref", "observed_commit", "dirty", "observed_at")} | {"locator": (b.get("repository") or {}).get("locator")} for b in branches], "spoken": f"Branches: {branch_text}."},
        "results": {"items": [{k: r.get(k) for k in ("result_id", "work_id", "state")} for r in results], "spoken": f"Results: {result_text}."},
        "checkpoints": {"items": [{k: c.get(k) for k in ("resident_id", "package_key", "work_ids")} | {"published": (c.get("published") or {}).get("state")} for c in checkpoints], "spoken": f"Checkpoints: {checkpoint_text}."},
        "outputs": {"items": [{k: o.get(k) for k in ("output_id", "work_id", "result_id")} | {"deployment": (o.get("deployment") or {}).get("state"), "delivery": (o.get("delivery") or {}).get("state")} for o in outputs], "spoken": f"Outputs: {output_text}."},
    }
    return axes


def report(record: dict) -> dict:
    """Structured five-axis report of one record plus its spoken form (axes in their fixed order)."""
    axes = summarize(record)
    updated = spoken_date(record.get("updated_at"))
    lead = f"{_short_title(record.get('title') or record['project_id'])}" + (f", as recorded on {updated}." if updated else ".")
    spoken = clean_spoken(" ".join([lead] + [axes[name]["spoken"] for name in ("plan", "branches", "results", "checkpoints", "outputs")]))
    return {
        "project_id": record["project_id"],
        "title": record.get("title"),
        "updated_at": record.get("updated_at"),
        "record_revision": record.get("record_revision"),
        "owner": record.get("owner"),
        "axes": axes,
        "unresolved": record.get("unresolved") or [],
        "record_path": record.get("_path"),
        "note": "Empty arrays in the record mean none recorded, not proven absence.",
        "spoken": spoken,
    }


def _normal_id(value: str) -> str:
    return re.sub(r"[\s_]+", "-", (value or "").strip().lower())


def project_tool(cfg: Config, description: str, confirm: str | None = None) -> dict:
    """Rank register records against `description`; report the clear (or confirmed) best match.

    Returns:
        `{available, description, candidates: [{project_id, title, score}] (top 3), confident,
        confirmed, ratio, report?, spoken}`.
    """
    records = load_records(cfg.project_register_records)
    ratio = float(load_policy(cfg)["thresholds"]["project_confidence_ratio"])
    base = {"description": description, "register": cfg.project_register_records, "ratio": ratio, "terms": query_terms(description)}
    if not records:
        return {**base, "available": False, "candidates": [], "confident": False, "confirmed": False, "spoken": "I cannot read the project register right now."}
    ranked = rank(records, description)
    candidates = ranked[:3]
    by_id = {r["project_id"]: r for r in records}
    if confirm:
        record = by_id.get(_normal_id(confirm))
        if record is None:
            return {**base, "available": True, "candidates": candidates, "confident": False, "confirmed": False, "spoken": f"I do not have a project called {confirm} in the register."}
        return {**base, "available": True, "candidates": candidates, "confident": True, "confirmed": True, "report": report(record), "spoken": report(record)["spoken"]}
    if not candidates:
        return {**base, "available": True, "candidates": [], "confident": False, "confirmed": False, "spoken": "I could not match that to a project in the register. Which project do you mean?"}
    # A score of ~0 means only terms present in most records matched (BM25 gives them no weight).
    top = candidates[0]["score"]
    confident = top > MIN_SCORE and (len(ranked) == 1 or top >= ratio * candidates[1]["score"])
    if confident:
        found = report(by_id[candidates[0]["project_id"]])
        return {**base, "available": True, "candidates": candidates, "confident": True, "confirmed": False, "report": found, "spoken": found["spoken"]}
    best = _short_title(candidates[0]["title"] or candidates[0]["project_id"])
    second = _short_title(candidates[1]["title"] or candidates[1]["project_id"])
    return {**base, "available": True, "candidates": candidates, "confident": False, "confirmed": False, "confirm_project_id": candidates[0]["project_id"], "spoken": f"Do you mean {best}? The next closest is {second}."}
