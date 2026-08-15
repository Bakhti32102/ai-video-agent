"""Structured logging setup for the AI Video Agent.

Configures a console logger whose level is driven by ``LOG_LEVEL`` from the
environment. All application modules should use :func:`get_logger`.

Security: log records pass through :class:`SecretRedactingFormatter`, which
masks values that look like API keys, tokens, passwords, or connection
strings before they reach any handler. This prevents accidental secret
leakage in log files.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

from app.config import PROJECT_ROOT, Settings, get_settings

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_CONFIGURED = False


# Patterns whose matched substrings are redacted from log output.
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Generic key=value assignments: api_key=..., token=..., password=...
    (
        re.compile(
            r"(?i)\b(api[_-]?key|token|secret|password|passwd|authorization|"
            r"access[_-]?token|refresh[_-]?token|private[_-]?key|client[_-]?secret)\b"
            r"\s*[:=]\s*['\"]?[^\s'\",;]+",
        ),
        r"\1=***REDACTED***",
    ),
    # Bearer tokens in headers.
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"), "bearer ***REDACTED***"),
    # AWS-style keys (20-char uppercase access key id).
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA***REDACTED***"),
    # Generic long hex/base64 secrets (40+ chars, typical of API keys).
    (re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"), "***REDACTED***"),
]

_REDACTED_PLACEHOLDER = "***REDACTED***"


def redact_secrets(message: str) -> str:
    """Redact secret-like substrings from ``message``."""
    if not message:
        return message
    redacted = message
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


class SecretRedactingFormatter(logging.Formatter):
    """Log formatter that redacts secret-like values from the message."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        record.msg = redact_secrets(str(record.msg))
        if record.args:
            record.args = tuple(redact_secrets(str(a)) if isinstance(a, str) else a for a in record.args) if isinstance(record.args, tuple) else record.args
        return super().format(record)


def _configure_logging(settings: Settings) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    root = logging.getLogger("ai_video_agent")
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(SecretRedactingFormatter(_LOG_FORMAT, _DATE_FORMAT))
    root.addHandler(console)

    # Optional file handler
    try:
        settings.ensure_runtime_dirs()
        log_file = Path(settings.logs_path) / "ai_video_agent.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(SecretRedactingFormatter(_LOG_FORMAT, _DATE_FORMAT))
        root.addHandler(file_handler)
    except OSError:
        # Logging must never break the app; fall back to console only.
        pass

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger under the application namespace."""
    settings = get_settings()
    _configure_logging(settings)
    if not name.startswith("ai_video_agent"):
        name = f"ai_video_agent.{name}"
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    """Emit a structured event log line with key=value fields.

    Example::

        log_event(logger, "agent.started", agent="script", project_id="proj_1")
        # -> agent.started | agent=script project_id=proj_1
    """
    parts = [f"{k}={v}" for k, v in fields.items()]
    msg = event
    if parts:
        msg = f"{event} | {' '.join(parts)}"
    logger.log(level, msg)


def reset_logging() -> None:
    """Reset logging state (used by tests)."""
    global _CONFIGURED
    logging.getLogger("ai_video_agent").handlers.clear()
    _CONFIGURED = False


__all__ = ["PROJECT_ROOT", "SecretRedactingFormatter", "get_logger", "log_event", "redact_secrets", "reset_logging"]
