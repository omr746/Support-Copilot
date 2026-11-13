-include .env
export CRAWL_CONTACT

.PHONY: install install-all test test-live lint format typecheck import-check check crawl crawl-check crawl-dry-run crawl-resume

crawl:
	uv run python -m copilot.rag.crawl --config configs/crawl.yaml

crawl-dry-run:
	uv run python -m copilot.rag.crawl --config configs/crawl.yaml --dry-run

crawl-resume:
	uv run python -m copilot.rag.crawl --config configs/crawl.yaml --resume

crawl-check:
	uv run python -m copilot.rag.crawl --config configs/crawl.yaml --check-only

install:
	uv sync --group dev

install-all:
	uv sync --group dev --all-extras

test:
	uv run pytest

test-live:
	COPILOT_RUN_LIVE=1 uv run pytest -m live -s

lint:
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts

format:
	uv run ruff format src tests scripts
	uv run ruff check --fix src tests scripts

typecheck:
	uv run mypy src

import-check:
	uv run lint-imports

check: lint typecheck import-check test
