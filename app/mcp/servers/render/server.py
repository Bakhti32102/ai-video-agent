"""Render MCP server (Phase 1 stub).

Final video rendering is explicitly a Phase 2+ deliverable. This stub enforces
the render-job contract and refuses to render until FFmpeg integration lands.
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName, RenderJobStatus
from app.core.result import Result
from app.mcp.servers.base import BaseMcpServer


class RenderMcpServer(BaseMcpServer):
    """Renders the final video from project/timeline data via FFmpeg."""

    name = AgentName.RENDER

    def list_tools(self) -> list[str]:
        return ["render_video", "probe_media"]

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        if tool == "render_video":
            return await self.render_video(arguments)
        if tool == "probe_media":
            return await self.probe_media(arguments)
        return self._fail(f"unknown tool '{tool}' for Render MCP server")

    async def render_video(self, arguments: dict[str, Any]) -> Result[dict]:
        project_id = arguments.get("project_id")
        if not project_id:
            return self._fail("project_id is required")
        # TODO(Phase 2): build an FFmpeg filtergraph from timeline events and
        # render a 16:9 MP4. Until then, refuse to produce a fake video.
        return self._fail(
            f"video rendering is not implemented in Phase 1 for project {project_id}; "
            "FFmpeg rendering pipeline is Phase 2"
        )

    async def probe_media(self, arguments: dict[str, Any]) -> Result[dict]:
        path = arguments.get("file_path", "")
        if not path or not str(path).strip():
            return self._fail("file_path is required")
        # TODO(Phase 2): call ffprobe via app.services.ffmpeg.
        return self._fail("ffprobe integration is not implemented in Phase 1")
