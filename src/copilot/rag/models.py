"""Data models for crawler results, page versions, snapshots, and manifest entries.

Note: Canonical implementation is now in `copilot.rag.crawler.models`.
This module re-exports symbols for backward compatibility.
"""

from copilot.rag.crawler.models import (
    CrawlMetrics,
    CrawlResult,
    CrawlSummary,
    PageVersion,
    Snapshot,
    SnapshotManifestEntry,
)

__all__ = [
    "CrawlMetrics",
    "CrawlResult",
    "CrawlSummary",
    "PageVersion",
    "Snapshot",
    "SnapshotManifestEntry",
]
