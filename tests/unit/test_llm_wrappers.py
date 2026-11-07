"""Unit tests for RetryingChatModel and FallbackChatModel wrappers (Plan Step 1.5)."""

from __future__ import annotations

import logging

import pytest

from copilot.core.errors import (
    AuthError,
    InvalidInputError,
    ProviderError,
    ProviderTimeout,
    RateLimitError,
)
from copilot.llm.base import ChatModel
from copilot.llm.schemas import ChatMessage, ChatRequest, ChatResponse
from copilot.llm.wrappers import FallbackChatModel, RetryingChatModel
from tests.fakes import FakeLLM, FakeSleeper


@pytest.fixture
def sample_request() -> ChatRequest:
    return ChatRequest(
        messages=(ChatMessage(role="user", content="Hello, Support Copilot!"),),
        temperature=0.0,
    )


# ---------------------------------------------------------------------------
# RetryingChatModel Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrying_succeeds_first_attempt(sample_request: ChatRequest) -> None:
    fake = FakeLLM(responses=["Instant answer"])
    sleeper = FakeSleeper()

    retrying = RetryingChatModel(fake, max_attempts=3, sleeper=sleeper)
    assert isinstance(retrying, ChatModel)
    assert retrying.provider_name == fake.provider_name

    response = await retrying.complete(sample_request)

    assert isinstance(response, ChatResponse)
    assert response.content == "Instant answer"
    assert len(fake.calls) == 1
    assert len(sleeper.sleeps) == 0


@pytest.mark.asyncio
async def test_retrying_succeeds_after_two_transient_errors(
    sample_request: ChatRequest,
) -> None:
    """Plan requirement: Retrying succeeds after two transient errors."""
    err1 = RateLimitError("Rate limit exceeded", provider="groq", retry_after=1.5)
    err2 = ProviderTimeout("Gateway timeout", provider="groq")
    fake = FakeLLM(responses=[err1, err2, "Third attempt succeeded"])
    sleeper = FakeSleeper()

    retrying = RetryingChatModel(
        fake,
        max_attempts=3,
        base_delay=0.5,
        jitter=False,
        sleeper=sleeper,
    )

    response = await retrying.complete(sample_request)

    assert response.content == "Third attempt succeeded"
    assert len(fake.calls) == 3
    assert len(sleeper.sleeps) == 2
    # First sleep respects retry_after=1.5, second sleep uses base_delay * (2 ** (2 - 1)) = 1.0
    assert sleeper.sleeps[0] == 1.5
    assert sleeper.sleeps[1] == 1.0


@pytest.mark.asyncio
async def test_retrying_gives_up_after_maximum_attempts(
    sample_request: ChatRequest,
) -> None:
    """Plan requirement: Retrying gives up after the maximum attempts."""
    err = RateLimitError("Continuous rate limiting", provider="groq")
    fake = FakeLLM(responses=[err, err, err, err])
    sleeper = FakeSleeper()

    retrying = RetryingChatModel(
        fake,
        max_attempts=3,
        base_delay=0.5,
        jitter=False,
        sleeper=sleeper,
    )

    with pytest.raises(RateLimitError) as exc_info:
        await retrying.complete(sample_request)

    assert exc_info.value is err
    assert len(fake.calls) == 3
    assert len(sleeper.sleeps) == 2  # Sleeps between 1->2 and 2->3


@pytest.mark.asyncio
async def test_retrying_never_retries_auth_error(sample_request: ChatRequest) -> None:
    """Plan requirement: Never retries AuthError."""
    auth_err = AuthError("Invalid API key", provider="groq")
    fake = FakeLLM(responses=[auth_err, "Should never reach here"])
    sleeper = FakeSleeper()

    retrying = RetryingChatModel(fake, max_attempts=3, sleeper=sleeper)

    with pytest.raises(AuthError) as exc_info:
        await retrying.complete(sample_request)

    assert exc_info.value is auth_err
    assert len(fake.calls) == 1
    assert len(sleeper.sleeps) == 0


@pytest.mark.asyncio
async def test_retrying_never_retries_invalid_input_error(
    sample_request: ChatRequest,
) -> None:
    """Plan requirement: Never retries InvalidInputError."""
    input_err = InvalidInputError("Prompt token length exceeds limit")
    fake = FakeLLM(responses=[input_err, "Should never reach here"])
    sleeper = FakeSleeper()

    retrying = RetryingChatModel(fake, max_attempts=3, sleeper=sleeper)

    with pytest.raises(InvalidInputError) as exc_info:
        await retrying.complete(sample_request)

    assert exc_info.value is input_err
    assert len(fake.calls) == 1
    assert len(sleeper.sleeps) == 0


@pytest.mark.asyncio
async def test_retrying_never_retries_programming_errors(
    sample_request: ChatRequest,
) -> None:
    """Ensures developer/code bugs (AttributeError, TypeError) fail fast without retrying."""
    fake = FakeLLM(responses=[TypeError("Unexpected NoneType in internal mapping")])
    sleeper = FakeSleeper()

    retrying = RetryingChatModel(fake, max_attempts=3, sleeper=sleeper)

    with pytest.raises(TypeError, match="Unexpected NoneType"):
        await retrying.complete(sample_request)

    assert len(fake.calls) == 1
    assert len(sleeper.sleeps) == 0


@pytest.mark.asyncio
async def test_retrying_exponential_backoff_and_max_delay(
    sample_request: ChatRequest,
) -> None:
    err = ProviderTimeout("Timeout", provider="groq")
    fake = FakeLLM(responses=[err, err, err, err, err])
    sleeper = FakeSleeper()

    retrying = RetryingChatModel(
        fake,
        max_attempts=4,
        base_delay=1.0,
        backoff_factor=3.0,
        max_delay=5.0,
        jitter=False,
        sleeper=sleeper,
    )

    with pytest.raises(ProviderTimeout):
        await retrying.complete(sample_request)

    # Attempt 1: 1.0 * (3 ** 0) = 1.0
    # Attempt 2: 1.0 * (3 ** 1) = 3.0
    # Attempt 3: 1.0 * (3 ** 2) = 9.0 -> capped at max_delay 5.0
    assert sleeper.sleeps == [1.0, 3.0, 5.0]


@pytest.mark.asyncio
async def test_retrying_jitter_never_reduces_retry_after(
    sample_request: ChatRequest,
) -> None:
    """Strictly respects server-specified retry_after as minimum wait time."""
    err = RateLimitError("Rate limited", provider="groq", retry_after=10.0)
    fake = FakeLLM(responses=[err, "OK"])
    sleeper = FakeSleeper()

    retrying = RetryingChatModel(
        fake,
        max_attempts=2,
        jitter=True,
        sleeper=sleeper,
    )

    await retrying.complete(sample_request)

    assert len(sleeper.sleeps) == 1
    # Must NEVER be less than 10.0s requested by the server
    assert 10.0 <= sleeper.sleeps[0] <= 11.0


@pytest.mark.asyncio
async def test_retrying_jitter_on_exponential_backoff(
    sample_request: ChatRequest,
) -> None:
    """Full jitter on exponential backoff scales within [0.0, raw_delay]."""
    err = ProviderTimeout("Timeout", provider="groq")
    fake = FakeLLM(responses=[err, "OK"])
    sleeper = FakeSleeper()

    retrying = RetryingChatModel(
        fake,
        max_attempts=2,
        base_delay=5.0,
        jitter=True,
        sleeper=sleeper,
    )

    await retrying.complete(sample_request)

    assert len(sleeper.sleeps) == 1
    assert 0.0 <= sleeper.sleeps[0] <= 5.0


@pytest.mark.asyncio
async def test_retrying_logs_non_retryable_error_distinctly(
    sample_request: ChatRequest,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Confirms non-retryable errors are logged with reason='non_retryable_error'."""
    # Attempt 1: Timeout (transient), Attempt 2: AuthError (non-retryable)
    timeout_err = ProviderTimeout("Transient timeout", provider="groq")
    auth_err = AuthError("Invalid credentials", provider="groq")
    fake = FakeLLM(responses=[timeout_err, auth_err], provider_name="groq")
    sleeper = FakeSleeper()

    retrying = RetryingChatModel(fake, max_attempts=3, jitter=False, sleeper=sleeper)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(AuthError):
            await retrying.complete(sample_request)

    # Verify log messages distinguish non_retryable_error
    records = [r for r in caplog.records if r.name == "copilot.llm.wrappers.retrying"]
    non_retry_records = [
        r
        for r in records
        if getattr(r, "reason", None) == "non_retryable_error" or "non-retryable error" in r.message
    ]
    assert len(non_retry_records) == 1
    assert "retries exhausted" not in non_retry_records[0].message


@pytest.mark.asyncio
async def test_retrying_logs_retry_exhausted_distinctly(
    sample_request: ChatRequest,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Confirms exhausted retries are logged with reason='retry_exhausted'."""
    timeout_err = ProviderTimeout("Transient timeout", provider="groq")
    fake = FakeLLM(responses=[timeout_err, timeout_err, timeout_err], provider_name="groq")
    sleeper = FakeSleeper()

    retrying = RetryingChatModel(fake, max_attempts=3, jitter=False, sleeper=sleeper)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ProviderTimeout):
            await retrying.complete(sample_request)

    records = [r for r in caplog.records if r.name == "copilot.llm.wrappers.retrying"]
    exhausted_records = [
        r
        for r in records
        if getattr(r, "reason", None) == "retry_exhausted" or "retries exhausted" in r.message
    ]
    assert len(exhausted_records) == 1


@pytest.mark.asyncio
async def test_retrying_aclose_delegates() -> None:
    fake = FakeLLM()
    retrying = RetryingChatModel(fake)

    assert not fake.closed
    await retrying.aclose()
    assert fake.closed


def test_retrying_invalid_arguments() -> None:
    fake = FakeLLM()
    with pytest.raises(ValueError, match="max_attempts must be >= 1"):
        RetryingChatModel(fake, max_attempts=0)

    with pytest.raises(ValueError, match="base_delay must be non-negative"):
        RetryingChatModel(fake, base_delay=-1.0)

    with pytest.raises(ValueError, match="max_delay must be >= base_delay"):
        RetryingChatModel(fake, base_delay=5.0, max_delay=2.0)

    with pytest.raises(ValueError, match=r"backoff_factor must be >= 1\.0"):
        RetryingChatModel(fake, backoff_factor=0.5)


# ---------------------------------------------------------------------------
# FallbackChatModel Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_succeeds_on_primary_without_using_fallback(
    sample_request: ChatRequest,
) -> None:
    primary = FakeLLM(responses=["Primary answer"], provider_name="groq")
    fallback = FakeLLM(responses=["Fallback answer"], provider_name="gemini")

    model = FallbackChatModel([primary, fallback])
    assert isinstance(model, ChatModel)
    assert model.provider_name == "groq"
    assert len(model.models) == 2

    response = await model.complete(sample_request)

    assert response.content == "Primary answer"
    assert response.provider == "groq"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 0


@pytest.mark.asyncio
async def test_fallback_moves_to_next_provider_on_rate_limit_error(
    sample_request: ChatRequest,
) -> None:
    """Plan requirement: Fallback moves to the next provider on RateLimitError."""
    primary = FakeLLM(
        responses=[RateLimitError("Groq 429 quota exhausted", provider="groq")],
        provider_name="groq",
    )
    fallback = FakeLLM(responses=["Gemini answer after fallback"], provider_name="gemini")

    model = FallbackChatModel([primary, fallback])

    response = await model.complete(sample_request)

    assert response.content == "Gemini answer after fallback"
    assert response.provider == "gemini"  # Plan requirement: records which provider answered
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


@pytest.mark.asyncio
async def test_fallback_moves_to_next_provider_on_provider_timeout(
    sample_request: ChatRequest,
) -> None:
    primary = FakeLLM(
        responses=[ProviderTimeout("Groq timed out after 30s", provider="groq")],
        provider_name="groq",
    )
    fallback = FakeLLM(responses=["Gemini answer"], provider_name="gemini")

    model = FallbackChatModel([primary, fallback])
    response = await model.complete(sample_request)

    assert response.content == "Gemini answer"
    assert response.provider == "gemini"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


@pytest.mark.asyncio
async def test_fallback_raises_last_error_when_all_fail(
    sample_request: ChatRequest,
) -> None:
    """Plan requirement: Raises the last error when all fail."""
    groq_err = RateLimitError("Groq 429", provider="groq")
    gemini_err = ProviderError("Gemini 500 internal error", provider="gemini")

    primary = FakeLLM(responses=[groq_err], provider_name="groq")
    secondary = FakeLLM(responses=[gemini_err], provider_name="gemini")

    model = FallbackChatModel([primary, secondary])

    with pytest.raises(ProviderError) as exc_info:
        await model.complete(sample_request)

    # Must be the LAST error (from Gemini), not the first
    assert exc_info.value is gemini_err
    assert exc_info.value.provider == "gemini"
    assert len(primary.calls) == 1
    assert len(secondary.calls) == 1


@pytest.mark.asyncio
async def test_fallback_multi_provider_chain_records_answering_provider(
    sample_request: ChatRequest,
) -> None:
    """Plan requirement: Records which provider answered in multi-provider chain."""
    p1 = FakeLLM(responses=[RateLimitError("P1 429", provider="p1")], provider_name="p1")
    p2 = FakeLLM(responses=[RateLimitError("P2 429", provider="p2")], provider_name="p2")
    p3 = FakeLLM(responses=["P3 success"], provider_name="p3")

    model = FallbackChatModel([p1, p2, p3])
    response = await model.complete(sample_request)

    assert response.content == "P3 success"
    assert response.provider == "p3"
    assert len(p1.calls) == 1
    assert len(p2.calls) == 1
    assert len(p3.calls) == 1


@pytest.mark.asyncio
async def test_fallback_does_not_catch_invalid_input_error(
    sample_request: ChatRequest,
) -> None:
    """Client error should fail immediately and never attempt fallback."""
    primary = FakeLLM(
        responses=[InvalidInputError("User message empty")],
        provider_name="groq",
    )
    secondary = FakeLLM(responses=["Fallback"], provider_name="gemini")

    model = FallbackChatModel([primary, secondary])

    with pytest.raises(InvalidInputError):
        await model.complete(sample_request)

    assert len(primary.calls) == 1
    assert len(secondary.calls) == 0


@pytest.mark.asyncio
async def test_fallback_does_not_catch_programming_error(
    sample_request: ChatRequest,
) -> None:
    primary = FakeLLM(
        responses=[AttributeError("Code bug in primary")],
        provider_name="groq",
    )
    secondary = FakeLLM(responses=["Fallback"], provider_name="gemini")

    model = FallbackChatModel([primary, secondary])

    with pytest.raises(AttributeError, match="Code bug"):
        await model.complete(sample_request)

    assert len(primary.calls) == 1
    assert len(secondary.calls) == 0


@pytest.mark.asyncio
async def test_fallback_aclose_closes_all_models() -> None:
    p1 = FakeLLM(provider_name="groq")
    p2 = FakeLLM(provider_name="gemini")

    model = FallbackChatModel([p1, p2])
    assert not p1.closed
    assert not p2.closed

    await model.aclose()
    assert p1.closed
    assert p2.closed


def test_fallback_empty_models_raises_value_error() -> None:
    with pytest.raises(ValueError, match="requires at least one ChatModel"):
        FallbackChatModel([])


# ---------------------------------------------------------------------------
# Composed Chain: Retrying + Fallback (Groq -> Gemini)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_composed_retrying_and_fallback_chain(sample_request: ChatRequest) -> None:
    """Real-world composition: Groq retries 2 times, exhausts, falls back to Gemini which succeeds."""
    groq_sleeper = FakeSleeper()
    gemini_sleeper = FakeSleeper()

    # Groq fails with 429 3 times (exhausting 3 attempts)
    groq_429 = RateLimitError("Groq 429", provider="groq")
    groq_fake = FakeLLM(
        responses=[groq_429, groq_429, groq_429],
        provider_name="groq",
    )
    groq_retrying = RetryingChatModel(
        groq_fake,
        max_attempts=3,
        base_delay=0.5,
        jitter=False,
        sleeper=groq_sleeper,
    )

    # Gemini fails once with timeout, then succeeds on retry
    gemini_timeout = ProviderTimeout("Gemini timeout", provider="gemini")
    gemini_fake = FakeLLM(
        responses=[gemini_timeout, "Gemini final answer"],
        provider_name="gemini",
    )
    gemini_retrying = RetryingChatModel(
        gemini_fake,
        max_attempts=3,
        base_delay=0.5,
        jitter=False,
        sleeper=gemini_sleeper,
    )

    # Compose: Groq with Retrying -> Gemini with Retrying
    pipeline = FallbackChatModel([groq_retrying, gemini_retrying])

    response = await pipeline.complete(sample_request)

    # Asserts
    assert response.content == "Gemini final answer"
    assert response.provider == "gemini"
    assert len(groq_fake.calls) == 3
    assert len(gemini_fake.calls) == 2
    assert len(groq_sleeper.sleeps) == 2
    assert len(gemini_sleeper.sleeps) == 1

    await pipeline.aclose()
    assert groq_fake.closed
    assert gemini_fake.closed


# ---------------------------------------------------------------------------
# Logging & Non-Leakage Verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrappers_logging_without_leaking_secrets(
    sample_request: ChatRequest,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify structured logging records provider, attempt, error, fallback without leaking sensitive data."""
    secret_key = "gsk_super_secret_never_log_this_key_12345"
    err = RateLimitError(f"Quota exceeded with key {secret_key}", provider="groq")

    groq = FakeLLM(responses=[err, err], provider_name="groq")
    gemini = FakeLLM(responses=["Gemini answer"], provider_name="gemini")

    sleeper = FakeSleeper()
    groq_retrying = RetryingChatModel(groq, max_attempts=2, jitter=False, sleeper=sleeper)

    pipeline = FallbackChatModel([groq_retrying, gemini])

    with caplog.at_level(logging.INFO):
        response = await pipeline.complete(sample_request)

    assert response.provider == "gemini"

    # Confirm key never appears in log messages
    for record in caplog.records:
        assert secret_key not in record.message
        # Confirm user prompt content is not leaked in log messages
        assert "Hello, Support Copilot!" not in record.message
