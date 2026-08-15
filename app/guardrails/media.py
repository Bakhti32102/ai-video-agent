"""Supported media formats and file-path helpers for guardrails."""

from __future__ import annotations

from pathlib import Path

# Media formats the pipeline can actually consume in Phase 1.
SUPPORTED_IMAGE_FORMATS: frozenset[str] = frozenset({"svg", "png", "webp", "jpg", "jpeg"})
SUPPORTED_AUDIO_FORMATS: frozenset[str] = frozenset({"mp3", "wav", "aac"})
SUPPORTED_VIDEO_FORMATS: frozenset[str] = frozenset({"mp4", "webm", "mov"})
SUPPORTED_GEO_FORMATS: frozenset[str] = frozenset({"geojson"})
SUPPORTED_FONT_FORMATS: frozenset[str] = frozenset({"ttf", "otf"})

ALL_SUPPORTED_FORMATS: frozenset[str] = (
    SUPPORTED_IMAGE_FORMATS
    | SUPPORTED_AUDIO_FORMATS
    | SUPPORTED_VIDEO_FORMATS
    | SUPPORTED_GEO_FORMATS
    | SUPPORTED_FONT_FORMATS
)


def file_extension(path: str) -> str:
    """Return the lowercased extension (without dot) of a path."""
    return Path(path).suffix.lower().lstrip(".")


def is_supported_media(path: str, allowed: frozenset[str] | None = None) -> bool:
    """Return True if the file extension is in the allowed set (or any supported)."""
    ext = file_extension(path)
    if not ext:
        return False
    pool = allowed if allowed is not None else ALL_SUPPORTED_FORMATS
    return ext in pool


__all__ = [
    "ALL_SUPPORTED_FORMATS",
    "SUPPORTED_AUDIO_FORMATS",
    "SUPPORTED_FONT_FORMATS",
    "SUPPORTED_GEO_FORMATS",
    "SUPPORTED_IMAGE_FORMATS",
    "SUPPORTED_VIDEO_FORMATS",
    "file_extension",
    "is_supported_media",
]
