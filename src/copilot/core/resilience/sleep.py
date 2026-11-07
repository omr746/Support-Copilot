"""Sleeper abstraction for dependency injection and deterministic clock control."""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable


@runtime_checkable
class Sleeper(Protocol):
    """Protocol for asynchronous sleeping, enabling clock dependency injection."""

    async def sleep(self, seconds: float) -> None:
        """Asynchronously sleep for the given number of seconds."""
        ...


class AsyncioSleeper:
    """Production sleeper delegating directly to asyncio.sleep."""

    async def sleep(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds)
