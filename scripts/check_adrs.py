#!/usr/bin/env python3
"""Lint the architecture decision records (ADRs) in docs/adr/.

Plan Step 0.5 test. The check fails (exit code 1) when:

  - docs/adr/ is missing or has no ADR file
  - a .md file in docs/adr/ is not named like 0001-short-title.md
    (README.md and TEMPLATE.md are ignored)
  - ADR numbers do not start at 0001 or have a gap or a duplicate
  - the first line is not a title like '# ADR-0001: Short title' whose
    number matches the file name
  - an ADR has no Status, Context or Decision heading, or one of those
    sections is empty
  - the Status does not start with Proposed, Accepted, Rejected,
    Deprecated or Superseded

A Consequences section is recommended but not required.
Headings inside fenced code blocks are ignored.

Exit codes: 0 = all good, 1 = problems found, 2 = wrong command-line usage.

Usage:
  python3 scripts/check_adrs.py
  python3 scripts/check_adrs.py --adr-dir path/to/docs/adr

No packages to install. Works on Python 3.8 or newer.
"""

from __future__ import annotations

import argparse
import itertools
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ADR_DIR = REPO_ROOT / "docs" / "adr"

FILE_PATTERN = re.compile(r"^(\d{4})-[a-z0-9][a-z0-9-]*\.md$")
IGNORED_FILES = {"readme.md", "template.md"}  # compared in lowercase
REQUIRED_SECTIONS = ("Status", "Context", "Decision")
VALID_STATUSES = ("proposed", "accepted", "rejected", "deprecated", "superseded")

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
TITLE = re.compile(r"^ADR-(\d{4}):\s*\S")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def split_sections(text: str) -> Tuple[Optional[str], Dict[str, str]]:
    """Return (title, sections).

    The title is the first line of the file if it is a level-1 heading,
    otherwise None. Every other heading starts a section, keyed by its
    lowercase name. Headings inside fenced code blocks are ignored.
    """
    title: Optional[str] = None
    sections: Dict[str, str] = {}
    current: Optional[str] = None
    in_fence = False
    seen_content = False

    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            seen_content = True
            if current is not None:
                sections[current] += line + "\n"
            continue

        if not in_fence:
            heading = HEADING.match(line)
            if heading:
                level = len(heading.group(1))
                name = heading.group(2).strip()
                if title is None and level == 1 and not seen_content:
                    title = name
                else:
                    current = name.rstrip(":").strip().lower()
                    sections.setdefault(current, "")
                seen_content = True
                continue

        if line.strip():
            seen_content = True
        if current is not None:
            sections[current] += line + "\n"

    return title, sections


def has_content(body: str) -> bool:
    return bool(HTML_COMMENT.sub("", body).strip())


def check_adr_file(path: Path, number: int) -> List[str]:
    """Return the problems found in one ADR file."""
    name = path.name
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return ["%s: cannot be read as UTF-8 text." % name]

    title, sections = split_sections(text)
    problems: List[str] = []

    if title is None:
        problems.append(
            "%s: the first line must be a title like '# ADR-%04d: Short title'." % (name, number)
        )
    else:
        match = TITLE.match(title)
        if not match:
            problems.append(
                "%s: the title '%s' must look like 'ADR-%04d: Short title'." % (name, title, number)
            )
        elif int(match.group(1)) != number:
            problems.append(
                "%s: the title says ADR-%s but the file name number is %04d."
                % (name, match.group(1), number)
            )

    for section in REQUIRED_SECTIONS:
        body = sections.get(section.lower())
        if body is None:
            problems.append(
                "%s: missing the '%s' heading (add a line like '## %s')." % (name, section, section)
            )
        elif not has_content(body):
            problems.append("%s: the '%s' section is empty." % (name, section))

    status_body = sections.get("status")
    if status_body is not None and has_content(status_body):
        words = re.findall(r"[A-Za-z]+", HTML_COMMENT.sub("", status_body))
        first = words[0].lower() if words else ""
        if first not in VALID_STATUSES:
            problems.append(
                "%s: the Status must start with one of Proposed, Accepted, Rejected, Deprecated, "
                "Superseded (found '%s')." % (name, words[0] if words else "")
            )

    return problems


def collect_adrs(adr_dir: Path) -> Tuple[List[Tuple[int, Path]], List[str]]:
    """Find ADR files. Returns (list of (number, path), problems about names)."""
    adrs: List[Tuple[int, Path]] = []
    problems: List[str] = []
    for path in sorted(adr_dir.iterdir()):
        if not path.is_file():
            continue
        lower = path.name.lower()
        if lower in IGNORED_FILES or not lower.endswith(".md"):
            continue  # .gitkeep and other non-markdown files
        match = FILE_PATTERN.match(path.name)
        if not match:
            problems.append(
                "%s: the file name must look like 0001-short-title.md "
                "(four digits, then lowercase words joined with hyphens)." % path.name
            )
            continue
        adrs.append((int(match.group(1)), path))
    return adrs, problems


def check_adr_dir(adr_dir: Path) -> List[str]:
    """Return every problem found in the ADR folder. An empty list means all good."""
    if not adr_dir.is_dir():
        return ["%s does not exist. Create it and write ADR-0001 (Plan Step 0.5)." % adr_dir]

    adrs, problems = collect_adrs(adr_dir)
    if not adrs:
        problems.append("no ADR files found. Write ADR-0001 (Plan Step 0.5).")
        return problems

    names_by_number: Dict[int, List[str]] = {}
    for number, path in adrs:
        names_by_number.setdefault(number, []).append(path.name)

    for number, names in sorted(names_by_number.items()):
        if len(names) > 1:
            problems.append(
                "number %04d is used by more than one file: %s." % (number, ", ".join(names))
            )

    numbers = sorted(names_by_number)
    if numbers[0] != 1:
        problems.append("numbering must start at 0001, but the lowest number is %04d." % numbers[0])
    for previous, current in itertools.pairwise(numbers):
        if current != previous + 1:
            missing = ", ".join("%04d" % value for value in range(previous + 1, current))
            problems.append(
                "numbering has a gap: %04d is followed by %04d (missing %s)."
                % (previous, current, missing)
            )

    for number, path in adrs:
        problems.extend(check_adr_file(path, number))
    return problems


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Lint the ADR files in docs/adr/.")
    parser.add_argument(
        "--adr-dir", default=str(DEFAULT_ADR_DIR), help="folder that holds the ADR files"
    )
    args = parser.parse_args(argv)

    adr_dir = Path(args.adr_dir)
    problems = check_adr_dir(adr_dir)

    if problems:
        print("ADR check failed (%s):" % adr_dir)
        for problem in problems:
            print("  - " + problem)
        print()
        print("%d problem(s). Fix them and run this script again." % len(problems))
        return 1

    adrs, _ = collect_adrs(adr_dir)
    print("ADR check passed: %d ADR file(s), numbering is sequential." % len(adrs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
