"""Telemetry, metrics collection, structured logging, and tracing for crawler workflows."""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import structlog

from copilot.rag.crawler.url_utils import hash_path

logger = structlog.get_logger("copilot.rag.crawler")

# --- OpenTelemetry Metrics Initialization with Graceful Fallback ---
_meter = None
_pages_visited_counter: Any = None
_page_duration_histogram: Any = None
_crawl_duration_histogram: Any = None

try:
    from opentelemetry import metrics as otel_metrics  # type: ignore[import-untyped]

    _meter = otel_metrics.get_meter("copilot.rag.crawler")
    _pages_visited_counter = _meter.create_counter(
        name="crawler_pages_visited_total",
        unit="1",
        description="Total count of visited crawler pages partitioned by outcome and HTTP status",
    )
    _page_duration_histogram = _meter.create_histogram(
        name="crawler_page_duration_ms",
        unit="ms",
        description="Distribution of page fetch and processing duration in milliseconds",
    )
    _crawl_duration_histogram = _meter.create_histogram(
        name="crawler_crawl_duration_seconds",
        unit="s",
        description="Distribution of total crawl run duration in seconds",
    )
except (ImportError, Exception):
    _meter = None
    _pages_visited_counter = None
    _page_duration_histogram = None
    _crawl_duration_histogram = None


def record_page_visit_metric(
    *,
    outcome: str,
    status_code: int,
    duration_ms: float = 0.0,
) -> None:
    """Record OpenTelemetry metrics for a page visit (Counter & Histogram)."""
    if _pages_visited_counter is not None:
        try:
            _pages_visited_counter.add(
                1,
                {"outcome": outcome, "status_code": status_code},
            )
        except Exception:
            pass

    if _page_duration_histogram is not None and duration_ms > 0:
        try:
            _page_duration_histogram.record(
                duration_ms,
                {"outcome": outcome},
            )
        except Exception:
            pass


def record_crawl_summary_metrics(duration_seconds: float, status: str = "completed") -> None:
    """Record OpenTelemetry duration metric for a complete crawl run."""
    if _crawl_duration_histogram is not None and duration_seconds > 0:
        try:
            _crawl_duration_histogram.record(
                duration_seconds,
                {"status": status},
            )
        except Exception:
            pass


def log_page_visit(
    log_func: Any = None,
    *,
    url: str,
    outcome: str,
    status_code: int,
    duration_ms: float = 0.0,
) -> None:
    """Emit a privacy-preserving structured log line and record OpenTelemetry metrics.

    Rule: Logs only host and path hash, NEVER the raw query string or full URL.
    Works seamlessly with structlog, stdlib logging, mock loggers, and OpenTelemetry.
    """
    # 1. Record OpenTelemetry metrics (Counter & Histogram)
    record_page_visit_metric(
        outcome=outcome,
        status_code=status_code,
        duration_ms=duration_ms,
    )

    # 2. Emit privacy-preserving structured log line
    target = log_func if log_func is not None else logger.info
    host, path_hash = hash_path(url)
    try:
        # If stdlib logger method (which requires extra={...})
        if hasattr(target, "__self__") and isinstance(target.__self__, logging.Logger):
            target.__self__.info(
                "crawl_page_visited",
                extra={
                    "host": host,
                    "path_hash": path_hash,
                    "outcome": outcome,
                    "status_code": status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            return
    except Exception:
        pass

    target(
        "crawl_page_visited",
        host=host,
        path_hash=path_hash,
        outcome=outcome,
        status_code=status_code,
        duration_ms=round(duration_ms, 2),
    )


def setup_tracing(
    service_name: str = "support-copilot",
    endpoint: str | None = None,
) -> None:
    """Configure OpenTelemetry TracerProvider with OTLP HTTP exporter for Jaeger.

    Degrades gracefully if OpenTelemetry SDK or exporter is not installed,
    or if Jaeger is unreachable.
    """
    try:
        import os

        from opentelemetry import trace  # type: ignore[import-untyped]
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource  # type: ignore[import-untyped]
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-untyped]
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore[import-untyped]

        current_provider = trace.get_tracer_provider()
        # If a real TracerProvider is already configured, skip re-registering
        if isinstance(current_provider, TracerProvider):
            return

        raw_endpoint: str = (
            endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "http://localhost:4318"
        )
        svc_name: str = os.getenv("OTEL_SERVICE_NAME") or service_name
        resource = Resource.create({SERVICE_NAME: svc_name})
        provider = TracerProvider(resource=resource)

        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-untyped]
                OTLPSpanExporter,
            )

            traces_endpoint = raw_endpoint.rstrip("/")
            if not traces_endpoint.endswith("/v1/traces"):
                traces_endpoint = f"{traces_endpoint}/v1/traces"

            exporter = OTLPSpanExporter(endpoint=traces_endpoint)
            processor = BatchSpanProcessor(exporter)
            provider.add_span_processor(processor)
        except Exception as ex:
            logger.debug("OTLP exporter could not be initialized: %s", ex)

        trace.set_tracer_provider(provider)
        import atexit

        atexit.register(provider.shutdown)
    except Exception as ex:
        logger.debug("Tracing setup skipped: %s", ex)


def flush_tracing() -> None:
    """Force flush all buffered spans to Jaeger."""
    try:
        from opentelemetry import trace  # type: ignore[import-untyped]
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-untyped]

        provider = trace.get_tracer_provider()
        if isinstance(provider, TracerProvider):
            provider.force_flush()
    except Exception:
        pass


@contextmanager
def trace_crawl_run(
    span_name: str = "copilot.rag.crawl",
    attributes: dict[str, Any] | None = None,
) -> Generator[Any, None, None]:
    """Context manager creating an OpenTelemetry span for the crawl run.

    Degrades gracefully if OpenTelemetry is not installed or configured.
    """
    setup_tracing()
    otel_tracer = None
    try:
        from opentelemetry import trace  # type: ignore[import-untyped]

        otel_tracer = trace.get_tracer("copilot.rag.crawler")
    except (ImportError, Exception):
        pass

    if otel_tracer is not None:
        try:
            with otel_tracer.start_as_current_span(span_name) as otel_span:
                if attributes:
                    for key, val in attributes.items():
                        otel_span.set_attribute(key, val)
                yield otel_span
        finally:
            flush_tracing()
    else:

        class _DummySpan:
            def __init__(self) -> None:
                self.attributes: dict[str, Any] = dict(attributes or {})

            def set_attribute(self, key: str, value: Any) -> None:
                self.attributes[key] = value

        yield _DummySpan()


@contextmanager
def trace_page_visit(
    url: str,
    span_name: str = "crawler.fetch_page",
) -> Generator[Any, None, None]:
    """Context manager creating a child OpenTelemetry span for an individual page."""
    host, path_hash = hash_path(url)
    with trace_crawl_run(span_name, {"host": host, "path_hash": path_hash}) as span:
        yield span
