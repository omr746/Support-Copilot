#!/usr/bin/env python3
"""Docs check (Plan Steps 0.3). Fails when the project log or the ADR folder is missing.

Checks
  1. LOG.md exists and is not empty.
  2. docs/adr/ exists and is a directory.
  3. LOG.md holds the three required decisions. Each one needs a heading with a real
     date and the text "Decision: <name>", and a "Reason:" line that is not empty:

         ## 2026-11-01 · Decision: Knowledge base
         - Decision: ...
         - Reason: ...

Uses only the Python standard library.
Run:   python3 scripts/check_docs.py          (add --root PATH to check another folder)
Exit:  0 = all good, 1 = at least one problem.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_DECISIONS = ("Knowledge base", "Hosting path", "UI language")

HEADING_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\b.*?\bDecision:\s*(.+?)\s*$", re.IGNORECASE)
H2_RE = re.compile(r"^##\s")
REASON_RE = re.compile(r"^\s*(?:[-*]\s*)?Reason:\s*(\S.*)$", re.IGNORECASE)


def _valid_date(text: str) -> bool:
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def check_log(log_path: Path) -> list[str]:
    if not log_path.is_file():
        return [f"LOG.md is missing (expected at {log_path})"]
    lines = log_path.read_text(encoding="utf-8-sig").splitlines()
    if not any(line.strip() for line in lines):
        return ["LOG.md is empty"]

    sections: dict[str, tuple[str, list[str]]] = {}
    current: tuple[str, str] | None = None
    body: list[str] = []
    for line in lines + ["## end"]:
        if H2_RE.match(line):
            if current is not None:
                sections[current[1].lower()] = (current[0], body)
            match = HEADING_RE.match(line)
            current = (match.group(1), match.group(2)) if match else None
            body = []
        elif current is not None:
            body.append(line)

    problems: list[str] = []
    for name in REQUIRED_DECISIONS:
        found = sections.get(name.lower())
        if found is None:
            problems.append(f'LOG.md has no heading like "## YYYY-MM-DD · Decision: {name}"')
            continue
        date, body_lines = found
        if not _valid_date(date):
            problems.append(f'Decision "{name}" has an invalid date: {date}')
        if not any(REASON_RE.match(line) for line in body_lines):
            problems.append(f'Decision "{name}" has no non-empty "Reason:" line')
    return problems


def check_adr_dir(adr_dir: Path) -> list[str]:
    if not adr_dir.is_dir():
        return [f"docs/adr/ is missing (expected at {adr_dir})"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check LOG.md and docs/adr/")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    root: Path = args.root
    checks = (
        ("LOG.md", check_log(root / "LOG.md")),
        ("docs/adr/", check_adr_dir(root / "docs" / "adr")),
    )
    failed = False
    for label, problems in checks:
        if problems:
            failed = True
            for problem in problems:
                print(f"[FAIL] {label}: {problem}")
        else:
            print(f"[OK] {label}")
    print()
    print("Docs check failed. Fix the [FAIL] lines and rerun." if failed else "Docs check passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())