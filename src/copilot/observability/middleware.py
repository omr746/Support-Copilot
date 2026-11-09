"""HTTP request correlation and structured access logging ASGI middleware (Plan Step 1.7)."""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from copilot.observability.context import reset_request_id, set_request_id

logger = structlog.get_logger("copilot.access")

# Restrict allowed request IDs to safe alphanumeric strings with . _ - up to 128 characters
REQUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")


def _extract_and_validate_request_id(scope: Scope) -> str:
    """Extract and validate X-Request-ID or X-Correlation-ID from ASGI headers."""
    raw_id: str | None = None
    for name, value in scope.get("headers", []):
        if name.lower() in (b"x-request-id", b"x-correlation-id"):
            try:
                raw_id = value.decode("latin1").strip()
            except UnicodeDecodeError:
                raw_id = None
            break

    if raw_id and REQUEST_ID_PATTERN.fullmatch(raw_id):
        return raw_id
    return uuid.uuid4().hex


class RequestIDMiddleware:
    """Pure ASGI middleware that ensures every HTTP request has a validated, correlated request_id.

    Benefits over BaseHTTPMiddleware:
    1. Direct ASGI lifecycle: no TaskGroup overhead or contextvars task-isolation bugs.
    2. Guaranteed response header injection: intercepts http.response.start so X-Request-ID
       is present on all responses (including 4xx, 5xx, and exception handler responses).
    3. Input validation: sanitizes incoming X-Request-ID against header injection / CRLF.
    4. Safe exception logging: avoids dumping raw error strings that could leak secrets or PII.
    5. Context restoration: restores previous ContextVar state via reset_request_id(token).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 1. Extract and sanitize incoming ID, or generate clean UUID
        request_id = _extract_and_validate_request_id(scope)

        # 2. Store on scope state for FastAPI route handlers
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["request_id"] = request_id

        # 3. Bind to contextvars and structlog
        token = set_request_id(request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start_time = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
                # Inject or overwrite X-Request-ID in response headers
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("latin1")))
                message["headers"] = headers

            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

            client = scope.get("client")
            client_ip = client[0] if client else None

            # 4. Structured access log at INFO: metadata only, NO MESSAGE BODY
            logger.info(
                "http_request_finished",
                method=scope.get("method", ""),
                path=scope.get("path", ""),
                status_code=status_code,
                duration_ms=duration_ms,
                client_ip=client_ip,
            )
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            # 5. Privacy: do not log raw str(exc) to prevent leaking secrets/PII
            logger.error(
                "http_request_failed",
                method=scope.get("method", ""),
                path=scope.get("path", ""),
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
            )
            raise
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
            reset_request_id(token)
