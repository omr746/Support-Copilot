"""Polite web crawler package for Support Copilot."""

from copilot.rag.crawler.client import CrawlResponse, PoliteClient
from copilot.rag.crawler.config import CrawlConfig
from copilot.rag.crawler.errors import (
    CrawlBlockedError,
    CrawlerError,
    InvalidUrlError,
    MissingContactError,
    RobotsDisallowedError,
)
from copilot.rag.crawler.orchestrator import (
    CrawlOrchestrator,
    CrawlResult,
    CrawlSummary,
    extract_links,
)
from copilot.rag.crawler.robots import RobotsPolicy
from copilot.rag.crawler.url_utils import is_allowed_url, normalize_url

__all__ = [
    "CrawlBlockedError",
    "CrawlConfig",
    "CrawlOrchestrator",
    "CrawlResponse",
    "CrawlResult",
    "CrawlSummary",
    "CrawlerError",
    "InvalidUrlError",
    "MissingContactError",
    "PoliteClient",
    "RobotsDisallowedError",
    "RobotsPolicy",
    "extract_links",
    "is_allowed_url",
    "normalize_url",
]
