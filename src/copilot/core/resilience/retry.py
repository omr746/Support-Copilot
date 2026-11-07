"""Retry policy and transient error classification."""

from __future__ import annotations

from copilot.core.errors import (
    AuthError,
    InvalidInputError,
    ProviderError,
    ProviderTimeout,
    RateLimitError,
)

TRANSIENT_SERVER_MARKERS: tuple[str, ...] = (
    "502",
    "503",
    "504",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "connection reset",
    "connection refused",
    "overloaded",
    "server error",
)


def is_transient_error(exc: Exception) -> bool:
    """Determine whether an error is transient and eligible for retry.

    Transient errors (eligible for retry):
      - RateLimitError (HTTP 429)
      - ProviderTimeout (HTTP/API timeouts)
      - ProviderError with transient 502/503/504 or connection failure indicators

    Non-transient errors (MUST NEVER be retried):
      - AuthError (HTTP 401/403)
      - InvalidInputError (bad request / schema validation)
      - Python runtime/programming bugs (TypeError, AttributeError, KeyError, ValueError, etc.)
    """
    # 1. Definitively non-transient errors: NEVER retry
    if isinstance(exc, (AuthError, InvalidInputError)):
        return False

    # 2. Definitively transient errors: ALWAYS retry
    if isinstance(exc, (RateLimitError, ProviderTimeout)):
        return True

    # 3. Python language/programming bugs: NEVER retry
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

    # 4. Check ProviderError for transient server indicators
    if isinstance(exc, ProviderError):
        msg = (getattr(exc, "message", None) or str(exc)).lower()
        if any(marker in msg for marker in TRANSIENT_SERVER_MARKERS):
            return True

    return False
