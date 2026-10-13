"""Read-only adapters over the retained source products. Each yields SourceRecord values."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceRecord:
    """One selected source text with its producer identity and revision."""

    source_family: str
    source_doc_id: str
    body: str
    title: str | None = None
    revision_hash: str | None = None  # producer revision (text_sha256 / body_sha256); None -> sha256(body)
    source_locator: str | None = None
    observed_at: str | None = None
    recorded_at: str | None = None
    recorded_at_basis: str | None = None
    metadata: dict = field(default_factory=dict)
    locators: list = field(default_factory=list)

    @property
    def body_sha256(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()

    @property
    def revision(self) -> str:
        return self.revision_hash or self.body_sha256


class SourceUnavailable(Exception):
    """The retained source could not be read at the configured path."""
