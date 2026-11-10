# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: Builder (compile dependencies and build virtualenv)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# Install uv from the official Astral image (latest stable release)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /build

# Configure uv for hermetic containerized builds
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Copy dependency specifications first for optimal layer caching
COPY pyproject.toml uv.lock ./

# Install project dependencies into /app/.venv without dev tools or project itself
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source code and install project into virtualenv
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-editable


# ---------------------------------------------------------------------------
# Stage 2: Runtime (minimal, non-root, production-hardened)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Install runtime system dependencies:
# - ffmpeg: audio normalization and format conversions for ASR/TTS
# - curl: container healthcheck probe
# - ca-certificates: TLS verification for external AI providers
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/*

# Create non-root system user and group (UID/GID 10001)
RUN groupadd --gid 10001 appgroup && \
    useradd --uid 10001 --gid 10001 --shell /bin/bash --create-home appuser

WORKDIR /app

# Copy the prepared virtual environment from the builder stage
COPY --from=builder --chown=appuser:appgroup /app/.venv /app/.venv

# Copy application code and configuration with non-root ownership
COPY --chown=appuser:appgroup src/ /app/src/
COPY --chown=appuser:appgroup configs/ /app/configs/
COPY --chown=appuser:appgroup pyproject.toml /app/pyproject.toml

# Set environment variables for the runtime application
# Default PORT is 10000 (Render default); overridden dynamically by Cloud or Docker Compose
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=10000 \
    ENVIRONMENT=production

# Expose default HTTP port
EXPOSE 10000

# Container healthcheck querying /healthz endpoint using dynamic $PORT
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD sh -c "curl -f http://localhost:${PORT:-10000}/healthz || exit 1"

# Run as non-root user
USER appuser

# Default command starts the FastAPI application via uvicorn with shell execution to expand $PORT
CMD ["sh", "-c", "exec uvicorn copilot.api.main:app --host 0.0.0.0 --port ${PORT:-10000}"]

