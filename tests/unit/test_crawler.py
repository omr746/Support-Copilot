"""Unit tests for crawler configuration, URL normalization, robots policy, and polite client."""

from __future__ import annotations

import httpx
import pytest

from copilot.rag.crawler.client import PoliteClient
from copilot.rag.crawler.config import CrawlConfig
from copilot.rag.crawler.errors import (
    CrawlBlockedError,
    MissingContactError,
)
from copilot.rag.crawler.orchestrator import CrawlOrchestrator, extract_links
from copilot.rag.crawler.robots import RobotsPolicy
from copilot.rag.crawler.url_utils import is_allowed_url, normalize_url
from tests.fakes.fake_sleeper import FakeSleeper


@pytest.fixture
def base_crawl_config() -> CrawlConfig:
    return CrawlConfig(
        start_urls=[
            "https://www.te.eg/wps/portal/te/Personal",
            "https://www.te.eg/wps/portal/te/Personal/Customer-Service",
            "https://www.te.eg/we",
        ],
        allowed_hosts=["www.te.eg", "te.eg"],
        allowed_path_prefixes=["/wps/portal/te", "/we"],
        denied_patterns=[
            r"(?i)login",
            r"(?i)signin",
            r"(?i)account",
            r"(?i)my[-_]?account",
            r"(?i)bill[-_]?pay",
            r"(?i)payment",
            r"(?i)recharge",
            r"(?i)checkout",
            r"(?i)cart",
            r"(?i)form",
            r"(?i)password",
            r"(?i)register",
            r"(?i)auth",
            r"\?",
        ],
        languages=["ar", "en"],
        page_cap=100,
        delay_seconds=1.0,
        timeout_seconds=5.0,
        max_retries=2,
        consecutive_error_limit=3,
        user_agent_prefix="SupportCopilot/0.1.0",
    )


# --- 1. CRAWL_CONTACT and User-Agent Tests ---


def test_crawler_refuses_to_run_without_crawl_contact(
    base_crawl_config: CrawlConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crawler must refuse to build user-agent or run if CRAWL_CONTACT is unset or empty."""
    monkeypatch.delenv("CRAWL_CONTACT", raising=False)
    with pytest.raises(MissingContactError, match="CRAWL_CONTACT environment variable must be set"):
        base_crawl_config.build_user_agent()

    with pytest.raises(MissingContactError):
        base_crawl_config.build_user_agent("   ")

    with pytest.raises(MissingContactError):
        PoliteClient(base_crawl_config, contact=None)


def test_crawler_builds_correct_user_agent(base_crawl_config: CrawlConfig) -> None:
    """User-Agent header format must identify the project and include the contact address."""
    ua = base_crawl_config.build_user_agent("crawler-admin@example.com")
    assert (
        ua
        == "SupportCopilot/0.1.0 (+https://github.com/3amorry/Support-Copilot; contact: crawler-admin@example.com)"
    )


def test_crawler_loads_crawl_yaml_config() -> None:
    """Verify loading from configs/crawl.yaml matches required start URLs and constraints."""
    config = CrawlConfig.from_yaml("configs/crawl.yaml")
    assert "https://www.te.eg" in config.start_urls
    assert "www.te.eg" in config.allowed_hosts
    assert "/" in config.allowed_path_prefixes
    assert config.delay_seconds == 1.0
    assert config.consecutive_error_limit == 3


# --- 2. URL Normalization and Allowlist Tests ---


def test_url_normalization_strips_fragments_and_tracking_parameters() -> None:
    """URL normalization removes fragments, tracking parameters, and standardizes case/slashes."""
    url = "HTTPS://WWW.TE.EG:443/we/support/?utm_source=facebook&utm_medium=cpc&fbclid=12345#faq-heading"
    normalized = normalize_url(url)
    assert normalized == "https://www.te.eg/we/support"


def test_url_normalization_collapses_multiple_slashes() -> None:
    url = "https://www.te.eg//wps///portal/te/Personal/"
    assert normalize_url(url) == "https://www.te.eg/wps/portal/te/Personal"


def test_allowlist_accepts_valid_customer_service_urls(base_crawl_config: CrawlConfig) -> None:
    assert is_allowed_url("https://www.te.eg/we", base_crawl_config)[0] is True
    assert (
        is_allowed_url(
            "https://www.te.eg/wps/portal/te/Personal/Customer-Service", base_crawl_config
        )[0]
        is True
    )
    assert is_allowed_url("https://te.eg/we/internet", base_crawl_config)[0] is True


def test_allowlist_rejects_unallowed_hosts(base_crawl_config: CrawlConfig) -> None:
    allowed, reason = is_allowed_url("https://attacker.com/we", base_crawl_config)
    assert allowed is False
    assert "not in allowed_hosts" in reason


def test_allowlist_rejects_unallowed_path_prefixes(base_crawl_config: CrawlConfig) -> None:
    allowed, reason = is_allowed_url("https://www.te.eg/other/path", base_crawl_config)
    assert allowed is False
    assert "does not match any allowed prefix" in reason


@pytest.mark.parametrize(
    "denied_url",
    [
        "https://www.te.eg/we/login",
        "https://www.te.eg/we/signin",
        "https://www.te.eg/we/my-account",
        "https://www.te.eg/we/bill-pay",
        "https://www.te.eg/we/payment",
        "https://www.te.eg/we/recharge",
        "https://www.te.eg/we/cart",
        "https://www.te.eg/we/checkout",
        "https://www.te.eg/we/feedback-form",
        "https://www.te.eg/we/reset-password",
        "https://www.te.eg/we/register",
        "https://www.te.eg/we/auth/token",
        "https://www.te.eg/we/support?query=1",
    ],
)
def test_allowlist_rejects_denied_patterns(base_crawl_config: CrawlConfig, denied_url: str) -> None:
    allowed, reason = is_allowed_url(denied_url, base_crawl_config)
    assert allowed is False
    assert "matches denied pattern" in reason


# --- 3. Robots.txt Policy Tests ---


def test_robots_allowed_and_disallowed_rules() -> None:
    robots_text = """
    User-Agent: *
    Disallow: /wps/portal/te/private/
    Crawl-delay: 2
    """
    policy = RobotsPolicy.from_content(robots_text, base_url="https://www.te.eg")
    assert (
        policy.can_fetch("SupportCopilot/0.1.0", "https://www.te.eg/wps/portal/te/public") is True
    )
    assert (
        policy.can_fetch("SupportCopilot/0.1.0", "https://www.te.eg/wps/portal/te/private/secret")
        is False
    )
    assert policy.crawl_delay("SupportCopilot/0.1.0") == 2.0


def test_robots_empty_disallow_allows_crawling() -> None:
    """Exact te.eg robots.txt content: User-Agent: * \n Disallow:"""
    robots_text = "User-Agent: *\nDisallow:\n"
    policy = RobotsPolicy.from_content(robots_text, base_url="https://www.te.eg")
    assert policy.can_fetch("SupportCopilot/0.1.0", "https://www.te.eg/we") is True
    assert (
        policy.can_fetch("SupportCopilot/0.1.0", "https://www.te.eg/wps/portal/te/Personal") is True
    )


@pytest.mark.asyncio
async def test_robots_404_allows_crawling() -> None:
    """Missing robots.txt (404) allows crawling site-wide."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_http:
        policy = await RobotsPolicy.fetch(mock_http, "https://www.te.eg", "SupportCopilot/0.1.0")
        assert policy.can_fetch("SupportCopilot/0.1.0", "https://www.te.eg/we") is True


@pytest.mark.asyncio
async def test_robots_5xx_blocks_crawling() -> None:
    """Server error (5xx) on robots.txt raises CrawlBlockedError (fail-closed policy)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_http:
        with pytest.raises(CrawlBlockedError, match=r"Server error .* fetching robots.txt"):
            await RobotsPolicy.fetch(mock_http, "https://www.te.eg", "SupportCopilot/0.1.0")


@pytest.mark.asyncio
async def test_robots_network_error_blocks_crawling() -> None:
    """Network connection failure on robots.txt raises CrawlBlockedError (fail-closed policy)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused by fake server")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_http:
        with pytest.raises(CrawlBlockedError, match=r"Network error while fetching robots.txt"):
            await RobotsPolicy.fetch(mock_http, "https://www.te.eg", "SupportCopilot/0.1.0")


# --- 4. Polite HTTP Client Tests ---


@pytest.mark.asyncio
async def test_polite_client_always_sends_user_agent(base_crawl_config: CrawlConfig) -> None:
    """Every request must carry the properly formatted User-Agent with contact info."""
    captured_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_headers
        captured_headers = dict(request.headers)
        return httpx.Response(200, text="<html>WE Customer Support</html>")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with PoliteClient(
        base_crawl_config, contact="team@example.com", client=mock_client
    ) as client:
        resp = await client.fetch("https://www.te.eg/we")
        assert resp.status_code == 200
        assert captured_headers["user-agent"] == (
            "SupportCopilot/0.1.0 (+https://github.com/3amorry/Support-Copilot; contact: team@example.com)"
        )


@pytest.mark.asyncio
async def test_polite_client_enforces_delay_via_fake_clock(base_crawl_config: CrawlConfig) -> None:
    """Fake clock (FakeSleeper) proves polite delay occurs between consecutive requests."""
    sleeper = FakeSleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="OK")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with PoliteClient(
        base_crawl_config,
        contact="team@example.com",
        sleeper=sleeper,
        client=mock_client,
    ) as client:
        # First request: immediate (no delay needed before request 1)
        await client.fetch("https://www.te.eg/we/page1")
        assert len(sleeper.sleeps) == 0

        # Second request: delay of 1.0 second enforced
        await client.fetch("https://www.te.eg/we/page2")
        assert len(sleeper.sleeps) == 1
        assert sleeper.sleeps[0] == 1.0

        # Third request: another 1.0 second delay enforced
        await client.fetch("https://www.te.eg/we/page3")
        assert len(sleeper.sleeps) == 2
        assert sleeper.total_slept == 2.0


@pytest.mark.asyncio
async def test_polite_client_conditional_requests_and_304_unchanged(
    base_crawl_config: CrawlConfig,
) -> None:
    """ETag and Last-Modified headers are sent, and 304 response is marked is_unchanged=True."""
    captured_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_headers
        captured_headers = dict(request.headers)
        if request.headers.get("if-none-match") == '"hash123"':
            return httpx.Response(304, headers={"ETag": '"hash123"'})
        return httpx.Response(
            200,
            text="content",
            headers={"ETag": '"hash123"', "Last-Modified": "Wed, 21 Oct 2026 07:28:00 GMT"},
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with PoliteClient(
        base_crawl_config, contact="team@example.com", client=mock_client
    ) as client:
        # First fetch: get 200 with ETag
        resp1 = await client.fetch("https://www.te.eg/we")
        assert resp1.status_code == 200
        assert resp1.is_unchanged is False
        assert resp1.etag == '"hash123"'

        # Second fetch: conditional request with ETag -> 304 Not Modified
        resp2 = await client.fetch(
            "https://www.te.eg/we", etag=resp1.etag, last_modified=resp1.last_modified
        )
        assert captured_headers["if-none-match"] == '"hash123"'
        assert captured_headers["if-modified-since"] == "Wed, 21 Oct 2026 07:28:00 GMT"
        assert resp2.status_code == 304
        assert resp2.is_unchanged is True
        assert resp2.etag == '"hash123"'


@pytest.mark.asyncio
async def test_polite_client_circuit_breaker_stops_after_repeated_429_or_403(
    base_crawl_config: CrawlConfig,
) -> None:
    """Client stops with CrawlBlockedError after consecutive 429 or 403 responses reach limit."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, headers={"Retry-After": "10"}, text="Rate limit exceeded")

    sleeper = FakeSleeper()
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with PoliteClient(
        base_crawl_config,
        contact="team@example.com",
        sleeper=sleeper,
        client=mock_client,
    ) as client:
        # 1st 429: recorded
        await client.fetch("https://www.te.eg/we/1")
        # 2nd 429: recorded
        await client.fetch("https://www.te.eg/we/2")
        # 3rd 429: limit reached (consecutive_error_limit=3) -> CrawlBlockedError
        with pytest.raises(
            CrawlBlockedError,
            match="Circuit breaker tripped: received 3 consecutive blocked responses",
        ):
            await client.fetch("https://www.te.eg/we/3")


@pytest.mark.asyncio
async def test_polite_client_retries_transient_5xx_errors(base_crawl_config: CrawlConfig) -> None:
    """Client retries 5xx server errors with backoff before giving up or succeeding."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(200, text="Recovered!")

    sleeper = FakeSleeper()
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with PoliteClient(
        base_crawl_config,
        contact="team@example.com",
        sleeper=sleeper,
        client=mock_client,
    ) as client:
        resp = await client.fetch("https://www.te.eg/we")
        assert resp.status_code == 200
        assert resp.text == "Recovered!"
        assert attempts == 3
        assert len(sleeper.sleeps) == 2  # Two retries with backoff


# --- 5. Crawl Orchestrator Tests ---


def test_extract_links_from_html() -> None:
    html = """
    <html>
      <body>
        <a href="/support">Support</a>
        <a href="https://www.te.eg/plans">Plans</a>
        <a href="#section">Fragment Only</a>
        <a href="javascript:void(0)">JS</a>
        <a href="mailto:info@te.eg">Email</a>
      </body>
    </html>
    """
    links = extract_links(html, "https://www.te.eg")
    assert links == [
        "https://www.te.eg/support",
        "https://www.te.eg/plans",
    ]


@pytest.mark.asyncio
async def test_orchestrator_bfs_crawls_and_enforces_page_cap(
    base_crawl_config: CrawlConfig,
) -> None:
    """Orchestrator explores links in BFS order and halts when page_cap is reached."""
    responses: dict[str, str] = {
        "https://www.te.eg/": '<html><a href="/page1">P1</a><a href="/page2">P2</a></html>',
        "https://www.te.eg/page1": '<html><a href="/page3">P3</a></html>',
        "https://www.te.eg/page2": "<html><p>Page 2</p></html>",
        "https://www.te.eg/page3": "<html><p>Page 3</p></html>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        content = responses.get(url, "<html>404 Not Found</html>")
        status = 200 if url in responses else 404
        return httpx.Response(status, text=content, headers={"Content-Type": "text/html"})

    config = CrawlConfig(
        start_urls=["https://www.te.eg/"],
        allowed_hosts=["www.te.eg", "te.eg"],
        allowed_path_prefixes=["/"],
        denied_patterns=[r"(?i)login", r"\?"],
        page_cap=3,
        delay_seconds=0.0,
    )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with PoliteClient(config, contact="test@example.com", client=mock_client) as client:
        orchestrator = CrawlOrchestrator(config, client)
        assert orchestrator.queue_size == 1

        summary = await orchestrator.run()
        # Cap was 3, so only 3 pages visited
        assert summary.total_visited == 3
        assert summary.fetched == 3
        assert len(summary.results) == 3


@pytest.mark.asyncio
async def test_orchestrator_obeys_robots_disallow(base_crawl_config: CrawlConfig) -> None:
    """Orchestrator checks robots policy and skips disallowed URLs."""
    responses: dict[str, str] = {
        "https://www.te.eg/": '<html><a href="/allowed">Allowed</a><a href="/secret">Secret</a></html>',
        "https://www.te.eg/allowed": "<html>Allowed Content</html>",
        "https://www.te.eg/secret": "<html>Secret Content</html>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        return httpx.Response(
            200, text=responses.get(url, "OK"), headers={"Content-Type": "text/html"}
        )

    robots_policy = RobotsPolicy.from_content(
        "User-Agent: *\nDisallow: /secret\n",
        base_url="https://www.te.eg",
    )

    config = CrawlConfig(
        start_urls=["https://www.te.eg/"],
        allowed_hosts=["www.te.eg", "te.eg"],
        allowed_path_prefixes=["/"],
        page_cap=10,
        delay_seconds=0.0,
    )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with PoliteClient(config, contact="test@example.com", client=mock_client) as client:
        orchestrator = CrawlOrchestrator(config, client, robots_policy=robots_policy)
        summary = await orchestrator.run()

        assert summary.blocked_robots == 1
        assert summary.fetched == 2
