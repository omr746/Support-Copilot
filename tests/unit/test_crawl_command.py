"""Tests for crawl CLI command, dry-run, resume, metrics, structured logging, and idempotence (Step 2.0c)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from copilot.rag.crawler.cli import run_crawler_cli
from copilot.rag.crawler.client import CrawlResponse, PoliteClient
from copilot.rag.crawler.config import CrawlConfig
from copilot.rag.crawler.metrics import log_page_visit, trace_crawl_run
from copilot.rag.crawler.models import CrawlMetrics, CrawlSummary
from copilot.rag.crawler.orchestrator import CrawlOrchestrator
from copilot.rag.crawler.robots import RobotsPolicy
from copilot.rag.crawler.snapshot import SnapshotStore
from copilot.rag.crawler.url_utils import hash_path


@pytest.fixture
def base_config() -> CrawlConfig:
    return CrawlConfig(
        name="test-crawler",
        user_agent_prefix="SupportCopilotTest",
        start_urls=["https://www.te.eg/customer-support", "https://www.te.eg/faq"],
        allowed_hosts=["www.te.eg"],
        allowed_path_prefixes=["/"],
        denied_patterns=[r"/login", r"/cart"],
        languages=["ar", "en"],
        page_cap=10,
        delay_seconds=0.0,
        timeout_seconds=5.0,
        max_retries=1,
        circuit_breaker_limit=3,
    )


def test_structured_log_never_contains_query_string() -> None:
    """Structured log line computes path hash without ever logging or leaking query strings."""
    url_with_query = "https://www.te.eg/customer-service?secret_token=12345&user_id=omar#frag"
    host, path_hash = hash_path(url_with_query)

    assert host == "www.te.eg"
    # Ensure query string parameters and fragment are NOT part of the hash
    _host_clean, path_hash_clean = hash_path("https://www.te.eg/customer-service")
    assert path_hash == path_hash_clean
    assert "secret_token" not in path_hash
    assert "12345" not in path_hash

    # Test logger invocation
    mock_log = MagicMock()
    log_page_visit(
        mock_log,
        url=url_with_query,
        outcome="fetched",
        status_code=200,
        duration_ms=45.2,
    )
    mock_log.assert_called_once()
    _, kwargs = mock_log.call_args
    assert kwargs["host"] == "www.te.eg"
    assert kwargs["path_hash"] == path_hash
    assert kwargs["outcome"] == "fetched"
    assert kwargs["status_code"] == 200
    assert kwargs["duration_ms"] == 45.2
    # Absolute privacy: no query parameter or raw URL in structured log fields
    for v in kwargs.values():
        assert "secret_token" not in str(v)
        assert "12345" not in str(v)


@pytest.mark.asyncio
async def test_dry_run_makes_no_network_calls_and_writes_nothing(
    base_config: CrawlConfig, tmp_path: Path
) -> None:
    """A dry run makes no network calls and writes nothing to disk."""
    client_mock = MagicMock(spec=PoliteClient)
    # If client.fetch is called, fail the test
    client_mock.fetch = AsyncMock(side_effect=AssertionError("Network call made in dry-run!"))

    store = SnapshotStore(base_dir=tmp_path)
    orchestrator = CrawlOrchestrator(
        config=base_config,
        client=client_mock,
        robots_policy=None,
        snapshot_store=store,
    )

    summary = await orchestrator.run(max_pages=5, dry_run=True)

    # Proves no network calls occurred
    client_mock.fetch.assert_not_called()
    assert summary.dry_run is True
    assert summary.total_visited == 2
    assert summary.dry_run_urls == [
        "https://www.te.eg/customer-support",
        "https://www.te.eg/faq",
    ]

    # Proves nothing was written to disk
    assert not (tmp_path / "snapshots").exists()
    assert not (tmp_path / "private").exists()


def test_cli_dry_run_command_exits_zero_without_contact(
    base_config: CrawlConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI execution with --dry-run prints URLs and exits 0 even if CRAWL_CONTACT is not set."""
    cfg_file = tmp_path / "test_crawl.yaml"
    cfg_file.write_text(
        """
name: cli-dry-run
user_agent_prefix: TestBot
start_urls:
  - https://www.te.eg/support
allowed_hosts:
  - www.te.eg
allowed_path_prefixes:
  - /
page_cap: 5
delay_seconds: 0.0
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("CRAWL_CONTACT", raising=False)

    exit_code = run_crawler_cli(
        config_path=str(cfg_file),
        dry_run=True,
    )
    assert exit_code == 0


@pytest.mark.asyncio
async def test_resume_continues_without_duplicates(
    base_config: CrawlConfig, tmp_path: Path
) -> None:
    """A resume continues after an interruption without duplicate fetches."""
    state_file = tmp_path / "crawl_state.json"
    # Pre-populate state: /customer-support already visited
    state_file.write_text(
        json.dumps(
            {
                "visited": ["https://www.te.eg/customer-support"],
                "queued": ["https://www.te.eg/faq"],
            }
        ),
        encoding="utf-8",
    )

    client_mock = MagicMock(spec=PoliteClient)
    client_mock.user_agent = "SupportCopilotBot/1.0"
    client_mock.fetch = AsyncMock(
        return_value=CrawlResponse(
            url="https://www.te.eg/faq",
            status_code=200,
            text="<html><body><h1>FAQ</h1><p>Frequently asked questions.</p></body></html>",
        )
    )

    store = SnapshotStore(base_dir=tmp_path)
    orchestrator = CrawlOrchestrator(
        config=base_config,
        client=client_mock,
        robots_policy=RobotsPolicy.allow_all(),
        snapshot_store=store,
        state_file=state_file,
    )

    summary = await orchestrator.run(max_pages=5, resume=True)

    # Proves /customer-support was skipped and ONLY /faq was fetched
    assert client_mock.fetch.call_count == 1
    call_url = client_mock.fetch.call_args[0][0]
    assert call_url == "https://www.te.eg/faq"
    assert summary.total_visited == 1
    assert summary.fetched == 1


@pytest.mark.asyncio
async def test_resume_preserves_past_results_and_builds_complete_snapshot(
    tmp_path: Path,
) -> None:
    """When a crawl is resumed, past CrawlResult records are restored and included in the final snapshot."""
    config = CrawlConfig(
        name="test-resume-full",
        user_agent_prefix="Bot",
        start_urls=["https://www.te.eg/page-1", "https://www.te.eg/page-2"],
        allowed_hosts=["www.te.eg"],
        allowed_path_prefixes=["/"],
        page_cap=10,
        delay_seconds=0.0,
    )
    state_file = tmp_path / "resume_state.json"
    store = SnapshotStore(base_dir=tmp_path)

    # Step 1: Run 1 crawls only 1 page due to max_pages=1
    client_mock_1 = MagicMock(spec=PoliteClient)
    client_mock_1.user_agent = "Bot/1.0"
    client_mock_1.fetch = AsyncMock(
        return_value=CrawlResponse(
            url="https://www.te.eg/page-1",
            status_code=200,
            text="<html><head><title>P1</title></head><body><h1>Page 1 Content</h1></body></html>",
        )
    )

    orch_1 = CrawlOrchestrator(
        config=config,
        client=client_mock_1,
        robots_policy=RobotsPolicy.allow_all(),
        snapshot_store=store,
        state_file=state_file,
    )
    summary_1 = await orch_1.run(max_pages=1)
    assert summary_1.total_visited == 1
    assert len(summary_1.results) == 1
    assert summary_1.results[0].url == "https://www.te.eg/page-1"

    # Crucial: State file is NOT deleted because page cap was hit with items remaining in queue
    assert state_file.exists()

    # Step 2: Run 2 resumes and crawls page-2
    client_mock_2 = MagicMock(spec=PoliteClient)
    client_mock_2.user_agent = "Bot/1.0"
    client_mock_2.fetch = AsyncMock(
        return_value=CrawlResponse(
            url="https://www.te.eg/page-2",
            status_code=200,
            text="<html><head><title>P2</title></head><body><h1>Page 2 Content</h1></body></html>",
        )
    )

    orch_2 = CrawlOrchestrator(
        config=config,
        client=client_mock_2,
        robots_policy=RobotsPolicy.allow_all(),
        snapshot_store=store,
        state_file=state_file,
    )
    summary_2 = await orch_2.run(max_pages=10, resume=True)

    # Both page-1 and page-2 results are present in summary_2.results
    result_urls = [r.url for r in summary_2.results]
    assert "https://www.te.eg/page-1" in result_urls
    assert "https://www.te.eg/page-2" in result_urls

    # Both pages are present in the final snapshot
    final_snap = store.load_snapshot(summary_2.snapshot_id)
    assert "https://www.te.eg/page-1" in final_snap.pages
    assert "https://www.te.eg/page-2" in final_snap.pages
    assert final_snap.pages["https://www.te.eg/page-1"].cleaned_text != ""
    assert final_snap.pages["https://www.te.eg/page-2"].cleaned_text != ""

    # Crawl completed fully without cap stop -> state file is now cleanly cleaned up
    assert not state_file.exists()


@pytest.mark.asyncio
async def test_corrupted_state_file_handled_gracefully(
    base_config: CrawlConfig, tmp_path: Path
) -> None:
    """A corrupted or invalid state file does not crash the orchestrator."""
    state_file = tmp_path / "corrupted.json"
    state_file.write_text('{"visited": "not_a_list", "queued": 12345}', encoding="utf-8")

    client_mock = MagicMock(spec=PoliteClient)
    client_mock.user_agent = "Bot/1.0"
    client_mock.fetch = AsyncMock(
        return_value=CrawlResponse(
            url="https://www.te.eg/customer-support", status_code=200, text="<html/>"
        )
    )

    orch = CrawlOrchestrator(
        config=base_config,
        client=client_mock,
        robots_policy=RobotsPolicy.allow_all(),
        snapshot_store=None,
        state_file=state_file,
    )
    summary = await orch.run(max_pages=1, resume=True)
    assert summary.total_visited == 1


@pytest.mark.asyncio
async def test_summary_counts_match_fake_server(base_config: CrawlConfig, tmp_path: Path) -> None:
    """Summary counts match a fake server with known page outcomes."""
    # 3 start URLs: normal page, JS-only shell, and 404 page
    config = CrawlConfig(
        name="test-summary",
        user_agent_prefix="Bot",
        start_urls=[
            "https://www.te.eg/good-page",
            "https://www.te.eg/spa-shell",
            "https://www.te.eg/missing",
            "https://www.te.eg/blocked",
        ],
        allowed_hosts=["www.te.eg"],
        allowed_path_prefixes=["/"],
        page_cap=10,
        delay_seconds=0.0,
    )

    async def fake_fetch(url: str, **kwargs: object) -> CrawlResponse:
        if url == "https://www.te.eg/good-page":
            return CrawlResponse(
                url=url,
                status_code=200,
                text="<html><body><h1>Good</h1><p>Real support text.</p></body></html>",
            )
        elif url == "https://www.te.eg/spa-shell":
            return CrawlResponse(
                url=url,
                status_code=200,
                text='<html><body><div id="root"></div><script src="/app.js"></script></body></html>',
            )
        elif url == "https://www.te.eg/missing":
            return CrawlResponse(url=url, status_code=404, text="Not Found")
        raise RuntimeError(f"Unexpected URL: {url}")

    client_mock = MagicMock(spec=PoliteClient)
    client_mock.user_agent = "Bot/1.0"
    client_mock.fetch = AsyncMock(side_effect=fake_fetch)

    # Custom robots policy: disallows /blocked
    robots_mock = MagicMock(spec=RobotsPolicy)
    robots_mock.can_fetch.side_effect = lambda ua, url: "/blocked" not in url

    store = SnapshotStore(base_dir=tmp_path)
    orchestrator = CrawlOrchestrator(
        config=config,
        client=client_mock,
        robots_policy=robots_mock,
        snapshot_store=store,
    )

    summary = await orchestrator.run()

    # Exact count matching
    assert summary.total_visited == 4
    assert summary.fetched == 1  # /good-page
    assert summary.needs_js == 1  # /spa-shell
    assert summary.not_found == 1  # /missing (404)
    assert summary.failed == 0
    assert summary.blocked_robots == 1  # /blocked
    assert summary.unchanged == 0
    assert summary.changed == 0


def test_failure_rate_threshold_exits_non_zero(
    base_config: CrawlConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit code is non-zero when failure rate exceeds the failure threshold."""
    monkeypatch.setenv("CRAWL_CONTACT", "test@domain.org")

    cfg_file = tmp_path / "crawl.yaml"
    cfg_file.write_text(
        """
name: test-fail
user_agent_prefix: Test
start_urls:
  - https://www.te.eg/page1
allowed_hosts:
  - www.te.eg
allowed_path_prefixes:
  - /
page_cap: 10
delay_seconds: 0.0
""",
        encoding="utf-8",
    )

    # Mock _execute_crawl returning high failure rate
    with patch("copilot.rag.crawler.cli.CrawlOrchestrator") as mock_orch_cls:
        mock_orch = MagicMock()
        mock_summary = CrawlSummary(
            total_visited=2,
            fetched=1,
            failed=1,  # 50% failure rate
        )
        mock_orch.run = AsyncMock(return_value=mock_summary)
        mock_orch_cls.return_value = mock_orch

        # With failure_threshold=0.20, 50% failure must return non-zero exit code (1)
        exit_code = run_crawler_cli(
            config_path=str(cfg_file),
            failure_threshold=0.20,
            data_dir=str(tmp_path),
        )
        assert exit_code == 1

        # With failure_threshold=0.60, 50% failure is acceptable and exits 0
        exit_code_ok = run_crawler_cli(
            config_path=str(cfg_file),
            failure_threshold=0.60,
            data_dir=str(tmp_path),
        )
        assert exit_code_ok == 0


@pytest.mark.asyncio
async def test_run_twice_unchanged_reports_unchanged_and_creates_no_new_page_versions(
    tmp_path: Path,
) -> None:
    """Running the crawl twice against an unchanged fake server reports everything as unchanged and creates no new page versions."""
    config = CrawlConfig(
        name="test-idempotence",
        user_agent_prefix="Bot",
        start_urls=["https://www.te.eg/page-a", "https://www.te.eg/page-b"],
        allowed_hosts=["www.te.eg"],
        allowed_path_prefixes=["/"],
        page_cap=10,
        delay_seconds=0.0,
    )

    html_a = "<html><head><title>Page A</title></head><body><h1>Service A</h1><p>Price: 100 EGP</p></body></html>"
    html_b = "<html><head><title>Page B</title></head><body><h1>Service B</h1><p>Troubleshooting steps.</p></body></html>"

    # --- Run 1: Initial Crawl ---
    client_mock_1 = MagicMock(spec=PoliteClient)
    client_mock_1.user_agent = "Bot/1.0"
    client_mock_1.fetch = AsyncMock(
        side_effect=lambda url, **kw: CrawlResponse(
            url=url,
            status_code=200,
            text=html_a if "page-a" in url else html_b,
            etag=f"etag-{'a' if 'page-a' in url else 'b'}",
        )
    )

    store = SnapshotStore(base_dir=tmp_path)
    orch_1 = CrawlOrchestrator(
        config=config,
        client=client_mock_1,
        robots_policy=RobotsPolicy.allow_all(),
        snapshot_store=store,
    )

    summary_1 = await orch_1.run()
    assert summary_1.fetched == 2
    assert summary_1.unchanged == 0
    assert summary_1.changed == 0
    assert summary_1.new_page_versions_count == 2
    snap_id_1 = summary_1.snapshot_id
    assert snap_id_1 != ""

    # Snapshot 1 was created; activation is decoupled and performed via activate_snapshot
    assert store.get_active_snapshot_id() is None
    store.activate_snapshot(snap_id_1)
    assert store.get_active_snapshot_id() == snap_id_1
    snap_1 = store.load_snapshot(snap_id_1)
    page_a_v1 = snap_1.pages["https://www.te.eg/page-a"]
    page_b_v1 = snap_1.pages["https://www.te.eg/page-b"]

    # --- Run 2: Second Crawl against unchanged server (returns 304 via conditional headers) ---
    client_mock_2 = MagicMock(spec=PoliteClient)
    client_mock_2.user_agent = "Bot/1.0"
    client_mock_2.fetch = AsyncMock(
        side_effect=lambda url, etag=None, **kw: CrawlResponse(
            url=url,
            status_code=304,
            is_unchanged=True,
            etag=etag,
        )
    )

    orch_2 = CrawlOrchestrator(
        config=config,
        client=client_mock_2,
        robots_policy=RobotsPolicy.allow_all(),
        snapshot_store=store,
    )

    summary_2 = await orch_2.run()
    # Reports everything as unchanged
    assert summary_2.unchanged == 2
    assert summary_2.fetched == 0
    assert summary_2.changed == 0
    assert summary_2.new_page_versions_count == 0

    # Proves no new page versions were created: exact same versions reused
    snap_2 = store.load_snapshot(summary_2.snapshot_id)
    assert snap_2.pages["https://www.te.eg/page-a"].content_hash == page_a_v1.content_hash
    assert snap_2.pages["https://www.te.eg/page-b"].content_hash == page_b_v1.content_hash


def test_crawl_span_and_metrics_emitted() -> None:
    """Crawl metrics model and trace_crawl_run span context work seamlessly."""
    metrics = CrawlMetrics(
        pages_fetched=10,
        pages_unchanged=5,
        pages_changed=2,
        pages_removed=1,
        pages_failed=0,
        pages_blocked_robots=0,
        pages_needs_js=1,
        crawl_duration_seconds=3.45,
        snapshot_age_seconds=86400.0,
    )
    d = metrics.to_dict()
    assert d["pages_fetched"] == 10
    assert d["pages_unchanged"] == 5
    assert d["pages_changed"] == 2
    assert d["pages_needs_js"] == 1
    assert d["crawl_duration_seconds"] == 3.45

    # Test trace_crawl_run context manager
    with trace_crawl_run("copilot.rag.crawl", {"test.attr": 123}) as span:
        assert span is not None
        span.set_attribute("runtime.metric", 42)


def test_opentelemetry_metrics_instruments_collection() -> None:
    """OpenTelemetry metrics instruments (Counters, Histograms) record page visits and crawl durations."""
    from copilot.rag.crawler.metrics import (
        record_crawl_summary_metrics,
        record_page_visit_metric,
    )

    # Must execute cleanly without error
    record_page_visit_metric(
        outcome="fetched",
        status_code=200,
        duration_ms=125.4,
    )
    record_page_visit_metric(
        outcome="failed",
        status_code=500,
        duration_ms=50.0,
    )
    record_crawl_summary_metrics(duration_seconds=12.5, status="completed")
