"""Export snapshot pages from private JSONL into human-readable Markdown format.

Formats each page with fields clearly separated vertically (field name on top, value below),
with readable multiline text for easy inspection, comparison, and debugging.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

# Ensure src/ is on sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from copilot.rag.crawler.cleaner import CLEANER_VERSION, clean_html
from copilot.rag.crawler.snapshot import SnapshotStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def slugify(text: str) -> str:
    """Create a safe file slug from text."""
    slug = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    return re.sub(r"[-\s]+", "_", slug)[:50] or "page"


def read_snapshot_records(file_path: Path) -> list[dict[str, Any]]:
    """Read snapshot records from JSONL or formatted JSON array."""
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
            while idx < length and content[idx] in " \t\r\n,":
                idx += 1
            if idx >= length:
                break
            record, idx = decoder.raw_decode(content, idx)
            records.append(record)
        return records


def format_page_markdown(
    record: dict[str, Any],
    index: int,
    total: int,
    compare_cleaner: bool = False,
) -> str:
    """Format a single page record into human-readable Markdown with up-and-down fields."""
    url = record.get("url", "")
    canonical_url = record.get("canonical_url", "")
    title = record.get("title", "") or "(No title)"
    language = record.get("language", "und")
    http_status = record.get("http_status", 200)
    cleaner_version = record.get("cleaner_version", "")
    extractor = record.get("extractor", "") or "not recorded"
    content_hash = record.get("content_hash", "") or "(empty)"
    needs_js = record.get("needs_js", False)
    fetched_at = record.get("fetched_at", "")
    last_verified_at = record.get("last_verified_at", fetched_at)
    cleaned_text = record.get("cleaned_text", "").strip()
    raw_html = record.get("raw_html", "")

    lines = [
        f"# [{index} / {total}] {title}",
        "",
        "### URL",
        url,
        "",
        "### Canonical URL",
        canonical_url,
        "",
        "### Title",
        title,
        "",
        "### Language",
        language,
        "",
        "### HTTP Status",
        str(http_status),
        "",
        "### Cleaner Version",
        cleaner_version,
        "",
        "### Extractor",
        extractor,
        "",
        "### Content Hash",
        content_hash,
        "",
        "### Needs JS",
        str(needs_js),
        "",
        "### Fetched At",
        fetched_at,
        "",
        "### Last Verified At",
        last_verified_at,
        "",
        "---",
        "",
        "### Cleaned Text",
        "",
    ]

    if cleaned_text:
        lines.append(cleaned_text)
    else:
        lines.append("*(No cleaned text — page is empty, removed, or client-rendered JavaScript shell)*")

    if compare_cleaner and raw_html:
        new_cleaned = clean_html(raw_html, url, base_url="https://www.te.eg")
        lines.extend([
            "",
            "---",
            "",
            f"### Re-Cleaned Text (Current Rules: {CLEANER_VERSION})",
            "",
            f"- **New Extractor**: {new_cleaned.extractor}",
            f"- **New Content Hash**: {new_cleaned.content_hash or '(empty)'}",
            f"- **New Needs JS**: {new_cleaned.needs_js}",
            "",
            new_cleaned.cleaned_text or "*(Re-cleaned output is empty / JS shell)*",
        ])

    lines.extend([
        "",
        "=" * 80,
        "",
    ])
    return "\n".join(lines)


def export_snapshot(
    snapshot_id: str | None = None,
    base_dir: Path | str = "data",
    output_file: Path | str | None = None,
    split_files: bool = False,
    compare_cleaner: bool = False,
) -> Path:
    """Export snapshot records into clear readable Markdown."""
    base_path = Path(base_dir)
    private_dir = base_path / "private" / "snapshots"

    # Resolve snapshot ID
    if not snapshot_id:
        active_file = base_path / "snapshots" / "active.txt"
        if active_file.exists():
            snapshot_id = active_file.read_text(encoding="utf-8").strip()
        else:
            dirs = sorted([d for d in private_dir.iterdir() if d.is_dir()])
            if dirs:
                snapshot_id = dirs[-1].name
            else:
                raise FileNotFoundError("No snapshots found in data/private/snapshots/")

    snap_path = private_dir / snapshot_id
    pages_jsonl = snap_path / "pages.jsonl"
    if not pages_jsonl.exists():
        raise FileNotFoundError(f"pages.jsonl not found at {pages_jsonl}")

    logger.info("Reading snapshot records from %s ...", pages_jsonl)
    records = read_snapshot_records(pages_jsonl)
    logger.info("Found %d page records.", len(records))

    # Single markdown file path
    out_md_path = Path(output_file) if output_file else (snap_path / "pages_view.md")

    md_blocks: list[str] = [
        f"# Snapshot Inspection View: `{snapshot_id}`",
        f"**Total Pages**: {len(records)}  ",
        f"**Source File**: `{pages_jsonl}`  ",
        f"**Cleaner in Code**: `{CLEANER_VERSION}`  ",
        "",
        "---",
        "",
    ]

    split_dir = snap_path / "pages"
    if split_files:
        split_dir.mkdir(parents=True, exist_ok=True)

    for i, record in enumerate(records, start=1):
        page_md = format_page_markdown(record, i, len(records), compare_cleaner=compare_cleaner)
        md_blocks.append(page_md)

        if split_files:
            safe_title = slugify(record.get("title", "") or f"page_{i}")
            page_file = split_dir / f"{i:03d}_{safe_title}.md"
            page_file.write_text(page_md, encoding="utf-8")

    out_md_path.write_text("\n".join(md_blocks), encoding="utf-8")
    logger.info("Successfully exported snapshot markdown view to: %s", out_md_path)
    if split_files:
        logger.info("Exported individual page files to: %s", split_dir)

    return out_md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export snapshot pages as clear, formatted Markdown.")
    parser.add_argument("--snapshot", "-s", help="Snapshot ID (default: active snapshot)")
    parser.add_argument("--output", "-o", help="Output markdown file path")
    parser.add_argument("--split-files", action="store_true", help="Also save individual page .md files")
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Include re-cleaned text comparison using current code cleaner rules",
    )
    args = parser.parse_args()

    export_snapshot(
        snapshot_id=args.snapshot,
        output_file=args.output,
        split_files=args.split_files,
        compare_cleaner=args.compare,
    )


if __name__ == "__main__":
    main()
