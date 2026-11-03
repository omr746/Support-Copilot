"""Shared domain errors across all providers and components."""

from __future__ import annotations


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
        retry_after: float | None = None,
    ) -> None:
        if retry_after is not None and retry_after < 0:
            raise ValueError("retry_after cannot be negative")
        super().__init__(message, provider)
        self.retry_after = retry_after


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
