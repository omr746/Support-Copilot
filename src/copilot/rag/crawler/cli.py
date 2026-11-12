"""Command line entrypoint for the polite web crawler."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from copilot.rag.crawler.client import PoliteClient
from copilot.rag.crawler.config import CrawlConfig
from copilot.rag.crawler.errors import MissingContactError
from copilot.rag.crawler.orchestrator import CrawlOrchestrator
from copilot.rag.crawler.robots import RobotsPolicy
from copilot.rag.crawler.url_utils import is_allowed_url, normalize_url

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="copilot.rag.crawler",
        description="Polite crawler CLI for Support Copilot knowledge base.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="configs/crawl.yaml",
        help="Path to crawler YAML configuration file (default: configs/crawl.yaml)",
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
    return parser.parse_args(argv)


async def _execute_crawl(config: CrawlConfig, contact: str, max_pages: int | None = None) -> int:
    """Execute crawl using PoliteClient, RobotsPolicy, and CrawlOrchestrator."""
    async with PoliteClient(config, contact=contact) as client:
        base_url = config.start_urls[0] if config.start_urls else "https://www.te.eg"
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

        orchestrator = CrawlOrchestrator(config, client, robots_policy=robots_policy)
        cap = max_pages or config.page_cap
        sys.stdout.write(
            f"[*] Starting crawl workflow (start_urls: {len(config.start_urls)}, page_cap: {cap})...\n"
        )

        summary = await orchestrator.run(max_pages=cap)

        sys.stdout.write("\n--- Crawl Summary Report ---\n")
        sys.stdout.write(f"Total Visited:        {summary.total_visited}\n")
        sys.stdout.write(f"Successfully Fetched: {summary.fetched}\n")
        sys.stdout.write(f"Unchanged (304):      {summary.unchanged}\n")
        sys.stdout.write(f"Blocked by robots:    {summary.blocked_robots}\n")
        sys.stdout.write(f"Failed / Errors:      {summary.failed}\n")
        sys.stdout.write("----------------------------\n\n")
        return 0


def run_crawler_cli(
    config_path: str, check_only: bool = False, max_pages: int | None = None
) -> int:
    """Run crawler verification or execution. Refuses to run without CRAWL_CONTACT."""
    contact = os.getenv("CRAWL_CONTACT")
    if not contact or not contact.strip():
        env_path = Path(".env")
        if env_path.exists():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    trimmed = line.strip()
                    if trimmed.startswith("CRAWL_CONTACT="):
                        parsed = trimmed.split("=", 1)[1].strip().strip('"').strip("'")
                        if parsed:
                            contact = parsed
                            break
            except Exception:
                pass

    if not contact or not contact.strip():
        sys.stderr.write(
            "ERROR: CRAWL_CONTACT environment variable must be set to run crawler.\n"
            "Provide your contact information (e.g. email or repository URL) via CRAWL_CONTACT or in .env.\n"
        )
        return 1

    path = Path(config_path)
    if not path.exists():
        sys.stderr.write(f"ERROR: Config file not found: {path}\n")
        return 1

    config = CrawlConfig.from_yaml(path)
    user_agent = config.build_user_agent(contact)
    sys.stdout.write(f"[OK] Contact verified: {contact}\n")
    sys.stdout.write(f"[OK] User-Agent: {user_agent}\n")
    sys.stdout.write(f"[OK] Loaded {len(config.start_urls)} start URL(s) from {config_path}\n")

    # Validate start URLs against rules
    for url in config.start_urls:
        normalized = normalize_url(url)
        allowed, reason = is_allowed_url(normalized, config)
        if not allowed:
            sys.stderr.write(f"ERROR: Start URL '{url}' is not allowed: {reason}\n")
            return 1
        sys.stdout.write(f"[OK] Start URL verified: {normalized}\n")

    if check_only:
        sys.stdout.write("[OK] Pre-flight crawl check passed successfully.\n")
        return 0

    return asyncio.run(_execute_crawl(config, contact, max_pages=max_pages))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run_crawler_cli(
            config_path=args.config,
            check_only=args.check_only,
            max_pages=args.max_pages,
        )
    except MissingContactError as err:
        sys.stderr.write(f"ERROR: {err}\n")
        return 1
    except Exception as err:
        sys.stderr.write(f"ERROR: Unexpected crawler failure: {err}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
