"""Unit tests for scripts/check_adrs.py (Plan Step 0.5)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_adrs.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("check_adrs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_adrs"] = module
    spec.loader.exec_module(module)
    return module


check_adrs = _load_script()


def write_adr(
    folder: Path,
    number: int,
    slug: str = "decision",
    title: Optional[str] = None,
    status: Optional[str] = "Accepted",
    context: Optional[str] = "Some context.",
    decision: Optional[str] = "Some decision.",
) -> Path:
    """Write a valid ADR. Pass None for a section to leave it out entirely."""
    lines: List[str] = [title if title is not None else "# ADR-%04d: A decision" % number, ""]
    for heading, body in (("Status", status), ("Context", context), ("Decision", decision)):
        if body is None:
            continue
        lines += ["## " + heading, "", body, ""]
    path = folder / ("%04d-%s.md" % (number, slug))
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def problems_for(folder: Path) -> List[str]:
    return check_adrs.check_adr_dir(folder)


def mentions(problems: List[str], *words: str) -> bool:
    return any(all(word in problem for word in words) for problem in problems)


# --- the real repository --------------------------------------------------

def test_real_adr_folder_passes():
    assert problems_for(ROOT / "docs" / "adr") == []


# --- valid cases ----------------------------------------------------------

def test_one_valid_adr_passes(tmp_path):
    write_adr(tmp_path, 1)
    assert problems_for(tmp_path) == []


def test_sequential_adrs_pass(tmp_path):
    for number in (1, 2, 3):
        write_adr(tmp_path, number, slug="decision-%d" % number)
    assert problems_for(tmp_path) == []


def test_other_files_are_ignored(tmp_path):
    write_adr(tmp_path, 1)
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not an adr", encoding="utf-8")
    (tmp_path / "README.md").write_text("no headings here", encoding="utf-8")
    (tmp_path / "TEMPLATE.md").write_text("no headings here", encoding="utf-8")
    assert problems_for(tmp_path) == []


def test_heading_level_and_trailing_colon_are_accepted(tmp_path):
    (tmp_path / "0001-decision.md").write_text(
        "# ADR-0001: A decision\n\n### Status\n\nAccepted\n\n## Context:\n\nText.\n\n# Decision\n\nText.\n",
        encoding="utf-8",
    )
    assert problems_for(tmp_path) == []


def test_windows_line_endings_are_accepted(tmp_path):
    path = write_adr(tmp_path, 1)
    path.write_bytes(path.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))
    assert problems_for(tmp_path) == []


@pytest.mark.parametrize("status", ["Accepted", "accepted", "Proposed", "Superseded by ADR-0004", "Deprecated. Replaced."])
def test_valid_status_values(tmp_path, status):
    write_adr(tmp_path, 1, status=status)
    assert problems_for(tmp_path) == []


# --- missing headings (the Step 0.5 test line) ----------------------------

@pytest.mark.parametrize("section", ["status", "context", "decision"])
def test_missing_required_heading_is_reported(tmp_path, section):
    write_adr(tmp_path, 1, **{section: None})
    problems = problems_for(tmp_path)
    assert mentions(problems, "missing", section.capitalize())


def test_heading_inside_code_fence_does_not_count(tmp_path):
    (tmp_path / "0001-decision.md").write_text(
        "# ADR-0001: A decision\n\n## Status\n\nAccepted\n\n## Context\n\nText.\n\n"
        "```\n## Decision\n\nthis is only an example\n```\n",
        encoding="utf-8",
    )
    assert mentions(problems_for(tmp_path), "missing", "Decision")


@pytest.mark.parametrize("section", ["status", "context", "decision"])
def test_empty_section_is_reported(tmp_path, section):
    write_adr(tmp_path, 1, **{section: "   "})
    assert mentions(problems_for(tmp_path), "empty", section.capitalize())


def test_comment_only_section_counts_as_empty(tmp_path):
    write_adr(tmp_path, 1, context="<!-- write the context here -->")
    assert mentions(problems_for(tmp_path), "empty", "Context")


# --- status value ---------------------------------------------------------

def test_invalid_status_is_reported(tmp_path):
    write_adr(tmp_path, 1, status="Maybe later")
    assert mentions(problems_for(tmp_path), "Status", "Maybe")


# --- title ----------------------------------------------------------------

def test_missing_title_is_reported(tmp_path):
    (tmp_path / "0001-decision.md").write_text(
        "## Status\n\nAccepted\n\n## Context\n\nText.\n\n## Decision\n\nText.\n", encoding="utf-8"
    )
    assert mentions(problems_for(tmp_path), "first line", "title")


def test_text_before_title_is_reported(tmp_path):
    (tmp_path / "0001-decision.md").write_text(
        "Some intro\n\n# ADR-0001: A decision\n\n## Status\n\nAccepted\n\n## Context\n\nText.\n\n## Decision\n\nText.\n",
        encoding="utf-8",
    )
    assert mentions(problems_for(tmp_path), "first line", "title")


def test_title_with_wrong_format_is_reported(tmp_path):
    write_adr(tmp_path, 1, title="# Ports and adapters")
    assert mentions(problems_for(tmp_path), "title", "ADR-0001")


def test_title_number_must_match_file_number(tmp_path):
    write_adr(tmp_path, 1, title="# ADR-0003: Wrong number")
    assert mentions(problems_for(tmp_path), "ADR-0003", "0001")


# --- file names and numbering ---------------------------------------------

@pytest.mark.parametrize("bad_name", ["adr-1.md", "1-short.md", "0002-Bad-Name.md", "0002_underscore.md", "0002-.md"])
def test_bad_file_name_is_reported(tmp_path, bad_name):
    write_adr(tmp_path, 1)
    (tmp_path / bad_name).write_text("# x\n", encoding="utf-8")
    assert mentions(problems_for(tmp_path), bad_name, "file name")


def test_numbering_gap_is_reported(tmp_path):
    write_adr(tmp_path, 1)
    write_adr(tmp_path, 4, slug="later")
    problems = problems_for(tmp_path)
    assert mentions(problems, "gap", "0001", "0004", "0002", "0003")


def test_duplicate_number_is_reported(tmp_path):
    write_adr(tmp_path, 1)
    write_adr(tmp_path, 2, slug="first")
    write_adr(tmp_path, 2, slug="second")
    assert mentions(problems_for(tmp_path), "0002", "more than one file")


def test_numbering_must_start_at_one(tmp_path):
    write_adr(tmp_path, 2)
    assert mentions(problems_for(tmp_path), "start at 0001", "0002")


# --- empty or missing folder ----------------------------------------------

def test_missing_folder_is_reported(tmp_path):
    assert mentions(problems_for(tmp_path / "nope"), "does not exist")


def test_folder_without_adrs_is_reported(tmp_path):
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
    assert mentions(problems_for(tmp_path), "no ADR files")


# --- command line ---------------------------------------------------------

def test_main_returns_zero_and_prints_count(tmp_path, capsys):
    write_adr(tmp_path, 1)
    assert check_adrs.main(["--adr-dir", str(tmp_path)]) == 0
    assert "1 ADR file" in capsys.readouterr().out


def test_main_returns_one_and_lists_problems(tmp_path, capsys):
    write_adr(tmp_path, 1, decision=None)
    assert check_adrs.main(["--adr-dir", str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert "ADR check failed" in output
    assert "missing the 'Decision' heading" in output


def test_main_rejects_unknown_option():
    with pytest.raises(SystemExit) as error:
        check_adrs.main(["--nope"])
    assert error.value.code == 2