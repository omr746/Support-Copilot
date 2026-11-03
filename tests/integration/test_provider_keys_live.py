"""Live test: every provider key in .env exists and works (Plan Step 0.2, Tests line).

Marked `live`: it makes real network calls, so it never runs in CI by default.
It also needs COPILOT_RUN_LIVE=1, so a plain `pytest` can never call providers by accident.

Run:  COPILOT_RUN_LIVE=1 pytest tests/integration/test_provider_keys_live.py -m live -s
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_keys_live.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("check_keys_live", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_keys_live"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(
    os.environ.get("COPILOT_RUN_LIVE") != "1",
    reason="live test: set COPILOT_RUN_LIVE=1 to call real providers",
)
def test_provider_keys_exist_and_work() -> None:
    checker = _load_script()
    results = checker.run_checks()
    failures = [f"{r.name}: {r.message}" for r in results if not r.ok]
    assert not failures, "\n" + "\n".join(failures)
