"""Unit tests for the LLM port Protocol and schemas (Plan Step 1.3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from copilot.llm.base import ChatModel
from copilot.llm.schemas import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    TokenUsage,
)
from tests.fakes.fake_llm import FakeLLM


def test_fake_llm_satisfies_chat_model_protocol_at_runtime() -> None:
    fake = FakeLLM()
    # Runtime Protocol check (verifies @runtime_checkable on ChatModel)
    assert isinstance(fake, ChatModel)
    assert fake.provider_name == "fake"


@pytest.mark.asyncio
async def test_fake_llm_returns_valid_response_and_records_call() -> None:
    fake = FakeLLM(responses=["First answer", "Second answer"])

    request = ChatRequest(
        messages=(ChatMessage(role="user", content="Hello"),),
        temperature=0.2,
    )

    response = await fake.complete(request)
    assert isinstance(response, ChatResponse)
    assert response.content == "First answer"
    assert response.provider == "fake"
    assert response.model == "fake-model"
    assert response.usage.total_tokens == 15
    assert len(fake.calls) == 1
    assert fake.calls[0] == request

    # Second call returns next queued response
    response2 = await fake.complete(request)
    assert response2.content == "Second answer"

    # Subsequent call returns default response
    response3 = await fake.complete(request)
    assert response3.content == fake.default_response


def test_schemas_are_frozen() -> None:
    msg = ChatMessage(role="user", content="Hello")
    with pytest.raises(ValidationError):
        msg.content = "New content"  # type: ignore[misc]

    usage = TokenUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10)
    with pytest.raises(ValidationError):
        usage.total_tokens = 20  # type: ignore[misc]

    req = ChatRequest(messages=(msg,))
    with pytest.raises(ValidationError):
        req.temperature = 1.0  # type: ignore[misc]

    res = ChatResponse(content="Hi", model="test", provider="test")
    with pytest.raises(ValidationError):
        res.content = "Changed"  # type: ignore[misc]


def test_invalid_role_is_rejected() -> None:
    # Literal["system", "user", "assistant", "tool"] rejects any unknown role
    with pytest.raises(ValidationError):
        ChatMessage(role="invalid_role", content="Hi")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        ChatMessage(role="admin", content="Hi")  # type: ignore[arg-type]


def test_chat_request_validation_boundaries() -> None:
    msg = ChatMessage(role="user", content="Hello")

    # Empty messages disallowed
    with pytest.raises(ValidationError):
        ChatRequest(messages=())

    # Temperature out of bounds (> 2.0 or < 0.0)
    with pytest.raises(ValidationError):
        ChatRequest(messages=(msg,), temperature=-0.1)

    with pytest.raises(ValidationError):
        ChatRequest(messages=(msg,), temperature=2.5)

    # Max tokens must be positive (> 0)
    with pytest.raises(ValidationError):
        ChatRequest(messages=(msg,), max_tokens=0)


def test_schema_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ChatMessage(
            role="user",
            content="Hello",
            extra_field="forbidden",  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError):
        ChatRequest(
            messages=(ChatMessage(role="user", content="Hello"),),
            unsupported_option="fail",  # type: ignore[call-arg]
        )
