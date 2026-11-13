"""Polite async HTTP client enforcing concurrency=1, polite delays, and circuit breakers."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from copilot.core.errors import _parse_retry_after
from copilot.core.resilience.sleep import AsyncioSleeper, Sleeper
from copilot.rag.crawler.config import CrawlConfig
from copilot.rag.crawler.errors import CrawlBlockedError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CrawlResponse:
    """Standardized crawler response representation."""

    url: str
    status_code: int
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    etag: str | None = None
    last_modified: str | None = None
    is_unchanged: bool = False


class PoliteClient:
    """Polite HTTP client for knowledge base crawling.

    Guarantees:
    - Concurrency = 1 (enforced by asyncio.Lock).
    - Configurable inter-request delay enforced by injected Sleeper.
    - Strict User-Agent header with verified contact address.
    - Automatic retries with exponential backoff on 5xx and network errors.
    - Conditional requests (If-None-Match / If-Modified-Since) with 304 handling.
    - Circuit breaker halting crawler upon consecutive 403 or 429 status codes.
    """

    def __init__(
        self,
        config: CrawlConfig,
        *,
        contact: str | None = None,
        sleeper: Sleeper | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.user_agent = config.build_user_agent(contact)
        self._sleeper: Sleeper = sleeper or AsyncioSleeper()
        self._delay_seconds = config.delay_seconds
        self._consecutive_error_limit = config.consecutive_error_limit
        self._consecutive_errors: int = 0
        self._request_count: int = 0
        self._lock = asyncio.Lock()
        self._owns_client = client is None

        if client is not None:
            self._client = client
        else:
            self._client = httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(config.timeout_seconds),
                follow_redirects=True,
            )

    @property
    def delay_seconds(self) -> float:
        """Current delay between consecutive requests in seconds."""
        return self._delay_seconds

    def update_delay(self, seconds: float) -> None:
        """Update delay if robots.txt specifies a larger crawl-delay."""
        if seconds > self._delay_seconds:
            logger.info(
                "Increasing crawl delay from %.1fs to %.1fs per robots.txt",
                self._delay_seconds,
                seconds,
            )
            self._delay_seconds = seconds

    async def __aenter__(self) -> PoliteClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close underlying HTTP client if created internally."""
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    async def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> CrawlResponse:
        """Fetch a single URL obeying polite constraints and resilience rules.

        Raises:
            CrawlBlockedError: If consecutive 403 or 429 limits are reached.
            httpx.TransportError: If network failures persist beyond max_retries.
        """
        async with self._lock:
            # Enforce polite delay between consecutive requests
            if self._request_count > 0 and self._delay_seconds > 0:
                await self._sleeper.sleep(self._delay_seconds)

            self._request_count += 1

            headers: dict[str, str] = {
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ar,en;q=0.9",
            }
            if etag:
                headers["If-None-Match"] = etag
            if last_modified:
                headers["If-Modified-Since"] = last_modified

            response = await self._fetch_with_retries(url, headers)
            return self._handle_response(
                url, response, original_etag=etag, original_last_modified=last_modified
            )

    async def _fetch_with_retries(self, url: str, headers: dict[str, str]) -> httpx.Response:
        """Execute request with retries and exponential backoff on 5xx / transient errors."""
        attempt = 0
        max_retries = self.config.max_retries

        while True:
            try:
                resp = await self._client.get(url, headers=headers)
                # Check for transient server errors (5xx)
                if 500 <= resp.status_code < 600 and attempt < max_retries:
                    attempt += 1
                    backoff = self._calculate_backoff(attempt, resp)
                    logger.warning(
                        "Server error %d from %s (attempt %d/%d). Backing off %.2fs",
                        resp.status_code,
                        url,
                        attempt,
                        max_retries,
                        backoff,
                    )
                    await self._sleeper.sleep(backoff)
                    continue
                return resp
            except (httpx.TransportError, httpx.TimeoutException) as err:
                if attempt < max_retries:
                    attempt += 1
                    backoff = 0.5 * (2 ** (attempt - 1))
                    logger.warning(
                        "Network error from %s: %s (attempt %d/%d). Backing off %.2fs",
                        url,
                        err,
                        attempt,
                        max_retries,
                        backoff,
                    )
                    await self._sleeper.sleep(backoff)
                    continue
                raise

    def _calculate_backoff(self, attempt: int, resp: httpx.Response) -> float:
        """Calculate backoff duration, respecting Retry-After header if present."""
        retry_after_hdr = resp.headers.get("retry-after")
        if retry_after_hdr:
            try:
                parsed = _parse_retry_after(retry_after_hdr)
                if parsed is not None:
                    return float(parsed)
            except Exception:
                pass
        return float(0.5 * (2 ** (attempt - 1)))

    def _handle_response(
        self,
        url: str,
        resp: httpx.Response,
        original_etag: str | None,
        original_last_modified: str | None,
    ) -> CrawlResponse:
        """Process response, update circuit breaker counters, and format CrawlResponse."""
        status = resp.status_code

        # Circuit breaker: track consecutive 403 / 429
        if status in (403, 429):
            self._consecutive_errors += 1
            logger.warning(
                "Received status %d from %s (consecutive blocked count: %d/%d)",
                status,
                url,
                self._consecutive_errors,
                self._consecutive_error_limit,
            )
            if self._consecutive_errors >= self._consecutive_error_limit:
                raise CrawlBlockedError(
                    f"Circuit breaker tripped: received {self._consecutive_errors} consecutive "
                    f"blocked responses ({status}) from host. Crawling stopped."
                )
        elif 200 <= status < 400:
            self._consecutive_errors = 0

        # Conditional 304 handling: resource unchanged
        if status == 304:
            return CrawlResponse(
                url=url,
                status_code=304,
                headers=dict(resp.headers),
                text="",
                etag=resp.headers.get("etag") or original_etag,
                last_modified=resp.headers.get("last-modified") or original_last_modified,
                is_unchanged=True,
            )

        return CrawlResponse(
            url=url,
            status_code=status,
            headers=dict(resp.headers),
            text=resp.text,
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
            is_unchanged=False,
        )
