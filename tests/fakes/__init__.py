"""Test doubles (fakes) for external service ports."""

try:
    from tests.fakes.fake_llm import FakeLLM
except ImportError:
    pass

try:
    from tests.fakes.fake_sleeper import FakeSleeper
except ImportError:
    pass

from tests.fakes.fake_token_counter import FakeCharTokenCounter, FakeWordTokenCounter

__all__ = [
    "FakeCharTokenCounter",
    "FakeLLM",
    "FakeSleeper",
    "FakeWordTokenCounter",
]
