"""Text MCP server.

Creates structured text overlay specifications (does NOT render text).

Tools:
- ``create_text_overlay`` — produce a validated text overlay spec

Supported overlay kinds: title, subtitle, lower_third, location_label,
date_label, annotation, historical_label.

Output: text, style, position, start/end time, animation, scene_id.

Legacy tools (backward compat):
- ``create_overlay`` — alias for ``create_text_overlay``
- ``check_safe_position`` — validates x/y within frame
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName
from app.core.result import Result
from app.mcp.schemas import CreateTextOverlayInput, CreateTextOverlayOutput
from app.mcp.servers.base import BaseMcpServer, ToolDefinition
from app.utils.ids import new_id

# Title-safe area (normalized): keep content within these margins.
SAFE_LEFT = 0.05
SAFE_RIGHT = 0.95
SAFE_TOP = 0.05
SAFE_BOTTOM = 0.95

VALID_KINDS = {
    "title", "subtitle", "lower_third", "caption", "annotation", "label",
    "location_label", "date_label", "historical_label",
}
VALID_ANIMATIONS = {"none", "fade_in", "fade_out", "slide_up", "slide_left", "typewriter"}


class TextMcpServer(BaseMcpServer):
    """Generates text overlays with safe positioning."""

    name = AgentName.TEXT
    version = "3.0.0"
    description = "Creates structured text overlay specifications (no rendering)."

    def __init__(self) -> None:
        super().__init__()
        self._register_tool(ToolDefinition(
            name="create_text_overlay",
            description="Create a structured text overlay spec with safe positioning and animation.",
            input_schema=CreateTextOverlayInput,
            output_schema=CreateTextOverlayOutput,
            handler=self._create_text_overlay,
            tags={"write"},
        ))

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        if tool == "create_overlay":
            return await self.execute_tool("create_text_overlay", arguments)
        if tool == "check_safe_position":
            return await self._check_safe_position(arguments)
        return await self.execute_tool(tool, arguments)

    # --- tool handlers ------------------------------------------------------

    async def _create_text_overlay(self, inp: CreateTextOverlayInput) -> Result[CreateTextOverlayOutput]:
        if inp.kind not in VALID_KINDS:
            return Result.fail(f"unsupported overlay kind: {inp.kind}; valid: {sorted(VALID_KINDS)}")
        if inp.animation not in VALID_ANIMATIONS:
            return Result.fail(f"unsupported animation: {inp.animation}; valid: {sorted(VALID_ANIMATIONS)}")
        if inp.end_time < inp.start_time:
            return Result.fail(f"end_time {inp.end_time} precedes start_time {inp.start_time}")

        # Normalize color to #RRGGBB.
        color = inp.color if inp.color.startswith("#") else f"#{inp.color}"

        warnings: list[str] = []
        if inp.x < SAFE_LEFT or inp.x > SAFE_RIGHT or inp.y < SAFE_TOP or inp.y > SAFE_BOTTOM:
            warnings.append("position is inside frame but outside title-safe area")

        overlay = {
            "id": new_id("text_"),
            "scene_id": inp.scene_id,
            "kind": inp.kind,
            "text": inp.text.strip(),
            "x": inp.x,
            "y": inp.y,
            "start_time": inp.start_time,
            "end_time": inp.end_time,
            "font_size": inp.font_size,
            "color": color,
            "animation": inp.animation,
        }
        return Result.ok(CreateTextOverlayOutput(
            overlay=overlay,
            safe_zone=not warnings,
            warnings=warnings,
        ))

    # --- legacy -------------------------------------------------------------

    async def _check_safe_position(self, arguments: dict[str, Any]) -> Result[dict]:
        try:
            x = float(arguments.get("x"))
            y = float(arguments.get("y"))
        except (TypeError, ValueError):
            return self._fail("x and y must be numeric")
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            return self._fail("x and y must be within [0, 1]")
        warnings: list[str] = []
        if x < SAFE_LEFT or x > SAFE_RIGHT or y < SAFE_TOP or y > SAFE_BOTTOM:
            warnings.append("position is inside frame but outside title-safe area")
        return self._ok({"x": x, "y": y}, warnings=warnings)
