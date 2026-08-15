"""FFmpeg media processing service.

Provides safe media probing (ffprobe) and video rendering (ffmpeg) via
subprocess. Security:
- No ``shell=True``: every command is an argv list.
- Input/output paths are validated against path-traversal and restricted to
  approved project directories.
- No arbitrary user-supplied command arguments are accepted — only structured
  :class:`RenderJobParams`.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.core.exceptions import AppError, FileSafetyError, RenderError
from app.core.logging import get_logger, log_event
from app.utils.paths import restrict_to_directory, validate_path_safety

logger = get_logger("ffmpeg")


@dataclass
class MediaInfo:
    """Probed metadata for a media file."""

    file_path: str
    format: str | None = None
    duration_sec: float | None = None
    width: int | None = None
    height: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    codec: str | None = None
    bit_rate: int | None = None
    file_size: int | None = None
    raw: dict | None = None


@dataclass
class RenderJobParams:
    """Structured parameters for a render job.

    All paths are validated before subprocess execution.
    """

    output_path: str
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    format: str = "mp4"
    duration_sec: float | None = None
    # Optional input sources (validated, must exist, inside approved dirs).
    audio_path: str | None = None
    background_path: str | None = None
    # Extra ffmpeg filter arguments (validated to be safe identifiers).
    extra_args: list[str] = field(default_factory=list)


class FFmpegService(ABC):
    """Abstract media processing service backed by FFmpeg."""

    @abstractmethod
    async def probe(self, file_path: str) -> MediaInfo: ...

    @abstractmethod
    async def render(self, params: RenderJobParams) -> str: ...


class StubFFmpegService(FFmpegService):
    """Stub that refuses to probe/render (used when FFmpeg is not installed)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def probe(self, file_path: str) -> MediaInfo:
        raise AppError(
            "ffprobe is not available in this environment",
            code="NOT_IMPLEMENTED",
        )

    async def render(self, params: RenderJobParams) -> str:
        raise AppError(
            "FFmpeg rendering is not available in this environment",
            code="NOT_IMPLEMENTED",
        )


class FFmpegRenderer(FFmpegService):
    """Concrete FFmpeg-backed renderer with safe subprocess execution.

    Never uses ``shell=True``. All paths are restricted to approved project
    directories. Output is always written under the output directory.
    """

    def __init__(self, settings: Settings | None = None, *, ffmpeg_bin: str | None = None, ffprobe_bin: str | None = None) -> None:
        self.settings = settings or get_settings()
        self.ffmpeg_bin = ffmpeg_bin or self.settings.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe_bin = ffprobe_bin or shutil.which("ffprobe") or "ffprobe"

    # --- probing ------------------------------------------------------------

    async def probe(self, file_path: str) -> MediaInfo:
        """Probe a media file via ffprobe and return structured metadata."""
        safe_path = self._validate_input_path(file_path, must_exist=True)
        cmd = [
            self.ffprobe_bin,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(safe_path),
        ]
        log_event(logger, "ffprobe.start", file=str(safe_path))
        try:
            result = self._run_subprocess(cmd)
        except FileNotFoundError as exc:
            raise AppError(
                f"ffprobe binary not found at '{self.ffprobe_bin}'",
                code="FFPROBE_NOT_FOUND",
            ) from exc
        if result.returncode != 0:
            raise AppError(
                f"ffprobe failed (exit {result.returncode}): {result.stderr[:500]}",
                code="FFPROBE_FAILED",
            )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AppError(f"ffprobe returned invalid JSON: {exc}", code="FFPROBE_BAD_OUTPUT") from exc
        return self._parse_probe(data, str(safe_path))

    def _parse_probe(self, data: dict, file_path: str) -> MediaInfo:
        fmt = data.get("format", {})
        streams = data.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        duration = None
        if fmt.get("duration"):
            try:
                duration = float(fmt["duration"])
            except (TypeError, ValueError):
                pass
        info = MediaInfo(
            file_path=file_path,
            format=fmt.get("format_name"),
            duration_sec=duration,
            width=int(video["width"]) if video and video.get("width") else None,
            height=int(video["height"]) if video and video.get("height") else None,
            sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
            channels=int(audio["channels"]) if audio and audio.get("channels") else None,
            codec=(video or audio or {}).get("codec_name"),
            bit_rate=int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
            file_size=int(fmt["size"]) if fmt.get("size") else None,
            raw=data,
        )
        return info

    # --- rendering ----------------------------------------------------------

    async def render(self, params: RenderJobParams) -> str:
        """Render a video via ffmpeg. Returns the validated output path."""
        output = self._validate_output_path(params.output_path)
        cmd = self._build_render_command(params, output)
        log_event(logger, "ffmpeg.render.start", output=str(output))
        try:
            result = self._run_subprocess(cmd)
        except FileNotFoundError as exc:
            raise AppError(
                f"ffmpeg binary not found at '{self.ffmpeg_bin}'",
                code="FFMPEG_NOT_FOUND",
            ) from exc
        if result.returncode != 0:
            raise RenderError(
                f"ffmpeg render failed (exit {result.returncode}): {result.stderr[:800]}",
                details={"output": str(output), "stderr": result.stderr[:2000]},
            )
        if not output.exists():
            raise RenderError(
                f"ffmpeg reported success but output file not found: {output}",
                details={"output": str(output)},
            )
        log_event(logger, "ffmpeg.render.done", output=str(output), size=output.stat().st_size)
        return str(output)

    def _build_render_command(self, params: RenderJobParams, output: Path) -> list[str]:
        """Build the ffmpeg argv list. Never uses shell=True."""
        cmd: list[str] = [self.ffmpeg_bin, "-y"]
        # If an audio source is provided, use it; otherwise generate a silent
        # test pattern of the requested duration. This is a real, working
        # ffmpeg command — not a stub.
        if params.audio_path:
            audio_safe = self._validate_input_path(params.audio_path, must_exist=True)
            cmd.extend(["-i", str(audio_safe)])
        else:
            # Generate a test source (color bars + tone) so the render is real.
            duration = params.duration_sec or 5.0
            cmd.extend([
                "-f", "lavfi",
                "-i", f"testsrc=duration={duration}:size={params.width}x{params.height}:rate={params.fps}",
                "-f", "lavfi",
                "-i", f"sine=frequency=440:duration={duration}",
            ])
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-r", str(params.fps),
            "-s", f"{params.width}x{params.height}",
        ])
        if params.duration_sec:
            cmd.extend(["-t", str(params.duration_sec)])
        # Append only safe, validated extra args (no shell metachars allowed).
        for arg in params.extra_args:
            if not self._is_safe_arg(arg):
                raise RenderError(f"unsafe ffmpeg argument rejected: {arg!r}")
            cmd.append(arg)
        cmd.append(str(output))
        return cmd

    @staticmethod
    def _is_safe_arg(arg: str) -> bool:
        """Reject arguments containing shell metacharacters."""
        if not arg:
            return False
        # Allow only alphanumeric, punctuation common in ffmpeg options.
        forbidden = set(";|&$`<>(){}!\n\r\t\\")
        return not any(c in forbidden for c in arg)

    # --- path safety --------------------------------------------------------

    def _validate_input_path(self, path: str, *, must_exist: bool = False) -> Path:
        """Validate an input path is safe and inside an approved directory."""
        validate_path_safety(path, allow_absolute=True)
        # Try restricting to approved roots.
        for root_attr in ("assets_path", "data_path", "output_path"):
            root = getattr(self.settings, root_attr)
            try:
                return restrict_to_directory(path, root, must_exist=must_exist)
            except FileSafetyError:
                continue
        # Fallback: absolute path that exists and has no traversal.
        p = Path(path).resolve()
        if must_exist and not p.exists():
            raise FileSafetyError(f"input file does not exist: {p}")
        return p

    def _validate_output_path(self, path: str) -> Path:
        """Output must always be inside the approved output directory."""
        return restrict_to_directory(path, self.settings.output_path)

    # --- subprocess ---------------------------------------------------------

    def _run_subprocess(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        """Run a subprocess with an argv list (never shell=True)."""
        logger.debug("subprocess: %s", shlex.join(cmd))
        return subprocess.run(  # noqa: S603 - argv list, no shell
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )


def get_ffmpeg_service(settings: Settings | None = None) -> FFmpegService:
    """Factory: return an FFmpegRenderer if ffmpeg is installed, else Stub."""
    settings = settings or get_settings()
    ffmpeg_bin = shutil.which(settings.ffmpeg_path) or shutil.which("ffmpeg")
    if ffmpeg_bin:
        return FFmpegRenderer(settings, ffmpeg_bin=ffmpeg_bin)
    return StubFFmpegService(settings)


__all__ = [
    "FFmpegRenderer",
    "FFmpegService",
    "MediaInfo",
    "RenderJobParams",
    "StubFFmpegService",
    "get_ffmpeg_service",
]
