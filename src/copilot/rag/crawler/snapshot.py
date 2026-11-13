"""Immutable snapshot management, deduplication, incremental versioning, and private backup store."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import shutil
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copilot.rag.crawler.cleaner import clean_html
from copilot.rag.crawler.client import CrawlResponse
from copilot.rag.crawler.config import CrawlConfig
from copilot.rag.crawler.models import (
    CrawlResult,
    PageVersion,
    Snapshot,
)
from copilot.rag.crawler.url_utils import is_allowed_url, normalize_url

logger = logging.getLogger(__name__)


def generate_snapshot_id(date_str: str | None = None, content_seed: str = "") -> str:
    """Generate a unique snapshot identifier (YYYY-MM-DD-<8-char-hash>)."""
    date_part = date_str or datetime.now(UTC).strftime("%Y-%m-%d")
    seed = content_seed or f"{date_part}-{datetime.now(UTC).timestamp()}"
    hash_part = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
    return f"{date_part}-{hash_part}"


class SnapshotStore:
    """Manages creation, immutable persistence, active pointers, and rollback of snapshots.

    Separation of storage:
    - Public (committed to git): data/snapshots/<id>/manifest.jsonl (URL, hash, language, no page text).
    - Private (git-ignored): data/private/snapshots/<id>/pages.jsonl (full page text backup).
    """

    def __init__(self, base_dir: Path | str = "data") -> None:
        self.base_dir = Path(base_dir)
        self.snapshots_dir = self.base_dir / "snapshots"
        self.private_snapshots_dir = self.base_dir / "private" / "snapshots"
        self.active_pointer_file = self.snapshots_dir / "active.txt"

    def build_snapshot(
        self,
        fetched_items: Sequence[CrawlResponse | CrawlResult | dict[str, Any]],
        *,
        previous_snapshot: Snapshot | None = None,
        snapshot_id: str | None = None,
        crawl_complete: bool = True,
        failed_urls: set[str] | list[str] | None = None,
        config: CrawlConfig | None = None,
        clock_now: str | None = None,
    ) -> Snapshot:
        """Construct an immutable Snapshot from crawled responses.

        Rules:
        - Content hash rule: hash cleaned main text after whitespace normalization.
        - Deduplication: two pages sharing the same canonical URL are stored once.
        - Canonical allowlist: unallowed canonical URLs fall back to normalized page URL.
        - Reuse: unchanged pages (same content hash or 304) reuse the previous PageVersion via O(1) lookup.
        - 304 without previous version: treated as error and skipped without junk creation.
        - 404/410 handling: known pages marked removed; unknown URLs omitted from snapshot.
        - Non-200 statuses: never treated as content; previous versions preserved.
        - Removals: marked removed only on 404/410 or complete crawl where page is no longer linked.
          Partial crawl carries previous versions over unchanged.
        """
        sid = snapshot_id or generate_snapshot_id()
        current_pages: dict[str, PageVersion] = {}
        now_iso = clock_now or datetime.now(UTC).isoformat()
        failed_set = {normalize_url(u) for u in failed_urls} if failed_urls else set()

        # O(1) lookup dictionary for existing pages by canonical URL and original URL
        previous_by_url: dict[str, PageVersion] = {}
        if previous_snapshot:
            for canonical, page in previous_snapshot.pages.items():
                previous_by_url[canonical] = page
                previous_by_url[page.url] = page
                previous_by_url[normalize_url(canonical)] = page
                previous_by_url[normalize_url(page.url)] = page

        for item in fetched_items:
            # Normalize item access
            if isinstance(item, (CrawlResponse, CrawlResult)):
                raw_url = item.url
                status_code = item.status_code
                raw_html = item.text
                etag = item.etag
                last_modified = item.last_modified
                is_unchanged = getattr(item, "is_unchanged", False)
            else:
                raw_url = str(item.get("url", ""))
                status_code = int(item.get("status_code", 200))
                raw_html = str(item.get("text", item.get("raw_html", "")))
                etag = item.get("etag")
                last_modified = item.get("last_modified")
                is_unchanged = bool(item.get("is_unchanged", False))

            norm_url = normalize_url(raw_url)

            # 1. Handle 304 Not Modified / Unchanged
            if status_code == 304 or is_unchanged:
                prev_version = previous_by_url.get(norm_url)
                if prev_version:
                    current_pages[prev_version.canonical_url] = dataclasses.replace(
                        prev_version, last_verified_at=now_iso
                    )
                else:
                    logger.warning(
                        "Received 304/unchanged for '%s' with no previous version in snapshot. Skipping.",
                        norm_url,
                    )
                continue

            # 2. Handle 404 / 410 Gone
            if status_code in (404, 410):
                prev_version = previous_by_url.get(norm_url)
                if prev_version:
                    # Known page: mark removed in this snapshot
                    canonical = prev_version.canonical_url
                    current_pages[canonical] = PageVersion(
                        url=prev_version.url,
                        canonical_url=canonical,
                        title=prev_version.title,
                        language=prev_version.language,
                        fetched_at=now_iso,
                        http_status=status_code,
                        content_hash="",
                        cleaner_version=prev_version.cleaner_version,
                        raw_html="",
                        cleaned_text="",
                        etag=None,
                        last_modified=None,
                        needs_js=False,
                        is_removed=True,
                    )
                else:
                    # Unknown URL 404: report records it, do NOT store error page in snapshot
                    logger.info(
                        "404/410 for unknown URL '%s'. Omitted from snapshot.",
                        norm_url,
                    )
                continue

            # 3. Handle other Non-200 error statuses (500, 502, 403, 429, etc.)
            if status_code != 200:
                logger.warning(
                    "Non-200 status code %d for '%s'. Error HTML is not indexed.",
                    status_code,
                    norm_url,
                )
                prev_version = previous_by_url.get(norm_url)
                if prev_version:
                    # Keep previous version for failed page
                    current_pages[prev_version.canonical_url] = prev_version
                continue

            # 4. Handle 200 OK — Clean HTML and validate canonical
            cleaned = clean_html(raw_html, norm_url, config=config)
            canonical = cleaned.canonical_url

            # Enforce allowlist on canonical URL
            if config:
                allowed, _ = is_allowed_url(canonical, config)
                if not allowed:
                    logger.warning(
                        "Canonical URL '%s' violates crawl allowlist. Falling back to page URL '%s'.",
                        canonical,
                        norm_url,
                    )
                    canonical = norm_url

            # Deduplication: if canonical already stored in this snapshot, skip duplicate
            if canonical in current_pages:
                logger.debug(
                    "Duplicate canonical URL '%s' already captured. Skipping '%s'.",
                    canonical,
                    norm_url,
                )
                continue

            # Check if unchanged from previous snapshot via content hash
            prev_version = previous_snapshot.pages.get(canonical) if previous_snapshot else None
            if (
                prev_version
                and prev_version.content_hash
                and prev_version.content_hash == cleaned.content_hash
                and not prev_version.is_removed
            ):
                current_pages[canonical] = dataclasses.replace(
                    prev_version, last_verified_at=now_iso
                )
                continue

            # Changed or new page: construct new PageVersion
            current_pages[canonical] = PageVersion(
                url=norm_url,
                canonical_url=canonical,
                title=cleaned.title,
                language=cleaned.language,
                fetched_at=now_iso,
                http_status=200,
                content_hash=cleaned.content_hash,
                cleaner_version=cleaned.cleaner_version,
                raw_html=raw_html,
                cleaned_text=cleaned.cleaned_text,
                etag=etag,
                last_modified=last_modified,
                needs_js=cleaned.needs_js,
                is_removed=False,
                last_verified_at=now_iso,
            )

        # Handle previous snapshot pages not present in current_pages
        if previous_snapshot:
            for canonical, prev_version in previous_snapshot.pages.items():
                if canonical not in current_pages:
                    norm_canon = normalize_url(canonical)
                    norm_prev_url = normalize_url(prev_version.url)
                    was_failed = (
                        norm_canon in failed_set
                        or norm_prev_url in failed_set
                        or canonical in failed_set
                        or prev_version.url in failed_set
                    )

                    if not crawl_complete or was_failed:
                        # Partial crawl or failed page: carry over previous version unchanged
                        current_pages[canonical] = prev_version
                    else:
                        # Full crawl and URL was no longer linked: mark removed
                        current_pages[canonical] = PageVersion(
                            url=prev_version.url,
                            canonical_url=canonical,
                            title=prev_version.title,
                            language=prev_version.language,
                            fetched_at=now_iso,
                            http_status=prev_version.http_status,
                            content_hash="",
                            cleaner_version=prev_version.cleaner_version,
                            raw_html="",
                            cleaned_text="",
                            etag=None,
                            last_modified=None,
                            needs_js=prev_version.needs_js,
                            is_removed=True,
                        )

        return Snapshot(
            snapshot_id=sid,
            created_at=now_iso,
            pages=current_pages,
        )

    def save_snapshot(self, snapshot: Snapshot) -> tuple[Path, Path]:
        """Persist snapshot manifest (public, committed) and page texts (private backup).

        Enforces:
        - Immutability: refuses and raises FileExistsError if snapshot folder already exists.
        - Atomic writes: writes into temporary directories and uses os.replace.
        - Decoupled activation: does NOT activate the snapshot. Activation is an explicit step.

        Returns:
            tuple (manifest_file_path, private_backup_path)
        """
        snap_dir = self.snapshots_dir / snapshot.snapshot_id
        private_dir = self.private_snapshots_dir / snapshot.snapshot_id

        if snap_dir.exists() or private_dir.exists():
            raise FileExistsError(
                f"Snapshot '{snapshot.snapshot_id}' already exists. Snapshots are immutable."
            )

        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.private_snapshots_dir.mkdir(parents=True, exist_ok=True)

        token = uuid.uuid4().hex[:8]
        tmp_snap_dir = self.snapshots_dir / f".tmp_{snapshot.snapshot_id}_{token}"
        tmp_private_dir = self.private_snapshots_dir / f".tmp_{snapshot.snapshot_id}_{token}"

        try:
            # 1. Public Manifest: data/snapshots/<id>/manifest.jsonl
            tmp_snap_dir.mkdir(parents=True, exist_ok=True)
            tmp_manifest_file = tmp_snap_dir / "manifest.jsonl"
            with tmp_manifest_file.open("w", encoding="utf-8") as f:
                for page in snapshot.pages.values():
                    entry = page.to_manifest_entry()
                    line = json.dumps(entry.to_dict(), ensure_ascii=False)
                    f.write(line + "\n")

            # 2. Private Backup: data/private/snapshots/<id>/pages.jsonl
            tmp_private_dir.mkdir(parents=True, exist_ok=True)
            tmp_private_file = tmp_private_dir / "pages.jsonl"
            with tmp_private_file.open("w", encoding="utf-8") as f:
                for page in snapshot.pages.values():
                    page_data = {
                        "url": page.url,
                        "canonical_url": page.canonical_url,
                        "title": page.title,
                        "language": page.language,
                        "fetched_at": page.fetched_at,
                        "http_status": page.http_status,
                        "content_hash": page.content_hash,
                        "cleaner_version": page.cleaner_version,
                        "etag": page.etag,
                        "last_modified": page.last_modified,
                        "needs_js": page.needs_js,
                        "is_removed": page.is_removed,
                        "last_verified_at": page.last_verified_at or page.fetched_at,
                        "cleaned_text": page.cleaned_text,
                        "raw_html": page.raw_html,
                    }
                    line = json.dumps(page_data, ensure_ascii=False)
                    f.write(line + "\n")

            # Atomic directory placement
            os.replace(tmp_snap_dir, snap_dir)
            os.replace(tmp_private_dir, private_dir)
        except Exception:
            shutil.rmtree(tmp_snap_dir, ignore_errors=True)
            shutil.rmtree(tmp_private_dir, ignore_errors=True)
            raise

        manifest_file = snap_dir / "manifest.jsonl"
        private_file = private_dir / "pages.jsonl"
        return manifest_file, private_file

    def get_active_snapshot_id(self) -> str | None:
        """Read the currently active snapshot ID from active.txt."""
        if not self.active_pointer_file.exists():
            return None
        sid = self.active_pointer_file.read_text(encoding="utf-8").strip()
        return sid or None

    def activate_snapshot(self, snapshot_id: str) -> None:
        """Atomically set the active snapshot pointer.

        Raises FileNotFoundError if snapshot manifest does not exist.
        """
        sid = snapshot_id.strip()
        target_manifest = self.snapshots_dir / sid / "manifest.jsonl"
        if not target_manifest.exists():
            raise FileNotFoundError(
                f"Cannot activate snapshot '{sid}': manifest does not exist at {target_manifest}"
            )

        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex[:8]
        tmp_pointer_file = self.snapshots_dir / f".tmp_active_{token}.txt"
        tmp_pointer_file.write_text(sid + "\n", encoding="utf-8")
        os.replace(tmp_pointer_file, self.active_pointer_file)

    set_active_snapshot_id = activate_snapshot
    set_active_snapshot = activate_snapshot

    @staticmethod
    def _read_records(file_path: Path) -> list[dict[str, Any]]:
        """Read records from either standard JSONL or formatted JSON/multi-line JSON."""
        content = file_path.read_text(encoding="utf-8").strip()
        if not content:
            return []
        if content.startswith("["):
            data = json.loads(content)
            if isinstance(data, list):
                return data
        try:
            return [json.loads(line) for line in content.splitlines() if line.strip()]
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            records: list[dict[str, Any]] = []
            idx = 0
            length = len(content)
            while idx < length:
                while idx < length and content[idx].isspace():
                    idx += 1
                if idx >= length:
                    break
                obj, end_idx = decoder.raw_decode(content, idx)
                records.append(obj)
                idx = end_idx
            return records

    def load_snapshot(self, snapshot_id: str) -> Snapshot:
        """Load a snapshot from disk using private backup if available, else public manifest."""
        private_file = self.private_snapshots_dir / snapshot_id / "pages.jsonl"
        manifest_file = self.snapshots_dir / snapshot_id / "manifest.jsonl"

        pages: dict[str, PageVersion] = {}

        if private_file.exists():
            for data in self._read_records(private_file):
                pv = PageVersion(
                    url=data["url"],
                    canonical_url=data["canonical_url"],
                    title=data.get("title", ""),
                    language=data.get("language", "und"),
                    fetched_at=data["fetched_at"],
                    http_status=data["http_status"],
                    content_hash=data["content_hash"],
                    cleaner_version=data["cleaner_version"],
                    raw_html=data.get("raw_html", ""),
                    cleaned_text=data.get("cleaned_text", ""),
                    etag=data.get("etag"),
                    last_modified=data.get("last_modified"),
                    needs_js=data.get("needs_js", False),
                    is_removed=data.get("is_removed", False),
                    last_verified_at=data.get("last_verified_at", data["fetched_at"]),
                )
                pages[pv.canonical_url] = pv
        elif manifest_file.exists():
            for data in self._read_records(manifest_file):
                pv = PageVersion(
                    url=data["url"],
                    canonical_url=data["canonical_url"],
                    title=data.get("title", ""),
                    language=data.get("language", "und"),
                    fetched_at=data["fetched_at"],
                    http_status=data["http_status"],
                    content_hash=data["content_hash"],
                    cleaner_version=data["cleaner_version"],
                    needs_js=data.get("needs_js", False),
                    is_removed=data.get("is_removed", False),
                    last_verified_at=data.get("last_verified_at", data["fetched_at"]),
                )
                pages[pv.canonical_url] = pv
        else:
            raise FileNotFoundError(f"Snapshot '{snapshot_id}' not found.")

        return Snapshot(snapshot_id=snapshot_id, pages=pages)

    def get_active_snapshot(self) -> Snapshot | None:
        """Load the currently active Snapshot, or return None if no active snapshot is set."""
        active_id = self.get_active_snapshot_id()
        if not active_id:
            return None
        try:
            return self.load_snapshot(active_id)
        except FileNotFoundError:
            return None

    def rollback(self, target_snapshot_id: str) -> None:
        """Rollback active pointer to a previous snapshot ID."""
        manifest_file = self.snapshots_dir / target_snapshot_id / "manifest.jsonl"
        if not manifest_file.exists():
            raise ValueError(
                f"Cannot rollback to '{target_snapshot_id}': snapshot manifest does not exist."
            )
        self.activate_snapshot(target_snapshot_id)
