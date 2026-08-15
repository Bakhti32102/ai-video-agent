"""A small, dependency-free Result type for structured success/failure returns.

Agents, services and guardrails return :class:`Result` objects instead of
raising for expected, recoverable failures. Critical/unrecoverable errors may
still raise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class Result(Generic[T]):
    """Structured outcome of an operation.

    Attributes:
        success: Whether the operation succeeded.
        data: The payload on success.
        errors: Human-readable error messages.
        warnings: Non-fatal warnings.
        metadata: Arbitrary structured context (e.g. validation rule names).
    """

    success: bool
    data: T | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @classmethod
    def ok(cls, data: T | None = None, *, warnings: list[str] | None = None, metadata: dict | None = None) -> "Result[T]":
        return cls(success=True, data=data, warnings=warnings or [], metadata=metadata or {})

    @classmethod
    def fail(
        cls,
        *errors: str,
        data: T | None = None,
        warnings: list[str] | None = None,
        metadata: dict | None = None,
    ) -> "Result[T]":
        return cls(
            success=False,
            data=data,
            errors=list(errors),
            warnings=warnings or [],
            metadata=metadata or {},
        )

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.success = False

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    @property
    def is_failure(self) -> bool:
        return not self.success

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "data": self.data,
            "errors": self.errors,
            "warnings": self.warnings,
            "metadata": self.metadata,
        }


__all__ = ["Result"]
