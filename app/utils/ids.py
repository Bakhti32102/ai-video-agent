"""ID generation utilities.

Uses UUIDv4 by default. Centralised so the format is consistent across agents
and the database.
"""

from __future__ import annotations

import uuid


def new_id(prefix: str = "") -> str:
    """Return a new unique identifier, optionally prefixed.

    The returned string always matches the ``^[A-Za-z0-9_\\-]{1,64}$`` contract
    used by Pydantic ID fields.
    """
    raw = uuid.uuid4().hex
    return f"{prefix}{raw}" if prefix else raw


__all__ = ["new_id"]
