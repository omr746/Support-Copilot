"""Retrying ChatModel wrapper with exponential backoff, jitter, and retry-after support."""

from __future__ import annotations

import logging
import random
from collections.abc import Callable

from copilot.core.errors import RateLimitError
from copilot.core.resilience.retry import is_transient_error
from copilot.core.resilience.sleep import AsyncioSleeper, Sleeper
from copilot.llm.base import ChatModel
from copilot.llm.schemas import ChatRequest, ChatResponse

logger = logging.getLogger("copilot.llm.wrappers.retrying")


class RetryingChatModel:
    """Wraps a ChatModel to retry transient failures with exponential backoff and jitter."""

    def __init__(
        self,
        model: ChatModel,
        max_attempts: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 60.0,
        backoff_factor: float = 2.0,
        jitter: bool = True,
        sleeper: Sleeper | None = None,
        is_transient: Callable[[Exception], bool] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if base_delay < 0:
            raise ValueError("base_delay must be non-negative")
        if max_delay < base_delay:
            raise ValueError("max_delay must be >= base_delay")
        if backoff_factor < 1.0:
            raise ValueError("backoff_factor must be >= 1.0")

        self._model = model
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
        self.jitter = jitter
        self._sleeper: Sleeper = sleeper if sleeper is not None else AsyncioSleeper()
        self._is_transient = is_transient or is_transient_error

    @property
    def provider_name(self) -> str:
        return self._model.provider_name

    def compute_delay(self, attempt: int, exc: Exception) -> float:
        """Compute backoff delay for the attempt that just failed (1-based index).

        If exc is RateLimitError and has retry_after:
          - Strictly respects retry_after as the mandatory minimum wait time (never reducing it).
          - If jitter is True, adds a small positive jitter buffer to avoid thundering herd.
        Otherwise:
          - Exponential backoff: delay = min(max_delay, base_delay * (backoff_factor ** (attempt - 1)))
          - If jitter is True, applies full jitter: random.uniform(0.0, delay).
        """
        if isinstance(exc, RateLimitError) and exc.retry_after is not None:
            delay = min(self.max_delay, max(0.0, exc.retry_after))
            if self.jitter and delay < self.max_delay:
                # Add small positive jitter (0 to 10% of delay, max 1.0s) without violating retry_after minimum
                jitter_buffer = min(
                    self.max_delay - delay,
                    random.uniform(0.0, min(1.0, max(0.1, delay * 0.1))),
                )
                delay += jitter_buffer
            return delay

        delay = min(self.max_delay, self.base_delay * (self.backoff_factor ** (attempt - 1)))
        if self.jitter and delay > 0:
            delay = random.uniform(0.0, delay)

        return delay

    async def complete(self, request: ChatRequest) -> ChatResponse:
        """Execute chat completion with automatic retries on transient errors."""
        attempt = 1
        while True:
            try:
                response = await self._model.complete(request)
                if attempt > 1:
                    logger.info(
                        "LLM call succeeded after retries",
                        extra={
                            "provider": response.provider,
                            "attempts": attempt,
                        },
                    )
                return response
            except Exception as exc:
                if not self._is_transient(exc):
                    logger.warning(
                        "LLM call encountered non-retryable error; failing immediately",
                        extra={
                            "provider": self.provider_name,
                            "attempt": attempt,
                            "error": type(exc).__name__,
                            "reason": "non_retryable_error",
                        },
                    )
                    raise

                if attempt >= self.max_attempts:
                    logger.warning(
                        "LLM call failed and retries exhausted",
                        extra={
                            "provider": self.provider_name,
                            "attempts": attempt,
                            "error": type(exc).__name__,
                            "reason": "retry_exhausted",
                        },
                    )
                    raise

                delay = self.compute_delay(attempt, exc)
                logger.warning(
                    "LLM call transient failure; backing off and retrying",
                    extra={
                        "provider": self.provider_name,
                        "attempt": attempt,
                        "error": type(exc).__name__,
                        "delay": round(delay, 3),
                    },
                )
                await self._sleeper.sleep(delay)
                attempt += 1

    async def aclose(self) -> None:
        """Release underlying resources of the wrapped model."""
        await self._model.aclose()
