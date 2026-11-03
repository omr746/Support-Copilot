"""Root conftest — skips live tests unless COPILOT_RUN_LIVE=1."""
from __future__ import annotations
import os
import pytest

def pytest_runtest_setup(item: pytest.Item) -> None:
    if "live" in item.keywords and os.environ.get("COPILOT_RUN_LIVE") != "1":
        pytest.skip("Set COPILOT_RUN_LIVE=1 to run live provider tests.")