"""Shared domain errors across all providers and components."""

from __future__ import annotations

import email.utils
from datetime import UTC, datetime


def _parse_retry_after(value: float | str | None) -> float | None:
    """Parse retry_after value from seconds (float/int/str) or HTTP-date (RFC 7231 / RFC 9110).

    Returns non-negative seconds as a float, or None if unparseable/empty.
    Raises ValueError if negative seconds are specified.
    """
    if value is None:
        return None

    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError("retry_after cannot be negative")
        return float(value)

    val_str = str(value).strip()
    if not val_str:
        return None

    # 1. Try numeric seconds
    try:
        seconds = float(val_str)
        if seconds < 0:
            raise ValueError("retry_after cannot be negative")
        return seconds
    except ValueError as err:
        if "cannot be negative" in str(err):
            raise

    # 2. Try HTTP-date format (e.g. 'Wed, 21 Oct 2026 07:28:00 GMT')
    try:
        target_dt = email.utils.parsedate_to_datetime(val_str)
        if target_dt.tzinfo is None:
            target_dt = target_dt.replace(tzinfo=UTC)
        now_dt = datetime.now(UTC)
        delta_seconds = (target_dt - now_dt).total_seconds()
        return max(0.0, delta_seconds)
    except Exception:
        return None


class CopilotError(Exception):
    """Base exception for all Support Copilot errors."""


class ProviderError(CopilotError):
    """Base error for third-party provider failures. Always carries provider name."""

    def __init__(self, message: str, provider: str) -> None:
        super().__init__(f"[{provider}] {message}")
        self.message = message
        self.provider = provider
        self.component = provider  # Provider is the originating component


class RateLimitError(ProviderError):
    """Raised when a provider hits rate limits or quota boundaries (HTTP 429)."""

    def __init__(
        self,
        message: str,
        provider: str,
        retry_after: float | str | None = None,
    ) -> None:
        parsed = _parse_retry_after(retry_after)
        super().__init__(message, provider)
        self.retry_after = parsed
        self.raw_retry_after = retry_after


class ProviderTimeout(ProviderError):
    """Raised when a provider request times out."""

    def __init__(self, message: str, provider: str) -> None:
        super().__init__(message, provider)


class AuthError(ProviderError):
    """Raised on authentication/authorization failure (HTTP 401/403)."""

    def __init__(self, message: str, provider: str) -> None:
        super().__init__(message, provider)


class InvalidInputError(CopilotError):
    """Raised when client input fails validation or exceeds allowed limits."""

    def __init__(self, message: str, component: str = "core") -> None:
        super().__init__(f"[{component}] {message}")
        self.message = message
        self.component = component

    @property
    def provider(self) -> str:
        """Alias to satisfy generic provider-facing error contracts."""
        return self.component
