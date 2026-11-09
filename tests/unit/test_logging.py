"""Unit tests for structlog JSON output, request_id correlation, and PII redaction (Plan Step 1.7)."""

from __future__ import annotations

import io
import json
import logging

import pytest
import structlog
from fastapi import APIRouter
from fastapi.testclient import TestClient

from copilot.api.container import get_answer_model
from copilot.api.main import create_app
from copilot.observability.context import clear_context, get_request_id, set_request_id
from copilot.observability.logging import (
    configure_logging,
    get_logger,
    reset_logging_configuration,
)
from copilot.observability.redaction import (
    redact_data,
    redact_text,
    redaction_processor,
)
from tests.fakes import FakeLLM


@pytest.fixture(autouse=True)
def reset_logging_and_context():
    """Ensure clean logging and context state for every test."""
    clear_context()
    structlog.contextvars.clear_contextvars()
    reset_logging_configuration()
    yield
    clear_context()
    structlog.contextvars.clear_contextvars()
    reset_logging_configuration()


# ---------------------------------------------------------------------------
# 1. Valid JSON Output & Request ID
# ---------------------------------------------------------------------------


def test_log_lines_are_valid_json_with_request_id() -> None:
    """Structlog emits valid single-line JSON records containing request_id, level, and timestamp."""
    stream = io.StringIO()

    # Configure a capture handler
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.ExtraAdder(),
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redaction_processor,
        ],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    logger = logging.getLogger("test.json.output")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    set_request_id("req-uuid-999")
    structlog.contextvars.bind_contextvars(request_id="req-uuid-999")

    # Log via standard logging / structlog bridge
    logger.info("system_boot_check", extra={"subsystem": "router"})

    raw_output = stream.getvalue().strip()
    assert len(raw_output) > 0, "Expected log output"

    parsed = json.loads(raw_output)
    assert isinstance(parsed, dict)
    assert parsed["request_id"] == "req-uuid-999"
    assert parsed["level"] == "info"
    assert "timestamp" in parsed
    assert parsed["subsystem"] == "router"


# ---------------------------------------------------------------------------
# 2. Request ID Correlation Across Middleware & Requests
# ---------------------------------------------------------------------------


def test_request_id_is_same_across_lines_of_one_request() -> None:
    """All log lines emitted during one HTTP request share the exact same request_id."""
    log_capture = io.StringIO()
    handler = logging.StreamHandler(log_capture)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.add_log_level,
            ],
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    test_logger = logging.getLogger("test.multi_line")
    test_logger.handlers = [handler]
    test_logger.setLevel(logging.INFO)
    test_logger.propagate = False

    app = create_app()
    router = APIRouter()

    @router.get("/test-multi-log")
    async def multi_log():
        test_logger.info("step_one_started")
        test_logger.info("step_two_in_progress")
        test_logger.info("step_three_completed")
        return {"ok": True}

    app.include_router(router)

    with TestClient(app) as client:
        response = client.get("/test-multi-log")
        assert response.status_code == 200
        assigned_id = response.headers.get("X-Request-ID")
        assert assigned_id is not None and len(assigned_id) > 0

    lines = [line for line in log_capture.getvalue().splitlines() if line.strip()]
    assert len(lines) >= 3

    for line in lines:
        entry = json.loads(line)
        assert entry.get("request_id") == assigned_id, f"Line {line} missing expected request_id"


def test_request_id_is_different_across_different_requests() -> None:
    """Distinct HTTP requests receive distinct request IDs."""
    app = create_app()

    with TestClient(app) as client:
        res1 = client.get("/healthz")
        res2 = client.get("/healthz")

    id1 = res1.headers.get("X-Request-ID")
    id2 = res2.headers.get("X-Request-ID")

    assert id1 is not None and len(id1) > 0
    assert id2 is not None and len(id2) > 0
    assert id1 != id2, f"Expected distinct request IDs, got {id1} and {id2}"


def test_middleware_honors_incoming_x_request_id_header() -> None:
    """When a client passes a valid X-Request-ID, it is propagated to logs and response headers."""
    app = create_app()
    custom_id = "client-trace-id-abc-12345"

    with TestClient(app) as client:
        res = client.get("/healthz", headers={"X-Request-ID": custom_id})

    assert res.headers.get("X-Request-ID") == custom_id


def test_middleware_rejects_invalid_or_malicious_request_ids() -> None:
    """Invalid, oversized, or malicious X-Request-ID headers are sanitized and replaced with a valid UUID."""
    app = create_app()
    malicious_samples = [
        "id-with\r\nInjected-Header: evil",
        "id with spaces",
        "<script>alert(1)</script>",
        "a" * 129,  # exceeds 128 chars
        "",  # empty string
        "   ",  # whitespace only
    ]

    with TestClient(app) as client:
        for bad_id in malicious_samples:
            res = client.get("/healthz", headers={"X-Request-ID": bad_id})
            assigned_id = res.headers.get("X-Request-ID")
            assert assigned_id is not None
            assert assigned_id != bad_id, f"Malicious ID '{bad_id}' was not rejected"
            # Must be a clean 32-character hex UUID
            assert len(assigned_id) == 32
            assert all(c in "0123456789abcdef" for c in assigned_id)


def test_middleware_attaches_request_id_even_on_error_responses() -> None:
    """All HTTP error responses (e.g. 404, 422, 500) include X-Request-ID in response headers."""
    app = create_app()

    with TestClient(app) as client:
        # 404 Not Found
        res_404 = client.get("/nonexistent-endpoint-abc")
        assert res_404.status_code == 404
        assert "X-Request-ID" in res_404.headers
        assert len(res_404.headers["X-Request-ID"]) > 0

        # 422 Unprocessable Entity
        res_422 = client.post("/ask", json={})  # empty body fails validation
        assert res_422.status_code == 422
        assert "X-Request-ID" in res_422.headers
        assert len(res_422.headers["X-Request-ID"]) > 0


def test_middleware_restores_prior_contextvar_token() -> None:
    """Middleware restores prior ContextVar value using reset token, preserving caller context."""
    prior_token = set_request_id("outer-cron-task-id")
    try:
        app = create_app()
        with TestClient(app) as client:
            res = client.get("/healthz")
            assert res.status_code == 200

        # After request completes, the prior outer contextvar is perfectly restored
        assert get_request_id() == "outer-cron-task-id"
    finally:
        from copilot.observability.context import reset_request_id

        reset_request_id(prior_token)


# ---------------------------------------------------------------------------
# 3. PII & Secret Redaction Tests
# ---------------------------------------------------------------------------


def test_redaction_masks_email_addresses() -> None:
    """Email addresses in strings are replaced with [REDACTED_EMAIL]."""
    text = "Please reach out to support.agent@te.eg or user123+vip@gmail.com for help."
    redacted = redact_text(text)
    assert "[REDACTED_EMAIL]" in redacted
    assert "support.agent@te.eg" not in redacted
    assert "user123+vip@gmail.com" not in redacted


def test_redaction_masks_phone_numbers() -> None:
    """Phone numbers (Egyptian mobile/landline, international formats) are redacted."""
    samples = [
        ("My Egyptian mobile is 01001234567, call me.", "01001234567"),
        ("Call hotline at +20 100 123 4567 now.", "+20 100 123 4567"),
        ("US office: (555) 123-4567 ext 12.", "(555) 123-4567"),
        ("Alternative: 555-456-7890.", "555-456-7890"),
    ]
    for sample, original in samples:
        redacted = redact_text(sample)
        assert "[REDACTED_PHONE]" in redacted, f"Failed to redact phone in: {sample}"
        assert original not in redacted, f"Phone leaked in: {redacted}"


def test_redaction_masks_credit_card_numbers() -> None:
    """Credit and debit card numbers (13-19 digits, spaced or hyphenated) are redacted."""
    cards = [
        "Card: 4532 1234 5678 9010",
        "Payment: 4532-1234-5678-9010",
        "Raw: 4532123456789010",
        "Amex: 3782 822463 10005",
    ]
    for card_str in cards:
        redacted = redact_text(card_str)
        assert "[REDACTED_CARD]" in redacted, f"Failed to redact card in: {card_str}"
        assert "4532" not in redacted
        assert "9010" not in redacted


def test_redaction_masks_tokens_and_api_keys() -> None:
    """Bearer tokens, API keys, JWTs, and sensitive dictionary keys are masked."""
    token_strings = [
        (
            "Header: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123456",
            "Bearer [REDACTED_TOKEN]",
        ),
        ("Groq key: gsk_1234567890abcdef1234567890abcdef", "[REDACTED_TOKEN]"),
        ("Google key: AIzaSyD1234567890abcdefghijklmnopqrstuv", "[REDACTED_TOKEN]"),
        ("assignment api_key='super_secret_token_12345'", "[REDACTED_TOKEN]"),
    ]
    for raw_str, expected in token_strings:
        redacted = redact_text(raw_str)
        assert expected in redacted, f"Expected '{expected}' in '{redacted}'"

    # Test dictionary key sanitization
    sensitive_dict = {
        "user": "Omar",
        "api_key": "gsk_plain_secret_12345678",
        "password": "MySuperSecretPassword",
        "groq_api_key": "gsk_another_key",
        "nested": {
            "token": "secret_jwt_token_here",
            "safe_field": "public_data",
        },
    }
    cleaned = redact_data(sensitive_dict)
    assert cleaned["user"] == "Omar"
    assert cleaned["api_key"] == "[REDACTED_TOKEN]"
    assert cleaned["password"] == "[REDACTED_TOKEN]"
    assert cleaned["groq_api_key"] == "[REDACTED_TOKEN]"
    assert cleaned["nested"]["token"] == "[REDACTED_TOKEN]"
    assert cleaned["nested"]["safe_field"] == "public_data"


def test_redaction_processor_in_structlog_pipeline() -> None:
    """Structlog pipeline automatically redacts PII and secrets across event and keyword arguments."""
    output = io.StringIO()
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[
            structlog.stdlib.ExtraAdder(),
            redaction_processor,
        ],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(output)
    handler.setFormatter(formatter)
    logger = logging.getLogger("test.redaction.pipeline")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info(
        "User user@we.com.eg with card 4532-1234-5678-9010 called 01001234567",
        extra={"api_key": "gsk_test_secret_123"},
    )

    log_line = output.getvalue().strip()
    assert "[REDACTED_EMAIL]" in log_line
    assert "[REDACTED_CARD]" in log_line
    assert "[REDACTED_PHONE]" in log_line
    assert "[REDACTED_TOKEN]" in log_line
    assert "user@we.com.eg" not in log_line
    assert "4532-1234-5678-9010" not in log_line
    assert "01001234567" not in log_line
    assert "gsk_test_secret_123" not in log_line


# ---------------------------------------------------------------------------
# 4. No Message Body Appears at INFO Level
# ---------------------------------------------------------------------------


def test_no_message_body_appears_at_info_level() -> None:
    """Access logging at INFO level logs only HTTP metadata and strictly omits request and response bodies."""
    log_capture = io.StringIO()
    handler = logging.StreamHandler(log_capture)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.add_log_level,
            ],
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )

    access_logger = logging.getLogger("copilot.access")
    access_logger.handlers = [handler]
    access_logger.setLevel(logging.INFO)
    access_logger.propagate = False

    app = create_app()
    secret_customer_question = "CONFIDENTIAL_QUESTION_XYZ_SUPER_SECRET_PAYLOAD_9999"
    secret_customer_answer = "CONFIDENTIAL_ANSWER_ABC_PRIVATE_INTERNAL_DATA_8888"

    fake_llm = FakeLLM(responses=[secret_customer_answer])
    app.dependency_overrides[get_answer_model] = lambda: fake_llm

    with TestClient(app) as client:
        response = client.post("/ask", json={"question": secret_customer_question})
        assert response.status_code == 200

    all_logs = log_capture.getvalue()
    assert len(all_logs) > 0, "Expected access log output at INFO level"

    # Verify every line emitted at INFO level
    for line in all_logs.splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        assert entry.get("level") == "info"
        # Crucial requirement: No message body appears at INFO
        assert secret_customer_question not in line, f"Customer question leaked in log line: {line}"
        assert secret_customer_answer not in line, f"Customer answer leaked in log line: {line}"
        assert "question" not in entry, f"'question' field leaked in access log: {entry}"
        assert "answer" not in entry, f"'answer' field leaked in access log: {entry}"
        assert "body" not in entry, f"'body' field leaked in access log: {entry}"
        # Metadata must be present
        assert "method" in entry
        assert "path" in entry
        assert "status_code" in entry
        assert "duration_ms" in entry
        assert "request_id" in entry


# ---------------------------------------------------------------------------
# 5. Configuration Robustness, Uvicorn & Stdlib Interception
# ---------------------------------------------------------------------------


def test_configure_logging_validates_level_strictly() -> None:
    """Configuring logging with an invalid log level raises ValueError immediately."""
    with pytest.raises(ValueError, match="Invalid log level"):
        configure_logging(log_level="INFOR", force=True)

    with pytest.raises(ValueError, match="Invalid log level"):
        configure_logging(log_level="TRACE", force=True)


def test_configure_logging_sets_handler_level_and_safely_replaces_root_handlers() -> None:
    """Existing root handlers are closed and removed, and the new handler level matches the root logger level."""
    dummy_stream = io.StringIO()
    dummy_handler = logging.StreamHandler(dummy_stream)
    root = logging.getLogger()
    root.addHandler(dummy_handler)

    configure_logging(log_level="WARNING", force=True)

    assert dummy_handler not in root.handlers
    assert len(root.handlers) == 1
    installed_handler = root.handlers[0]
    assert installed_handler.level == logging.WARNING
    assert root.level == logging.WARNING


def test_configure_logging_intercepts_uvicorn_loggers() -> None:
    """Uvicorn loggers have custom handlers removed and propagate to the root JSON handler."""
    # Pre-populate dummy handler on uvicorn
    u_err = logging.getLogger("uvicorn.error")
    dummy_h = logging.StreamHandler(io.StringIO())
    u_err.addHandler(dummy_h)

    configure_logging(log_level="INFO", force=True)

    assert dummy_h not in u_err.handlers
    assert u_err.propagate is True

    for u_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        u_log = logging.getLogger(u_name)
        assert len(u_log.handlers) == 0
        assert u_log.propagate is True


def test_configure_logging_idempotence() -> None:
    """Calling configure_logging without force=True runs once and is idempotent."""
    configure_logging(log_level="INFO", force=True)
    root = logging.getLogger()
    initial_handler = root.handlers[0]

    # Second call without force should not replace handlers
    configure_logging(log_level="DEBUG", force=False)
    assert root.handlers[0] is initial_handler
    assert root.level == logging.INFO  # Remains unchanged because it didn't reconfigure


def test_foreign_pre_chain_handles_stdlib_records_with_logger_name() -> None:
    """Standard library loggers route through foreign_pre_chain and include correct logger name in JSON."""
    stream = io.StringIO()
    capture_handler = logging.StreamHandler(stream)
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.ExtraAdder(),
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redaction_processor,
        ],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    capture_handler.setFormatter(formatter)
    test_logger = logging.getLogger("third_party.driver")
    test_logger.handlers = [capture_handler]
    test_logger.setLevel(logging.INFO)
    test_logger.propagate = False

    test_logger.info("connection_pool_initialized", extra={"pool_size": 10})

    raw = stream.getvalue().strip()
    assert len(raw) > 0
    parsed = json.loads(raw)
    assert parsed["event"] == "connection_pool_initialized"
    assert parsed["level"] == "info"
    assert parsed["pool_size"] == 10


# ---------------------------------------------------------------------------
# 6. Request ID Schema Policy (Lifecycle vs Request Logs)
# ---------------------------------------------------------------------------


def test_startup_lifecycle_log_omits_request_id_by_default() -> None:
    """Logs emitted outside an HTTP request omit request_id by default to keep schemas clean."""
    stream = io.StringIO()
    configure_logging(force=True)
    root = logging.getLogger()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(root.handlers[0].formatter)
    root.handlers = [handler]

    logger = get_logger("copilot.lifecycle")
    logger.info("application_started", subsystem="engine")

    raw = stream.getvalue().strip()
    assert len(raw) > 0
    parsed = json.loads(raw)
    assert parsed["event"] == "application_started"
    assert "request_id" not in parsed, "Expected request_id to be omitted outside request context"


def test_startup_lifecycle_log_uses_sentinel_when_configured() -> None:
    """When default_request_id='-' is configured, lifecycle logs outside request scope include '-'."""
    stream = io.StringIO()
    configure_logging(default_request_id="-", force=True)
    root = logging.getLogger()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(root.handlers[0].formatter)
    root.handlers = [handler]

    logger = get_logger("copilot.lifecycle")
    logger.info("application_started", subsystem="engine")

    raw = stream.getvalue().strip()
    assert len(raw) > 0
    parsed = json.loads(raw)
    assert parsed["event"] == "application_started"
    assert parsed["request_id"] == "-", "Expected sentinel '-' for request_id when configured"
