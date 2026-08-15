"""Custom exception hierarchy for the AI Video Agent.

Errors are intentionally structured so that agents and the supervisor can
return them as part of structured results instead of leaking tracebacks to
callers. Every error carries a machine-readable ``code`` and optional
``details`` dict.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all application errors."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "APP_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Serialize the error to a machine-readable dict."""
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class ConfigError(AppError):
    """Raised when configuration is invalid or missing."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="CONFIG_ERROR", details=details)


class ValidationError(AppError):
    """Raised by guardrails when data fails validation."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="VALIDATION_ERROR", details=details)


class GuardrailError(ValidationError):
    """A guardrail-specific validation failure."""

    def __init__(self, message: str, rule: str = "unknown", details: dict[str, Any] | None = None) -> None:
        details = {**(details or {}), "rule": rule}
        super().__init__(message, details=details)
        self.rule = rule


class DatabaseError(AppError):
    """Raised on database-level failures."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="DATABASE_ERROR", details=details)


class AgentError(AppError):
    """Raised when an agent fails to produce valid output."""

    def __init__(self, message: str, agent: str = "unknown", details: dict[str, Any] | None = None) -> None:
        details = {**(details or {}), "agent": agent}
        super().__init__(message, code="AGENT_ERROR", details=details)
        self.agent = agent


class McpError(AppError):
    """Raised on MCP transport / protocol failures."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="MCP_ERROR", details=details)


class GeoError(AppError):
    """Raised when geographic data is invalid or unverifiable."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="GEO_ERROR", details=details)


class MediaError(AppError):
    """Raised when media files are invalid, unsupported, or corrupt."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="MEDIA_ERROR", details=details)


class RenderError(AppError):
    """Raised when video rendering fails."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="RENDER_ERROR", details=details)


class WorkflowError(AppError):
    """Raised when an invalid workflow state transition is attempted."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="WORKFLOW_ERROR", details=details)


class FileSafetyError(AppError):
    """Raised when a file path violates security constraints (traversal, etc.)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="FILE_SAFETY_ERROR", details=details)


__all__ = [
    "AgentError",
    "AppError",
    "ConfigError",
    "DatabaseError",
    "FileSafetyError",
    "GeoError",
    "GuardrailError",
    "MediaError",
    "McpError",
    "RenderError",
    "ValidationError",
    "WorkflowError",
]
