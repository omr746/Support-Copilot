"""Fallback ChatModel wrapper executing a sequential chain of providers."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from copilot.core.errors import InvalidInputError, ProviderError
from copilot.llm.base import ChatModel
from copilot.llm.schemas import ChatRequest, ChatResponse

logger = logging.getLogger("copilot.llm.wrappers.fallback")


def default_should_fallback(exc: Exception) -> bool:
    """Determine whether a provider exception warrants falling back to the next provider.

    By default:
      - ProviderError (RateLimitError, ProviderTimeout, ProviderError) triggers fallback.
      - InvalidInputError (bad user request) NEVER triggers fallback.
      - Programming bugs (TypeError, etc.) NEVER trigger fallback.
    """
    if isinstance(exc, InvalidInputError):
        return False
    if isinstance(
        exc,
        (
            TypeError,
            AttributeError,
            KeyError,
            ValueError,
            IndexError,
            NotImplementedError,
            AssertionError,
        ),
    ):
        return False
    return isinstance(exc, ProviderError)


class FallbackChatModel:
    """Wraps a sequence of ChatModels to fall back when earlier providers fail."""

    def __init__(
        self,
        models: Sequence[ChatModel],
        should_fallback: Callable[[Exception], bool] | None = None,
    ) -> None:
        if not models:
            raise ValueError("FallbackChatModel requires at least one ChatModel")
        self._models: list[ChatModel] = list(models)
        self._should_fallback = should_fallback or default_should_fallback

    @property
    def models(self) -> tuple[ChatModel, ...]:
        """Underlying models configured in the fallback chain."""
        return tuple(self._models)

    @property
    def providers(self) -> tuple[ChatModel, ...]:
        """Sequence of underlying models in the fallback chain (alias for models)."""
        return self.models

    @property
    def provider_name(self) -> str:
        """Name of the primary provider in the fallback chain."""
        return self._models[0].provider_name

    async def complete(self, request: ChatRequest) -> ChatResponse:
        """Try each model in order. Fall back when eligible error occurs.

        Raises the last error if all providers in the chain fail.
        Records which provider answered in the returned ChatResponse.
        """
        last_error: Exception | None = None

        for idx, model in enumerate(self._models):
            try:
                response = await model.complete(request)
                if idx > 0:
                    logger.info(
                        "LLM completion succeeded via fallback provider",
                        extra={
                            "provider": response.provider,
                            "fallback_used": True,
                            "chain_index": idx,
                        },
                    )
                return response
            except Exception as exc:
                last_error = exc
                is_last = idx == len(self._models) - 1

                if is_last or not self._should_fallback(exc):
                    if idx > 0:
                        logger.error(
                            "LLM fallback chain exhausted",
                            extra={
                                "failed_provider": model.provider_name,
                                "chain_length": len(self._models),
                                "error": type(exc).__name__,
                            },
                        )
                    raise exc

                next_model = self._models[idx + 1]
                logger.warning(
                    "LLM provider failed; falling back to next provider",
                    extra={
                        "failed_provider": model.provider_name,
                        "next_provider": next_model.provider_name,
                        "error": type(exc).__name__,
                        "fallback_used": True,
                    },
                )

        if last_error is not None:
            raise last_error
        raise RuntimeError("FallbackChatModel exhausted without result or error")

    async def aclose(self) -> None:
        """Release resources across all models in the fallback chain."""
        for model in self._models:
            await model.aclose()
