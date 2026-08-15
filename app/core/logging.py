"""Structured logging setup for the AI Video Agent.

Configures a console logger whose level is driven by ``LOG_LEVEL`` from the
environment. All application modules should use :func:`get_logger`.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from app.config import PROJECT_ROOT, Settings, get_settings

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_CONFIGURED = False


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
    console.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
    root.addHandler(console)

    # Optional file handler
    try:
        settings.ensure_runtime_dirs()
        log_file = Path(settings.logs_path) / "ai_video_agent.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
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


def reset_logging() -> None:
    """Reset logging state (used by tests)."""
    global _CONFIGURED
    logging.getLogger("ai_video_agent").handlers.clear()
    _CONFIGURED = False


__all__ = ["PROJECT_ROOT", "get_logger", "reset_logging"]
