"""Structlog configuration and JSON formatting pipeline (Plan Step 1.7)."""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog
from structlog.types import EventDict, Processor

from copilot.observability.context import get_request_id
from copilot.observability.redaction import redaction_processor

VALID_LOG_LEVELS: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

UVICORN_LOGGERS: tuple[str, ...] = ("uvicorn", "uvicorn.error", "uvicorn.access")

_is_configured: bool = False


def _extract_stdlib_logger_name(
    logger: object,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Extract logger name safely from a standard library LogRecord or logger."""
    if "logger" not in event_dict:
        record = event_dict.get("_record")
        if record is not None and hasattr(record, "name"):
            event_dict["logger"] = record.name
        elif hasattr(logger, "name"):
            event_dict["logger"] = logger.name
    return event_dict


def reset_logging_configuration() -> None:
    """Reset configuration state and clear structlog defaults (for testing or re-initialization)."""
    global _is_configured
    _is_configured = False
    structlog.reset_defaults()


def configure_logging(
    log_level: str = "INFO",
    json_output: bool = True,
    default_request_id: str | None = None,
    force: bool = False,
) -> None:
    """Configure structlog and standard library logging for JSON output.

    Features:
    - Guaranteed valid JSON per log line
    - Automatic request_id injection via contextvars when inside an HTTP request
    - Schema-flexible request_id policy: omitted during startup/shutdown by default (None),
      or populated with a sentinel (e.g. '-') if a fixed schema is required
    - PII and secret redaction (email, phone, card, token)
    - Cleanly separated structlog vs stdlib (foreign_pre_chain) processor pipelines
    - Intercepts Uvicorn loggers for consistent JSON output without duplicate lines
    - Validates log_level strictly (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    - Safely replaces root handlers with explicit handler-level filtering
    - Idempotent: executes once at startup unless force=True is passed
    """
    global _is_configured
    if _is_configured and not force:
        return

    # 1. Strictly validate log level
    normalized_level = log_level.strip().upper()
    if normalized_level not in VALID_LOG_LEVELS:
        valid_options = ", ".join(VALID_LOG_LEVELS.keys())
        raise ValueError(f"Invalid log level: {log_level!r}. Expected one of: {valid_options}.")
    level_num = VALID_LOG_LEVELS[normalized_level]

    # 2. Reset structlog defaults to prevent stale cached logger bindings
    structlog.reset_defaults()

    # 3. Define processors
    def _inject_request_id(
        _logger: object,
        _method_name: str,
        event_dict: EventDict,
    ) -> EventDict:
        """Inject active request_id from contextvars or apply optional default sentinel."""
        if "request_id" not in event_dict:
            active_id = get_request_id()
            if active_id:
                event_dict["request_id"] = active_id
            elif default_request_id is not None:
                event_dict["request_id"] = default_request_id
        return event_dict

    # Structlog-native chain: uses add_logger_name with BoundLogger
    structlog_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        _inject_request_id,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redaction_processor,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]

    # Foreign stdlib chain: separates stdlib records from structlog processors,
    # safely extracting extra attributes and logger name before redaction
    foreign_pre_chain: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.ExtraAdder(),
        _extract_stdlib_logger_name,
        _inject_request_id,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redaction_processor,
    ]

    if json_output:
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    # 4. Configure structlog library defaults
    structlog.configure(
        processors=structlog_processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # 5. Build standard library ProcessorFormatter
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=foreign_pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    # 6. Configure root handler with explicit level and clean handler management
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.setLevel(level_num)

    root_logger = logging.getLogger()
    for existing_handler in list(root_logger.handlers):
        try:
            existing_handler.flush()
            existing_handler.close()
        except Exception:
            pass
        root_logger.removeHandler(existing_handler)

    root_logger.addHandler(handler)
    root_logger.setLevel(level_num)

    # 7. Intercept Uvicorn loggers to route cleanly through root without duplicates
    for uvicorn_name in UVICORN_LOGGERS:
        u_logger = logging.getLogger(uvicorn_name)
        for existing_u_handler in list(u_logger.handlers):
            try:
                existing_u_handler.flush()
                existing_u_handler.close()
            except Exception:
                pass
            u_logger.removeHandler(existing_u_handler)
        u_logger.propagate = True
        u_logger.setLevel(level_num)

    _is_configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a configured structlog logger instance."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
