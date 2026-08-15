"""Transition MCP server (Phase 1 stub).

Selects transitions between scenes. Policy: avoid excessive transitions by
defaulting to simple cuts unless a stronger transition is justified.
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName
from app.core.result import Result
from app.mcp.servers.base import BaseMcpServer


class TransitionMcpServer(BaseMcpServer):
    """Selects and specifies scene transitions."""

    name = AgentName.TRANSITION

    def list_tools(self) -> list[str]:
        return ["select_transition"]

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        if tool == "select_transition":
            return await self.select_transition(arguments)
        return self._fail(f"unknown tool '{tool}' for Transition MCP server")

    async def select_transition(self, arguments: dict[str, Any]) -> Result[dict]:
        from_scene = arguments.get("from_scene_id")
        to_scene = arguments.get("to_scene_id")
        start_time = arguments.get("start_time")
        if to_scene is None:
            return self._fail("to_scene_id is required")
        try:
            start_time = float(start_time) if start_time is not None else 0.0
        except (TypeError, ValueError):
            return self._fail("start_time must be numeric")
        if start_time < 0:
            return self._fail("start_time must be non-negative")
        # Default to a cut to avoid excessive transitions.
        kind = arguments.get("kind", "cut")
        if kind not in {"cut", "fade", "dissolve", "slide", "wipe", "zoom"}:
            return self._fail(f"unsupported transition kind: {kind}")
        duration = float(arguments.get("duration_sec", 0.5))
        if duration <= 0 or duration > 5.0:
            return self._fail("duration_sec must be in (0, 5]")
        return self._ok(
            {
                "from_scene_id": from_scene,
                "to_scene_id": to_scene,
                "kind": kind,
                "start_time": start_time,
                "duration_sec": duration,
            },
            warnings=["defaults to 'cut' to avoid excessive transitions"],
        )
