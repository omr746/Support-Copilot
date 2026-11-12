"""Domain error classes for web crawler."""

from __future__ import annotations

from copilot.core.errors import CopilotError


class CrawlerError(CopilotError):
    """Base error for all crawler operations."""


class CrawlBlockedError(CrawlerError):
    """Raised when crawling is blocked (e.g. circuit breaker tripped on 403/429 or robots.txt unreachable)."""


class RobotsDisallowedError(CrawlerError):
    """Raised when an attempt is made to fetch a URL disallowed by robots.txt."""


class InvalidUrlError(CrawlerError):
    """Raised when a URL is malformed, not allowed by host/prefix, or matches denied patterns."""


class MissingContactError(CrawlerError):
    """Raised when the mandatory CRAWL_CONTACT environment variable is missing or empty."""
