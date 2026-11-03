"""Unit tests for shared core errors (Plan Step 1.2)."""

from __future__ import annotations

import pytest

from copilot.core.errors import (
    AuthError,
    CopilotError,
    InvalidInputError,
    ProviderError,
    ProviderTimeout,
    RateLimitError,
)


def test_provider_error_carries_provider_and_component() -> None:
    err = ProviderError("Service unavailable", provider="groq")
    assert err.provider == "groq"
    assert err.component == "groq"
    assert err.message == "Service unavailable"
    assert "[groq]" in str(err)
    assert isinstance(err, CopilotError)


@pytest.mark.parametrize(
    "error_cls",
    [RateLimitError, ProviderTimeout, AuthError],
)
def test_shared_provider_errors_inherit_and_carry_provider(
    error_cls: type[ProviderError],
) -> None:
    err = error_cls("failed", provider="test_provider")
    assert err.provider == "test_provider"
    assert err.component == "test_provider"
    assert isinstance(err, ProviderError)
    assert isinstance(err, CopilotError)


def test_invalid_input_error_hierarchy_and_aliases() -> None:
    # General input validation error defaults to component="core"
    err = InvalidInputError("Prompt cannot be empty")
    assert err.component == "core"
    assert err.provider == "core"  # alias works seamlessly
    assert "[core]" in str(err)
    assert isinstance(err, CopilotError)
    assert not isinstance(err, ProviderError)

    # Input error rejected by a specific component or provider
    custom_err = InvalidInputError("File exceeds 2MB", component="uploads")
    assert custom_err.component == "uploads"
    assert custom_err.provider == "uploads"
    assert "[uploads]" in str(custom_err)


def test_rate_limit_error_carries_retry_after() -> None:
    err = RateLimitError("Quota exceeded", provider="groq", retry_after=30.0)
    assert err.provider == "groq"
    assert err.retry_after == 30.0

    err_no_retry = RateLimitError("Quota exceeded", provider="groq")
    assert err_no_retry.retry_after is None


def test_rate_limit_error_rejects_negative_retry_after() -> None:
    with pytest.raises(ValueError, match="retry_after cannot be negative"):
        RateLimitError("Quota exceeded", provider="groq", retry_after=-5.0)
