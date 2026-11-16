"""Data models for crawler results, snapshots, and RAG document chunks (Step 2.1)."""

from __future__ import annotations

import dataclasses
from typing import Any

from copilot.rag.crawler.models import (
    CrawlMetrics,
    CrawlResult,
    CrawlSummary,
    PageVersion,
    Snapshot,
    SnapshotManifestEntry,
)


@dataclasses.dataclass(frozen=True)
class ChunkInput:
    """Typed input contract for document chunking (Step 2.1)."""

    source_type: str  # "builtin" | "upload" | "website"
    owner_id: str | None
    source_id: str
    page_version_id: str
    url_or_filename: str
    title: str
    language: str
    fetched_at: str
    text: str

    @classmethod
    def from_page_version(cls, page: PageVersion) -> ChunkInput | None:
        """Create a ChunkInput from a PageVersion if eligible for chunking.

        Returns None if page is removed, needs JS, or has a non-200 HTTP status.
        """
        if getattr(page, "is_removed", False):
            return None
        if getattr(page, "needs_js", False):
            return None
        if getattr(page, "http_status", 200) != 200:
            return None

        canonical = getattr(page, "canonical_url", "") or getattr(page, "url", "")
        content_hash = getattr(page, "content_hash", "") or getattr(page, "page_version_id", "")

        return cls(
            source_type="website",
            owner_id=None,
            source_id=canonical,
            page_version_id=content_hash or canonical,
            url_or_filename=canonical,
            title=getattr(page, "title", "") or "",
            language=getattr(page, "language", "") or "und",
            fetched_at=getattr(page, "fetched_at", "") or "",
            text=getattr(page, "cleaned_text", "") or "",
        )


@dataclasses.dataclass(frozen=True)
class Chunk:
    """Immutable, deterministic chunk of a document version (Step 2.1).

    Maintains the original slice of text alongside its normalized search representation.
    """

    id: str
    chunk_hash: str
    index: int
    source_type: str  # "builtin" | "upload" | "website"
    owner_id: str | None
    source_id: str
    page_version_id: str
    url_or_filename: str
    title: str
    fetched_at: str
    section_path: str
    language: str
    text: str
    normalized_text: str
    token_count: int
    normalizer_version: str
    chunking_digest: str
    char_start: int = 0
    char_end: int = 0
    overlap_chars: int = 0

    @property
    def chunker_settings_digest(self) -> str:
        """Backward compatibility alias for chunking_digest."""
        return self.chunking_digest

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["chunker_settings_digest"] = self.chunking_digest
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Chunk:
        d = dict(data)
        if "chunking_digest" not in d and "chunker_settings_digest" in d:
            d["chunking_digest"] = d["chunker_settings_digest"]
        d.pop("chunker_settings_digest", None)
        return cls(**d)


__all__ = [
    "Chunk",
    "ChunkInput",
    "CrawlMetrics",
    "CrawlResult",
    "CrawlSummary",
    "PageVersion",
    "Snapshot",
    "SnapshotManifestEntry",
]
