"""FFmpeg service interface (Phase 1 stub).

Defines the contract for media probing and rendering. The actual FFmpeg
integration is implemented in Phase 2. Keeping the interface here lets the
Render/QA agents depend on an abstraction rather than a concrete implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.config import Settings, get_settings
from app.core.exceptions import AppError


@dataclass
class MediaInfo:
    """Probed metadata for a media file."""

    file_path: str
    format: str | None
    duration_sec: float | None
    width: int | None
    height: int | None
    sample_rate: int | None
    channels: int | None
    raw: dict | None = None


class FFmpegService(ABC):
    """Abstract media processing service backed by FFmpeg."""

    @abstractmethod
    async def probe(self, file_path: str) -> MediaInfo: ...

    @abstractmethod
    async def render(self, job_params: dict[str, Any]) -> str: ...


class StubFFmpegService(FFmpegService):
    """Phase 1 stub: refuses to probe/render until FFmpeg is wired up."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def probe(self, file_path: str) -> MediaInfo:
        raise AppError(
            "ffprobe is not implemented in Phase 1; use app.services.ffmpeg.FFmpegService in Phase 2",
            code="NOT_IMPLEMENTED",
        )

    async def render(self, job_params: dict[str, Any]) -> str:
        raise AppError(
            "FFmpeg rendering is not implemented in Phase 1",
            code="NOT_IMPLEMENTED",
        )


__all__ = ["FFmpegService", "MediaInfo", "StubFFmpegService"]
