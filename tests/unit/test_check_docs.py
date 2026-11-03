"""Unit tests for scripts/check_docs.py (Plan Step 0.3 Tests line)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_docs.py"

GOOD_LOG = """# LOG.md

## 2026-11-01 · Decision: Knowledge base
- Decision: FastAPI docs.
- Reason: MIT licensed.

## 2026-11-01 · Decision: Hosting path
- Decision: Render.
- Reason: No card needed.

## 2026-11-01 · Decision: UI language
- Decision: Bilingual.
- Reason: Arabic and English users.
"""


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)], capture_output=True, text=True
    )


def make_repo(tmp_path: Path, log: str | None = GOOD_LOG, adr: bool = True) -> Path:
    if log is not None:
        (tmp_path / "LOG.md").write_text(log, encoding="utf-8")
    if adr:
        (tmp_path / "docs" / "adr").mkdir(parents=True)
    return tmp_path


def test_passes_when_everything_is_present(tmp_path: Path) -> None:
    result = run(make_repo(tmp_path))
    assert result.returncode == 0, result.stdout
    assert "Docs check passed." in result.stdout


def test_fails_when_log_is_missing(tmp_path: Path) -> None:
    result = run(make_repo(tmp_path, log=None))
    assert result.returncode == 1
    assert "LOG.md is missing" in result.stdout


def test_fails_when_log_is_empty(tmp_path: Path) -> None:
    result = run(make_repo(tmp_path, log="\n  \n"))
    assert result.returncode == 1
    assert "LOG.md is empty" in result.stdout


def test_fails_when_adr_folder_is_missing(tmp_path: Path) -> None:
    result = run(make_repo(tmp_path, adr=False))
    assert result.returncode == 1
    assert "docs/adr/ is missing" in result.stdout


@pytest.mark.parametrize("name", ["Knowledge base", "Hosting path", "UI language"])
def test_fails_when_a_decision_is_missing(tmp_path: Path, name: str) -> None:
    log = GOOD_LOG.replace(f"Decision: {name}", "Something else")
    result = run(make_repo(tmp_path, log=log))
    assert result.returncode == 1
    assert name in result.stdout


def test_fails_when_reason_line_is_missing(tmp_path: Path) -> None:
    log = GOOD_LOG.replace("- Reason: No card needed.\n", "")
    result = run(make_repo(tmp_path, log=log))
    assert result.returncode == 1
    assert 'Decision "Hosting path" has no non-empty "Reason:" line' in result.stdout


def test_fails_when_reason_is_empty(tmp_path: Path) -> None:
    log = GOOD_LOG.replace("- Reason: Arabic and English users.", "- Reason:   ")
    result = run(make_repo(tmp_path, log=log))
    assert result.returncode == 1
    assert "UI language" in result.stdout


def test_fails_on_impossible_date(tmp_path: Path) -> None:
    log = GOOD_LOG.replace(
        "2026-11-01 · Decision: Hosting path", "2026-13-45 · Decision: Hosting path"
    )
    result = run(make_repo(tmp_path, log=log))
    assert result.returncode == 1
    assert "invalid date" in result.stdout


def test_reason_of_one_decision_does_not_count_for_the_next(tmp_path: Path) -> None:
    log = GOOD_LOG.replace("- Reason: No card needed.\n", "")
    result = run(make_repo(tmp_path, log=log))
    assert result.returncode == 1


def test_accepts_windows_line_endings(tmp_path: Path) -> None:
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "LOG.md").write_bytes(GOOD_LOG.replace("\n", "\r\n").encode("utf-8"))
    assert run(tmp_path).returncode == 0
