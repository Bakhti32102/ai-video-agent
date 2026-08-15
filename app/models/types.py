"""Shared SQLAlchemy type aliases and helpers for ORM models."""

from __future__ import annotations

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column


def json_column() -> Mapped[dict | None]:
    """A nullable JSON column used for flexible structured payloads."""
    return mapped_column(JSON, nullable=True)


def str_column(length: int = 255, nullable: bool = False) -> Mapped[str]:
    return mapped_column(String(length), nullable=nullable)


def text_column(nullable: bool = True) -> Mapped[str | None]:
    return mapped_column(Text, nullable=nullable)
