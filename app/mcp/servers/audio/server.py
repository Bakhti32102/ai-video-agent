"""Audio MCP server.

Analyzes voice-over audio files. Uses FFmpeg/ffprobe (via
:class:`~app.services.ffmpeg.FFmpegService`) when available to extract real
metadata (duration, sample rate, channels, codec, format, file size). When
ffprobe is unavailable, falls back to caller-supplied duration with a warning.

Tools:
- ``inspect_audio`` — extract real audio metadata via ffprobe
- ``create_audio_timeline`` — build a timed scene/audio alignment
- ``detect_silence`` — detect silence segments (interface ready for future
  FFmpeg silencedetect filter integration)

Never modifies the original audio file. Designed so future Whisper/forced-
alignment can be layered in without changing the contract.

Legacy tools (backward compat):
- ``analyze_audio`` — alias for ``inspect_audio``
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from app.core.enums import AgentName
from app.core.exceptions import AppError
from app.core.logging import get_logger, log_event
from app.core.result import Result
from app.mcp.schemas import (
    CreateAudioTimelineInput,
    CreateAudioTimelineOutput,
    DetectSilenceInput,
    DetectSilenceOutput,
    InspectAudioInput,
    InspectAudioOutput,
)
from app.mcp.servers.base import BaseMcpServer, ToolDefinition
from app.services.ffmpeg import FFmpegService, MediaInfo, get_ffmpeg_service
from app.utils.paths import contains_traversal, validate_path_safety

logger = get_logger("mcp.audio")

# Parse ffmpeg silencedetect log lines:
#   [silencedetect @ ...] silence_start: 12.3400
#   [silencedetect @ ...] silence_end: 13.5600 | silence_duration: 1.2200
_SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)")


class AudioMcpServer(BaseMcpServer):
    """Analyzes voice-over audio and produces timestamps / silence map."""

    name = AgentName.AUDIO
    version = "4.0.0"
    description = "Inspects voice-over audio: duration, sample rate, channels, codec, silence."

    def __init__(self, ffmpeg_service: FFmpegService | None = None) -> None:
        super().__init__()
        self.ffmpeg = ffmpeg_service or get_ffmpeg_service()
        self._register_tool(ToolDefinition(
            name="inspect_audio",
            description="Inspect an audio file: duration, sample rate, channels, codec, format, file size.",
            input_schema=InspectAudioInput,
            output_schema=InspectAudioOutput,
            handler=self._inspect_audio,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="create_audio_timeline",
            description="Create a timed audio/scene alignment from duration and scene count.",
            input_schema=CreateAudioTimelineInput,
            output_schema=CreateAudioTimelineOutput,
            handler=self._create_audio_timeline,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="detect_silence",
            description="Detect silence segments in an audio file (interface for future silencedetect filter).",
            input_schema=DetectSilenceInput,
            output_schema=DetectSilenceOutput,
            handler=self._detect_silence,
            tags={"read"},
        ))

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        if tool == "analyze_audio":
            return await self.execute_tool("inspect_audio", arguments)
        return await self.execute_tool(tool, arguments)

    # --- tool handlers ------------------------------------------------------

    async def _inspect_audio(self, inp: InspectAudioInput) -> Result[InspectAudioOutput]:
        # Path safety: reject traversal/control chars.
        if contains_traversal(inp.file_path):
            return Result.fail(f"path traversal detected in audio path: {inp.file_path}")
        try:
            validate_path_safety(inp.file_path, allow_absolute=True)
        except Exception as exc:
            return Result.fail(f"unsafe audio path: {exc}")

        warnings: list[str] = []
        # Try real ffprobe first.
        try:
            info: MediaInfo = await self.ffmpeg.probe(inp.file_path)
            return Result.ok(InspectAudioOutput(
                file_path=inp.file_path,
                duration_sec=info.duration_sec,
                sample_rate=info.sample_rate,
                channels=info.channels,
                codec=info.codec,
                format=info.format,
                file_size=info.file_size,
                warnings=warnings,
            ))
        except AppError as exc:
            # ffprobe unavailable — fall back to caller-supplied duration.
            warnings.append(f"ffprobe unavailable ({exc.code}); using caller-supplied metadata")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"ffprobe failed: {exc}; using caller-supplied metadata")

        # Fallback: trust caller-supplied duration.
        if inp.duration_sec is None:
            return Result.fail(
                "could not probe audio (ffprobe unavailable) and no duration_sec supplied; "
                "cannot inspect audio without metadata"
            )
        # Try to get file size from disk if the file exists.
        file_size = None
        try:
            if os.path.exists(inp.file_path):
                file_size = os.path.getsize(inp.file_path)
        except OSError:
            pass
        return Result.ok(InspectAudioOutput(
            file_path=inp.file_path,
            duration_sec=inp.duration_sec,
            sample_rate=None,
            channels=None,
            codec=None,
            format=None,
            file_size=file_size,
            warnings=warnings,
        ))

    async def _create_audio_timeline(self, inp: CreateAudioTimelineInput) -> Result[CreateAudioTimelineOutput]:
        duration = inp.duration_sec
        n = inp.scene_count
        per = duration / n
        timeline: list[dict[str, Any]] = []
        for i in range(n):
            start = i * per
            timeline.append({
                "scene_index": i,
                "start_time": round(start, 3),
                "end_time": round(start + per, 3),
                "has_silence": False,
            })
        # Mark silence regions.
        for seg in inp.silence_segments:
            seg_start = float(seg.get("start_time", 0))
            for entry in timeline:
                if entry["start_time"] <= seg_start < entry["end_time"]:
                    entry["has_silence"] = True
        return Result.ok(CreateAudioTimelineOutput(
            timeline=timeline,
            total_duration_sec=duration,
        ))

    async def _detect_silence(self, inp: DetectSilenceInput) -> Result[DetectSilenceOutput]:
        if contains_traversal(inp.file_path):
            return Result.fail(f"path traversal detected: {inp.file_path}")
        try:
            validate_path_safety(inp.file_path, allow_absolute=True)
        except Exception as exc:
            return Result.fail(f"unsafe audio path: {exc}")

        warnings: list[str] = []
        # Try real silence detection via ffmpeg silencedetect filter.
        # Only the concrete FFmpegRenderer has the binary; the stub cannot.
        from app.services.ffmpeg import FFmpegRenderer, StubFFmpegService
        if isinstance(self.ffmpeg, StubFFmpegService):
            warnings.append("ffmpeg unavailable; silence detection not performed")
            return Result.ok(DetectSilenceOutput(
                file_path=inp.file_path,
                silence_segments=[],
                warnings=warnings,
            ))
        if not os.path.exists(inp.file_path):
            return Result.fail(f"audio file does not exist: {inp.file_path}")

        try:
            segments = self._run_silencedetect(inp.file_path, inp.min_silence_sec)
        except FileNotFoundError as exc:
            warnings.append(f"ffmpeg binary not found: {exc}")
            return Result.ok(DetectSilenceOutput(
                file_path=inp.file_path, silence_segments=[], warnings=warnings,
            ))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"silence detection failed: {exc}")
            return Result.ok(DetectSilenceOutput(
                file_path=inp.file_path, silence_segments=[], warnings=warnings,
            ))
        if not segments:
            warnings.append("no silence segments detected")
        return Result.ok(DetectSilenceOutput(
            file_path=inp.file_path,
            silence_segments=segments,
            warnings=warnings,
        ))

    def _run_silencedetect(self, file_path: str, min_silence_sec: float) -> list[dict[str, Any]]:
        """Run ffmpeg silencedetect and parse the log output.

        Uses subprocess argv list (never shell=True). Returns a list of
        ``{start_time, end_time, duration_sec, confidence}`` dicts.
        """
        renderer = self.ffmpeg
        # FFmpegRenderer is guaranteed here; access its binary path.
        ffmpeg_bin = getattr(renderer, "ffmpeg_bin", "ffmpeg")
        cmd = [
            ffmpeg_bin,
            "-hide_banner",
            "-i", str(file_path),
            "-af", f"silencedetect=noise=-50dB:d={min_silence_sec}",
            "-f", "null",
            "-",
        ]
        log_event(logger, "silencedetect.start", file=file_path)
        result = subprocess.run(  # noqa: S603 - argv list, no shell
            cmd, capture_output=True, text=True, timeout=120, check=False,
        )
        # silencedetect writes to stderr.
        log_output = result.stderr or ""
        return self._parse_silence_log(log_output)

    @staticmethod
    def _parse_silence_log(log_output: str) -> list[dict[str, Any]]:
        """Parse ffmpeg silencedetect stderr output into segments."""
        segments: list[dict[str, Any]] = []
        starts: list[float] = []
        for line in log_output.splitlines():
            m = _SILENCE_START_RE.search(line)
            if m:
                starts.append(float(m.group(1)))
            m = _SILENCE_END_RE.search(line)
            if m:
                end = float(m.group(1))
                dur = float(m.group(2))
                start = starts.pop(0) if starts else end - dur
                segments.append({
                    "start_time": round(start, 3),
                    "end_time": round(end, 3),
                    "duration_sec": round(dur, 3),
                    "confidence": 1.0,
                })
        return segments
