"""Fake sleeper implementation for zero-delay deterministic testing."""

from __future__ import annotations


class FakeSleeper:
    """Deterministic in-memory sleeper that records requested sleep durations without delay."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    @property
    def total_slept(self) -> float:
        """Total seconds accumulated across all sleep calls."""
        return sum(self.sleeps)

    async def sleep(self, seconds: float) -> None:
        """Record the sleep without pausing execution."""
        self.sleeps.append(seconds)
