"""observability - logging, tracing, metrics, health, redaction."""

from copilot.observability.context import clear_context, get_request_id, set_request_id
from copilot.observability.logging import (
    VALID_LOG_LEVELS,
    configure_logging,
    get_logger,
    reset_logging_configuration,
)
from copilot.observability.middleware import REQUEST_ID_PATTERN, RequestIDMiddleware
from copilot.observability.redaction import (
    CARD_PATTERN,
    EMAIL_PATTERN,
    PHONE_PATTERN,
    SENSITIVE_KEYS,
    TOKEN_PREFIX_PATTERN,
    redact_data,
    redact_text,
    redaction_processor,
)

__all__ = [
    "CARD_PATTERN",
    "EMAIL_PATTERN",
    "PHONE_PATTERN",
    "REQUEST_ID_PATTERN",
    "SENSITIVE_KEYS",
    "TOKEN_PREFIX_PATTERN",
    "VALID_LOG_LEVELS",
    "RequestIDMiddleware",
    "clear_context",
    "configure_logging",
    "get_logger",
    "get_request_id",
    "redact_data",
    "redact_text",
    "redaction_processor",
    "reset_logging_configuration",
    "set_request_id",
]
