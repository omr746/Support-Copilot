"""Redaction processor for structlog and text sanitization (Plan Step 1.7).

Masks PII and secrets (emails, phone numbers, credit card numbers, and tokens)
to prevent sensitive customer data or credentials from leaking into log outputs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping
from typing import Any

# ---------------------------------------------------------------------------
# Regex Patterns for Sensitive Data Detection
# ---------------------------------------------------------------------------

# Payment card numbers (13 to 19 digits, with spaces, dashes, or contiguous)
CARD_PATTERN = re.compile(
    r"\b(?:\d{4}[-\s]?){3}\d{4}\b"  # 16-digit cards (Visa, MasterCard, etc.)
    r"|\b(?:\d{4}[-\s]?){3}\d{1,3}\b"  # 13 to 15 digit cards (e.g. Amex 4-6-5)
    r"|\b\d{4}[-\s]\d{6}[-\s]\d{5}\b"  # 15-digit Amex formatted
    r"|\b\d{13,19}\b"  # 13-19 continuous digits
)

# Email addresses
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Phone numbers (Egyptian local, international with country code, US/standard formats)
PHONE_PATTERN = re.compile(
    r"(?:\+?\d{1,3}[-.\s]?)?"  # Optional international country code (+20, +1, +44)
    r"(?:\(?\d{2,4}\)?[-.\s]?)"  # Area code or mobile prefix (010, 011, 012, 015, 555)
    r"\d{3,4}[-.\s]?\d{3,4}\b"  # Main phone number subscriber digits
)

# Bearer Authorization headers / tokens
BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*")

# Known API key / token prefixes (Groq gsk_, OpenAI sk-, Google AIza, GitHub ghp_, JWTs)
TOKEN_PREFIX_PATTERN = re.compile(
    r"\b(?:gsk|sk|ghp|gho|glpat|xoxb|xoxp)_[A-Za-z0-9_-]{16,}\b"
    r"|\bAIza[0-9A-Za-z-_]{35}\b"
    r"|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
)

# Inline key-value assignments (e.g. api_key="secret123", token=abc12345)
KEY_VALUE_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|secret|password|jwt|authorization|auth)"
    r"\s*[:=]\s*['\"]?([A-Za-z0-9\-_.~+/=]{8,})['\"]?"
)

# Dictionary keys that should always have their values completely redacted
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "app_jwt_secret",
        "auth",
        "authorization",
        "bearer",
        "card",
        "card_number",
        "credit_card",
        "cvv",
        "gemini_api_key",
        "groq_api_key",
        "jwt",
        "langfuse_public_key",
        "langfuse_secret_key",
        "password",
        "refresh_token",
        "secret",
        "token",
    }
)


def redact_text(text: str) -> str:
    """Mask sensitive PII and secret patterns in a string."""
    if not text:
        return text

    # 1. Cards first (to avoid phone regex overlapping card digits)
    text = CARD_PATTERN.sub("[REDACTED_CARD]", text)

    # 2. Email addresses
    text = EMAIL_PATTERN.sub("[REDACTED_EMAIL]", text)

    # 3. Authentication tokens and API keys
    text = BEARER_PATTERN.sub("Bearer [REDACTED_TOKEN]", text)
    text = TOKEN_PREFIX_PATTERN.sub("[REDACTED_TOKEN]", text)
    text = KEY_VALUE_SECRET_PATTERN.sub(r"\1=[REDACTED_TOKEN]", text)

    # 4. Phone numbers
    text = PHONE_PATTERN.sub("[REDACTED_PHONE]", text)

    return text


def redact_data(val: Any) -> Any:
    """Recursively traverse and sanitize data structures (dicts, lists, strings)."""
    if isinstance(val, str):
        return redact_text(val)

    if isinstance(val, Mapping):
        sanitized_dict: dict[str, Any] = {}
        for k, v in val.items():
            k_str = str(k).lower().strip()
            if k_str in SENSITIVE_KEYS or any(
                s in k_str for s in ("secret", "password", "api_key", "token")
            ):
                sanitized_dict[str(k)] = "[REDACTED_TOKEN]"
            else:
                sanitized_dict[str(k)] = redact_data(v)
        return sanitized_dict

    if isinstance(val, (list, tuple, set)):
        sanitized_seq = [redact_data(item) for item in val]
        return type(val)(sanitized_seq)

    return val


# Standard attributes of logging.LogRecord to ignore when extracting extra
_LOGRECORD_RESERVED_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def redaction_processor(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Structlog processor that redacts PII and secrets from all fields of event_dict."""
    # If this is a foreign stdlib log record, ensure any extra attributes are extracted
    record = event_dict.get("_record")
    if record is not None and hasattr(record, "__dict__"):
        for k, v in list(record.__dict__.items()):
            if k not in _LOGRECORD_RESERVED_ATTRS and k not in event_dict:
                event_dict[k] = v

    for k, v in list(event_dict.items()):
        if k.startswith("_"):
            continue
        k_str = str(k).lower().strip()
        if k_str in SENSITIVE_KEYS or any(
            s in k_str for s in ("secret", "password", "api_key", "token")
        ):
            event_dict[k] = "[REDACTED_TOKEN]"
        else:
            event_dict[k] = redact_data(v)
    return event_dict
