"""Tests for logging: secret redaction and structured event logging."""

from __future__ import annotations

import logging

from app.core.logging import (
    SecretRedactingFormatter,
    get_logger,
    log_event,
    redact_secrets,
    reset_logging,
)


class TestRedactSecrets:
    def test_api_key_redacted(self) -> None:
        msg = "api_key=sk-abc123def456ghi789jkl012mno345pqr789"
        redacted = redact_secrets(msg)
        assert "sk-abc123" not in redacted
        assert "***REDACTED***" in redacted

    def test_password_redacted(self) -> None:
        msg = "password=secret123"
        redacted = redact_secrets(msg)
        assert "secret123" not in redacted

    def test_token_redacted(self) -> None:
        msg = "token=ghp_1234567890abcdefghijklmnopqrstuvwxyz"
        redacted = redact_secrets(msg)
        assert "ghp_1234567890" not in redacted

    def test_bearer_token_redacted(self) -> None:
        msg = "Authorization: Bearer abc123def456ghi789"
        redacted = redact_secrets(msg)
        # The value should be redacted.
        assert "***REDACTED***" in redacted

    def test_aws_key_redacted(self) -> None:
        msg = "found AKIAABCDEFGHIJKLMNOP in config"
        redacted = redact_secrets(msg)
        assert "AKIAABCDEFGHIJKLMNOP" not in redacted

    def test_normal_message_unchanged(self) -> None:
        msg = "agent script completed successfully"
        assert redact_secrets(msg) == msg

    def test_empty_message(self) -> None:
        assert redact_secrets("") == ""

    def test_client_secret_redacted(self) -> None:
        msg = "client_secret=myverylongsecretvalue1234567890abcdefghij"
        redacted = redact_secrets(msg)
        assert "myverylongsecretvalue" not in redacted


class TestSecretRedactingFormatter:
    def test_formatter_redacts_in_record(self) -> None:
        formatter = SecretRedactingFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="api_key=sk-secret1234567890abcdefghijklmnopqrstuv",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        assert "sk-secret" not in formatted
        assert "***REDACTED***" in formatted


class TestLogEvent:
    def test_emits_event_with_fields(self) -> None:
        logger = get_logger("test_event")
        # Just verify it doesn't raise.
        log_event(logger, "agent.started", agent="script", project_id="proj_1")

    def test_emits_event_without_fields(self) -> None:
        logger = get_logger("test_event_no_fields")
        log_event(logger, "simple.event")

    def test_emits_at_warning_level(self) -> None:
        logger = get_logger("test_event_warn")
        log_event(logger, "agent.failed", level=logging.WARNING, errors="timeout")
