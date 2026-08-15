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
from app.mcp.schemas import (
    BuildFiltergraphInput,
    BuildFiltergraphOutput,
    CreateTransitionInput,
    CreateTransitionOutput,
)
from app.mcp.servers.base import BaseMcpServer, ToolDefinition
from app.utils.ids import new_id

# Map transitions require map data on both scenes.
MAP_TRANSITIONS = {"map_zoom", "map_to_map"}
VALID_KINDS = {"cut", "fade", "dissolve", "slide", "wipe", "zoom", "map_zoom", "map_to_map"}


class TransitionMcpServer(BaseMcpServer):
    """Selects and specifies scene transitions, with FFmpeg filtergraph output."""

    name = AgentName.TRANSITION
    version = "4.0.0"
    description = "Creates validated transition specs and FFmpeg-compatible filtergraph strings."

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
        self._register_tool(ToolDefinition(
            name="build_filtergraph",
            description="Generate an FFmpeg-compatible filtergraph string for a transition.",
            input_schema=BuildFiltergraphInput,
            output_schema=BuildFiltergraphOutput,
            handler=self._build_filtergraph,
            tags={"read"},
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

    async def _build_filtergraph(self, inp: BuildFiltergraphInput) -> Result[BuildFiltergraphOutput]:
        """Generate an FFmpeg-compatible filtergraph string for a transition.

        These filtergraphs are designed to be passed directly to ffmpeg's
        ``-filter_complex`` option. They cover the common documentary
        transitions: fade, dissolve, slide, wipe, zoom.

        For cross-fade (dissolve) between two clips, the xfade filter is used.
        For single-clip transitions (fade in/out), the fade filter is used.
        """
        kind = inp.transition_kind
        valid_kinds = {"cut", "fade", "dissolve", "slide", "wipe", "zoom"}
        if kind not in valid_kinds:
            return Result.fail(
                f"unsupported transition_kind: {kind}; valid: {sorted(valid_kinds)}"
            )
        if inp.direction not in {"left", "right", "up", "down"}:
            return Result.fail(f"invalid direction: {inp.direction}; valid: left, right, up, down")

        warnings: list[str] = []
        offset = inp.offset_sec
        dur = inp.duration_sec
        total = inp.total_duration_sec
        if offset + dur > total:
            warnings.append(
                f"transition end ({offset + dur:.2f}s) exceeds total duration ({total:.2f}s)"
            )

        fg, desc = self._generate_filtergraph(kind, offset, dur, total, inp.direction, inp.fade_color)
        if kind != "cut":
            warnings.append("filtergraph is FFmpeg-compatible; test with actual ffmpeg binary")
        return Result.ok(BuildFiltergraphOutput(
            filtergraph=fg,
            filter_description=desc,
            warnings=warnings,
        ))

    @staticmethod
    def _generate_filtergraph(
        kind: str,
        offset: float,
        duration: float,
        total: float,
        direction: str,
        fade_color: str,
    ) -> tuple[str, str]:
        """Return (filtergraph_string, description)."""
        if kind == "cut":
            return "", "cut: no filter needed; concatenate at offset"
        if kind == "fade":
            # Fade in at start, fade out at end.
            fade_out_start = max(0.0, total - duration)
            fg = (
                f"fade=t=in:st={offset}:d={duration},"
                f"fade=t=out:st={fade_out_start:.3f}:d={duration}"
            )
            return fg, f"fade in at {offset}s for {duration}s, fade out at {fade_out_start:.3f}s"
        if kind == "dissolve":
            # Cross-fade between two inputs using xfade.
            fg = (
                f"[0:v][1:v]xfade=transition=fade:duration={duration}:offset={offset}"
            )
            return fg, f"cross-fade (xfade) between [0:v] and [1:v] at {offset}s for {duration}s"
        if kind == "slide":
            # xfade with slide transition.
            slide_dir_map = {"left": "slideleft", "right": "slideright",
                             "up": "slideup", "down": "slidedown"}
            transition = slide_dir_map[direction]
            fg = f"[0:v][1:v]xfade=transition={transition}:duration={duration}:offset={offset}"
            return fg, f"slide {direction} (xfade) at {offset}s for {duration}s"
        if kind == "wipe":
            # xfade with wipe transition.
            wipe_dir_map = {"left": "wipeleft", "right": "wiperight",
                            "up": "wipeup", "down": "wipedown"}
            transition = wipe_dir_map[direction]
            fg = f"[0:v][1:v]xfade=transition={transition}:duration={duration}:offset={offset}"
            return fg, f"wipe {direction} (xfade) at {offset}s for {duration}s"
        if kind == "zoom":
            # Zoompan for a Ken Burns effect.
            # Calculate frame count from duration (assuming 30fps).
            frames = int(duration * 30)
            fg = (
                f"zoompan=z='min(zoom+0.0015,1.5)':d={frames}"
                f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080"
            )
            return fg, f"zoom (Ken Burns) for {frames} frames ({duration}s)"
        return "", "unknown"
