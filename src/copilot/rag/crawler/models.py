"""Data models for crawler results, page versions, snapshots, and metrics."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class PageVersion:
    """Immutable representation of a specific version of a web page."""

    url: str
    canonical_url: str
    title: str
    language: str
    fetched_at: str
    http_status: int
    content_hash: str
    cleaner_version: str
    raw_html: str = ""
    cleaned_text: str = ""
    etag: str | None = None
    last_modified: str | None = None
    needs_js: bool = False
    is_removed: bool = False
    last_verified_at: str = ""

    def to_manifest_entry(self) -> SnapshotManifestEntry:
        """Convert page version to public manifest entry (strictly omitting page text)."""
        return SnapshotManifestEntry(
            url=self.url,
            canonical_url=self.canonical_url,
            title=self.title,
            language=self.language,
            fetched_at=self.fetched_at,
            http_status=self.http_status,
            content_hash=self.content_hash,
            cleaner_version=self.cleaner_version,
            needs_js=self.needs_js,
            is_removed=self.is_removed,
            last_verified_at=self.last_verified_at or self.fetched_at,
        )


@dataclass(frozen=True)
class SnapshotManifestEntry:
    """Public manifest record committed to git (contains NO raw or cleaned text)."""

    url: str
    canonical_url: str
    title: str
    language: str
    fetched_at: str
    http_status: int
    content_hash: str
    cleaner_version: str
    needs_js: bool = False
    is_removed: bool = False
    last_verified_at: str = ""

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary suitable for JSONL serialization."""
        d = asdict(self)
        # Ensure no text field is ever present
        assert "text" not in d and "cleaned_text" not in d and "raw_html" not in d
        return d


@dataclass
class Snapshot:
    """Immutable collection of page versions representing a site snapshot."""

    snapshot_id: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    pages: dict[str, PageVersion] = field(default_factory=dict)

    @property
    def active_pages(self) -> list[PageVersion]:
        """Return all non-removed, indexable page versions."""
        return [
            p
            for p in self.pages.values()
            if not p.is_removed and not p.needs_js and p.http_status == 200
        ]

    def get_manifest_entries(self) -> list[SnapshotManifestEntry]:
        """Return all manifest entries for this snapshot."""
        return [p.to_manifest_entry() for p in self.pages.values()]


@dataclass
class CrawlResult:
    """Record of a single crawled page."""

    url: str
    status_code: int
    text: str = ""
    etag: str | None = None
    last_modified: str | None = None
    is_unchanged: bool = False
    needs_js: bool = False
    canonical_url: str = ""
    content_hash: str = ""
    links_discovered: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Convert result to dictionary representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrawlResult:
        """Construct CrawlResult from a dictionary representation."""
        raw_status = data.get("status_code", 200)
        try:
            status_code = int(raw_status) if raw_status is not None else 200
        except (ValueError, TypeError):
            status_code = 200

        raw_links = data.get("links_discovered")
        links: list[str] = [str(link) for link in raw_links] if isinstance(raw_links, list) else []

        return cls(
            url=str(data.get("url", "")),
            status_code=status_code,
            text=str(data.get("text", "")),
            etag=str(data["etag"]) if data.get("etag") is not None else None,
            last_modified=str(data["last_modified"])
            if data.get("last_modified") is not None
            else None,
            is_unchanged=bool(data.get("is_unchanged", False)),
            needs_js=bool(data.get("needs_js", False)),
            canonical_url=str(data.get("canonical_url", "")),
            content_hash=str(data.get("content_hash", "")),
            links_discovered=links,
        )


@dataclass
class CrawlMetrics:
    """Ingestion metrics tracked for each crawl run."""

    pages_fetched: int = 0
    pages_unchanged: int = 0
    pages_changed: int = 0
    pages_removed: int = 0
    pages_failed: int = 0
    pages_not_found: int = 0
    pages_blocked_robots: int = 0
    pages_needs_js: int = 0
    crawl_duration_seconds: float = 0.0
    snapshot_age_seconds: float = 0.0

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


@dataclass
class CrawlSummary:
    """Consolidated summary of a crawl run."""

    total_visited: int = 0
    fetched: int = 0
    unchanged: int = 0
    changed: int = 0
    removed: int = 0
    failed: int = 0
    not_found: int = 0
    blocked_robots: int = 0
    needs_js: int = 0
    snapshot_id: str = ""
    manifest_path: str = ""
    duration_seconds: float = 0.0
    dry_run: bool = False
    dry_run_urls: list[str] = field(default_factory=list)
    results: list[CrawlResult] = field(default_factory=list)
    new_page_versions_count: int = 0

    @property
    def total_attempted(self) -> int:
        """Count of pages where a network fetch was attempted."""
        return self.fetched + self.unchanged + self.changed + self.failed + self.not_found

    @property
    def failure_rate(self) -> float:
        """Ratio of failed pages over total attempted network requests."""
        if self.total_attempted == 0:
            return 0.0
        return self.failed / self.total_attempted

    @property
    def success_count(self) -> int:
        return self.fetched + self.unchanged + self.changed
