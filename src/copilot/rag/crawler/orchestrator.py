"""Crawler orchestrator controlling the crawling queue, link extraction, and page visits."""

from __future__ import annotations

import collections
import json
import os
import time
import uuid
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import structlog

from copilot.rag.crawler.cleaner import clean_html
from copilot.rag.crawler.client import CrawlResponse, PoliteClient
from copilot.rag.crawler.config import CrawlConfig
from copilot.rag.crawler.errors import CrawlBlockedError
from copilot.rag.crawler.metrics import (
    log_page_visit,
    record_crawl_summary_metrics,
    trace_crawl_run,
)
from copilot.rag.crawler.models import (
    CrawlResult,
    CrawlSummary,
    PageVersion,
    Snapshot,
)
from copilot.rag.crawler.robots import RobotsPolicy
from copilot.rag.crawler.snapshot import SnapshotStore
from copilot.rag.crawler.url_utils import is_allowed_url, normalize_url

logger = structlog.get_logger("copilot.rag.crawler")


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
                    # Skip anchor-only or javascript/mailto/tel links
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


class CrawlOrchestrator:
    """Workflow controller managing the crawler frontier queue, safety limits, and page processing.

    Responsibilities:
    1. Frontier Queue (BFS): tracks queued and visited URLs without duplicates.
    2. Robots Policy Enforcement: checks every URL against robots rules before fetching.
    3. Link Discovery: extracts hyperlinks, validates them against allowed rules, and schedules them.
    4. Safety: enforces page_cap, handles conditional 304s, and catches circuit-breaker exceptions.
    5. Clean & Snapshot: coordinates HTML cleaning, JS-shell detection, and snapshot persistence.
    6. Observability: emits per-page structured log lines, crawl metrics, and OpenTelemetry spans.
    """

    def __init__(
        self,
        config: CrawlConfig,
        client: PoliteClient,
        robots_policy: RobotsPolicy | None = None,
        snapshot_store: SnapshotStore | None = None,
        state_file: Path | str | None = None,
    ) -> None:
        self.config = config
        self.client = client
        if robots_policy is None:
            logger.warning(
                "No RobotsPolicy provided to CrawlOrchestrator; defaulting to RobotsPolicy.allow_all(). "
                "In production, provide an explicit RobotsPolicy instance."
            )
            self.robots_policy = RobotsPolicy.allow_all()
        else:
            self.robots_policy = robots_policy

        self.snapshot_store = snapshot_store
        self.state_file = Path(state_file) if state_file else None
        self._queue: collections.deque[str] = collections.deque()
        self._queued_set: set[str] = set()
        self._visited: set[str] = set()

        # Initialize frontier queue with allowed start URLs
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

    def _save_state(
        self,
        results: list[CrawlResult] | None = None,
        failed_urls: set[str] | None = None,
    ) -> None:
        """Atomically persist crawler state and page results to disk.

        Uses temporary file with os.replace to guarantee atomic disk writes.
        """
        if not self.state_file:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            state_data = {
                "version": 1,
                "config_name": self.config.user_agent_prefix,
                "visited": sorted(self._visited),
                "queued": list(self._queue),
                "failed": sorted(failed_urls) if failed_urls else [],
                "results": [r.to_dict() for r in results] if results else [],
            }
            token = uuid.uuid4().hex[:8]
            tmp_file = self.state_file.parent / f".tmp_{self.state_file.name}_{token}"
            tmp_file.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
            os.replace(tmp_file, self.state_file)
        except Exception as err:
            logger.debug(
                "Failed to atomically save crawler state to %s: %s",
                self.state_file,
                err,
            )

    def _load_state(self) -> tuple[list[CrawlResult], set[str]]:
        """Load crawler state and previously completed page results to safely resume.

        Returns:
            tuple (previous_results, previous_failed_urls)
        """
        if not self.state_file or not self.state_file.exists():
            return [], set()

        previous_results: list[CrawlResult] = []
        previous_failed: set[str] = set()

        try:
            raw_text = self.state_file.read_text(encoding="utf-8")
            data = json.loads(raw_text)
            if not isinstance(data, dict):
                logger.warning(
                    "Crawler state file %s has invalid non-dictionary JSON.",
                    self.state_file,
                )
                return [], set()

            visited_list = data.get("visited", [])
            if isinstance(visited_list, list):
                for url in visited_list:
                    if isinstance(url, str):
                        norm = normalize_url(url)
                        self._visited.add(norm)
                        self._queued_set.add(norm)

            # Re-enqueue any remaining unvisited URLs from saved queue
            saved_queued = data.get("queued", [])
            if isinstance(saved_queued, list):
                for url in saved_queued:
                    if isinstance(url, str):
                        norm = normalize_url(url)
                        if norm not in self._visited and norm not in self._queue:
                            self._queue.append(norm)
                            self._queued_set.add(norm)

            # Restore failed URLs
            saved_failed = data.get("failed", [])
            if isinstance(saved_failed, list):
                for url in saved_failed:
                    if isinstance(url, str):
                        previous_failed.add(normalize_url(url))

            # Restore previous CrawlResult items so data is not lost on resume
            saved_results = data.get("results", [])
            if isinstance(saved_results, list):
                for item in saved_results:
                    if isinstance(item, dict):
                        try:
                            previous_results.append(CrawlResult.from_dict(item))
                        except Exception:
                            continue

            logger.info(
                "Resumed crawl: loaded %d visited URLs, %d queued URLs, %d previous page results",
                len(self._visited),
                len(self._queue),
                len(previous_results),
            )
        except Exception as err:
            logger.warning(
                "Could not load crawl resume state from %s: %s",
                self.state_file,
                err,
            )

        return previous_results, previous_failed

    def _cleanup_state(self) -> None:
        """Remove state file upon full crawl completion."""
        if self.state_file and self.state_file.exists():
            try:
                self.state_file.unlink()
            except Exception:
                pass

    async def run(
        self,
        max_pages: int | None = None,
        *,
        dry_run: bool = False,
        resume: bool = False,
        snapshot_id: str | None = None,
        clock_now: str | None = None,
    ) -> CrawlSummary:
        """Execute the crawling workflow until queue is exhausted or page cap is reached."""
        start_time = time.monotonic()
        cap = max_pages if max_pages is not None else self.config.page_cap
        summary = CrawlSummary(dry_run=dry_run)

        # 1. Handle Dry-Run Mode (strictly NO network calls, NO disk writes)
        if dry_run:
            logger.info("Executing crawler in DRY-RUN mode (no network requests, no disk writes)")
            dry_run_urls: list[str] = []
            for url in list(self._queue)[:cap]:
                dry_run_urls.append(url)
            summary.dry_run_urls = dry_run_urls
            summary.total_visited = len(dry_run_urls)
            summary.duration_seconds = time.monotonic() - start_time
            return summary

        # 2. Handle Resume Option: restore visited, queued, failed, and past page results
        results: list[CrawlResult] = []
        failed_urls: set[str] = set()
        if resume:
            prev_results, prev_failed = self._load_state()
            results.extend(prev_results)
            failed_urls.update(prev_failed)
            # Recompute summary baseline counts from previous results
            for prev_res in prev_results:
                if prev_res.is_unchanged or prev_res.status_code == 304:
                    summary.unchanged += 1
                elif prev_res.status_code == 200:
                    if prev_res.needs_js:
                        summary.needs_js += 1
                    else:
                        summary.fetched += 1
                elif prev_res.status_code in (404, 410):
                    summary.not_found += 1
                elif prev_res.status_code >= 400:
                    summary.failed += 1

        # Load active snapshot for unified O(1) conditional lookup and version diffing
        active_snapshot: Snapshot | None = None
        snapshot_age_seconds: float = 0.0
        previous_by_url: dict[str, PageVersion] = {}

        if self.snapshot_store:
            active_snapshot = self.snapshot_store.get_active_snapshot()
            if active_snapshot:
                try:
                    created_dt = datetime.fromisoformat(active_snapshot.created_at)
                    snapshot_age_seconds = max(
                        0.0,
                        (datetime.now(created_dt.tzinfo or UTC) - created_dt).total_seconds(),
                    )
                except Exception:
                    snapshot_age_seconds = 0.0

                # Unified O(1) index across canonical and raw page URLs
                for canonical, page in active_snapshot.pages.items():
                    previous_by_url[canonical] = page
                    previous_by_url[page.url] = page
                    previous_by_url[normalize_url(canonical)] = page
                    previous_by_url[normalize_url(page.url)] = page

        with trace_crawl_run(
            "copilot.rag.crawl",
            {
                "crawl.page_cap": cap,
                "crawl.start_urls_count": len(self.config.start_urls),
                "crawl.resume": resume,
            },
        ) as span:
            logger.info(
                "Starting crawl workflow (start_urls: %d, page_cap: %d, resume: %s)",
                len(self._queue),
                cap,
                resume,
            )

            circuit_broken = False

            while self._queue and len(self._visited) < cap:
                current_url = self._queue.popleft()
                if current_url in self._visited:
                    continue
                self._visited.add(current_url)
                summary.total_visited += 1

                # Step 1: Robots.txt policy check
                if not self.robots_policy.can_fetch(self.client.user_agent, current_url):
                    logger.warning("URL disallowed by robots.txt: %s", current_url)
                    summary.blocked_robots += 1
                    failed_urls.add(current_url)
                    log_page_visit(
                        logger.info,
                        url=current_url,
                        outcome="blocked_robots",
                        status_code=403,
                    )
                    self._save_state(results=results, failed_urls=failed_urls)
                    continue

                # Step 2: Unified conditional request lookup from active snapshot
                prev_version = previous_by_url.get(normalize_url(current_url))
                etag = prev_version.etag if prev_version else None
                last_modified = prev_version.last_modified if prev_version else None

                # Step 3: Fetch page via polite HTTP client
                fetch_start = time.monotonic()
                try:
                    response: CrawlResponse = await self.client.fetch(
                        current_url,
                        etag=etag,
                        last_modified=last_modified,
                    )
                except CrawlBlockedError:
                    logger.error("Crawl circuit breaker triggered. Halting crawl immediately.")
                    summary.failed += 1
                    failed_urls.add(current_url)
                    circuit_broken = True
                    log_page_visit(
                        logger.error,
                        url=current_url,
                        outcome="circuit_broken",
                        status_code=429,
                    )
                    self._save_state(results=results, failed_urls=failed_urls)
                    break
                except Exception as err:
                    logger.warning("Failed to fetch %s: %s", current_url, err)
                    summary.failed += 1
                    failed_urls.add(current_url)
                    log_page_visit(
                        logger.warning,
                        url=current_url,
                        outcome="failed",
                        status_code=500,
                    )
                    self._save_state(results=results, failed_urls=failed_urls)
                    continue

                duration_ms = (time.monotonic() - fetch_start) * 1000.0

                # Step 4: Handle 304 Not Modified
                if response.is_unchanged or response.status_code == 304:
                    if not prev_version:
                        # 304 without previous version: treat as error in report and skip
                        logger.warning(
                            "Received 304 for '%s' without previous version in active snapshot.",
                            current_url,
                        )
                        summary.failed += 1
                        failed_urls.add(current_url)
                        log_page_visit(
                            logger.warning,
                            url=current_url,
                            outcome="failed_304_no_previous",
                            status_code=304,
                            duration_ms=duration_ms,
                        )
                    else:
                        summary.unchanged += 1
                        log_page_visit(
                            logger.info,
                            url=current_url,
                            outcome="unchanged",
                            status_code=304,
                            duration_ms=duration_ms,
                        )
                        results.append(
                            CrawlResult(
                                url=current_url,
                                status_code=304,
                                is_unchanged=True,
                                etag=response.etag,
                                last_modified=response.last_modified,
                            )
                        )
                    self._save_state(results=results, failed_urls=failed_urls)
                    continue

                # Step 5: Handle 404 / 410 Not Found
                if response.status_code in (404, 410):
                    summary.not_found += 1
                    failed_urls.add(current_url)
                    log_page_visit(
                        logger.info,
                        url=current_url,
                        outcome="not_found",
                        status_code=response.status_code,
                        duration_ms=duration_ms,
                    )
                    results.append(
                        CrawlResult(
                            url=current_url,
                            status_code=response.status_code,
                            text="",
                        )
                    )
                    self._save_state(results=results, failed_urls=failed_urls)
                    continue

                # Step 6: Handle Non-200 error statuses (500, 403, 429, etc.)
                if response.status_code != 200:
                    summary.failed += 1
                    failed_urls.add(current_url)
                    log_page_visit(
                        logger.warning,
                        url=current_url,
                        outcome="failed",
                        status_code=response.status_code,
                        duration_ms=duration_ms,
                    )
                    results.append(
                        CrawlResult(
                            url=current_url,
                            status_code=response.status_code,
                            text="",
                        )
                    )
                    self._save_state(results=results, failed_urls=failed_urls)
                    continue

                # Step 7: Handle 200 OK — Clean HTML & Extract Content
                cleaned = clean_html(response.text, current_url, config=self.config)
                canonical = cleaned.canonical_url
                allowed, _ = is_allowed_url(canonical, self.config)
                if not allowed:
                    canonical = normalize_url(current_url)

                if cleaned.needs_js:
                    summary.needs_js += 1
                    outcome = "needs_js"
                else:
                    # Check whether content hash is unchanged from previous snapshot via unified lookup
                    matched_prev = previous_by_url.get(canonical) or previous_by_url.get(
                        normalize_url(current_url)
                    )

                    if (
                        matched_prev
                        and matched_prev.content_hash == cleaned.content_hash
                        and not matched_prev.is_removed
                    ):
                        summary.unchanged += 1
                        outcome = "unchanged"
                    else:
                        if matched_prev and not matched_prev.is_removed:
                            summary.changed += 1
                            outcome = "changed"
                        else:
                            summary.fetched += 1
                            outcome = "fetched"

                log_page_visit(
                    logger.info,
                    url=current_url,
                    outcome=outcome,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                )

                # Step 8: Discover new links from HTML (only on 200 OK)
                new_links = extract_links(response.text, current_url)
                for link in new_links:
                    norm_link = normalize_url(link)
                    allowed_link, _ = is_allowed_url(norm_link, self.config)
                    if (
                        allowed_link
                        and norm_link not in self._queued_set
                        and norm_link not in self._visited
                    ):
                        self._queue.append(norm_link)
                        self._queued_set.add(norm_link)

                results.append(
                    CrawlResult(
                        url=current_url,
                        status_code=response.status_code,
                        text=response.text,
                        etag=response.etag,
                        last_modified=response.last_modified,
                        needs_js=cleaned.needs_js,
                        canonical_url=canonical,
                        content_hash=cleaned.content_hash,
                        links_discovered=new_links,
                    )
                )
                self._save_state(results=results, failed_urls=failed_urls)

            # Step 9: Determine explicit crawl completion status
            queue_exhausted = len(self._queue) == 0
            stopped_by_cap = len(self._visited) >= cap and not queue_exhausted
            crawl_complete = queue_exhausted and not circuit_broken
            crawl_successful = crawl_complete and len(failed_urls) == 0

            # Step 10: Build and persist immutable snapshot
            if self.snapshot_store and results:
                snapshot = self.snapshot_store.build_snapshot(
                    results,
                    previous_snapshot=active_snapshot,
                    snapshot_id=snapshot_id,
                    crawl_complete=crawl_complete,
                    failed_urls=failed_urls,
                    config=self.config,
                    clock_now=clock_now,
                )
                manifest_path, _ = self.snapshot_store.save_snapshot(snapshot)
                summary.snapshot_id = snapshot.snapshot_id
                summary.manifest_path = str(manifest_path)

                # Count removals: pages marked is_removed in the new snapshot
                summary.removed = sum(1 for p in snapshot.pages.values() if p.is_removed)

                # Count new page versions created
                if active_snapshot:
                    new_versions = sum(
                        1
                        for canon, pv in snapshot.pages.items()
                        if not pv.is_removed
                        and (
                            canon not in active_snapshot.pages
                            or active_snapshot.pages[canon].content_hash != pv.content_hash
                        )
                    )
                    summary.new_page_versions_count = new_versions
                else:
                    summary.new_page_versions_count = len(snapshot.active_pages)

            summary.results = results
            summary.duration_seconds = time.monotonic() - start_time
            record_crawl_summary_metrics(
                summary.duration_seconds,
                "completed" if crawl_complete else "partial",
            )

            # Update span attributes
            if span:
                span.set_attribute("crawl.total_visited", summary.total_visited)
                span.set_attribute("crawl.fetched", summary.fetched)
                span.set_attribute("crawl.unchanged", summary.unchanged)
                span.set_attribute("crawl.changed", summary.changed)
                span.set_attribute("crawl.removed", summary.removed)
                span.set_attribute("crawl.failed", summary.failed)
                span.set_attribute("crawl.not_found", summary.not_found)
                span.set_attribute("crawl.blocked_robots", summary.blocked_robots)
                span.set_attribute("crawl.needs_js", summary.needs_js)
                span.set_attribute("crawl.duration_seconds", summary.duration_seconds)
                span.set_attribute("crawl.snapshot_age_seconds", snapshot_age_seconds)
                span.set_attribute("crawl.crawl_complete", crawl_complete)
                span.set_attribute("crawl.crawl_successful", crawl_successful)

            # Cleanup state ONLY if crawl ran to completion without circuit break or cap stop
            if crawl_complete and not circuit_broken and not stopped_by_cap:
                self._cleanup_state()
            else:
                self._save_state(results=results, failed_urls=failed_urls)

            return summary
