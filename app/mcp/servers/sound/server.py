"""Sound Design MCP server (Phase 1 stub).

Selects sound effects, ambience and background music, and synchronizes
important SFX with visual events. Phase 2 will integrate a real sound library.
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName
from app.core.result import Result
from app.mcp.servers.base import BaseMcpServer


class SoundMcpServer(BaseMcpServer):
    """Manages SFX, ambience and background music."""

    name = AgentName.SOUND

    def list_tools(self) -> list[str]:
        return ["select_sfx", "select_music"]

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        if tool == "select_sfx":
            return await self.select_sfx(arguments)
        if tool == "select_music":
            return await self.select_music(arguments)
        return self._fail(f"unknown tool '{tool}' for Sound MCP server")

    async def select_sfx(self, arguments: dict[str, Any]) -> Result[dict]:
        cue = arguments.get("cue", "")
        if not cue or not str(cue).strip():
            return self._fail("cue must not be empty")
        # TODO(Phase 2): match cue to a real sound library asset.
        return self._fail(f"SFX selection not implemented in Phase 1 for cue: {cue}")

    async def select_music(self, arguments: dict[str, Any]) -> Result[dict]:
        mood = arguments.get("mood", "")
        if not mood or not str(mood).strip():
            return self._fail("mood must not be empty")
        # TODO(Phase 2): select licensed background music by mood.
        return self._fail(f"music selection not implemented in Phase 1 for mood: {mood}")
