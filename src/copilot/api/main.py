"""FastAPI application factory and lifecycle entrypoint (Plan Step 1.6)."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from copilot.api.container import Container
from copilot.api.routes import ask_router, health_router
from copilot.core.config import AppConfig
from copilot.core.errors import (
    CopilotError,
    InvalidInputError,
    ProviderError,
    ProviderTimeout,
    RateLimitError,
)
from copilot.observability import RequestIDMiddleware, configure_logging

logger = logging.getLogger("copilot.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown lifecycle with guaranteed cleanup."""
    # Startup: ensure structlog and standard library logging are configured once
    configure_logging()

    # Startup: ensure Container exists on app.state
    container: Container | None = getattr(app.state, "container", None)
    if container is None:
        cfg = getattr(app.state, "config", None) or AppConfig.load()
        container = Container(config=cfg)
        app.state.container = container

    try:
        yield
    finally:
        # Shutdown: guaranteed resource cleanup in finally block even on errors/cancellations
        target_container: Container | None = getattr(app.state, "container", None)
        if target_container is not None:
            await target_container.aclose()


def create_app(
    config: AppConfig | None = None,
    container: Container | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Support Copilot API",
        version="0.1.0",
        description="Production AI customer-support assistant (RAG + agent + voice, Arabic and English)",
        lifespan=lifespan,
    )

    app.state.config = config
    app.state.container = container

    # Register middleware
    app.add_middleware(RequestIDMiddleware)

    # Register routers
    app.include_router(health_router)
    app.include_router(ask_router)

    # Register core exception handlers with sanitized public messages
    @app.exception_handler(RateLimitError)
    async def rate_limit_handler(_request: Request, exc: RateLimitError) -> JSONResponse:
        logger.warning(
            "AI provider rate limit exhausted: %s (provider=%s, retry_after=%s)",
            exc,
            exc.provider,
            exc.retry_after,
        )
        headers: dict[str, str] = {}
        if exc.retry_after is not None:
            headers["Retry-After"] = str(int(exc.retry_after))
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "detail": "Too many requests to the AI service. Please try again later.",
                "provider": exc.provider,
            },
            headers=headers,
        )

    @app.exception_handler(ProviderTimeout)
    async def timeout_handler(_request: Request, exc: ProviderTimeout) -> JSONResponse:
        logger.warning(
            "AI provider timed out: %s (provider=%s)",
            exc,
            exc.provider,
        )
        return JSONResponse(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            content={
                "detail": "The AI provider request timed out.",
                "provider": exc.provider,
            },
        )

    @app.exception_handler(ProviderError)
    async def provider_error_handler(_request: Request, exc: ProviderError) -> JSONResponse:
        logger.warning(
            "AI provider request failed: %s (provider=%s)",
            exc,
            exc.provider,
        )
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "detail": "The AI provider request failed.",
                "provider": exc.provider,
            },
        )

    @app.exception_handler(InvalidInputError)
    async def invalid_input_handler(_request: Request, exc: InvalidInputError) -> JSONResponse:
        logger.warning("Invalid input to AI service: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Invalid input provided to the AI service."},
        )

    @app.exception_handler(CopilotError)
    async def copilot_error_handler(_request: Request, exc: CopilotError) -> JSONResponse:
        logger.error("Unhandled Copilot internal error: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An internal server error occurred."},
        )

    return app


# Default ASGI application instance for uvicorn (e.g. uvicorn copilot.api.main:app)
app: FastAPI = create_app()
