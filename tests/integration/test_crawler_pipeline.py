"""Integration tests for the complete crawler, cleaning, snapshotting, and resume pipeline.

Exercises the full subsystem end-to-end:
- PoliteClient with concurrency=1, polite delays, and conditional requests.
- RobotsPolicy fetching and obeying robots.txt rules.
- CrawlOrchestrator BFS frontier queue, link discovery, and error handling.
- Cleaner with Arabic text retention, HTML boilerplate removal, and content hashing.
- SnapshotStore with atomic writes, immutability, private/public separation, and rollback.
- Crawl resume preserving previous results across interruptions with no duplicate fetches.
- Network guard ensuring zero accidental external network access.
- Real socket HTTP server verifying redirects, gzip compression, and timeout handling.
"""

from __future__ import annotations

import gzip
import json
import socket
import threading
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from copilot.rag.crawler.cleaner import clean_html
from copilot.rag.crawler.client import PoliteClient
from copilot.rag.crawler.config import CrawlConfig
from copilot.rag.crawler.orchestrator import CrawlOrchestrator
from copilot.rag.crawler.robots import RobotsPolicy
from copilot.rag.crawler.snapshot import SnapshotStore
from tests.fakes.fake_sleeper import FakeSleeper


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard against accidental external network access during automated test execution.

    Permits only loopback socket connections (127.0.0.1, localhost, ::1).
    Any attempt to connect to external hosts raises RuntimeError immediately.
    """
    orig_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        allowed_hosts = {"127.0.0.1", "localhost", "::1"}
        if host not in allowed_hosts:
            raise RuntimeError(
                f"ACCIDENTAL EXTERNAL NETWORK ACCESS BLOCKED: Attempted to connect to '{host}'. "
                "Integration tests must run offline or against local test servers only."
            )
        return orig_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


class SimulatedSiteHandler:
    """Stateful simulated web server for Telecom Egypt customer support."""

    def __init__(self) -> None:
        self.internet_price: str = "120"
        self.internet_etag: str = 'W/"internet-v1"'
        self.mobile_etag: str = 'W/"mobile-v1"'
        self.requests_log: list[tuple[str, str, dict[str, str]]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        path = request.url.path
        headers = {k.lower(): v for k, v in request.headers.items()}
        self.requests_log.append((request.method, url_str, headers))

        # 1. robots.txt (Disallows internal notes and login)
        if path == "/robots.txt":
            body = (
                "User-agent: *\n"
                "Disallow: /customer-service/internal-notes\n"
                "Disallow: /login\n"
                "Disallow: /admin\n"
                "Crawl-delay: 0\n"
            )
            return httpx.Response(
                200,
                text=body,
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )

        # 2. Main customer service hub (Arabic)
        if path == "/customer-service":
            body = """
            <!DOCTYPE html>
            <html lang="ar">
            <head>
                <meta charset="utf-8">
                <title>المصرية للاتصالات - خدمة العملاء والدعم الفني</title>
                <link rel="canonical" href="https://www.te.eg/customer-service" />
            </head>
            <body>
                <header><nav><a href="/home">الرئيسية</a></nav></header>
                <main>
                    <h1>خدمة العملاء والدعم الفني</h1>
                    <p>مرحباً بك في مركز المساعدة الرسمي للمصرية للاتصالات WE.</p>
                    <ul>
                        <li><a href="/customer-service/internet">باقات الإنترنت المنزلي الدعم والأسعار</a></li>
                        <li><a href="/customer-service/mobile">خدمات وباقات الموبايل والشرائح</a></li>
                        <li><a href="/customer-service/app-portal">البوابة التفاعلية</a></li>
                        <li><a href="/customer-service/archived-faq">الأسئلة القديمة</a></li>
                        <li><a href="/customer-service/internal-notes">ملاحظات داخلية للموظفين</a></li>
                        <li><a href="/login">تسجيل الدخول للحساب الشخصي</a></li>
                    </ul>
                </main>
                <footer>جميع الحقوق محفوظة 2026</footer>
            </body>
            </html>
            """
            return httpx.Response(
                200,
                text=body,
                headers={
                    "Content-Type": "text/html; charset=utf-8",
                    "ETag": 'W/"hub-v1"',
                },
            )

        # 3. Internet Support & Pricing Page (with conditional request support)
        if path == "/customer-service/internet":
            if_none_match = headers.get("if-none-match")
            if if_none_match and if_none_match == self.internet_etag:
                return httpx.Response(304, headers={"ETag": self.internet_etag})

            body = f"""
            <!DOCTYPE html>
            <html lang="ar">
            <head>
                <meta charset="utf-8">
                <title>باقات الإنترنت المنزلي - الدعم الفني WE</title>
                <link rel="canonical" href="https://www.te.eg/customer-service/internet" />
            </head>
            <body>
                <main>
                    <h1>باقات الإنترنت المنزلي فائق السرعة</h1>
                    <p>تفاصيل الباقات وسرعات خطوط VDSL المنزلية:</p>
                    <table>
                        <tr><th>الباقة</th><th>السرعة</th><th>السعر الشهري</th></tr>
                        <tr><td>سوبر 140 جيجابايت</td><td>حتى 30 ميجابت/ث</td><td>{self.internet_price} جنيه</td></tr>
                        <tr><td>ميجا 250 جيجابايت</td><td>حتى 70 ميجابت/ث</td><td>220 جنيه</td></tr>
                    </table>
                </main>
            </body>
            </html>
            """
            return httpx.Response(
                200,
                text=body,
                headers={
                    "Content-Type": "text/html; charset=utf-8",
                    "ETag": self.internet_etag,
                    "Last-Modified": "Wed, 12 Nov 2026 12:00:00 GMT",
                },
            )

        # 4. Mobile Services Page (with conditional request support)
        if path == "/customer-service/mobile":
            if_none_match = headers.get("if-none-match")
            if if_none_match and if_none_match == self.mobile_etag:
                return httpx.Response(304, headers={"ETag": self.mobile_etag})

            body = """
            <!DOCTYPE html>
            <html lang="ar">
            <head>
                <meta charset="utf-8">
                <title>باقات الموبايل والإنترنت - الدعم الفني</title>
                <link rel="canonical" href="https://www.te.eg/customer-service/mobile" />
            </head>
            <body>
                <main>
                    <h1>خطوط وباقات الموبايل</h1>
                    <p>أنظمة الدفع المسبق والفاتورة الشهرية للشرائح.</p>
                </main>
            </body>
            </html>
            """
            return httpx.Response(
                200,
                text=body,
                headers={
                    "Content-Type": "text/html; charset=utf-8",
                    "ETag": self.mobile_etag,
                    "Last-Modified": "Wed, 12 Nov 2026 12:00:00 GMT",
                },
            )

        # 5. Interactive SPA App Shell (needs_js=True)
        if path == "/customer-service/app-portal":
            body = """
            <!DOCTYPE html>
            <html>
            <head><title>App Portal</title></head>
            <body>
                <noscript>You need to enable JavaScript to run this app.</noscript>
                <div id="root"></div>
                <script src="/static/app.bundle.js"></script>
            </body>
            </html>
            """
            return httpx.Response(
                200,
                text=body,
                headers={"Content-Type": "text/html; charset=utf-8"},
            )

        # 6. Archived FAQ (404 Not Found)
        if path == "/customer-service/archived-faq":
            return httpx.Response(
                404,
                text="<html><body><h1>404 Not Found</h1></body></html>",
                headers={"Content-Type": "text/html; charset=utf-8"},
            )

        # 7. Internal Notes (Allowed by prefix, disallowed strictly by robots.txt)
        if path == "/customer-service/internal-notes":
            return httpx.Response(
                200,
                text="<html><body><h1>Internal Agent Notes - Secret</h1></body></html>",
                headers={"Content-Type": "text/html; charset=utf-8"},
            )

        # 8. Login page (Denied by frontier prefix/pattern)
        if path == "/login":
            return httpx.Response(
                200,
                text="<html><body><h1>Login Page</h1></body></html>",
                headers={"Content-Type": "text/html; charset=utf-8"},
            )

        return httpx.Response(404, text="Not Found")


class _RealLocalServerHandler(BaseHTTPRequestHandler):
    """Local HTTP request handler testing real socket behavior (redirects, gzip, timeouts)."""

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Quiet stdout logging in tests

    def do_GET(self) -> None:
        if self.path == "/arabic-gzip":
            body = (
                "<!DOCTYPE html><html lang='ar'><head><meta charset='utf-8'>"
                "<title>دعم فني حقيقي</title></head><body><main>"
                "<h1>خدمات الدعم الفني المباشر</h1>"
                "<p>تم استلام البيانات عبر مقبس شبكة حقيقي بنجاح 100%.</p>"
                "</main></body></html>"
            ).encode("utf-8")
            compressed = gzip.compress(body)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(compressed)))
            self.end_headers()
            self.wfile.write(compressed)
            return

        if self.path == "/old-faq":
            self.send_response(302)
            self.send_header("Location", "/new-faq")
            self.end_headers()
            return

        if self.path == "/new-faq":
            body = (
                "<!DOCTYPE html><html lang='ar'><head><meta charset='utf-8'>"
                "<title>الصفحة المحولة</title></head><body><main>"
                "<h1>الأسئلة الشائعة بعد التحويل</h1>"
                "</main></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/slow-page":
            import time

            time.sleep(1.2)
            self.send_response(200)
            self.end_headers()
            return

        self.send_response(404)
        self.end_headers()


@pytest.fixture
def real_local_server() -> Generator[str, None, None]:
    """Start a real local TCP HTTP server bound to an ephemeral loopback port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RealLocalServerHandler)
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def site_handler() -> SimulatedSiteHandler:
    return SimulatedSiteHandler()


@pytest.fixture
def crawl_config() -> CrawlConfig:
    return CrawlConfig(
        name="integration-test-crawler",
        user_agent_prefix="SupportCopilotBot",
        start_urls=["https://www.te.eg/customer-service"],
        allowed_hosts=["www.te.eg"],
        allowed_path_prefixes=["/customer-service"],
        denied_patterns=[r"^/login", r"^/admin"],
        languages=["ar", "en"],
        page_cap=20,
        delay_seconds=0.0,
        timeout_seconds=5.0,
        max_retries=1,
    )


@pytest.fixture
def test_client(crawl_config: CrawlConfig, site_handler: SimulatedSiteHandler) -> PoliteClient:
    transport = httpx.MockTransport(site_handler.handle)
    return PoliteClient(
        crawl_config,
        contact="team@support-copilot.org",
        sleeper=FakeSleeper(),
        transport=transport,
    )


# ==============================================================================
# Integration Test 1: Full Crawl Pipeline with Exact Counts & Real Robots Check
# ==============================================================================


@pytest.mark.asyncio
async def test_full_crawl_pipeline_end_to_end(
    crawl_config: CrawlConfig,
    test_client: PoliteClient,
    site_handler: SimulatedSiteHandler,
    tmp_path: Path,
) -> None:
    """End-to-End integration test: crawl discovery, robots policy, cleaning, and atomic snapshot creation."""
    store = SnapshotStore(base_dir=tmp_path)
    robots_policy = await RobotsPolicy.fetch(
        test_client._client, "https://www.te.eg", test_client.user_agent
    )

    orchestrator = CrawlOrchestrator(
        config=crawl_config,
        client=test_client,
        robots_policy=robots_policy,
        snapshot_store=store,
    )

    summary = await orchestrator.run(clock_now="2026-11-13T10:00:00Z")

    # 1. Exact Metrics Assertions (Site is completely deterministic)
    # Total visited = 6:
    # 1. /customer-service (hub, 200) -> fetched
    # 2. /customer-service/internet (200) -> fetched
    # 3. /customer-service/mobile (200) -> fetched
    # 4. /customer-service/app-portal (200, SPA shell) -> needs_js
    # 5. /customer-service/internal-notes (inside prefix, disallowed by robots.txt) -> blocked_robots
    # 6. /customer-service/archived-faq (404) -> not_found
    assert summary.total_visited == 6
    assert summary.fetched == 3
    assert summary.needs_js == 1
    assert summary.blocked_robots == 1
    assert summary.not_found == 1
    assert summary.failed == 0
    assert summary.unchanged == 0
    assert summary.changed == 0
    assert summary.removed == 0
    assert (
        summary.new_page_versions_count == 3
    )  # Active pages in snapshot: hub, internet, mobile (app-portal has needs_js=True)

    # 2. Verify Robots.txt enforcement
    visited_paths = [req[1] for req in site_handler.requests_log]
    assert any("/robots.txt" in p for p in visited_paths)
    # /customer-service/internal-notes matches allowed_path_prefixes, so ONLY robots.txt stopped it!
    assert not any("/customer-service/internal-notes" in p for p in visited_paths)
    assert not any("/login" in p for p in visited_paths)

    # 3. Verify Public Snapshot Manifest (Strict Privacy Boundary)
    snap_id = summary.snapshot_id
    manifest_path = tmp_path / "snapshots" / snap_id / "manifest.jsonl"
    assert manifest_path.exists()

    manifest_lines = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(manifest_lines) == 4

    # Manifest must NEVER contain page text
    for entry in manifest_lines:
        assert "text" not in entry
        assert "cleaned_text" not in entry
        assert "raw_html" not in entry
        assert "canonical_url" in entry
        assert "content_hash" in entry
        assert "last_verified_at" in entry
        assert entry["last_verified_at"] == "2026-11-13T10:00:00Z"

    # 4. Verify Private Backup Store (Full Arabic Text Preserved)
    private_backup_path = tmp_path / "private" / "snapshots" / snap_id / "pages.jsonl"
    assert private_backup_path.exists()

    private_lines = [
        json.loads(line)
        for line in private_backup_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    internet_page = next(
        p
        for p in private_lines
        if p["canonical_url"] == "https://www.te.eg/customer-service/internet"
    )
    assert "باقات الإنترنت المنزلي" in internet_page["cleaned_text"]
    assert "120 جنيه" in internet_page["cleaned_text"]
    assert internet_page["language"] == "ar"

    # 5. Verify Decoupled Activation: snapshot is NOT automatically active
    assert store.get_active_snapshot_id() is None
    assert store.get_active_snapshot() is None


# ==============================================================================
# Integration Test 2: Politeness (Delay and Honest Contact User-Agent)
# ==============================================================================


@pytest.mark.asyncio
async def test_polite_delay_and_contact_user_agent(
    crawl_config: CrawlConfig,
    site_handler: SimulatedSiteHandler,
    tmp_path: Path,
) -> None:
    """Verify politeness guarantees: inter-request sleep delay and honest User-Agent with contact."""
    sleeper = FakeSleeper()
    transport = httpx.MockTransport(site_handler.handle)
    polite_config = CrawlConfig(
        name="polite-tester",
        user_agent_prefix="SupportCopilotBot",
        start_urls=["https://www.te.eg/customer-service"],
        allowed_hosts=["www.te.eg"],
        allowed_path_prefixes=["/customer-service"],
        denied_patterns=[r"^/login", r"^/admin"],
        page_cap=3,
        delay_seconds=0.75,
        timeout_seconds=5.0,
    )
    client = PoliteClient(
        polite_config,
        contact="crawler-ops@support-copilot.org",
        sleeper=sleeper,
        transport=transport,
    )
    robots_policy = await RobotsPolicy.fetch(client._client, "https://www.te.eg", client.user_agent)
    store = SnapshotStore(base_dir=tmp_path)
    orchestrator = CrawlOrchestrator(
        config=polite_config,
        client=client,
        robots_policy=robots_policy,
        snapshot_store=store,
    )

    summary = await orchestrator.run(max_pages=3)
    assert summary.total_visited == 3

    # Assert politeness delay: for N requests made through PoliteClient.fetch,
    # sleep was called N-1 times with exactly 0.75 seconds
    expected_requests_via_client = summary.total_attempted
    assert len(sleeper.sleeps) == expected_requests_via_client - 1
    for s in sleeper.sleeps:
        assert s == 0.75

    # Assert honest User-Agent containing contact address across all requests
    assert len(site_handler.requests_log) > 0
    expected_ua = polite_config.build_user_agent("crawler-ops@support-copilot.org")
    for _method, _url, headers in site_handler.requests_log:
        assert headers.get("user-agent") == expected_ua


# ==============================================================================
# Integration Test 3: Incremental Crawl Idempotence & 304 Reuse with Injected Clock
# ==============================================================================


@pytest.mark.asyncio
async def test_incremental_crawl_idempotence_and_304_reuse(
    crawl_config: CrawlConfig,
    test_client: PoliteClient,
    site_handler: SimulatedSiteHandler,
    tmp_path: Path,
) -> None:
    """End-to-End integration test: running a second crawl against an unchanged server reuses versions via 304."""
    store = SnapshotStore(base_dir=tmp_path)
    robots_policy = await RobotsPolicy.fetch(
        test_client._client, "https://www.te.eg", test_client.user_agent
    )

    # --- Run 1: Initial Crawl at T1 ---
    orch_1 = CrawlOrchestrator(
        config=crawl_config,
        client=test_client,
        robots_policy=robots_policy,
        snapshot_store=store,
    )
    summary_1 = await orch_1.run(clock_now="2026-11-13T10:00:00Z")
    snap_1_id = summary_1.snapshot_id
    assert summary_1.new_page_versions_count == 3

    # Activate snapshot 1 (manual promotion step)
    store.activate_snapshot(snap_1_id)
    assert store.get_active_snapshot_id() == snap_1_id
    snap_1 = store.load_snapshot(snap_1_id)

    # Reset request log count
    site_handler.requests_log.clear()

    # --- Run 2: Second Crawl against unchanged server at T2 ---
    orch_2 = CrawlOrchestrator(
        config=crawl_config,
        client=test_client,
        robots_policy=robots_policy,
        snapshot_store=store,
    )
    summary_2 = await orch_2.run(clock_now="2026-11-13T12:00:00Z")

    # Verify Idempotence and exact counts:
    # - total_visited == 6
    # - unchanged == 3 (hub reused via identical content hash; internet and mobile reused via HTTP 304)
    # - fetched == 0 (zero new pages downloaded)
    # - changed == 0
    # - needs_js == 1
    # - blocked_robots == 1
    # - not_found == 1
    # - failed == 0
    # - new_page_versions_count == 0
    assert summary_2.total_visited == 6
    assert summary_2.unchanged == 3
    assert summary_2.fetched == 0
    assert summary_2.changed == 0
    assert summary_2.needs_js == 1
    assert summary_2.blocked_robots == 1
    assert summary_2.not_found == 1
    assert summary_2.failed == 0
    assert summary_2.new_page_versions_count == 0

    # Verify that conditional headers were sent for articles with ETag
    internet_requests = [
        req for req in site_handler.requests_log if "customer-service/internet" in req[1]
    ]
    assert len(internet_requests) == 1
    assert internet_requests[0][2].get("if-none-match") == 'W/"internet-v1"'

    # Verify Injected Clock & last_verified_at
    snap_2 = store.load_snapshot(summary_2.snapshot_id)
    orig_mobile = snap_1.pages["https://www.te.eg/customer-service/mobile"]
    new_mobile = snap_2.pages["https://www.te.eg/customer-service/mobile"]

    # fetched_at must be preserved from Run 1 (never re-downloaded)
    assert orig_mobile.fetched_at == "2026-11-13T10:00:00Z"
    assert new_mobile.fetched_at == "2026-11-13T10:00:00Z"

    # last_verified_at is refreshed to Run 2 timestamp
    assert orig_mobile.last_verified_at == "2026-11-13T10:00:00Z"
    assert new_mobile.last_verified_at == "2026-11-13T12:00:00Z"
    assert new_mobile.last_verified_at != orig_mobile.last_verified_at


# ==============================================================================
# Integration Test 4: Content Change Detection & Incremental Versioning
# ==============================================================================


@pytest.mark.asyncio
async def test_content_change_detection_and_incremental_versioning(
    crawl_config: CrawlConfig,
    test_client: PoliteClient,
    site_handler: SimulatedSiteHandler,
    tmp_path: Path,
) -> None:
    """End-to-End integration test: modified pages create new versions, while unchanged pages are preserved."""
    store = SnapshotStore(base_dir=tmp_path)
    robots_policy = await RobotsPolicy.fetch(
        test_client._client, "https://www.te.eg", test_client.user_agent
    )

    # --- Run 1: Initial Crawl at T1 ---
    orch_1 = CrawlOrchestrator(
        config=crawl_config,
        client=test_client,
        robots_policy=robots_policy,
        snapshot_store=store,
    )
    summary_1 = await orch_1.run(clock_now="2026-11-13T10:00:00Z")
    store.activate_snapshot(summary_1.snapshot_id)
    snap_1 = store.load_snapshot(summary_1.snapshot_id)
    orig_hash = snap_1.pages["https://www.te.eg/customer-service/internet"].content_hash

    # --- Server Mutation: Price updated from 120 to 180 EGP ---
    site_handler.internet_price = "180"
    site_handler.internet_etag = 'W/"internet-v2"'

    # --- Run 2: Incremental Crawl after mutation at T2 ---
    orch_2 = CrawlOrchestrator(
        config=crawl_config,
        client=test_client,
        robots_policy=robots_policy,
        snapshot_store=store,
    )
    summary_2 = await orch_2.run(clock_now="2026-11-13T14:00:00Z")

    # Proves change was detected for internet page, while mobile page was unchanged (304)
    assert summary_2.total_visited == 6
    assert summary_2.changed == 1
    assert summary_2.unchanged == 2  # Hub and mobile
    assert summary_2.fetched == 0
    assert summary_2.needs_js == 1
    assert summary_2.blocked_robots == 1
    assert summary_2.not_found == 1
    assert summary_2.failed == 0
    assert summary_2.new_page_versions_count == 1

    snap_2 = store.load_snapshot(summary_2.snapshot_id)
    new_internet_page = snap_2.pages["https://www.te.eg/customer-service/internet"]
    assert new_internet_page.content_hash != orig_hash
    assert "180 جنيه" in new_internet_page.cleaned_text
    assert "120 جنيه" not in new_internet_page.cleaned_text
    assert new_internet_page.fetched_at == "2026-11-13T14:00:00Z"
    assert new_internet_page.last_verified_at == "2026-11-13T14:00:00Z"

    # Unchanged mobile page reused the exact existing PageVersion
    orig_mobile = snap_1.pages["https://www.te.eg/customer-service/mobile"]
    new_mobile = snap_2.pages["https://www.te.eg/customer-service/mobile"]
    assert new_mobile.content_hash == orig_mobile.content_hash
    assert new_mobile.fetched_at == "2026-11-13T10:00:00Z"
    assert new_mobile.last_verified_at == "2026-11-13T14:00:00Z"


# ==============================================================================
# Integration Test 5: Crawl Interruption, Atomic Resume & Baseline Equivalence
# ==============================================================================


@pytest.mark.asyncio
async def test_crawl_interruption_and_atomic_resume(
    crawl_config: CrawlConfig,
    test_client: PoliteClient,
    site_handler: SimulatedSiteHandler,
    tmp_path: Path,
) -> None:
    """End-to-End integration test: an interrupted crawl safely resumes with zero duplicate fetches."""
    store = SnapshotStore(base_dir=tmp_path)
    state_file = tmp_path / "crawl_resume_state.json"
    robots_policy = await RobotsPolicy.fetch(
        test_client._client, "https://www.te.eg", test_client.user_agent
    )
    site_handler.requests_log.clear()

    # --- Phase 1: Run with cap of 2 pages (Simulating early interruption) ---
    orch_1 = CrawlOrchestrator(
        config=crawl_config,
        client=test_client,
        robots_policy=robots_policy,
        snapshot_store=store,
        state_file=state_file,
    )
    summary_1 = await orch_1.run(max_pages=2)
    assert summary_1.total_visited == 2
    assert state_file.exists()

    phase1_requests = [req[1] for req in site_handler.requests_log]
    assert len(phase1_requests) == 2  # Hub and first child

    # --- Phase 2: Resume crawl to completion ---
    orch_2 = CrawlOrchestrator(
        config=crawl_config,
        client=test_client,
        robots_policy=robots_policy,
        snapshot_store=store,
        state_file=state_file,
    )
    reqs_before_resume = len(site_handler.requests_log)
    summary_2 = await orch_2.run(max_pages=20, resume=True)

    phase2_requests = [req[1] for req in site_handler.requests_log[reqs_before_resume:]]

    # PROVE NO DUPLICATE FETCHES: Phase 2 requests must have zero intersection with Phase 1 requests!
    assert set(phase1_requests).isdisjoint(set(phase2_requests))

    # All visited URLs accounted for
    resumed_urls = [r.url for r in summary_2.results]
    assert "https://www.te.eg/customer-service" in resumed_urls
    assert "https://www.te.eg/customer-service/internet" in resumed_urls
    assert "https://www.te.eg/customer-service/mobile" in resumed_urls
    assert summary_2.total_visited == 4  # 4 remaining pages visited during Phase 2
    assert (
        len(summary_2.results) == 5
    )  # Total cumulative results (5 fetched/attempted, 1 robots blocked)

    # Full completion cleans up the state file
    assert not state_file.exists()

    # --- Baseline Equivalence: Prove resumed snapshot equals uninterrupted crawl ---
    baseline_store = SnapshotStore(base_dir=tmp_path / "baseline_store")
    baseline_orch = CrawlOrchestrator(
        config=crawl_config,
        client=test_client,
        robots_policy=robots_policy,
        snapshot_store=baseline_store,
    )
    baseline_summary = await baseline_orch.run()

    resumed_snap = store.load_snapshot(summary_2.snapshot_id)
    baseline_snap = baseline_store.load_snapshot(baseline_summary.snapshot_id)

    assert resumed_snap.pages.keys() == baseline_snap.pages.keys()
    for page_url in resumed_snap.pages:
        assert (
            resumed_snap.pages[page_url].content_hash == baseline_snap.pages[page_url].content_hash
        )
        assert (
            resumed_snap.pages[page_url].cleaned_text == baseline_snap.pages[page_url].cleaned_text
        )
        assert resumed_snap.pages[page_url].title == baseline_snap.pages[page_url].title
        assert resumed_snap.pages[page_url].needs_js == baseline_snap.pages[page_url].needs_js


# ==============================================================================
# Integration Tests 6, 7, 8: Real Socket Server (Gzip, Redirect, Timeout)
# ==============================================================================


@pytest.mark.asyncio
async def test_real_socket_arabic_bytes_and_gzip_compression(
    real_local_server: str,
) -> None:
    """Verify real socket behavior: Gzip decompression, UTF-8 Arabic bytes, and clean_html over TCP."""
    config = CrawlConfig(
        name="real-socket-tester",
        start_urls=[f"{real_local_server}/arabic-gzip"],
        allowed_hosts=["127.0.0.1", "localhost"],
        allowed_path_prefixes=["/"],
        delay_seconds=0.0,
        timeout_seconds=5.0,
    )
    async with PoliteClient(config, contact="team@local-test.org") as client:
        resp = await client.fetch(f"{real_local_server}/arabic-gzip")
        assert resp.status_code == 200
        assert "خدمات الدعم الفني المباشر" in resp.text
        assert "مقبس شبكة حقيقي بنجاح 100%" in resp.text

        # Ensure cleaner handles real decompressed socket stream
        cleaned = clean_html(resp.text, f"{real_local_server}/arabic-gzip", config=config)
        assert cleaned.language == "ar"
        assert "خدمات الدعم الفني المباشر" in cleaned.cleaned_text
        assert len(cleaned.content_hash) == 64


@pytest.mark.asyncio
async def test_real_socket_http_redirect_handling(
    real_local_server: str,
) -> None:
    """Verify real socket HTTP redirect: PoliteClient automatically follows 302 redirects across TCP."""
    config = CrawlConfig(
        name="redirect-tester",
        start_urls=[f"{real_local_server}/old-faq"],
        allowed_hosts=["127.0.0.1", "localhost"],
        allowed_path_prefixes=["/"],
        delay_seconds=0.0,
        timeout_seconds=5.0,
    )
    async with PoliteClient(config, contact="team@local-test.org") as client:
        resp = await client.fetch(f"{real_local_server}/old-faq")
        assert resp.status_code == 200
        assert "الأسئلة الشائعة بعد التحويل" in resp.text


@pytest.mark.asyncio
async def test_real_socket_timeout_handling(
    real_local_server: str,
) -> None:
    """Verify real socket timeout: slow socket responses trigger TimeoutException within timeout_seconds."""
    config = CrawlConfig(
        name="timeout-tester",
        start_urls=[f"{real_local_server}/slow-page"],
        allowed_hosts=["127.0.0.1", "localhost"],
        allowed_path_prefixes=["/"],
        delay_seconds=0.0,
        timeout_seconds=0.3,  # Shorter than server's 1.2s sleep
        max_retries=0,
    )
    async with PoliteClient(config, contact="team@local-test.org") as client:
        with pytest.raises(httpx.TimeoutException):
            await client.fetch(f"{real_local_server}/slow-page")
