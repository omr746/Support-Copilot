"""Polite web crawler, HTML cleaner, and snapshot package for Support Copilot."""

from copilot.rag.crawler.cleaner import (
    CLEANER_VERSION,
    CleanedPage,
    clean_html,
    compute_content_hash,
    detect_language,
    is_javascript_shell,
    normalize_whitespace,
)
from copilot.rag.crawler.client import CrawlResponse, PoliteClient
from copilot.rag.crawler.config import CrawlConfig
from copilot.rag.crawler.errors import (
    CrawlBlockedError,
    CrawlerError,
    InvalidUrlError,
    MissingContactError,
    RobotsDisallowedError,
)
from copilot.rag.crawler.metrics import (
    log_page_visit,
    setup_tracing,
    trace_crawl_run,
    trace_page_visit,
)
from copilot.rag.crawler.models import (
    CrawlMetrics,
    CrawlResult,
    CrawlSummary,
    PageVersion,
    Snapshot,
    SnapshotManifestEntry,
)
from copilot.rag.crawler.orchestrator import (
    CrawlOrchestrator,
    extract_links,
)
from copilot.rag.crawler.robots import RobotsPolicy
from copilot.rag.crawler.snapshot import (
    SnapshotStore,
    generate_snapshot_id,
)
from copilot.rag.crawler.url_utils import (
    hash_path,
    is_allowed_url,
    normalize_url,
)

__all__ = [
    "CLEANER_VERSION",
    "CleanedPage",
    "CrawlBlockedError",
    "CrawlConfig",
    "CrawlMetrics",
    "CrawlOrchestrator",
    "CrawlResponse",
    "CrawlResult",
    "CrawlSummary",
    "CrawlerError",
    "InvalidUrlError",
    "MissingContactError",
    "PageVersion",
    "PoliteClient",
    "RobotsDisallowedError",
    "RobotsPolicy",
    "Snapshot",
    "SnapshotManifestEntry",
    "SnapshotStore",
    "clean_html",
    "compute_content_hash",
    "detect_language",
    "extract_links",
    "generate_snapshot_id",
    "hash_path",
    "is_allowed_url",
    "is_javascript_shell",
    "log_page_visit",
    "normalize_url",
    "normalize_whitespace",
    "setup_tracing",
    "trace_crawl_run",
    "trace_page_visit",
]
