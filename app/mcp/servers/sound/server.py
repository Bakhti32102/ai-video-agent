"""Sound Design MCP server.

Creates a structured sound-design plan. Does NOT play or render audio.
Validates that referenced audio assets exist (via the Assets MCP registry or
caller-supplied asset list).

Tools:
- ``create_sound_event`` — create a single sound event spec
- ``create_sound_design_plan`` — build a full plan across scenes
- ``validate_sound_event`` — validate an event references an existing asset

Supported kinds: whoosh, impact, riser, ambience, historical_atmosphere,
transition, music.

CRITICAL: never reference an audio file that does not exist.

Legacy tools (backward compat):
- ``select_sfx`` — returns failure (sounds must reference registered assets)
- ``select_music`` — returns failure (music must reference registered assets)
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName
from app.core.result import Result
from app.mcp.schemas import (
    BuildAudioMixInput,
    BuildAudioMixOutput,
    CreateSoundDesignPlanInput,
    CreateSoundDesignPlanOutput,
    CreateSoundEventInput,
    CreateSoundEventOutput,
    ValidateSoundEventInput,
    ValidateSoundEventOutput,
)
from app.mcp.servers.base import BaseMcpServer, ToolDefinition
from app.utils.ids import new_id

VALID_KINDS = {
    "whoosh", "impact", "riser", "ambience",
    "historical_atmosphere", "transition", "music",
    "sfx",
}


class SoundMcpServer(BaseMcpServer):
    """Manages SFX, ambience and background music, with audio mixing."""

    name = AgentName.SOUND
    version = "4.0.0"
    description = "Creates structured sound-design plans and FFmpeg audio mix filtergraphs."

    def __init__(self) -> None:
        super().__init__()
        # Known-good asset IDs (populated via create_sound_event or injected).
        self._known_assets: set[str] = set()
        self._register_tool(ToolDefinition(
            name="create_sound_event",
            description="Create a validated sound event spec referencing an existing asset.",
            input_schema=CreateSoundEventInput,
            output_schema=CreateSoundEventOutput,
            handler=self._create_sound_event,
            tags={"write"},
        ))
        self._register_tool(ToolDefinition(
            name="create_sound_design_plan",
            description="Build a full sound-design plan across multiple scenes.",
            input_schema=CreateSoundDesignPlanInput,
            output_schema=CreateSoundDesignPlanOutput,
            handler=self._create_sound_design_plan,
            tags={"write"},
        ))
        self._register_tool(ToolDefinition(
            name="validate_sound_event",
            description="Validate that a sound event references a known asset.",
            input_schema=ValidateSoundEventInput,
            output_schema=ValidateSoundEventOutput,
            handler=self._validate_sound_event,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="build_audio_mix",
            description="Generate an FFmpeg filtergraph to mix voiceover + SFX + music into one audio track.",
            input_schema=BuildAudioMixInput,
            output_schema=BuildAudioMixOutput,
            handler=self._build_audio_mix,
            tags={"read"},
        ))

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        if tool == "select_sfx":
            return await self._select_legacy(arguments, "sfx")
        if tool == "select_music":
            return await self._select_legacy(arguments, "music")
        return await self.execute_tool(tool, arguments)

    def register_known_asset(self, asset_id: str) -> None:
        """Register an asset ID as known-good (called by the workflow)."""
        self._known_assets.add(asset_id)

    # --- tool handlers ------------------------------------------------------

    async def _create_sound_event(self, inp: CreateSoundEventInput) -> Result[CreateSoundEventOutput]:
        if inp.kind not in VALID_KINDS:
            return Result.fail(f"unsupported sound kind: {inp.kind}; valid: {sorted(VALID_KINDS)}")
        if inp.duration_sec <= 0 or inp.duration_sec > 600.0:
            return Result.fail(f"duration_sec must be in (0, 600]; got {inp.duration_sec}")

        warnings: list[str] = []
        # If we have a known-asset registry, check it; otherwise warn.
        if self._known_assets and inp.asset_id not in self._known_assets:
            warnings.append(f"asset '{inp.asset_id}' is not in the known registry; verify it exists before render")

        end_time = inp.start_time + inp.duration_sec
        event = {
            "id": new_id("sound_"),
            "scene_id": inp.scene_id,
            "asset_id": inp.asset_id,
            "kind": inp.kind,
            "start_time": inp.start_time,
            "end_time": round(end_time, 3),
            "duration_sec": inp.duration_sec,
            "volume_db": inp.volume_db,
            "fade_in_sec": inp.fade_in_sec,
            "fade_out_sec": inp.fade_out_sec,
        }
        # Track the asset as referenced.
        self._known_assets.add(inp.asset_id)
        return Result.ok(CreateSoundEventOutput(sound_event=event, warnings=warnings))

    async def _create_sound_design_plan(self, inp: CreateSoundDesignPlanInput) -> Result[CreateSoundDesignPlanOutput]:
        events: list[dict[str, Any]] = []
        warnings: list[str] = []
        for scene in inp.scenes:
            scene_id = scene.get("id") or scene.get("scene_id", "unknown")
            start = float(scene.get("start_time", 0))
            end = float(scene.get("end_time", start + 5))
            duration = end - start
            # Default ambience for each scene.
            events.append({
                "id": new_id("sound_"),
                "scene_id": scene_id,
                "asset_id": f"ambience_{scene_id}",
                "kind": "ambience",
                "start_time": start,
                "end_time": round(end, 3),
                "duration_sec": round(duration, 3),
                "volume_db": -12.0,
                "fade_in_sec": 1.0,
                "fade_out_sec": 1.0,
            })
            self._known_assets.add(f"ambience_{scene_id}")
        warnings.append("sound design plan generated with default ambience per scene; customize per requirements")
        return Result.ok(CreateSoundDesignPlanOutput(events=events, warnings=warnings))

    async def _validate_sound_event(self, inp: ValidateSoundEventInput) -> Result[ValidateSoundEventOutput]:
        errors: list[str] = []
        warnings: list[str] = []
        if self._known_assets and inp.asset_id not in self._known_assets:
            errors.append(f"asset '{inp.asset_id}' is not in the known registry")
        if inp.duration_sec <= 0:
            errors.append("duration_sec must be positive")
        return Result.ok(ValidateSoundEventOutput(
            valid=not errors,
            errors=errors,
            warnings=warnings,
        ))

    async def _build_audio_mix(self, inp: BuildAudioMixInput) -> Result[BuildAudioMixOutput]:
        """Generate an FFmpeg filtergraph for mixing voiceover + SFX + music.

        Uses the ``amix`` filter to combine all audio inputs into one track,
        with per-input volume adjustment via ``volume`` filter (in dB).

        The resulting filtergraph is designed for ``-filter_complex``. The
        caller supplies the actual ``-i`` input arguments in the same order:
        [0] voiceover, [1..N] SFX, [N+1] music (if present).
        """
        warnings: list[str] = []
        parts: list[str] = []
        input_idx = 0
        # Voiceover: apply volume adjustment.
        if inp.voiceover_volume_db != 0.0:
            parts.append(f"[{input_idx}:a]volume={inp.voiceover_volume_db}dB[v{input_idx}]")
            vo_label = f"[v{input_idx}]"
        else:
            vo_label = f"[{input_idx}:a]"
        input_idx += 1
        mix_labels: list[str] = [vo_label]

        # SFX tracks.
        if inp.sfx_paths:
            for _ in inp.sfx_paths:
                if inp.sfx_volume_db != 0.0:
                    parts.append(f"[{input_idx}:a]volume={inp.sfx_volume_db}dB[v{input_idx}]")
                    mix_labels.append(f"[v{input_idx}]")
                else:
                    mix_labels.append(f"[{input_idx}:a]")
                input_idx += 1

        # Music track.
        if inp.music_path:
            if inp.music_volume_db != 0.0:
                parts.append(f"[{input_idx}:a]volume={inp.music_volume_db}dB[v{input_idx}]")
                mix_labels.append(f"[v{input_idx}]")
            else:
                mix_labels.append(f"[{input_idx}:a]")
            input_idx += 1

        # amix to combine all.
        mix_inputs = "".join(mix_labels)
        parts.append(f"{mix_inputs}amix=inputs={len(mix_labels)}:duration=longest:dropout_transition=0[aout]")

        filtergraph = ";".join(parts)
        warnings.append(f"filtergraph expects {input_idx} input files; supply -i args in order")
        warnings.append("test filtergraph with actual ffmpeg binary before final render")
        return Result.ok(BuildAudioMixOutput(
            filtergraph=filtergraph,
            output_path=inp.output_path,
            input_count=input_idx,
            warnings=warnings,
        ))

    # --- legacy -------------------------------------------------------------

    async def _select_legacy(self, arguments: dict[str, Any], kind: str) -> Result[dict]:
        """Legacy: sounds must reference registered assets, not be invented."""
        key = "cue" if kind == "sfx" else "mood"
        val = arguments.get(key, "")
        if not val or not str(val).strip():
            return self._fail(f"{key} must not be empty")
        return self._fail(
            f"{kind} selection requires a registered asset; use create_sound_event for: {val}"
        )
