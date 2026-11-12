"""Crawler configuration model and YAML loader."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from copilot.rag.crawler.errors import MissingContactError


class CrawlConfig(BaseModel):
    """Configuration settings for polite crawler."""

    model_config = ConfigDict(extra="ignore")

    start_urls: list[str] = Field(default_factory=list)
    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_path_prefixes: list[str] = Field(default_factory=list)
    denied_patterns: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=lambda: ["ar", "en"])
    page_cap: int = Field(default=100, ge=1)
    delay_seconds: float = Field(default=1.0, ge=0.0)
    timeout_seconds: float = Field(default=10.0, gt=0.0)
    max_retries: int = Field(default=3, ge=0)
    consecutive_error_limit: int = Field(default=3, ge=1)
    user_agent_prefix: str = Field(default="SupportCopilot/0.1.0")

    def build_user_agent(self, contact: str | None = None) -> str:
        """Construct User-Agent header with mandatory contact address.

        Raises MissingContactError if contact is missing or empty.
        """
        contact_val = contact if contact is not None else os.getenv("CRAWL_CONTACT")
        if not contact_val or not contact_val.strip():
            raise MissingContactError(
                "CRAWL_CONTACT environment variable must be set to run crawler. "
                "Never commit contact addresses directly into the repository."
            )
        clean_contact = contact_val.strip()
        return f"{self.user_agent_prefix} (+https://github.com/3amorry/Support-Copilot; contact: {clean_contact})"

    @classmethod
    def from_yaml(cls, path: str | Path) -> CrawlConfig:
        """Load crawler configuration from a YAML file."""
        cfg_path = Path(path)
        if not cfg_path.exists():
            raise FileNotFoundError(f"Crawl config file not found: {cfg_path}")
        with open(cfg_path, encoding="utf-8") as f:
            raw_data: Any = yaml.safe_load(f)
        if not isinstance(raw_data, dict):
            raise ValueError(f"Invalid YAML content in {cfg_path}: expected dictionary")
        return cls.model_validate(raw_data)
