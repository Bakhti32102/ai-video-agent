"""Icon/Asset MCP server (Phase 1 stub).

Phase 2 will resolve/generate icons and visual assets from real sources and
maintain full metadata. For now it validates asset references only.
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName
from app.core.result import Result
from app.guardrails.media import is_supported_media
from app.mcp.servers.base import BaseMcpServer


class AssetMcpServer(BaseMcpServer):
    """Finds/generates/selects icons and visual assets with metadata."""

    name = AgentName.ASSET

    def list_tools(self) -> list[str]:
        return ["select_asset", "list_assets"]

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        if tool == "select_asset":
            return await self.select_asset(arguments)
        if tool == "list_assets":
            return await self.list_assets(arguments)
        return self._fail(f"unknown tool '{tool}' for Asset MCP server")

    async def select_asset(self, arguments: dict[str, Any]) -> Result[dict]:
        """Select an appropriate asset for a visual requirement.

        TODO(Phase 2): implement real icon/asset discovery and generation.
        """
        requirement = arguments.get("requirement", "")
        if not requirement or not str(requirement).strip():
            return self._fail("requirement must not be empty")
        return self._fail(
            f"asset selection not implemented in Phase 1 for requirement: {requirement}"
        )

    async def list_assets(self, arguments: dict[str, Any]) -> Result[list[dict]]:
        """List registered assets, optionally filtered by supported format."""
        assets = arguments.get("assets", [])
        if not isinstance(assets, list):
            return self._fail("assets must be a list")
        validated = [a for a in assets if isinstance(a, dict) and a.get("file_path") and is_supported_media(a["file_path"])]
        return self._ok(validated, warnings=["asset registry is in-memory only in Phase 1"])
