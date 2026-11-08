"""Unit tests for FastAPI application, probes, and composition root (Plan Step 1.6)."""

from __future__ import annotations

import logging
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from copilot.api.container import Container, get_answer_model, get_container
from copilot.api.main import create_app
from copilot.core.config import AppConfig
from copilot.core.errors import ProviderError, ProviderTimeout, RateLimitError
from copilot.llm.base import ChatModel
from copilot.llm.wrappers import FallbackChatModel, RetryingChatModel
from tests.fakes import FakeLLM


@pytest.fixture
def app():
    """Create a clean FastAPI application instance for each test."""
    application = create_app()
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
def client(app) -> Generator[TestClient, None, None]:
    """TestClient that runs inside the application lifespan context."""
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Liveness Probe (/healthz) Tests
# ---------------------------------------------------------------------------


def test_healthz_returns_200(client: TestClient) -> None:
    """Liveness probe /healthz returns 200 OK unconditionally."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_with_trailing_slash_returns_200(client: TestClient) -> None:
    """Liveness probe /healthz/ returns 200 OK without redirection."""
    response = client.get("/healthz/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Readiness Probe (/readyz) Tests
# ---------------------------------------------------------------------------


def test_readyz_returns_200_by_default(client: TestClient) -> None:
    """Readiness probe returns 200 OK when no failing dependencies are present."""
    response = client.get("/readyz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["checks"]["app"] == "ok"


def test_readyz_returns_200_when_dependencies_are_up(app, client: TestClient) -> None:
    """Readiness probe returns 200 OK when all registered dependencies report healthy."""
    container: Container = app.state.container
    container.register_readiness_check("database", lambda: True)
    container.register_readiness_check("redis", lambda: True)

    response = client.get("/readyz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["checks"]["database"] == "ok"
    assert data["checks"]["redis"] == "ok"


def test_readyz_returns_503_when_a_fake_dependency_is_down(app, client: TestClient) -> None:
    """Readiness probe returns 503 Service Unavailable when any dependency check returns False."""
    container: Container = app.state.container
    container.register_readiness_check("database", lambda: True)
    container.register_readiness_check("fake_external_service", lambda: False)

    response = client.get("/readyz")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "unavailable"
    assert data["checks"]["database"] == "ok"
    assert data["checks"]["fake_external_service"] == "down"


def test_readyz_returns_503_when_dependency_check_raises_exception(app, client: TestClient) -> None:
    """Readiness probe returns 503 when a dependency check raises an unexpected error."""
    container: Container = app.state.container

    def failing_check() -> bool:
        raise ConnectionError("Network connection refused")

    container.register_readiness_check("payment_gateway", failing_check)

    response = client.get("/readyz")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "unavailable"
    assert data["checks"]["payment_gateway"] == "down"
    # Security verification: never leak raw exception message or credentials in the response body
    assert "Network connection refused" not in response.text


@pytest.mark.asyncio
async def test_readyz_supports_async_readiness_checks(app) -> None:
    """Readiness probe properly awaits async coroutine checks."""
    container: Container = Container()

    async def async_ok_check() -> bool:
        return True

    async def async_down_check() -> bool:
        return False

    container.register_readiness_check("async_ok", async_ok_check)
    container.register_readiness_check("async_down", async_down_check)

    is_ready, details = await container.check_readiness()
    assert is_ready is False
    assert details["async_ok"] == "ok"
    assert details["async_down"] == "down"


# ---------------------------------------------------------------------------
# /ask Endpoint & Composition Root Dependency Overrides
# ---------------------------------------------------------------------------


def test_ask_returns_fake_llm_answer_through_dependency_override(app, client: TestClient) -> None:
    """/ask returns the FakeLLM answer via get_answer_model dependency override.

    This proves that the composition root and dependency injection work seamlessly.
    """
    fake_llm = FakeLLM(
        responses=["To check your internet quota, dial *414# or log into WE app."],
        model_name="fake-gpt-model",
        provider_name="fake-groq",
    )
    app.dependency_overrides[get_answer_model] = lambda: fake_llm

    payload = {"question": "How do I check my remaining internet quota?"}
    response = client.post("/ask", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "To check your internet quota, dial *414# or log into WE app."
    assert data["model"] == "fake-gpt-model"
    assert data["provider"] == "fake-groq"

    # Verify FakeLLM received the translated user message
    assert len(fake_llm.calls) == 1
    assert fake_llm.calls[0].messages[0].role == "user"
    assert fake_llm.calls[0].messages[0].content == "How do I check my remaining internet quota?"


def test_ask_returns_fake_llm_answer_through_container_override(app, client: TestClient) -> None:
    """/ask returns the FakeLLM answer via full Container composition root override."""
    fake_llm = FakeLLM(
        responses=["Container override successfully resolved answer model."],
        model_name="fake-container-model",
        provider_name="fake-provider",
    )
    test_container = Container(answer_model=fake_llm)
    app.dependency_overrides[get_container] = lambda: test_container

    response = client.post("/ask", json={"question": "Test question for container override"})

    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "Container override successfully resolved answer model."
    assert data["model"] == "fake-container-model"
    assert data["provider"] == "fake-provider"


def test_ask_preserves_session_id_when_provided(app, client: TestClient) -> None:
    """/ask echoes the optional session_id for client turn correlation."""
    fake_llm = FakeLLM(responses=["Hello!"])
    app.dependency_overrides[get_answer_model] = lambda: fake_llm

    payload = {"question": "Hello", "session_id": "session-xyz-12345"}
    response = client.post("/ask", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "session-xyz-12345"


# ---------------------------------------------------------------------------
# Bad Payload (Validation 422) Tests
# ---------------------------------------------------------------------------


def test_ask_returns_422_on_empty_payload(client: TestClient) -> None:
    """Sending an empty JSON dictionary returns 422 Unprocessable Entity."""
    response = client.post("/ask", json={})
    assert response.status_code == 422


def test_ask_returns_422_on_empty_question_string(client: TestClient) -> None:
    """Sending an empty question string (min_length=1 violation) returns 422."""
    response = client.post("/ask", json={"question": ""})
    assert response.status_code == 422


def test_ask_returns_422_on_invalid_data_type(client: TestClient) -> None:
    """Sending a non-string question field returns 422."""
    response = client.post("/ask", json={"question": 12345})
    assert response.status_code == 422


def test_ask_returns_422_on_extra_forbidden_fields(client: TestClient) -> None:
    """Sending undeclared payload fields (extra='forbid') returns 422."""
    payload = {
        "question": "Valid question?",
        "unauthorized_field": "injected_data",
    }
    response = client.post("/ask", json=payload)
    assert response.status_code == 422


def test_ask_returns_422_on_malformed_json_body(client: TestClient) -> None:
    """Sending invalid raw body with application/json header returns 422."""
    response = client.post(
        "/ask",
        content="not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Error Translation Tests
# ---------------------------------------------------------------------------


def test_ask_translates_rate_limit_to_429(app, client: TestClient) -> None:
    """RateLimitError is translated to HTTP 429 with sanitized public message and Retry-After header."""
    fake_llm = FakeLLM(
        responses=[
            RateLimitError("Internal vendor 429 quota exhausted", provider="groq", retry_after=15.0)
        ]
    )
    app.dependency_overrides[get_answer_model] = lambda: fake_llm

    response = client.post("/ask", json={"question": "Trigger rate limit"})
    assert response.status_code == 429
    assert response.headers.get("retry-after") == "15"
    data = response.json()
    assert data["detail"] == "Too many requests to the AI service. Please try again later."
    assert data["provider"] == "groq"
    # Security: ensure raw internal vendor error message is never leaked in the response
    assert "Internal vendor 429 quota exhausted" not in response.text


def test_ask_translates_provider_timeout_to_504(app, client: TestClient) -> None:
    """ProviderTimeout is translated to HTTP 504 Gateway Timeout with sanitized public message."""
    fake_llm = FakeLLM(
        responses=[ProviderTimeout("Upstream socket read timeout after 30s", provider="gemini")]
    )
    app.dependency_overrides[get_answer_model] = lambda: fake_llm

    response = client.post("/ask", json={"question": "Trigger timeout"})
    assert response.status_code == 504
    data = response.json()
    assert data["detail"] == "The AI provider request timed out."
    assert data["provider"] == "gemini"
    assert "Upstream socket read timeout" not in response.text


def test_ask_translates_provider_error_to_502(app, client: TestClient) -> None:
    """ProviderError is translated to HTTP 502 Bad Gateway with sanitized public message."""
    fake_llm = FakeLLM(
        responses=[ProviderError("Upstream server 500 JSON parse failure", provider="groq")]
    )
    app.dependency_overrides[get_answer_model] = lambda: fake_llm

    response = client.post("/ask", json={"question": "Trigger provider error"})
    assert response.status_code == 502
    data = response.json()
    assert data["detail"] == "The AI provider request failed."
    assert data["provider"] == "groq"
    assert "Upstream server 500 JSON parse failure" not in response.text


# ---------------------------------------------------------------------------
# Container Architecture & Lifecycle Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_container_lifecycle_aclose_cleans_up_models() -> None:
    """Container.aclose() cleanly delegates to all tracked root models."""
    fake_answer = FakeLLM()
    fake_dep = FakeLLM()

    container = Container(answer_model=fake_answer)
    container._root_models.append(fake_dep)

    assert fake_answer.closed is False
    assert fake_dep.closed is False

    await container.aclose()

    assert fake_answer.closed is True
    assert fake_dep.closed is True


def test_container_constructs_production_resilient_chain() -> None:
    """Container builds a FallbackChatModel chaining RetryingChatModel instances."""
    config = AppConfig(
        groq_api_key="gsk_test_123",
        gemini_api_key="gemini_test_456",
    )
    container = Container(config=config)
    model: ChatModel = container.get_answer_model()

    assert isinstance(model, FallbackChatModel)
    assert len(model.models) == 2
    assert len(model.providers) == 2
    assert isinstance(model.models[0], RetryingChatModel)
    assert isinstance(model.models[1], RetryingChatModel)


def test_container_omits_fallback_chain_when_fallback_provider_is_none() -> None:
    """When fallback_provider is None, container returns only the primary RetryingChatModel."""
    config = AppConfig(
        groq_api_key="gsk_test_123",
    )
    config.llm.fallback_provider = None

    container = Container(config=config)
    model: ChatModel = container.get_answer_model()

    assert isinstance(model, RetryingChatModel)
    assert not isinstance(model, FallbackChatModel)


def test_container_respects_custom_fallback_model_from_config() -> None:
    """Container sets fallback_adapter.default_model from config.llm.fallback_model."""
    config = AppConfig(
        groq_api_key="gsk_test_123",
        gemini_api_key="gemini_test_456",
    )
    config.llm.fallback_provider = "gemini"
    config.llm.fallback_model = "custom-fallback-model-id"

    container = Container(config=config)
    model: ChatModel = container.get_answer_model()

    assert isinstance(model, FallbackChatModel)
    fallback_wrapper = model.models[1]
    assert isinstance(fallback_wrapper, RetryingChatModel)
    assert fallback_wrapper.model.default_model == "custom-fallback-model-id"


@pytest.mark.asyncio
async def test_container_closes_leaf_adapters_exactly_once() -> None:
    """Container.aclose() delegates via wrapper tree without double-closing leaf adapters."""
    close_count = 0

    class TrackingLLM(FakeLLM):
        async def aclose(self) -> None:
            nonlocal close_count
            close_count += 1
            await super().aclose()

    adapter = TrackingLLM()
    retrying = RetryingChatModel(adapter)
    fallback = FallbackChatModel([retrying])

    container = Container(answer_model=fallback)
    await container.aclose()

    assert close_count == 1
    assert adapter.closed is True


def test_container_logs_warning_when_fallback_provider_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Container logs a warning when fallback provider initialization fails instead of silently passing."""
    config = AppConfig(
        groq_api_key="gsk_test_123",
        gemini_api_key="invalid_placeholder_key",
    )
    config.llm.fallback_provider = "non_existent_provider_name"

    with caplog.at_level(logging.WARNING):
        container = Container(config=config)
        model = container.get_answer_model()

    assert isinstance(model, RetryingChatModel)
    assert any(
        "fallback provider 'non_existent_provider_name' not found" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_lifespan_try_finally_guarantees_cleanup() -> None:
    """Lifespan try/finally guarantees container.aclose() runs even if an error is raised during lifecycle."""
    from copilot.api.main import lifespan

    fake_model = FakeLLM()
    container = Container(answer_model=fake_model)
    test_app = create_app(container=container)

    # Simulate an unhandled exception interrupting the lifespan context
    with pytest.raises(RuntimeError, match="Simulated application crash"):
        async with lifespan(test_app):
            assert fake_model.closed is False
            raise RuntimeError("Simulated application crash")

    # finally block must have cleanly closed the container
    assert fake_model.closed is True
