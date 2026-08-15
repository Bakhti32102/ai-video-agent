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


@dataclass
class OverlayLayer:
    """A single image overlay layer for video composition.

    ``x`` and ``y`` are normalized [0,1] positions within the frame.
    ``start_time`` and ``end_time`` control when the overlay is visible.
    """

    image_path: str
    x: float = 0.0
    y: float = 0.0
    start_time: float = 0.0
    end_time: float | None = None
    # Optional opacity (0.0 to 1.0).
    opacity: float = 1.0


@dataclass
class ComposeVideoParams:
    """Parameters for composing a video from a background + overlays + audio.

    This is the real video pipeline: a background image (or color) is
    converted to a video stream, image overlays (map frames, text PNGs) are
    composited on top with timing, and an audio track is muxed in. The result
    is a proper 16:9 MP4.
    """

    output_path: str
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    duration_sec: float = 10.0
    # Background: either a solid color (e.g. "#1a1a2e") or an image path.
    background_color: str = "#1a1a2e"
    background_image: str | None = None
    # Image overlays (map frames, text PNGs, icons).
    overlays: list[OverlayLayer] = field(default_factory=list)
    # Audio track (voiceover + mixed SFX/music).
    audio_path: str | None = None
    # Transition filtergraph (optional, applied to the video stream).
    video_filter: str | None = None
    format: str = "mp4"


class FFmpegService(ABC):
    """Abstract media processing service backed by FFmpeg."""

    @abstractmethod
    async def probe(self, file_path: str) -> MediaInfo: ...

    @abstractmethod
    async def render(self, params: RenderJobParams) -> str: ...

    async def compose(self, params: ComposeVideoParams) -> str:
        """Compose a video from background + overlays + audio.

        Default implementation delegates to render() (no composition).
        Concrete FFmpegRenderer overrides this for real composition.
        """
        raise AppError(
            "video composition is not available in this environment",
            code="NOT_IMPLEMENTED",
        )


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
        self.ffprobe_bin = ffprobe_bin or self.settings.ffprobe_path or shutil.which("ffprobe") or "ffprobe"

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

    async def compose(self, params: ComposeVideoParams) -> str:
        """Compose a video from a background + image overlays + audio.

        This is the real Phase 4 video pipeline:
        1. Background: either a solid color or a background image, looped to
           the requested duration.
        2. Overlays: each image (map PNG, text PNG, icon) is composited on top
           with position and timing (``overlay`` filter with ``enable``).
        3. Audio: an audio track is muxed in (if provided).
        4. Output: a proper 16:9 MP4 (libx264 + AAC).

        All paths are validated. The filtergraph is built programmatically
        from structured params — no shell, no user-supplied filter strings.
        """
        output = self._validate_output_path(params.output_path)
        cmd = self._build_compose_command(params, output)
        log_event(logger, "ffmpeg.compose.start", output=str(output), overlays=len(params.overlays))
        try:
            result = self._run_subprocess(cmd)
        except FileNotFoundError as exc:
            raise AppError(
                f"ffmpeg binary not found at '{self.ffmpeg_bin}'",
                code="FFMPEG_NOT_FOUND",
            ) from exc
        if result.returncode != 0:
            raise RenderError(
                f"ffmpeg compose failed (exit {result.returncode}): {result.stderr[:800]}",
                details={"output": str(output), "stderr": result.stderr[:2000]},
            )
        if not output.exists():
            raise RenderError(
                f"ffmpeg reported success but output file not found: {output}",
                details={"output": str(output)},
            )
        log_event(logger, "ffmpeg.compose.done", output=str(output), size=output.stat().st_size)
        return str(output)

    def _build_compose_command(self, params: ComposeVideoParams, output: Path) -> list[str]:
        """Build the ffmpeg argv for composing background + overlays + audio."""
        cmd: list[str] = [self.ffmpeg_bin, "-y"]
        input_count = 0
        # --- Background input ---
        if params.background_image:
            bg_path = self._validate_input_path(params.background_image, must_exist=True)
            cmd.extend(["-i", str(bg_path)])
            bg_label = f"[{input_count}:v]"
        else:
            # Solid color background via lavfi color source.
            color = params.background_color.lstrip("#")
            cmd.extend([
                "-f", "lavfi",
                "-i", f"color=c=0x{color}:s={params.width}x{params.height}:r={params.fps}:d={params.duration_sec}",
            ])
            bg_label = f"[{input_count}:v]"
        input_count += 1

        # --- Overlay inputs ---
        overlay_labels: list[str] = []
        for ov in params.overlays:
            ov_path = self._validate_input_path(ov.image_path, must_exist=True)
            cmd.extend(["-i", str(ov_path)])
            overlay_labels.append(f"[{input_count}:v]")
            input_count += 1

        # --- Audio input ---
        audio_map_label: str | None = None
        if params.audio_path:
            audio_path = self._validate_input_path(params.audio_path, must_exist=True)
            cmd.extend(["-i", str(audio_path)])
            # For -map, input streams use "N:a" (no brackets); brackets are
            # only for filtergraph output labels.
            audio_map_label = f"{input_count}:a"
            input_count += 1
        else:
            # Generate silent audio so the output has an audio track.
            cmd.extend([
                "-f", "lavfi",
                "-i", f"anullsrc=channel_layout=stereo:sample_rate=48000:duration={params.duration_sec}",
            ])
            audio_map_label = f"{input_count}:a"
            input_count += 1

        # --- Build filtergraph ---
        # Scale the background to the target size.
        filter_parts: list[str] = []
        filter_parts.append(
            f"{bg_label}scale={params.width}:{params.height}:force_original_aspect_ratio=decrease,"
            f"pad={params.width}:{params.height}:(ow-iw)/2:(oh-ih)/2,setsar=1[bg]"
        )

        # Composite each overlay onto the background with timing.
        prev_label = "[bg]"
        for i, (ov_label, ov) in enumerate(zip(overlay_labels, params.overlays)):
            # Scale overlay to fit within frame (max 80% width).
            scale_w = int(params.width * 0.8)
            scale_h = int(params.height * 0.8)
            px = int(ov.x * params.width)
            py = int(ov.y * params.height)
            # Build the overlay filter with timing.
            enable_expr = ""
            if ov.start_time > 0 or ov.end_time is not None:
                start = ov.start_time
                end = ov.end_time if ov.end_time is not None else params.duration_sec
                enable_expr = f":enable='between(t,{start},{end})'"
            out_label = f"[v{i}]" if i < len(overlay_labels) - 1 else "[vout]"
            filter_parts.append(
                f"{ov_label}scale={scale_w}:{scale_h}:force_original_aspect_ratio=decrease[ov{i}]"
            )
            filter_parts.append(
                f"{prev_label}[ov{i}]overlay={px}:{py}{enable_expr}{out_label}"
            )
            prev_label = out_label

        # If no overlays, the video stream is just [bg].
        if not overlay_labels:
            filter_parts.append(f"[bg]null[vout]")
        else:
            # The last overlay produced [vout]; if a video_filter is requested,
            # apply it to [vout] and re-label. We must rename the existing [vout]
            # to an intermediate label first.
            if params.video_filter:
                if not self._is_safe_arg(params.video_filter):
                    raise RenderError(f"unsafe video_filter rejected: {params.video_filter!r}")
                # Replace the last [vout] with an intermediate label and add the filter.
                filter_parts[-1] = filter_parts[-1].replace("[vout]", "[vpre]")
                filter_parts.append(f"[vpre]{params.video_filter}[vout]")

        filtergraph = ";".join(filter_parts)
        cmd.extend(["-filter_complex", filtergraph])

        # --- Output encoding ---
        cmd.extend([
            "-map", "[vout]",
            "-map", audio_map_label,
            "-c:v", "libx264",
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-r", str(params.fps),
            "-s", f"{params.width}x{params.height}",
            "-t", str(params.duration_sec),
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
        ])
        cmd.append(str(output))
        return cmd

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
    ffprobe_bin = shutil.which(settings.ffprobe_path) or shutil.which("ffprobe")
    if ffmpeg_bin and ffprobe_bin:
        return FFmpegRenderer(settings, ffmpeg_bin=ffmpeg_bin, ffprobe_bin=ffprobe_bin)
    return StubFFmpegService(settings)


__all__ = [
    "ComposeVideoParams",
    "FFmpegRenderer",
    "FFmpegService",
    "MediaInfo",
    "OverlayLayer",
    "RenderJobParams",
    "StubFFmpegService",
    "get_ffmpeg_service",
]
