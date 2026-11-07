"""Test doubles (fakes) for external service ports."""

from tests.fakes.fake_llm import FakeLLM
from tests.fakes.fake_sleeper import FakeSleeper

__all__ = ["FakeLLM", "FakeSleeper"]
