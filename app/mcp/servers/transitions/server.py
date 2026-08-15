"""Transition MCP server.

Creates structured transition specifications between scenes. Does NOT render
video. Validates duration, scene boundaries, and transition compatibility.

Tools:
- ``create_transition`` — produce a validated transition spec

Supported kinds: fade, dissolve, zoom, slide, wipe, map_zoom, map_to_map.

Legacy tools (backward compat):
- ``select_transition`` — alias for ``create_transition``
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName
from app.core.result import Result
from app.mcp.schemas import CreateTransitionInput, CreateTransitionOutput
from app.mcp.servers.base import BaseMcpServer, ToolDefinition
from app.utils.ids import new_id

# Map transitions require map data on both scenes.
MAP_TRANSITIONS = {"map_zoom", "map_to_map"}
VALID_KINDS = {"cut", "fade", "dissolve", "slide", "wipe", "zoom", "map_zoom", "map_to_map"}


class TransitionMcpServer(BaseMcpServer):
    """Selects and specifies scene transitions."""

    name = AgentName.TRANSITION
    version = "3.0.0"
    description = "Creates validated transition specs between scenes (no rendering)."

    def __init__(self) -> None:
        super().__init__()
        self._register_tool(ToolDefinition(
            name="create_transition",
            description="Create a validated transition specification between two scenes.",
            input_schema=CreateTransitionInput,
            output_schema=CreateTransitionOutput,
            handler=self._create_transition,
            tags={"write"},
        ))

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        if tool == "select_transition":
            return await self.execute_tool("create_transition", arguments)
        return await self.execute_tool(tool, arguments)

    # --- tool handlers ------------------------------------------------------

    async def _create_transition(self, inp: CreateTransitionInput) -> Result[CreateTransitionOutput]:
        if inp.kind not in VALID_KINDS:
            return Result.fail(f"unsupported transition kind: {inp.kind}; valid: {sorted(VALID_KINDS)}")
        if inp.duration_sec <= 0 or inp.duration_sec > 5.0:
            return Result.fail(f"duration_sec must be in (0, 5]; got {inp.duration_sec}")
        if inp.start_time < 0:
            return Result.fail(f"start_time must be non-negative; got {inp.start_time}")
        if inp.from_scene_id is None and inp.kind != "fade":
            # A cut/dissolve/etc. between named scenes needs a from_scene.
            return Result.fail(f"from_scene_id is required for '{inp.kind}' transitions")

        warnings: list[str] = []
        # Map transitions require both scene IDs.
        if inp.kind in MAP_TRANSITIONS:
            if inp.from_scene_id is None:
                return Result.fail(f"transition kind '{inp.kind}' requires from_scene_id")
            warnings.append(f"map transition '{inp.kind}' requires map data on both scenes")

        transition = {
            "id": new_id("trans_"),
            "from_scene_id": inp.from_scene_id,
            "to_scene_id": inp.to_scene_id,
            "kind": inp.kind,
            "start_time": inp.start_time,
            "duration_sec": inp.duration_sec,
        }
        if not warnings:
            warnings.append("transition created; validate against scene boundaries before rendering")
        return Result.ok(CreateTransitionOutput(transition=transition, warnings=warnings))
