"""Contract tests for ChatModel providers with mocked HTTP and live execution (Plan Step 1.4)."""

from __future__ import annotations

import email.utils
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import httpx
import openai
import pytest

# Ensure providers are loaded into the registry
import copilot.llm.providers.openai_compatible  # noqa: F401
from copilot.core.config import AppConfig
from copilot.core.errors import (
    AuthError,
    ProviderError,
    ProviderTimeout,
    RateLimitError,
)
from copilot.llm.base import ChatModel
from copilot.llm.providers.openai_compatible import OpenAICompatibleChatModel
from copilot.llm.registry import llm_registry
from copilot.llm.schemas import ChatMessage, ChatRequest, ChatResponse
from copilot.llm.wrappers import FallbackChatModel

REGISTERED_PROVIDERS = llm_registry.registered_names()


def _make_mock_client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _instantiate_provider(provider_name: str, client: httpx.AsyncClient) -> ChatModel:
    return llm_registry.create(
        provider_name,
        provider_name=provider_name,
        base_url="https://api.mock.test/v1",
        default_model="llama-3.3-70b-versatile",
        api_key="mock-test-key",
        http_client=client,
    )


@pytest.mark.parametrize("provider_name", REGISTERED_PROVIDERS)
@pytest.mark.asyncio
async def test_contract_success_maps_to_valid_response(provider_name: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test-123",
                "object": "chat.completion",
                "created": 1677652288,
                "model": "llama-3.3-70b-versatile",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Welcome to Support Copilot!",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 6,
                    "total_tokens": 18,
                },
            },
        )

    client = _make_mock_client(handler)
    model = _instantiate_provider(provider_name, client)

    req = ChatRequest(
        messages=(ChatMessage(role="user", content="Hello"),),
        temperature=0.5,
    )

    response = await model.complete(req)

    assert isinstance(response, ChatResponse)
    assert response.content == "Welcome to Support Copilot!"
    assert response.model == "llama-3.3-70b-versatile"
    assert response.provider == provider_name
    assert response.usage.prompt_tokens == 12
    assert response.usage.completion_tokens == 6
    assert response.usage.total_tokens == 18
    assert response.finish_reason == "stop"


@pytest.mark.parametrize("provider_name", REGISTERED_PROVIDERS)
@pytest.mark.asyncio
async def test_contract_429_becomes_rate_limit_error_with_numeric_retry_after(
    provider_name: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": "45.0"},
            json={
                "error": {
                    "message": "Rate limit exceeded. Try again in 45s.",
                    "type": "tokens",
                    "code": "rate_limit_exceeded",
                }
            },
        )

    client = _make_mock_client(handler)
    model = _instantiate_provider(provider_name, client)

    req = ChatRequest(messages=(ChatMessage(role="user", content="Hello"),))

    with pytest.raises(RateLimitError) as exc_info:
        await model.complete(req)

    err = exc_info.value
    assert err.provider == provider_name
    assert err.retry_after == 45.0
    assert err.raw_retry_after == "45.0"


@pytest.mark.parametrize("provider_name", REGISTERED_PROVIDERS)
@pytest.mark.asyncio
async def test_contract_429_becomes_rate_limit_error_with_date_retry_after(
    provider_name: str,
) -> None:
    future_dt = datetime.now(timezone.utc) + timedelta(seconds=60)
    date_str = email.utils.format_datetime(future_dt, usegmt=True)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": date_str},
            json={
                "error": {
                    "message": "Quota limit reached. Retry after specified timestamp.",
                    "type": "tokens",
                }
            },
        )

    client = _make_mock_client(handler)
    model = _instantiate_provider(provider_name, client)

    req = ChatRequest(messages=(ChatMessage(role="user", content="Hello"),))

    with pytest.raises(RateLimitError) as exc_info:
        await model.complete(req)

    err = exc_info.value
    assert err.provider == provider_name
    assert err.raw_retry_after == date_str
    assert err.retry_after is not None
    assert 45.0 <= err.retry_after <= 75.0


@pytest.mark.parametrize("provider_name", REGISTERED_PROVIDERS)
@pytest.mark.asyncio
async def test_contract_timeout_becomes_provider_timeout(
    provider_name: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Socket read timed out", request=request)

    client = _make_mock_client(handler)
    model = _instantiate_provider(provider_name, client)

    req = ChatRequest(messages=(ChatMessage(role="user", content="Hello"),))

    with pytest.raises(ProviderTimeout) as exc_info:
        await model.complete(req)

    assert exc_info.value.provider == provider_name


@pytest.mark.parametrize("provider_name", REGISTERED_PROVIDERS)
@pytest.mark.asyncio
async def test_contract_401_becomes_auth_error(
    provider_name: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "message": "Invalid API Key provided.",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            },
        )

    client = _make_mock_client(handler)
    model = _instantiate_provider(provider_name, client)

    req = ChatRequest(messages=(ChatMessage(role="user", content="Hello"),))

    with pytest.raises(AuthError) as exc_info:
        await model.complete(req)

    assert exc_info.value.provider == provider_name


@pytest.mark.parametrize("provider_name", REGISTERED_PROVIDERS)
@pytest.mark.asyncio
async def test_contract_malformed_body_becomes_provider_error(
    provider_name: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"this is corrupt non-json output {",
        )

    client = _make_mock_client(handler)
    model = _instantiate_provider(provider_name, client)

    req = ChatRequest(messages=(ChatMessage(role="user", content="Hello"),))

    with pytest.raises(ProviderError) as exc_info:
        await model.complete(req)

    assert exc_info.value.provider == provider_name


@pytest.mark.asyncio
async def test_shared_http_client_is_not_closed_by_provider() -> None:
    shared_client = httpx.AsyncClient()
    try:
        model = OpenAICompatibleChatModel(
            provider_name="groq",
            base_url="https://api.groq.com/openai/v1",
            default_model="llama-3.3-70b-versatile",
            api_key="test-key",
            http_client=shared_client,
        )
        assert model._owns_http_client is False
        await model.aclose()
        # External client remains open for other providers or app lifecycle
        assert not shared_client.is_closed
    finally:
        await shared_client.aclose()


@pytest.mark.asyncio
async def test_internal_http_client_is_closed_by_aclose() -> None:
    model = OpenAICompatibleChatModel(
        provider_name="groq",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        api_key="test-key",
    )
    assert model._owns_http_client is True
    await model.aclose()
    assert model._client._client.is_closed


@pytest.mark.asyncio
async def test_code_bugs_in_mapping_are_not_masked_as_provider_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "test-123",
                "object": "chat.completion",
                "created": 1677652288,
                "model": "llama-3.3-70b-versatile",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = _make_mock_client(handler)
    model = OpenAICompatibleChatModel(
        provider_name="groq",
        base_url="https://api.mock.test/v1",
        default_model="llama-3.3-70b-versatile",
        api_key="mock-key",
        http_client=client,
    )

    req = ChatRequest(messages=(ChatMessage(role="user", content="Hello"),))

    # Simulate an internal bug inside _map_response (e.g. AttributeError or TypeError)
    with patch.object(
        model,
        "_map_response",
        side_effect=TypeError("Simulated internal bug in developer mapping code"),
    ):
        with pytest.raises(TypeError, match="Simulated internal bug"):
            await model.complete(req)


def test_from_config_resolves_all_parameters_consistently() -> None:
    config = AppConfig(
        groq_api_key="gsk_custom_test_key_123",
        gemini_api_key="gemini_custom_test_key_456",
    )
    groq_model = OpenAICompatibleChatModel.from_config(config, "groq")
    assert groq_model.provider_name == "groq"
    assert groq_model.base_url == "https://api.groq.com/openai/v1"
    assert groq_model.default_model == "openai/gpt-oss-20b"

    gemini_model = OpenAICompatibleChatModel.from_config(config, "gemini")
    assert gemini_model.provider_name == "gemini"
    assert gemini_model.base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert gemini_model.default_model == "gemini-3.1-flash-lite"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_groq_manual_call() -> None:
    """Manual live verification call against Groq API. Requires COPILOT_RUN_LIVE=1."""
    config = AppConfig.load()
    if not config.groq_api_key or config.groq_api_key.startswith("gsk_placeholder"):
        pytest.skip("Valid GROQ_API_KEY required in .env for live test.")

    model = OpenAICompatibleChatModel.from_config(config, "groq")
    try:
        target_model = model.default_model
        try:
            models_page = await model._client.models.list()
            active_ids = {m.id for m in models_page.data}
            chat_models = [
                mid
                for mid in sorted(active_ids)
                if not any(
                    excl in mid.lower()
                    for excl in ("guard", "whisper", "orpheus", "embed", "safeguard")
                )
            ]
            for candidate in (
                model.default_model,
                "openai/gpt-oss-20b",
                "openai/gpt-oss-120b",
                "qwen/qwen3.8-27b",
                "allam-2-7b",
            ):
                if candidate in chat_models:
                    target_model = candidate
                    break
            else:
                if chat_models:
                    target_model = chat_models[0]
                elif active_ids:
                    target_model = next(iter(active_ids))
        except Exception:
            pass

        req = ChatRequest(
            messages=(ChatMessage(role="user", content="Ping"),),
            model=target_model,
            temperature=0.0,
            max_tokens=50,
        )

        response = await model.complete(req)

        assert isinstance(response, ChatResponse)
        assert len(response.content.strip()) > 0
        assert response.provider == "groq"
        assert response.usage.total_tokens > 0 or response.usage.prompt_tokens > 0
    finally:
        await model.aclose()


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_gemini_manual_call() -> None:
    """Manual live verification call against Gemini API. Requires COPILOT_RUN_LIVE=1."""
    config = AppConfig.load()
    if not config.gemini_api_key or config.gemini_api_key.startswith("your_"):
        pytest.skip("Valid GEMINI_API_KEY required in .env for live test.")

    model = OpenAICompatibleChatModel.from_config(config, "gemini")
    try:
        last_err: Exception | None = None
        for candidate_model in (
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash-lite",
            "gemini-3.8-flash",
            "gemini-3.6-flash",
        ):
            try:
                req = ChatRequest(
                    messages=(ChatMessage(role="user", content="Ping"),),
                    model=candidate_model,
                    temperature=0.0,
                    max_tokens=100,
                )
                response = await model.complete(req)
                assert isinstance(response, ChatResponse)
                assert len(response.content.strip()) > 0 or response.usage.total_tokens > 0
                assert response.provider == "gemini"
                break
            except (ProviderError, openai.APIError) as exc:
                last_err = exc
        else:
            if last_err:
                raise last_err
    finally:
        await model.aclose()


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_fallback_groq_to_gemini_chain() -> None:
    """Manual live verification of fallback chain (Groq fail -> Gemini answer). Requires COPILOT_RUN_LIVE=1."""
    config = AppConfig.load()
    if not config.gemini_api_key or config.gemini_api_key.startswith("your_"):
        pytest.skip("Valid GEMINI_API_KEY required in .env for live test.")

    groq_model = OpenAICompatibleChatModel.from_config(config, "groq")
    groq_model.default_model = "non-existent-model-to-trigger-fallback"
    gemini_model = OpenAICompatibleChatModel.from_config(config, "gemini")

    fallback_chain = FallbackChatModel([groq_model, gemini_model])
    try:
        last_err: Exception | None = None
        for candidate_model in (
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash-lite",
            "gemini-3.8-flash",
            "gemini-3.6-flash",
        ):
            try:
                gemini_model.default_model = candidate_model
                req = ChatRequest(
                    messages=(ChatMessage(role="user", content="Ping"),),
                    temperature=0.0,
                    max_tokens=100,
                )
                response = await fallback_chain.complete(req)
                assert isinstance(response, ChatResponse)
                assert response.provider == "gemini"
                assert len(response.content.strip()) > 0 or response.usage.total_tokens > 0
                break
            except (ProviderError, openai.APIError) as exc:
                last_err = exc
        else:
            if last_err:
                raise last_err
    finally:
        await fallback_chain.aclose()
