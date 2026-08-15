"""Audio MCP server (Phase 1 stub).

Phase 2 will use FFmpeg/ffprobe to analyze the voice-over. For now it exposes
the contract and a duration stub that trusts a caller-supplied duration.
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName
from app.core.result import Result
from app.mcp.servers.base import BaseMcpServer


class AudioMcpServer(BaseMcpServer):
    """Analyzes voice-over audio and produces timestamps / silence map."""

    name = AgentName.AUDIO

    def list_tools(self) -> list[str]:
        return ["analyze_audio", "detect_silence"]

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        if tool == "analyze_audio":
            return await self.analyze_audio(arguments)
        if tool == "detect_silence":
            return await self.detect_silence(arguments)
        return self._fail(f"unknown tool '{tool}' for Audio MCP server")

    async def analyze_audio(self, arguments: dict[str, Any]) -> Result[dict]:
        path = arguments.get("file_path", "")
        if not path or not str(path).strip():
            return self._fail("file_path must not be empty")
        # TODO(Phase 2): invoke ffprobe for real duration, sample rate, channels.
        duration = arguments.get("duration_sec")
        if duration is None:
            return self._fail("duration_sec required until ffprobe analysis is implemented (Phase 2)")
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            return self._fail("duration_sec must be numeric")
        if duration <= 0:
            return self._fail("duration_sec must be positive")
        return self._ok(
            {
                "file_path": path,
                "duration_sec": duration,
                "sample_rate": None,
                "channels": None,
                "format": None,
            },
            warnings=["audio analysis uses caller-supplied duration; ffprobe is Phase 2"],
        )

    async def detect_silence(self, arguments: dict[str, Any]) -> Result[dict]:
        path = arguments.get("file_path", "")
        if not path or not str(path).strip():
            return self._fail("file_path must not be empty")
        # TODO(Phase 2): silencedetect filter via FFmpeg.
        return self._ok(
            {"file_path": path, "silence_segments": []},
            warnings=["silence detection not implemented; returns empty (Phase 2)"],
        )
