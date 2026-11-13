"""Command line entrypoint for the polite web crawler."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from copilot.observability.logging import configure_logging
from copilot.rag.crawler.client import PoliteClient
from copilot.rag.crawler.config import CrawlConfig
from copilot.rag.crawler.errors import MissingContactError
from copilot.rag.crawler.orchestrator import CrawlOrchestrator
from copilot.rag.crawler.robots import RobotsPolicy
from copilot.rag.crawler.snapshot import SnapshotStore
from copilot.rag.crawler.url_utils import is_allowed_url, normalize_url

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="copilot.rag.crawl",
        description="Polite crawler CLI and snapshot generator for Support Copilot.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="configs/crawl.yaml",
        help="Path to crawler YAML configuration file (default: configs/crawl.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List URLs that would be fetched without making network calls or writing data",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an interrupted crawl without duplicate fetches",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate configuration, contact, and URLs without starting crawl",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Override page cap limit for this crawl run",
    )
    parser.add_argument(
        "--failure-threshold",
        type=float,
        default=0.20,
        help="Maximum acceptable failure rate (0.0 to 1.0) before exiting with non-zero code (default: 0.20)",
    )
    parser.add_argument(
        "--snapshot-id",
        type=str,
        default=None,
        help="Explicit snapshot identifier (default: auto-generated date and hash)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data",
        help="Base directory for snapshots and private storage (default: data)",
    )
    return parser.parse_args(argv)


def _load_contact() -> str | None:
    contact = os.getenv("CRAWL_CONTACT")
    if contact and contact.strip():
        return contact.strip()

    env_path = Path(".env")
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                trimmed = line.strip()
                if trimmed.startswith("CRAWL_CONTACT="):
                    parsed = trimmed.split("=", 1)[1].strip().strip('"').strip("'")
                    if parsed:
                        return parsed
        except Exception:
            pass
    return None


async def _execute_crawl(
    config: CrawlConfig,
    contact: str,
    max_pages: int | None = None,
    dry_run: bool = False,
    resume: bool = False,
    snapshot_id: str | None = None,
    data_dir: str = "data",
    failure_threshold: float = 0.20,
) -> int:
    """Execute crawl using PoliteClient, RobotsPolicy, and CrawlOrchestrator."""
    snapshot_store = None if dry_run else SnapshotStore(base_dir=data_dir)
    state_file = Path(data_dir) / "snapshots" / ".crawl_state.json"

    if dry_run:
        # Dry run makes NO network calls
        orchestrator = CrawlOrchestrator(
            config,
            client=None,  # type: ignore[arg-type]
            robots_policy=None,
            snapshot_store=None,
        )
        cap = max_pages or config.page_cap
        summary = await orchestrator.run(max_pages=cap, dry_run=True)

        sys.stdout.write("\n=== Crawl Dry-Run Report ===\n")
        sys.stdout.write(f"User-Agent:       {config.user_agent_prefix}\n")
        sys.stdout.write(f"Page Cap:         {cap}\n")
        sys.stdout.write(f"Discovered URLs:  {len(summary.dry_run_urls)}\n")
        for idx, url in enumerate(summary.dry_run_urls, 1):
            sys.stdout.write(f"  {idx:2d}. {url}\n")
        sys.stdout.write("============================\n\n")
        return 0

    async with PoliteClient(config, contact=contact) as client:
        base_url = config.start_urls[0] if config.start_urls else "https://www.te.eg"
        robots_policy = None
        try:
            sys.stdout.write(f"[*] Fetching robots.txt from {base_url}...\n")
            robots_policy = await RobotsPolicy.fetch(client._client, base_url, client.user_agent)
            delay = robots_policy.crawl_delay(client.user_agent)
            if delay is not None:
                client.update_delay(delay)
                sys.stdout.write(f"[*] Applied Crawl-delay from robots.txt: {delay:.1f}s\n")
        except Exception as err:
            sys.stderr.write(f"ERROR: Cannot fetch robots.txt: {err}\n")
            return 1

        orchestrator = CrawlOrchestrator(
            config,
            client,
            robots_policy=robots_policy,
            snapshot_store=snapshot_store,
            state_file=state_file,
        )
        cap = max_pages or config.page_cap
        sys.stdout.write(
            f"[*] Starting crawl workflow (start_urls: {len(config.start_urls)}, page_cap: {cap}, resume: {resume})...\n"
        )

        summary = await orchestrator.run(
            max_pages=cap,
            dry_run=False,
            resume=resume,
            snapshot_id=snapshot_id,
        )

        sys.stdout.write("\n=== Crawl Summary Report ===\n")
        sys.stdout.write(f"Snapshot ID:        {summary.snapshot_id or 'none'}\n")
        sys.stdout.write(f"Duration:           {summary.duration_seconds:.2f}s\n")
        sys.stdout.write(f"Total Visited:      {summary.total_visited}\n")
        sys.stdout.write(f"Fetched (New):      {summary.fetched}\n")
        sys.stdout.write(f"Unchanged:          {summary.unchanged}\n")
        sys.stdout.write(f"Changed:            {summary.changed}\n")
        sys.stdout.write(f"Removed:            {summary.removed}\n")
        sys.stdout.write(f"Needs JS:           {summary.needs_js}\n")
        sys.stdout.write(f"Blocked by robots:  {summary.blocked_robots}\n")
        sys.stdout.write(f"Failed / Errors:    {summary.failed}\n")
        sys.stdout.write(f"Failure Rate:       {summary.failure_rate:.1%}\n")
        if summary.manifest_path:
            sys.stdout.write(f"Manifest:           {summary.manifest_path}\n")
        sys.stdout.write("============================\n\n")

        # Threshold check
        if summary.failure_rate > failure_threshold:
            sys.stderr.write(
                f"ERROR: Crawl failure rate ({summary.failure_rate:.1%}) exceeds threshold ({failure_threshold:.1%})\n"
            )
            return 1

        return 0


def run_crawler_cli(
    config_path: str,
    check_only: bool = False,
    max_pages: int | None = None,
    dry_run: bool = False,
    resume: bool = False,
    snapshot_id: str | None = None,
    data_dir: str = "data",
    failure_threshold: float = 0.20,
) -> int:
    """Run crawler verification or execution. Refuses to run without CRAWL_CONTACT (except in dry-run)."""
    # Initialize structlog and stdlib logging pipeline
    try:
        configure_logging(log_level="INFO", json_output=True)
    except Exception:
        pass

    contact = _load_contact()

    if not dry_run and not check_only and (not contact or not contact.strip()):
        sys.stderr.write(
            "ERROR: Missing contact email or URL for User-Agent.\n"
            "Crawler refused to run without contact identifier.\n"
            "Set the CRAWL_CONTACT environment variable (e.g. export CRAWL_CONTACT=ops@myproject.org)\n"
        )
        return 1

    cfg_file = Path(config_path)
    if not cfg_file.exists():
        sys.stderr.write(f"ERROR: Configuration file '{config_path}' not found.\n")
        return 1

    try:
        config = CrawlConfig.from_yaml(cfg_file)
    except Exception as err:
        sys.stderr.write(f"ERROR: Invalid configuration file: {err}\n")
        return 1

    # In dry-run or check-only, if contact is missing, provide fallback placeholder
    resolved_contact = contact if (contact and contact.strip()) else "dryrun@example.com"

    try:
        user_agent = config.build_user_agent(resolved_contact)
    except MissingContactError as err:
        sys.stderr.write(f"ERROR: {err}\n")
        return 1

    # Validate start URLs
    for start_url in config.start_urls:
        normalized = normalize_url(start_url)
        allowed, reason = is_allowed_url(normalized, config)
        if not allowed:
            sys.stderr.write(f"ERROR: Start URL '{start_url}' rejected by allowlist: {reason}\n")
            return 1

    if check_only:
        sys.stdout.write("[OK] Configuration valid.\n")
        sys.stdout.write(f"[*] User-Agent: {user_agent}\n")
        sys.stdout.write(f"[*] Start URLs ({len(config.start_urls)}):\n")
        for u in config.start_urls:
            sys.stdout.write(f"    - {u}\n")
        sys.stdout.write(f"[*] Allowed Hosts: {config.allowed_hosts}\n")
        sys.stdout.write(f"[*] Page Cap: {config.page_cap}\n")
        sys.stdout.write(f"[*] Inter-request Delay: {config.delay_seconds}s\n")
        return 0

    return asyncio.run(
        _execute_crawl(
            config=config,
            contact=resolved_contact,
            max_pages=max_pages,
            dry_run=dry_run,
            resume=resume,
            snapshot_id=snapshot_id,
            data_dir=data_dir,
            failure_threshold=failure_threshold,
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run_crawler_cli(
        config_path=args.config,
        check_only=args.check_only,
        max_pages=args.max_pages,
        dry_run=args.dry_run,
        resume=args.resume,
        snapshot_id=args.snapshot_id,
        data_dir=args.data_dir,
        failure_threshold=args.failure_threshold,
    )


if __name__ == "__main__":
    sys.exit(main())
