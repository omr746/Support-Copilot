"""Composition root for Support Copilot (ADR-0001 Plan Step 1.6).

Builds and manages the application object graph, model factories, dependency
injection bindings, and readiness health probes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, Request

from copilot.core.config import AppConfig
from copilot.llm.base import ChatModel
from copilot.llm.providers.openai_compatible import OpenAICompatibleChatModel
from copilot.llm.schemas import ModelRole
from copilot.llm.wrappers import FallbackChatModel, RetryingChatModel

if TYPE_CHECKING:
    pass

logger = logging.getLogger("copilot.api.container")

ReadinessCheck = Callable[[], Awaitable[bool] | bool]


class Container:
    """Dependency injection container and composition root."""

    def __init__(
        self,
        config: AppConfig | None = None,
        answer_model: ChatModel | None = None,
        readiness_checks: dict[str, ReadinessCheck] | None = None,
    ) -> None:
        self.config: AppConfig = config if config is not None else AppConfig.load()
        self._answer_model: ChatModel | None = answer_model
        self._readiness_checks: dict[str, ReadinessCheck] = dict(readiness_checks or {})
        # Container owns root model chains; wrappers own leaf adapters
        self._root_models: list[ChatModel] = []
        if answer_model is not None:
            self._root_models.append(answer_model)

    def register_readiness_check(self, name: str, check: ReadinessCheck) -> None:
        """Register a named dependency readiness check for /readyz."""
        self._readiness_checks[name] = check

    def unregister_readiness_check(self, name: str) -> None:
        """Remove a named readiness check."""
        self._readiness_checks.pop(name, None)

    async def check_readiness(self) -> tuple[bool, dict[str, Any]]:
        """Run all registered readiness checks.

        Returns:
            (is_ready, details_dict) where is_ready is True only if all checks evaluate to True.
            Raw exception details are logged internally to avoid leaking credentials or hostnames.
        """
        if not self._readiness_checks:
            return True, {"app": "ok"}

        details: dict[str, Any] = {}
        all_ok = True

        for name, check_fn in self._readiness_checks.items():
            try:
                result = check_fn()
                if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                    result = await result  # type: ignore[misc]

                if bool(result):
                    details[name] = "ok"
                else:
                    logger.warning("Readiness probe check '%s' reported unhealthy", name)
                    details[name] = "down"
                    all_ok = False
            except Exception as exc:
                # Security: log full exception internally; never expose error string/credentials to callers
                logger.warning(
                    "Readiness probe check '%s' raised exception: %s: %s",
                    name,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                details[name] = "down"
                all_ok = False

        return all_ok, details

    def get_answer_model(self) -> ChatModel:
        """Resolve or build the ChatModel configured for the 'answer' role."""
        if self._answer_model is not None:
            return self._answer_model

        model = self.get_model_for_role("answer")
        self._answer_model = model
        return model

    def get_model_for_role(self, role: ModelRole) -> ChatModel:
        """Construct the production resilient provider chain for a specified model role.

        Builds primary retrying provider with fallback to secondary provider from config.
        """
        target_model = self.config.llm.roles.get(role)
        primary_name = self.config.llm.default_provider

        # 1. Primary provider
        primary_adapter = OpenAICompatibleChatModel.from_config(self.config, primary_name)
        if target_model:
            primary_adapter.default_model = target_model
        primary_retrying = RetryingChatModel(primary_adapter, max_attempts=3)

        # 2. Secondary fallback provider if configured in LLMConfig
        chain: list[ChatModel] = [primary_retrying]
        fallback_name = self.config.llm.fallback_provider
        if fallback_name:
            if fallback_name not in self.config.llm.providers:
                logger.warning(
                    "Configured fallback provider '%s' not found in LLM providers config; fallback disabled",
                    fallback_name,
                )
            else:
                try:
                    fallback_adapter = OpenAICompatibleChatModel.from_config(
                        self.config, fallback_name
                    )
                    if self.config.llm.fallback_model:
                        fallback_adapter.default_model = self.config.llm.fallback_model
                    fallback_retrying = RetryingChatModel(fallback_adapter, max_attempts=3)
                    chain.append(fallback_retrying)
                except Exception as exc:
                    logger.warning(
                        "Failed to initialize fallback provider '%s'; continuing with primary only: %s: %s",
                        fallback_name,
                        type(exc).__name__,
                        exc,
                        exc_info=True,
                    )

        root_model: ChatModel
        if len(chain) > 1:
            root_model = FallbackChatModel(chain)
        else:
            root_model = primary_retrying

        self._root_models.append(root_model)
        return root_model

    async def aclose(self) -> None:
        """Cleanly close all instantiated root models and associated resources.

        Ownership model:
        Container owns root model chains (or injected answer_model). Closing the root
        model delegates recursively down the wrapper hierarchy to the underlying leaf
        adapters, ensuring every adapter's HTTP client is closed exactly once (no double-close).
        """
        models_to_close = list(self._root_models)
        self._root_models.clear()
        self._answer_model = None

        for model in models_to_close:
            if hasattr(model, "aclose"):
                try:
                    await model.aclose()
                except Exception as exc:
                    logger.warning(
                        "Error during model cleanup in container.aclose: %s: %s",
                        type(exc).__name__,
                        exc,
                    )


def get_container(request: Request) -> Container:
    """FastAPI dependency to retrieve the Container from application state."""
    container = getattr(request.app.state, "container", None)
    if container is None:
        cfg = getattr(request.app.state, "config", None) or AppConfig.load()
        container = Container(config=cfg)
        request.app.state.container = container
    return container


def get_answer_model(
    container: Annotated[Container, Depends(get_container)],
) -> ChatModel:
    """FastAPI dependency to retrieve the ChatModel for the 'answer' role."""
    return container.get_answer_model()
