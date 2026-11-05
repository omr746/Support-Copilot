"""OpenAI-compatible ChatModel provider adapter (supports Groq, vLLM, Ollama, etc.)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import httpx
import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

from copilot.core.errors import (
    AuthError,
    ProviderError,
    ProviderTimeout,
    RateLimitError,
)
from copilot.llm.base import ChatModel
from copilot.llm.registry import llm_registry
from copilot.llm.schemas import (
    ChatRequest,
    ChatResponse,
    FinishReason,
    TokenUsage,
)

if TYPE_CHECKING:
    from copilot.core.config import AppConfig


class OpenAICompatibleChatModel(ChatModel):
    """ChatModel adapter translating between Support Copilot schemas and OpenAI-compatible APIs."""

    def __init__(
        self,
        provider_name: str,
        base_url: str,
        default_model: str,
        api_key: str = "",
        timeout: float = 30.0,
        http_client: httpx.AsyncClient | Any = None,
    ) -> None:
        self._provider_name = provider_name
        self.base_url = base_url
        self.default_model = default_model
        self.timeout = timeout
        self._owns_http_client = http_client is None

        effective_key = api_key or "mock-key-for-testing"
        self._client = AsyncOpenAI(
            api_key=effective_key,
            base_url=base_url,
            timeout=timeout,
            http_client=cast(Any, http_client),
            max_retries=0,
        )

    @property
    def provider_name(self) -> str:
        return self._provider_name

    async def aclose(self) -> None:
        """Close underlying HTTP client only if owned by this provider instance."""
        if self._owns_http_client:
            await self._client.close()

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        provider_name: str | None = None,
        http_client: httpx.AsyncClient | Any = None,
    ) -> OpenAICompatibleChatModel:
        """Construct an adapter instance with configuration resolved from AppConfig."""
        p_name = provider_name or config.llm.default_provider
        provider_cfg = config.llm.providers.get(p_name)
        if not provider_cfg:
            available = ", ".join(sorted(config.llm.providers)) or "none"
            raise ValueError(
                f"Provider '{p_name}' not configured in LLM providers. Available: [{available}]"
            )

        api_key = getattr(config, f"{p_name}_api_key", "")

        return cls(
            provider_name=p_name,
            base_url=provider_cfg.base_url,
            default_model=provider_cfg.default_model,
            api_key=api_key,
            timeout=provider_cfg.timeout,
            http_client=http_client,
        )

    async def complete(self, request: ChatRequest) -> ChatResponse:
        """Execute chat completion and map response or errors."""
        model = request.model or self.default_model

        messages: list[dict[str, Any]] = []
        for msg in request.messages:
            entry: dict[str, Any] = {
                "role": msg.role,
                "content": msg.content,
            }
            if msg.name:
                entry["name"] = msg.name
            messages.append(entry)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        if request.stop is not None:
            kwargs["stop"] = list(request.stop)

        try:
            raw_response: ChatCompletion = await self._client.chat.completions.create(**kwargs)
        except (openai.APIError, httpx.HTTPError, json.JSONDecodeError) as exc:
            raise self._map_error(exc) from exc

        return self._map_response(raw_response)

    def _map_response(self, raw: ChatCompletion) -> ChatResponse:
        """Map raw OpenAI ChatCompletion to internal ChatResponse schema."""
        if not raw.choices:
            raise ProviderError(
                "Provider returned empty choices in completion",
                provider=self.provider_name,
            )

        choice = raw.choices[0]
        content = choice.message.content or ""
        if not content:
            # Handle reasoning/thinking models where output might reside in reasoning_content
            reasoning = getattr(choice.message, "reasoning_content", None) or getattr(
                choice.message, "reasoning", None
            )
            if reasoning and isinstance(reasoning, str):
                content = reasoning

        raw_reason = choice.finish_reason or "stop"
        finish_reason: FinishReason = "stop"
        if raw_reason in ("stop", "length", "tool_calls", "content_filter"):
            finish_reason = raw_reason  # type: ignore[assignment]

        usage = TokenUsage()
        if raw.usage:
            prompt_tokens = raw.usage.prompt_tokens or 0
            completion_tokens = raw.usage.completion_tokens or 0
            total_tokens = raw.usage.total_tokens
            if not total_tokens:
                total_tokens = prompt_tokens + completion_tokens

            usage = TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )

        return ChatResponse(
            content=content,
            model=raw.model or self.default_model,
            provider=self.provider_name,
            usage=usage,
            finish_reason=finish_reason,
        )

    def _map_error(
        self, exc: openai.APIError | httpx.HTTPError | json.JSONDecodeError
    ) -> Exception:
        """Translate third-party vendor exceptions to core CopilotError domain errors."""
        # 1. Malformed JSON response body from provider
        if isinstance(exc, json.JSONDecodeError):
            return ProviderError(
                f"Malformed JSON response body from provider: {exc}",
                provider=self.provider_name,
            )

        # 2. Timeout errors
        if isinstance(exc, (openai.APITimeoutError, httpx.TimeoutException)):
            return ProviderTimeout(str(exc), provider=self.provider_name)

        # 3. Rate limit errors (429)
        if isinstance(exc, openai.RateLimitError):
            retry_after: str | float | None = None
            if exc.response is not None and hasattr(exc.response, "headers"):
                retry_after = exc.response.headers.get("retry-after")
                if retry_after is None:
                    retry_ms = exc.response.headers.get("retry-after-ms")
                    if retry_ms:
                        try:
                            retry_after = float(retry_ms) / 1000.0
                        except ValueError:
                            pass
            return RateLimitError(str(exc), provider=self.provider_name, retry_after=retry_after)

        # 4. Authentication & Permission errors (401, 403)
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return AuthError(str(exc), provider=self.provider_name)

        # 5. Status code inspections from generic APIStatusError
        status_code = getattr(exc, "status_code", None)
        if status_code == 429:
            response = getattr(exc, "response", None)
            headers = getattr(response, "headers", {}) if response else {}
            return RateLimitError(
                str(exc), provider=self.provider_name, retry_after=headers.get("retry-after")
            )
        if status_code in (401, 403):
            return AuthError(str(exc), provider=self.provider_name)

        # 6. Malformed body, response validation, bad request, or server errors
        return ProviderError(str(exc), provider=self.provider_name)


# Register in the global LLM registry under canonical names
llm_registry.register("openai_compatible", OpenAICompatibleChatModel)
llm_registry.register("groq", OpenAICompatibleChatModel)
