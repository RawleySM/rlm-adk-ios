"""Teaching texts from the scripture_reference library store, projected into the teaching family.

Two retained sources are selected by default (config `reference_teaching_sources`):

- `ccel:edwards`: Jonathan Edwards' works as CCEL ThML. Each leaf ThML division (a titled division
  with no titled division inside it) becomes one teaching document, so a citation names the work and
  the section. The teacher is the ThML head's own `DC.Creator` short form; the work is the head's
  `DC.Title`, or, inside the collected "Works of ..." volumes, the top-level division title. Index and
  title-page divisions are skipped. A division whose text is mostly already selected (the collected
  volumes reprint the single works under other headings) is skipped and counted: sampled 8-word
  shingles (CRC32, deterministic) of each division are compared with every division selected before
  it, single works first, so a passage is projected once.
- `bibleproject:transcript`: BibleProject video transcripts (PDF, extracted through the readers'
  pinned `pdftotext -layout` path). One document per transcript; the title is the transcript's own
  heading; page-number lines are dropped and each speaker turn becomes one paragraph.

Bodies are spans of the readers' deterministic text revision (transcript layout whitespace
collapsed). Metadata keeps the entry id, asset body hash, reader text hash and extractor so every
citation leads back to the retained bytes. The store is opened read-only through the readers.
"""

from __future__ import annotations

import re
import zlib
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Any

from . import SourceRecord, SourceUnavailable
from .scripture import load_reader

FAMILY = "teaching"
# Membership scope of this adapter inside the shared teaching family.
DOC_PREFIX = "scripture_reference/"
SELECTION_RULE = "thml_leaf_divisions_and_whole_transcripts_v1"
# Per-source labels that the retained bytes do not carry themselves. Edwards' name comes from the
# ThML head; a BibleProject PDF names its speakers by first name only, so the publisher is the teacher.
SOURCE_LABELS: dict[str, dict[str, str]] = {
    "ccel:edwards": {"publisher": "Christian Classics Ethereal Library (CCEL)"},
    "bibleproject:transcript": {"publisher": "BibleProject", "teacher": "BibleProject"},
}
MIN_SECTION_CHARS = 400
SHINGLE_WORDS = 8
SHINGLE_SAMPLE = 8  # keep shingles whose CRC32 is divisible by this
DUPLICATE_SHARE = 0.6  # a division with at least this share of sampled shingles already seen is a reprint
_SKIP_TITLE = re.compile(r"^\s*(indexes|index\b.*|title page|contents|table of contents)\s*\.?\s*$", re.IGNORECASE)
_ENUMERATION = re.compile(r"^\s*(?:[IVXLC]+|\d+)\.?\s+(?=\S)")
_SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with", "from", "into", "upon"}
_SPEAKER = re.compile(r"^(?:[A-Z][A-Za-z.'\-]{0,20}(?: [A-Z][A-Za-z.'\-]{0,20})?:\s|Chapter \d+[:.])")
_PAGE_NUMBER = re.compile(r"^\d{1,4}$")


def _head_value(head: str, tag: str, attribute_pattern: str = "") -> str | None:
    match = re.search(rf"<{re.escape(tag)}\b[^>]*{attribute_pattern}[^>]*>\s*([^<]+?)\s*</{re.escape(tag)}>", head, re.IGNORECASE | re.DOTALL)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else None


def thml_identity(head: bytes) -> dict[str, str | None]:
    """Work title and author named by a ThML file's own Dublin Core head.

    Args:
        head: The first bytes of the ThML body (16 KiB covers the head of every CCEL file seen).

    Returns:
        `{"title": DC.Title, "creator": DC.Creator short form (else the file-as form reordered)}`;
        values are None when the head does not carry them.
    """
    text = head.decode("utf-8", "replace")
    creator = _head_value(text, "DC.Creator", r'scheme="short-form"')
    if creator is None:
        file_as = _head_value(text, "DC.Creator", r'scheme="file-as"')
        if file_as:
            name = re.sub(r"\s*\(.*?\)\s*", "", file_as)
            last, _, first = name.partition(",")
            creator = f"{first.strip()} {last.strip()}".strip() if first else last.strip()
    return {"title": _head_value(text, "DC.Title"), "creator": creator}


def clean_title(title: str, *, drop_enumeration: bool = False) -> str:
    """Collapse whitespace, title-case an all-capitals heading, optionally drop a leading `VIII.`."""
    value = re.sub(r"\s+", " ", title or "").strip()
    if drop_enumeration:
        value = _ENUMERATION.sub("", value)
    letters = [c for c in value if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        out: list[str] = []
        for index, word in enumerate(value.split(" ")):
            if re.fullmatch(r"[IVXLC]+[.,:;]?", word):
                out.append(word)  # a Roman numeral stays upper case
                continue
            lower = word.lower()
            starts_clause = index == 0 or out[-1][-1:] in ".:;"
            out.append(lower if (lower in _SMALL_WORDS and not starts_clause) else lower[:1].upper() + lower[1:])
        value = " ".join(out)
    return value.rstrip(" .")


def shingle_sample(body: str) -> set[int]:
    """Deterministic sample of a text's 8-word shingles (lower-case words, CRC32 % SHINGLE_SAMPLE == 0)."""
    words = re.findall(r"[a-z0-9]+", body.lower())
    sample: set[int] = set()
    for index in range(max(0, len(words) - SHINGLE_WORDS + 1)):
        value = zlib.crc32(" ".join(words[index:index + SHINGLE_WORDS]).encode("utf-8"))
        if value % SHINGLE_SAMPLE == 0:
            sample.add(value)
    return sample


def is_reprint(sample: set[int], seen: set[int]) -> bool:
    """True when most of a division's sampled shingles were already selected."""
    return bool(sample) and len(sample & seen) / len(sample) >= DUPLICATE_SHARE


def _level(locator: Any) -> int:
    try:
        return int(locator.meta.get("level") or 0)
    except (TypeError, ValueError):
        return 0


def _contains(outer: Any, inner: Any) -> bool:
    if inner is outer or not (outer.start <= inner.start and inner.end <= outer.end):
        return False
    return (inner.end - inner.start) < (outer.end - outer.start) or _level(inner) > _level(outer)


def edwards_records(asset: Any, revision: Any, identity: dict[str, str | None], seen: set[int], store_meta: dict) -> tuple[list[SourceRecord], dict[str, int]]:
    """Leaf ThML divisions of one work as teaching records.

    Args:
        asset: Reader asset (entry_id, source_id, source_path, url, body_sha256).
        revision: Reader `TextRevision` of the asset (text, text_sha256, locators, extractor).
        identity: `thml_identity` of the file's head.
        seen: Sampled shingles of every division already selected (shared across files; mutated).
        store_meta: Store identity recorded in every record's metadata.

    Returns:
        (records, counts) where counts has `documents`, `skipped_index`, `skipped_short`,
        `skipped_duplicate`.
    """
    counts = {"documents": 0, "skipped_index": 0, "skipped_short": 0, "skipped_duplicate": 0}
    text = revision.text
    sections = [loc for loc in revision.locators if loc.kind == "section" and (loc.meta or {}).get("title")]
    labels = SOURCE_LABELS.get(asset.source_id, {})
    teacher = identity.get("creator") or labels.get("teacher")
    file_title = clean_title(identity.get("title") or Path(asset.source_path).stem)
    collected = file_title.lower().startswith("the works of")
    records: list[SourceRecord] = []
    for leaf in sections:
        if any(_contains(leaf, other) for other in sections):
            continue
        ancestors = sorted((s for s in sections if _contains(s, leaf)), key=_level)
        path_titles = [clean_title(s.meta["title"]) for s in ancestors] + [clean_title(leaf.meta["title"])]
        if any(_SKIP_TITLE.match(t) for t in path_titles):
            counts["skipped_index"] += 1
            continue
        raw = text[leaf.start:leaf.end]
        start = leaf.start + (len(raw) - len(raw.lstrip()))
        body = raw.strip()
        if len(body) < MIN_SECTION_CHARS:
            counts["skipped_short"] += 1
            continue
        sample = shingle_sample(body)
        if is_reprint(sample, seen):
            counts["skipped_duplicate"] += 1
            continue
        seen |= sample
        if collected and ancestors:
            # Inside a collected volume the top-level division is the work ("Religious Affections").
            work = clean_title(ancestors[0].meta["title"], drop_enumeration=True)
            section = " / ".join(path_titles[1:]) or None
        else:
            work = file_title
            section = " / ".join(t for t in path_titles if t != work) or None
        div_id = (leaf.meta or {}).get("id") or leaf.ref
        records.append(
            SourceRecord(
                source_family=FAMILY,
                source_doc_id=f"{DOC_PREFIX}{asset.source_id}/{asset.source_path}#{div_id}",
                body=body,
                title=f"{work}: {section}" if section else work,
                source_locator=f"{asset.entry_id}:{asset.source_path}#{div_id}",
                metadata={
                    "provider": asset.source_id,
                    "publisher": labels.get("publisher") or asset.source_id,
                    "teacher": teacher,
                    "teacher_basis": "thml_dc_creator" if identity.get("creator") else ("source_label" if teacher else None),
                    "work": work,
                    "section": section,
                    "section_id": div_id,
                    "file_title": file_title,
                    "date": None,
                    "url": asset.url,
                    "source_path": asset.source_path,
                    "entry_id": asset.entry_id,
                    "asset_body_sha256": asset.body_sha256,
                    "text_sha256": revision.text_sha256,
                    "extractor": f"{revision.extractor}/{revision.extractor_version}",
                    "span": {"start": start, "end": start + len(body)},
                    "selection_rule": SELECTION_RULE,
                    **store_meta,
                },
            )
        )
        counts["documents"] += 1
    return records, counts


def transcript_body(text: str) -> str:
    """Normalise `pdftotext -layout` output: drop page-number lines, one paragraph per speaker turn.

    A line that starts with a speaker label (`Tim:`) or a `Chapter N:` heading opens a paragraph; an
    indented line continues the current one; any other line opens a paragraph. Runs of spaces collapse.
    """
    paragraphs: list[list[str]] = []
    for raw in text.replace("\f", "\n").split("\n"):
        line = re.sub(r"[ \t ]+", " ", raw).strip()
        if not line or _PAGE_NUMBER.match(line):
            continue
        indented = raw[:1] in (" ", "\t")
        if paragraphs and indented and not _SPEAKER.match(line):
            paragraphs[-1].append(line)
        else:
            paragraphs.append([line])
    return "\n\n".join(" ".join(p) for p in paragraphs)


def transcript_title(body: str) -> str | None:
    """The transcript's own heading: the lines before its `... Transcript` line."""
    lines = [p.strip() for p in body.split("\n\n") if p.strip()][:6]
    for index, line in enumerate(lines):
        if re.search(r"\btranscript\b", line, re.IGNORECASE):
            heading = " ".join(lines[:index]) if index else re.sub(r"\s*\b(video |podcast )?transcript\b.*$", "", line, flags=re.IGNORECASE)
            return clean_title(heading) or None
    return clean_title(lines[0]) if lines else None


def transcript_record(asset: Any, revision: Any, store_meta: dict) -> SourceRecord | None:
    """One BibleProject transcript as a teaching record; None when the extraction produced no text."""
    body = transcript_body(revision.text)
    if len(body) < 40:
        return None
    labels = SOURCE_LABELS.get(asset.source_id, {})
    title = transcript_title(body) or Path(asset.source_path).stem
    return SourceRecord(
        source_family=FAMILY,
        source_doc_id=f"{DOC_PREFIX}{asset.source_id}/{asset.source_path}",
        body=body,
        title=f"{title} (transcript)",
        source_locator=f"{asset.entry_id}:{asset.source_path}",
        metadata={
            "provider": asset.source_id,
            "publisher": labels.get("publisher") or asset.source_id,
            "teacher": labels.get("teacher"),
            "teacher_basis": "source_label" if labels.get("teacher") else None,
            "work": title,
            "section": None,
            "date": None,
            "url": asset.url,
            "source_path": asset.source_path,
            "entry_id": asset.entry_id,
            "asset_body_sha256": asset.body_sha256,
            "text_sha256": revision.text_sha256,
            "extractor": f"{revision.extractor}/{revision.extractor_version}",
            "extraction_warnings": list(revision.warnings or ()),
            "selection_rule": SELECTION_RULE,
            **store_meta,
        },
    )


def read_reference_teaching(store_root: str, source_ids: Iterable[str], reader_path: str | None, *, readers: ModuleType | Any | None = None) -> tuple[list[SourceRecord], dict]:
    """Select and extract the configured library sources.

    Args:
        store_root: The scripture_reference store root.
        source_ids: Catalog source ids to project (`ccel:edwards`, `bibleproject:transcript`).
        reader_path: Directory holding the pinned `scripture_reference_readers` package.
        readers: Injected readers module (tests); loaded from `reader_path` when None.

    Returns:
        (records, selection) for `import_family(..., membership_prefix=DOC_PREFIX)`.

    Raises:
        SourceUnavailable: The readers or the store cannot be opened.
    """
    readers = readers or load_reader(reader_path)
    try:
        store = readers.open_store(store_root)
    except Exception as exc:
        raise SourceUnavailable(f"scripture store unreadable at {store_root}: {exc}") from exc
    store_meta = {"store_manifest_id": store.manifest_id, "store_run_id": store.run_id, "catalog_version": store.catalog_version}
    records: list[SourceRecord] = []
    seen: set[int] = set()
    selection: dict = {"store": str(store_root), **store_meta, "rule": SELECTION_RULE, "sources": {}}
    for source_id in source_ids:
        assets = list(readers.iter_assets(store, source_id=source_id))
        # Single works before collected volumes, so a reprinted division keeps the single work's identity.
        assets.sort(key=lambda a: (a.source_path.lower().startswith("works"), a.source_path))
        stats = {"assets": len(assets), "documents": 0, "skipped_index": 0, "skipped_short": 0, "skipped_duplicate": 0, "empty_text": 0, "errors": []}
        for asset in assets:
            try:
                revision = readers.extract_text(store, asset)
            except Exception as exc:  # one unreadable body is reported, not fatal for the family
                stats["errors"].append(f"{asset.source_path}: {exc}")
                continue
            if not revision.text.strip():
                stats["empty_text"] += 1
                continue
            if revision.extractor == "thml":
                with open(readers.body_path(store, asset), "rb") as handle:
                    identity = thml_identity(handle.read(16384))
                found, counts = edwards_records(asset, revision, identity, seen, store_meta)
                records.extend(found)
                for key, value in counts.items():
                    stats[key] += value
            else:
                record = transcript_record(asset, revision, store_meta)
                if record is None:
                    stats["empty_text"] += 1
                else:
                    records.append(record)
                    stats["documents"] += 1
        selection["sources"][source_id] = stats
    selection["count"] = len(records)
    return records, selection
