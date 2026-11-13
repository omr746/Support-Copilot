"""Crawl CLI entrypoint for Support Copilot knowledge base.

Usage:
    python -m copilot.rag.crawl --config configs/crawl.yaml
    python -m copilot.rag.crawl --dry-run
    python -m copilot.rag.crawl --resume
"""

from __future__ import annotations

import sys

from copilot.rag.crawler.cli import main

if __name__ == "__main__":
    sys.exit(main())
