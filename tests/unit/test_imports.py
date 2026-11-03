"""Step 1.1 tests — skeleton, imports, extras isolation, live marker guard."""

from __future__ import annotations

import importlib
import sys

import pytest

SUBPACKAGES = [
    "copilot",
    "copilot.core",
    "copilot.llm",
    "copilot.asr",
    "copilot.tts",
    "copilot.rag",
    "copilot.agent",
    "copilot.voice",
    "copilot.observability",
    "copilot.api",
]


@pytest.mark.parametrize("module_name", SUBPACKAGES, ids=lambda m: m)
def test_subpackage_importable(module_name: str) -> None:
    importlib.import_module(module_name)


def test_copilot_version_defined() -> None:
    import copilot

    assert isinstance(copilot.__version__, str) and copilot.__version__


_OPTIONAL_MODULES = frozenset({"faster_whisper", "piper", "datasets", "torch", "sklearn"})


def test_optional_extras_not_leaked() -> None:
    for pkg in SUBPACKAGES:
        importlib.import_module(pkg)
    top_level = {m.split(".")[0] for m in sys.modules}
    leaked = _OPTIONAL_MODULES & top_level
    assert not leaked, f"Optional extras leaked into default import: {sorted(leaked)}"


@pytest.mark.live
def test_live_marker_is_skipped_by_default() -> None:  # pragma: no cover
    pytest.fail("A @live test ran without COPILOT_RUN_LIVE=1 — guard is broken.")
