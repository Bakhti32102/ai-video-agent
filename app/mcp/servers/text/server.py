"""Text MCP server (Phase 1 stub).

Generates titles, labels, lower thirds, captions and annotations with safe
positioning. Phase 2 will add typographic measurement / overflow checks.
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName
from app.core.result import Result
from app.mcp.servers.base import BaseMcpServer

# Title-safe area (normalized): keep content within these margins.
SAFE_LEFT = 0.05
SAFE_RIGHT = 0.95
SAFE_TOP = 0.05
SAFE_BOTTOM = 0.95


class TextMcpServer(BaseMcpServer):
    """Generates text overlays with safe positioning."""

    name = AgentName.TEXT

    def list_tools(self) -> list[str]:
        return ["create_overlay", "check_safe_position"]

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        if tool == "create_overlay":
            return await self.create_overlay(arguments)
        if tool == "check_safe_position":
            return await self.check_safe_position(arguments)
        return self._fail(f"unknown tool '{tool}' for Text MCP server")

    async def create_overlay(self, arguments: dict[str, Any]) -> Result[dict]:
        kind = arguments.get("kind", "caption")
        text = arguments.get("text", "")
        if not text or not str(text).strip():
            return self._fail("text must not be empty")
        if kind not in {"title", "subtitle", "lower_third", "caption", "annotation", "label"}:
            return self._fail(f"unsupported overlay kind: {kind}")
        x = float(arguments.get("x", 0.1))
        y = float(arguments.get("y", 0.1))
        pos = await self.check_safe_position({"x": x, "y": y})
        if pos.is_failure:
            return pos
        return self._ok(
            {
                "kind": kind,
                "text": text.strip(),
                "x": x,
                "y": y,
                "font_size": int(arguments.get("font_size", 48)),
                "color": arguments.get("color", "#FFFFFF"),
                "safe_zone": True,
            }
        )

    async def check_safe_position(self, arguments: dict[str, Any]) -> Result[dict]:
        try:
            x = float(arguments.get("x"))
            y = float(arguments.get("y"))
        except (TypeError, ValueError):
            return self._fail("x and y must be numeric")
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            return self._fail("x and y must be within [0, 1]")
        # Warn (do not fail) if inside the frame but outside the title-safe area.
        warnings: list[str] = []
        if x < SAFE_LEFT or x > SAFE_RIGHT or y < SAFE_TOP or y > SAFE_BOTTOM:
            warnings.append("position is inside frame but outside title-safe area")
        return self._ok({"x": x, "y": y}, warnings=warnings)
