"""Context variable management for request tracing and log correlation."""

from __future__ import annotations

from contextvars import ContextVar, Token

# Request correlation ID propagated across async tasks
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """Return the active request_id in the current async context, or empty string."""
    return _request_id_var.get()


def set_request_id(request_id: str) -> Token[str]:
    """Set the active request_id in the current async context and return the reset token."""
    return _request_id_var.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    """Reset the request_id context variable using the provided token."""
    _request_id_var.reset(token)


def clear_context() -> None:
    """Clear all request context variables."""
    _request_id_var.set("")
