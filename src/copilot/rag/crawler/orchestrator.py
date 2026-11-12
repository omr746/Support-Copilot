"""Crawler orchestrator controlling the crawling queue, link extraction, and page visits."""

from __future__ import annotations

import collections
import logging
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

from copilot.rag.crawler.client import PoliteClient
from copilot.rag.crawler.config import CrawlConfig
from copilot.rag.crawler.errors import CrawlBlockedError
from copilot.rag.crawler.robots import RobotsPolicy
from copilot.rag.crawler.url_utils import is_allowed_url, normalize_url

logger = logging.getLogger(__name__)


class _HTMLLinkParser(HTMLParser):
    """Fast, zero-dependency HTML parser extracting href links."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            for attr_name, attr_val in attrs:
                if attr_name.lower() == "href" and attr_val:
                    href = attr_val.strip()
                    # Skip anchor-only or javascript/mailto links
                    if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                        self.links.append(href)


def extract_links(html_text: str, current_url: str) -> list[str]:
    """Extract and resolve valid links from an HTML document."""
    parser = _HTMLLinkParser()
    try:
        parser.feed(html_text)
    except Exception as err:
        logger.debug("HTML parser encountered error on %s: %s", current_url, err)

    resolved: list[str] = []
    for href in parser.links:
        try:
            absolute = urljoin(current_url, href)
            resolved.append(absolute)
        except Exception:
            continue
    return resolved


@dataclass
class CrawlResult:
    """Record of a single crawled page."""

    url: str
    status_code: int
    text: str
    etag: str | None = None
    last_modified: str | None = None
    is_unchanged: bool = False
    links_discovered: list[str] = field(default_factory=list)


@dataclass
class CrawlSummary:
    """Consolidated summary of a crawl run."""

    total_visited: int = 0
    fetched: int = 0
    unchanged: int = 0
    blocked_robots: int = 0
    failed: int = 0
    results: list[CrawlResult] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return self.fetched + self.unchanged


class CrawlOrchestrator:
    """Workflow controller managing the crawler frontier queue, safety limits, and page processing.

    Responsibilities:
    1. Frontier Queue (BFS): tracks queued and visited URLs without duplicates.
    2. Robots Policy Enforcement: checks every URL against robots rules before fetching.
    3. Link Discovery: extracts hyperlinks, validates them against allowed rules, and schedules them.
    4. Safety: enforces page_cap, handles conditional 304s, and catches circuit-breaker exceptions.
    """

    def __init__(
        self,
        config: CrawlConfig,
        client: PoliteClient,
        robots_policy: RobotsPolicy | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.robots_policy = robots_policy or RobotsPolicy.allow_all()
        self._queue: collections.deque[str] = collections.deque()
        self._queued_set: set[str] = set()
        self._visited: set[str] = set()

        # Initialize frontier queue with start URLs
        for start_url in config.start_urls:
            normalized = normalize_url(start_url)
            allowed, _ = is_allowed_url(normalized, config)
            if allowed and normalized not in self._queued_set:
                self._queue.append(normalized)
                self._queued_set.add(normalized)

    @property
    def queue_size(self) -> int:
        return len(self._queue)

    @property
    def visited_count(self) -> int:
        return len(self._visited)

    async def run(self, max_pages: int | None = None) -> CrawlSummary:
        """Execute the crawling workflow until queue is exhausted or page cap is reached."""
        cap = max_pages if max_pages is not None else self.config.page_cap
        summary = CrawlSummary()

        logger.info(
            "Starting crawl workflow (start_urls: %d, page_cap: %d)",
            len(self._queue),
            cap,
        )

        while self._queue and len(self._visited) < cap:
            current_url = self._queue.popleft()
            self._visited.add(current_url)
            summary.total_visited += 1

            # 1. Robots.txt policy check
            if not self.robots_policy.can_fetch(self.client.user_agent, current_url):
                logger.info("URL blocked by robots.txt: %s", current_url)
                summary.blocked_robots += 1
                continue

            # 2. Fetch page using polite client
            try:
                resp = await self.client.fetch(current_url)
            except CrawlBlockedError as err:
                logger.error("Crawl blocked by circuit breaker: %s", err)
                summary.failed += 1
                break
            except Exception as err:
                logger.warning("Failed fetching %s: %s", current_url, err)
                summary.failed += 1
                continue

            # 3. Handle response status
            if resp.status_code == 304 or resp.is_unchanged:
                summary.unchanged += 1
                summary.results.append(
                    CrawlResult(
                        url=current_url,
                        status_code=304,
                        text="",
                        etag=resp.etag,
                        last_modified=resp.last_modified,
                        is_unchanged=True,
                    )
                )
                continue

            if resp.status_code != 200:
                summary.failed += 1
                continue

            summary.fetched += 1

            # 4. Extract outgoing links if HTML
            discovered_links: list[str] = []
            content_type = resp.headers.get("content-type", "")
            if "text/html" in content_type.lower() or not content_type:
                raw_links = extract_links(resp.text, current_url)
                for link in raw_links:
                    normalized = normalize_url(link)
                    allowed, _ = is_allowed_url(normalized, self.config)
                    if (
                        allowed
                        and normalized not in self._visited
                        and normalized not in self._queued_set
                    ):
                        self._queue.append(normalized)
                        self._queued_set.add(normalized)
                        discovered_links.append(normalized)

            summary.results.append(
                CrawlResult(
                    url=current_url,
                    status_code=200,
                    text=resp.text,
                    etag=resp.etag,
                    last_modified=resp.last_modified,
                    is_unchanged=False,
                    links_discovered=discovered_links,
                )
            )

        logger.info(
            "Crawl finished. Visited: %d, Fetched: %d, Unchanged: %d, Blocked by robots: %d, Failed: %d",
            summary.total_visited,
            summary.fetched,
            summary.unchanged,
            summary.blocked_robots,
            summary.failed,
        )
        return summary
