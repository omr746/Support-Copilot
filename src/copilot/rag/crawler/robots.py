"""Robots.txt parser and policy enforcement using urllib.robotparser."""

from __future__ import annotations

import logging
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from copilot.rag.crawler.errors import CrawlBlockedError

logger = logging.getLogger(__name__)


class RobotsPolicy:
    """Robots.txt evaluation policy.

    Implements the standard robots.txt exclusion protocol:
    - 404 Not Found: Crawling is permitted site-wide.
    - 5xx / Network / Fetch errors: Crawling is blocked (fail-closed security).
    - 200 OK: Evaluates rules using RobotFileParser and extracts crawl-delay.
    """

    def __init__(
        self, parser: RobotFileParser | None = None, *, is_allow_all: bool = False
    ) -> None:
        self._parser = parser
        self._is_allow_all = is_allow_all

    @classmethod
    def allow_all(cls) -> RobotsPolicy:
        """Create policy allowing all requests (used when robots.txt returns 404)."""
        return cls(is_allow_all=True)

    @classmethod
    def from_content(cls, content: str, base_url: str = "https://www.te.eg") -> RobotsPolicy:
        """Parse robots.txt content string into a RobotsPolicy."""
        parser = RobotFileParser()
        parser.set_url(urljoin(base_url, "/robots.txt"))
        lines = [line.strip() for line in content.splitlines()]
        parser.parse(lines)
        return cls(parser=parser)

    def can_fetch(self, user_agent: str, url: str) -> bool:
        """Check if user_agent is permitted to fetch url according to robots.txt rules."""
        if self._is_allow_all:
            return True
        if self._parser is None:
            return False
        return self._parser.can_fetch(user_agent, url)

    def crawl_delay(self, user_agent: str) -> float | None:
        """Extract crawl-delay directive for user_agent, if specified."""
        if self._is_allow_all or self._parser is None:
            return None
        delay = self._parser.crawl_delay(user_agent)
        return float(delay) if delay is not None else None

    @classmethod
    async def fetch(
        cls,
        client: httpx.AsyncClient,
        base_url: str,
        user_agent: str,
    ) -> RobotsPolicy:
        """Fetch and parse robots.txt from base_url using an async HTTP client.

        Behavior:
        - 200 OK: parses rules and returns RobotsPolicy.
        - 404 Not Found: returns RobotsPolicy.allow_all().
        - 5xx or Network/Timeout errors: raises CrawlBlockedError (fail-closed).
        - 403 / other errors: raises CrawlBlockedError (fail-closed).
        """
        parts = urlsplit(base_url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        headers = {"User-Agent": user_agent}

        try:
            resp = await client.get(robots_url, headers=headers)
        except (httpx.TransportError, httpx.TimeoutException) as err:
            logger.error("Failed to connect to robots.txt at %s: %s", robots_url, err)
            raise CrawlBlockedError(
                f"Network error while fetching robots.txt from {robots_url}: {err}. "
                "Crawling is blocked by fail-closed policy."
            ) from err

        if resp.status_code == 200:
            return cls.from_content(resp.text, base_url=base_url)
        elif resp.status_code == 404:
            logger.info("robots.txt not found (404) at %s: allowing crawling site-wide", robots_url)
            return cls.allow_all()
        elif 500 <= resp.status_code < 600:
            logger.error("robots.txt returned server error %d at %s", resp.status_code, robots_url)
            raise CrawlBlockedError(
                f"Server error (HTTP {resp.status_code}) fetching robots.txt from {robots_url}. "
                "Crawling is blocked by fail-closed policy."
            )
        else:
            logger.error(
                "robots.txt returned unexpected status %d at %s", resp.status_code, robots_url
            )
            raise CrawlBlockedError(
                f"Unexpected status (HTTP {resp.status_code}) fetching robots.txt from {robots_url}. "
                "Crawling is blocked by fail-closed policy."
            )
