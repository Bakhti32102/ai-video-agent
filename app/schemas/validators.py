"""Shared Pydantic field validators reused across data contracts."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from pydantic import field_validator

# ISO-8601-ish date (YYYY[-MM[-DD]])
_DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def non_empty_str(value: str | None) -> str | None:
    if value is None:
        return value
    stripped = value.strip()
    if not stripped:
        raise ValueError("string must not be empty or whitespace-only")
    return stripped


def validate_date_string(value: str | None) -> str | None:
    if value is None:
        return None
    if not _DATE_RE.match(value):
        raise ValueError("date must be in YYYY or YYYY-MM or YYYY-MM-DD format")
    return value


def validate_latitude(value: float) -> float:
    if not -90.0 <= value <= 90.0:
        raise ValueError("latitude must be between -90 and 90")
    return value


def validate_longitude(value: float) -> float:
    if not -180.0 <= value <= 180.0:
        raise ValueError("longitude must be between -180 and 180")
    return value


def validate_duration(value: float) -> float:
    if value < 0:
        raise ValueError("duration must be non-negative")
    return value


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "non_empty_str",
    "validate_date_string",
    "validate_duration",
    "validate_latitude",
    "validate_longitude",
    "utc_now_iso",
]
