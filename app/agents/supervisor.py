"""Supervisor agent.

Top-level orchestrator. It does NOT perform specialized processing itself —
it coordinates the 9 MCP servers through the MCP client, driving the project
through the workflow state machine.

Phase 3 workflow:
1. receive project
2. Script MCP (analyze_script)
3. Audio MCP (inspect_audio)
4. synchronize scene/audio timing
5. Geo MCP (geocode_location)
6. Assets MCP (register_asset / list_assets)
7. Text MCP (create_text_overlay)
8. Transitions MCP (create_transition)
9. Sound MCP (create_sound_event)
10. Render MCP (create_render_job / render_video)
11. QA MCP (create_qa_report)
12. decide COMPLETED / FAILED / retry

Uses the existing workflow state machine, bounded retries, structured logging,
and guardrail validation on every result.
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName, WorkflowPhase, WorkflowState as WorkflowStateEnum
from app.core.logging import get_logger, log_event
from app.core.result import Result
from app.core.workflow import next_state as _next_workflow_state
from app.agents.base import BaseAgent
from app.guardrails.guardrails import Guardrails
from app.guardrails.pipeline import GuardrailPipeline, validate_before_accept
from app.mcp.client import McpClient
from app.schemas.contracts import AgentResult, WorkflowState
from app.services.geo import normalize_geo_query
from app.utils.ids import new_id

logger = get_logger("agent.supervisor")

# Maximum retry attempts per agent before the supervisor gives up.
DEFAULT_MAX_RETRIES = 2


class SupervisorAgent:
    """Top-level coordinator. Not a BaseAgent (it has no upstream caller)."""

    def __init__(
        self,
        client: McpClient,
        guardrails: Guardrails | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        pipeline: GuardrailPipeline | None = None,
    ) -> None:
        self.client = client
        self.guardrails = guardrails or Guardrails()
        self.max_retries = max_retries
        self.pipeline = pipeline or GuardrailPipeline(guardrails=self.guardrails)

    async def run_agent(
        self, name: AgentName, tool: str, arguments: dict[str, Any]
    ) -> AgentResult:
        """Run a single agent tool with bounded retries.

        Retries on failure up to :attr:`max_retries`. Never silently accepts
        an invalid result: every successful result is validated through the
        guardrail pipeline before being accepted. A final failure is returned
        as a failed AgentResult.
        """
        result: AgentResult | None = None
        for attempt in range(1, self.max_retries + 2):
            result = await self.client.call(name, tool, arguments, attempt=attempt)
            if result.success:
                # Validate through guardrail pipeline before accepting.
                validation = validate_before_accept(result, self.pipeline)
                if validation.is_failure:
                    log_event(
                        logger,
                        "agent.guardrail_rejected",
                        level=30,  # WARNING
                        agent=name.value,
                        attempt=attempt,
                        errors="; ".join(validation.errors),
                    )
                    # Convert to a failed result so it triggers a retry.
                    result = AgentResult(
                        agent=name,
                        status="failed",
                        success=False,
                        errors=[f"guardrail validation failed: {e}" for e in validation.errors],
                        attempt=attempt,
                        project_id=result.project_id,
                        scene_id=result.scene_id,
                        output=result.output,
                    )
                    if attempt <= self.max_retries:
                        continue
                    return result
                log_event(
                    logger,
                    "agent.succeeded",
                    agent=name.value,
                    attempt=attempt,
                    run_id=result.run_id,
                )
                return result
            log_event(
                logger,
                "agent.failed",
                level=30,  # WARNING
                agent=name.value,
                attempt=attempt,
                errors="; ".join(result.errors),
            )
        assert result is not None
        return result

    async def run_step(
        self, state: WorkflowState, name: AgentName, tool: str, arguments: dict[str, Any]
    ) -> Result[WorkflowState]:
        """Run one workflow step and update workflow state.

        Uses the :class:`WorkflowStateMachine` to validate state transitions.
        On success, advances the workflow state; on failure, tracks retries
        and may transition to ``FAILED`` if retries are exhausted.
        """
        result = await self.run_agent(name, tool, arguments)
        new_statuses = dict(state.agent_statuses)
        new_statuses[name.value] = result.status
        new_retries = dict(state.retries)
        new_retries[name.value] = result.attempt - 1 if not result.success else 0

        # Determine the next workflow state using the state machine.
        current_enum = state.current_state
        if result.success:
            nxt = _next_workflow_state(current_enum)
            target_enum = nxt if nxt is not None else current_enum
        else:
            # If retries exhausted, transition to FAILED.
            if new_retries[name.value] >= self.max_retries:
                target_enum = WorkflowStateEnum.FAILED
            else:
                target_enum = current_enum

        updated = state.model_copy(
            update={
                "agent_statuses": new_statuses,
                "retries": new_retries,
                "previous_state": state.current_state,
                "previous_phase": state.current_phase,
                "current_state": target_enum,
                "current_phase": _next_phase(state.current_phase) if result.success else state.current_phase,
            }
        )
        if result.success:
            log_event(
                logger,
                "workflow.advanced",
                agent=name.value,
                state=current_enum.value,
                next_state=target_enum.value,
            )
            return Result.ok(updated)
        return Result.fail(*result.errors, data=updated)

    # --- full orchestration workflow ----------------------------------------

    async def run_project(
        self,
        project_id: str,
        script_text: str,
        voiceover_path: str | None = None,
        audio_duration_sec: float | None = None,
        total_duration_sec: float = 30.0,
        sfx_paths: list[str] | None = None,
        music_path: str | None = None,
    ) -> dict[str, Any]:
        """Run the full 9-step orchestration workflow.

        Optional ``sfx_paths`` and ``music_path`` (Phase 5B) supply real audio
        asset files to mix with the voiceover. When omitted (or when the files
        do not exist), the pipeline falls back to voiceover-only mixing.

        Returns a dict with the final workflow state, all agent results, and
        the QA report. If any step fails after retries, the workflow
        transitions to FAILED and stops.
        """
        log_event(logger, "workflow.start", project_id=project_id, duration=total_duration_sec)
        workflow_id = new_id("wf_")
        state = WorkflowState(
            id=workflow_id,
            project_id=project_id,
            current_state=WorkflowStateEnum.CREATED,
            current_phase=WorkflowPhase.INIT,
        )
        results: dict[str, AgentResult] = {}
        context: dict[str, Any] = {"project_id": project_id, "scenes": [], "audio_duration_sec": None}

        # Step 1: Script MCP — analyze the script.
        script_result = await self.run_agent(
            AgentName.SCRIPT, "analyze_script",
            {"script_text": script_text, "total_duration_sec": total_duration_sec, "project_id": project_id},
        )
        results["script"] = script_result
        if not script_result.success:
            return self._finalize(state, results, context, failed=True)
        context["scenes"] = script_result.output.get("scenes", []) if script_result.output else []
        context["entities"] = script_result.output.get("entities", {}) if script_result.output else {}
        context["locations"] = script_result.output.get("locations", []) if script_result.output else []
        context["requirements"] = script_result.output.get("requirements", {}) if script_result.output else {}
        state = self._advance_state(state, WorkflowStateEnum.ANALYZING_SCRIPT)

        # Step 2: Audio MCP — inspect the voiceover.
        if voiceover_path:
            audio_args: dict[str, Any] = {"file_path": voiceover_path}
            if audio_duration_sec is not None:
                audio_args["duration_sec"] = audio_duration_sec
            audio_result = await self.run_agent(AgentName.AUDIO, "inspect_audio", audio_args)
        else:
            # No voiceover — synthesize a minimal result.
            audio_result = AgentResult(
                agent=AgentName.AUDIO,
                status="success",
                success=True,
                output={"file_path": None, "duration_sec": total_duration_sec, "warnings": ["no voiceover provided"]},
                errors=[],
                project_id=project_id,
            )
        results["audio"] = audio_result
        if not audio_result.success:
            return self._finalize(state, results, context, failed=True)
        context["audio_duration_sec"] = audio_result.output.get("duration_sec") if audio_result.output else total_duration_sec
        state = self._advance_state(state, WorkflowStateEnum.ANALYZING_AUDIO)

        # Step 3: Synchronize scene/audio timing.
        context = self._synchronize_timing(context)
        state = self._advance_state(state, WorkflowStateEnum.BUILDING_SCENES)

        # Step 4: Geo MCP — resolve locations.
        # Build a name->geocode cache so repeated locations (e.g. "mexico"
        # appearing in multiple scenes) are geocoded once.
        geo_cache: dict[str, dict[str, Any]] = {}
        geo_results: list[dict[str, Any]] = []
        for loc in context.get("locations", []):
            loc_name = loc.get("name", "")
            if not loc_name:
                continue
            query = normalize_geo_query(loc_name)
            if query in geo_cache:
                continue
            geo_result = await self.run_agent(AgentName.GEO, "geocode_location", {"query": query})
            if geo_result.success and geo_result.output:
                geo_cache[query] = geo_result.output
                geo_cache[loc_name] = geo_result.output
                geo_results.append(geo_result.output)
        results["geo"] = AgentResult(
            agent=AgentName.GEO,
            status="success",
            success=True,
            output={"locations": geo_results},
            errors=[],
            project_id=project_id,
        )
        context["resolved_locations"] = geo_results
        state = self._advance_state(state, WorkflowStateEnum.GENERATING_MAPS)

        # Step 4b: Build map plans for resolved locations and render to PNG.
        # For every scene that requires a map, find its first resolved
        # location, build a map plan, render it to a PNG, and record the
        # path keyed by scene index. Failures are logged and skipped so the
        # pipeline falls back to the existing visual behavior for that scene.
        map_images: list[dict[str, Any]] = []
        scene_map_paths: dict[int, str] = {}
        scenes = context.get("scenes", [])
        for scene in scenes:
            if not scene.get("map_required"):
                continue
            scene_idx = scene.get("index", 0)
            scene_locations = scene.get("locations", []) or []
            resolved: dict[str, Any] | None = None
            for sloc in scene_locations:
                sname = sloc.get("name", "")
                if not sname:
                    continue
                query = normalize_geo_query(sname)
                resolved = geo_cache.get(query) or geo_cache.get(sname)
                if resolved and resolved.get("latitude") is not None and resolved.get("longitude") is not None:
                    break
                resolved = None
            if resolved is None:
                logger.warning(
                    "supervisor.map.no_resolved_location",
                    extra={"scene_index": scene_idx, "locations": [l.get("name") for l in scene_locations]},
                )
                continue
            lat = resolved.get("latitude")
            lon = resolved.get("longitude")
            name = resolved.get("display_name") or resolved.get("query") or "location"
            plan_result = await self.run_agent(
                AgentName.GEO, "build_map_plan",
                {
                    "location": {
                        "id": new_id("loc_"),
                        "name": name,
                        "latitude": lat,
                        "longitude": lon,
                        "source": resolved.get("provider", "unknown"),
                        "provenance": resolved.get("provenance"),
                    },
                    "animation_type": "static",
                    "scene_id": scene.get("id", ""),
                    "duration_sec": float(scene.get("duration", scene.get("end_time", 5.0) - scene.get("start_time", 0.0))) or 5.0,
                    "zoom_start": 5.0,
                    "style": "default",
                },
            )
            if not (plan_result.success and plan_result.output):
                logger.warning("supervisor.map.plan_failed", extra={"scene_index": scene_idx})
                continue
            plan_dict = plan_result.output.get("plan", {})
            map_filename = f"map_scene_{scene_idx}_{new_id('')[:8]}.png"
            render_result = await self.run_agent(
                AgentName.GEO, "render_map",
                {"plan": plan_dict, "output_filename": map_filename},
            )
            if render_result.success and render_result.output:
                png_path = render_result.output.get("output_path")
                if png_path:
                    scene_map_paths[scene_idx] = png_path
                    map_images.append({
                        "scene_index": scene_idx,
                        "output_path": png_path,
                        "plan": plan_dict,
                    })
            else:
                logger.warning("supervisor.map.render_failed", extra={"scene_index": scene_idx})
        context["map_images"] = map_images
        context["scene_map_paths"] = scene_map_paths

        # Step 5: Assets MCP — list assets (none registered yet in mock).
        assets_result = await self.run_agent(AgentName.ASSET, "list_assets", {})
        results["assets"] = assets_result
        state = self._advance_state(state, WorkflowStateEnum.GENERATING_ASSETS)

        # Step 6: Text MCP — create text overlays per scene + render to PNG.
        text_overlays: list[dict[str, Any]] = []
        for scene in context["scenes"]:
            scene_id = scene.get("id", new_id("scene_"))
            title = scene.get("title", f"Scene {scene.get('index', 0)}")
            text_result = await self.run_agent(
                AgentName.TEXT, "create_text_overlay",
                {
                    "scene_id": scene_id,
                    "kind": "title",
                    "text": title,
                    "start_time": scene.get("start_time", 0.0),
                    "end_time": scene.get("end_time", 5.0),
                },
            )
            if text_result.success and text_result.output:
                text_overlays.append(text_result.output.get("overlay", {}))
        # Render each text overlay to a PNG image (real Pillow rendering).
        for overlay in text_overlays:
            render_text_result = await self.run_agent(
                AgentName.TEXT, "render_text",
                {
                    "text": overlay.get("text", ""),
                    "output_path": f"text_{overlay.get('scene_id', 'scene')}.png",
                    "font_size": 72,
                },
            )
            if render_text_result.success and render_text_result.output:
                overlay["rendered_path"] = render_text_result.output.get("output_path")
        results["text"] = AgentResult(
            agent=AgentName.TEXT,
            status="success",
            success=True,
            output={"overlays": text_overlays},
            errors=[],
            project_id=project_id,
        )
        context["text_overlays"] = text_overlays
        state = self._advance_state(state, WorkflowStateEnum.GENERATING_TEXT)

        # Step 7: Transitions MCP — create transitions between scenes.
        # The transition kind is derived from each scene's transition_type
        # (set by the Script MCP) and mapped to a renderable FFmpeg xfade kind.
        transitions: list[dict[str, Any]] = []
        scenes = context["scenes"]
        for i in range(1, len(scenes)):
            prev = scenes[i - 1]
            curr = scenes[i]
            # Derive the transition kind from the *incoming* scene's type, then
            # map it to a kind the Transitions/Render MCP servers understand.
            raw_type = curr.get("transition_type") or prev.get("transition_type") or "fade"
            kind = _map_transition_kind(raw_type)
            trans_result = await self.run_agent(
                AgentName.TRANSITION, "create_transition",
                {
                    "from_scene_id": prev.get("id"),
                    "to_scene_id": curr.get("id"),
                    "kind": kind,
                    "duration_sec": 0.5,
                    "start_time": prev.get("end_time", 0.0),
                },
            )
            if trans_result.success and trans_result.output:
                t = trans_result.output.get("transition", {})
                # Carry the renderable kind + direction through to composition.
                t["kind"] = kind
                t["direction"] = "left"
                transitions.append(t)
        results["transitions"] = AgentResult(
            agent=AgentName.TRANSITION,
            status="success",
            success=True,
            output={"transitions": transitions},
            errors=[],
            project_id=project_id,
        )
        context["transitions"] = transitions
        state = self._advance_state(state, WorkflowStateEnum.GENERATING_TRANSITIONS)

        # Step 8: Sound MCP — create sound design plan.
        sound_result = await self.run_agent(
            AgentName.SOUND, "create_sound_design_plan",
            {"scenes": scenes, "total_duration_sec": context["audio_duration_sec"] or total_duration_sec},
        )
        results["sound"] = sound_result
        if sound_result.success and sound_result.output:
            context["sound_events"] = sound_result.output.get("events", [])
        state = self._advance_state(state, WorkflowStateEnum.GENERATING_SOUND)

        # Step 8b: Sound MCP — build the real mixed audio track.
        # Turn the structured sound design + the (optional) caller-supplied
        # SFX/music files into a single audio file via the mix_audio tool. Only
        # assets that exist on disk are mixed; missing optional assets are
        # skipped (never fabricated). The resulting mixed audio (voiceover +
        # SFX + music) is muxed into the final video in place of the raw
        # voiceover, so all sound layers reach the MP4.
        mixed_audio_path: str | None = None
        duration = context["audio_duration_sec"] or total_duration_sec
        # Collect real, on-disk SFX tracks with scene timing. Map each
        # supplied SFX file to a scene's start_time so it is positioned in the
        # timeline; files that don't exist are dropped (safe default).
        sfx_specs: list[dict[str, Any]] = []
        if sfx_paths:
            for i, sp in enumerate(sfx_paths):
                if not sp:
                    continue
                # Position each SFX at the start of the i-th scene when known.
                scene_start = 0.0
                if i < len(scenes):
                    scene_start = float(scenes[i].get("start_time", 0.0))
                sfx_specs.append({
                    "file_path": sp,
                    "start_time": scene_start,
                    "volume_db": -6.0,
                    "fade_in_sec": 0.0,
                    "fade_out_sec": 0.0,
                })
        # Only invoke mix_audio when there is something to mix beyond the raw
        # voiceover, OR when we want a normalized/padded voiceover track. We
        # always invoke it so the audio is padded to the exact video duration
        # and normalized; if it fails, fall back to the raw voiceover path.
        mix_args: dict[str, Any] = {
            "output_filename": f"{project_id}_mixed.wav",
            "duration_sec": duration,
            "sample_rate": 44100,
            "channels": 1,
            "format": "wav",
        }
        if voiceover_path:
            mix_args["voiceover_path"] = voiceover_path
        if sfx_specs:
            mix_args["sfx_tracks"] = sfx_specs
        if music_path:
            mix_args["music_path"] = music_path
        mix_result = await self.run_agent(AgentName.SOUND, "mix_audio", mix_args)
        results["sound_mix"] = mix_result
        if mix_result.success and mix_result.output:
            mixed_audio_path = mix_result.output.get("output_path")
            context["mixed_audio_path"] = mixed_audio_path
            context["sound_mix"] = mix_result.output
        else:
            # Mixing failed (e.g. StubFFmpegService in test env, or no ffmpeg).
            # Fall back to the raw voiceover so the pipeline keeps working.
            logger.warning(
                "supervisor.sound.mix_failed",
                extra={"project_id": project_id, "errors": mix_result.errors},
            )
            if voiceover_path:
                mixed_audio_path = voiceover_path
        state = self._advance_state(state, WorkflowStateEnum.GENERATING_SOUND)

        # Step 9: Render MCP — compose the final video with overlays.
        # Phase 5C: when there are ≥2 scenes and planned transitions, render
        # each scene as an independent segment and join them with real FFmpeg
        # xfade transitions (compose_with_transitions). This preserves Phase 5A
        # map overlays (scoped per scene) and Phase 5B mixed audio (muxed onto
        # the final video). Falls back to the single continuous compose_video
        # when transitions are absent or composition fails.
        scene_map_paths = context.get("scene_map_paths", {})
        scenes = context.get("scenes", [])
        transitions = context.get("transitions", [])
        duration = context["audio_duration_sec"] or total_duration_sec

        # Build per-scene overlay lists (map PNG + text PNG), with times
        # relative to each scene's own segment start (0-based within segment).
        scene_segments: list[dict[str, Any]] = []
        for scene in scenes:
            sidx = scene.get("index", 0)
            seg_overlays: list[dict[str, Any]] = []
            map_path = scene_map_paths.get(sidx)
            if map_path:
                seg_overlays.append({
                    "image_path": map_path,
                    "x": 0.0, "y": 0.0,
                    "start_time": 0.0,
                    "end_time": float(scene.get("duration", scene.get("end_time", 5.0)) - scene.get("start_time", 0.0)),
                    "opacity": 1.0,
                })
            for overlay in text_overlays:
                # Attach a text overlay to the scene it belongs to (by timing).
                if overlay.get("scene_id") == scene.get("id"):
                    rendered_path = overlay.get("rendered_path")
                    if rendered_path:
                        seg_start = float(scene.get("start_time", 0.0))
                        ov_start = float(overlay.get("start_time", 0.0)) - seg_start
                        ov_end = float(overlay.get("end_time", 5.0)) - seg_start
                        seg_overlays.append({
                            "image_path": rendered_path,
                            "x": 0.1, "y": 0.05,
                            "start_time": max(0.0, ov_start),
                            "end_time": max(0.0, ov_end),
                        })
            scene_dur = float(scene.get("duration", 0.0)) or (
                float(scene.get("end_time", 0.0)) - float(scene.get("start_time", 0.0))
            )
            scene_segments.append({
                "scene_id": scene.get("id", f"scene_{sidx}"),
                "duration_sec": scene_dur,
                "background_color": "#1a1a2e",
                "overlays": seg_overlays,
            })

        # Transition specs for the compose_with_transitions tool.
        trans_specs: list[dict[str, Any]] = [
            {
                "kind": t.get("kind", "fade"),
                "duration_sec": float(t.get("duration_sec", 0.5)),
                "direction": t.get("direction", "left"),
            }
            for t in transitions
        ]

        render_output_path: str | None = None
        used_transition_compose = False
        # Use transition composition when we have ≥2 scenes and transitions.
        if len(scene_segments) >= 2 and trans_specs:
            trans_audio = mixed_audio_path or voiceover_path
            trans_args: dict[str, Any] = {
                "project_id": project_id,
                "output_filename": f"{project_id}.mp4",
                "segments": scene_segments,
                "transitions": trans_specs,
                "width": 1920,
                "height": 1080,
                "fps": 30.0,
            }
            if trans_audio:
                trans_args["audio_path"] = trans_audio
            trans_result = await self.run_agent(
                AgentName.RENDER, "compose_with_transitions", trans_args,
            )
            results["render"] = trans_result
            if trans_result.success and trans_result.output:
                render_output_path = trans_result.output.get("output_path")
                used_transition_compose = True
            else:
                logger.warning(
                    "supervisor.transition_compose.failed_fallback",
                    extra={"project_id": project_id, "errors": trans_result.errors},
                )

        # Fall back to (or use directly) the continuous single-video compose.
        if render_output_path is None:
            compose_overlays: list[dict[str, Any]] = []
            for scene in scenes:
                sidx = scene.get("index", 0)
                map_path = scene_map_paths.get(sidx)
                if map_path:
                    compose_overlays.append({
                        "image_path": map_path,
                        "x": 0.0, "y": 0.0,
                        "start_time": float(scene.get("start_time", 0.0)),
                        "end_time": float(scene.get("end_time", 5.0)),
                        "opacity": 1.0,
                    })
            for overlay in text_overlays:
                rendered_path = overlay.get("rendered_path")
                if rendered_path:
                    compose_overlays.append({
                        "image_path": rendered_path,
                        "x": 0.1, "y": 0.05,
                        "start_time": overlay.get("start_time", 0.0),
                        "end_time": overlay.get("end_time", 5.0),
                    })
            compose_args: dict[str, Any] = {
                "project_id": project_id,
                "output_filename": f"{project_id}.mp4",
                "duration_sec": duration,
                "width": 1920,
                "height": 1080,
                "fps": 30.0,
                "background_color": "#1a1a2e",
                "overlays": compose_overlays,
            }
            if mixed_audio_path:
                # Use the mixed audio track (voiceover + SFX + music) so all
                # sound layers reach the final MP4. Falls back to raw voiceover
                # when mixing was unavailable.
                compose_args["audio_path"] = mixed_audio_path
            elif voiceover_path:
                compose_args["audio_path"] = voiceover_path
            compose_result = await self.run_agent(AgentName.RENDER, "compose_video", compose_args)
            if not used_transition_compose:
                results["render"] = compose_result
            else:
                results["render_fallback"] = compose_result
            if compose_result.success and compose_result.output:
                render_output_path = compose_result.output.get("output_path")
            elif not compose_result.success:
                # Compose failed (e.g., StubFFmpegService in test env). Fall back
                # to the basic render_video tool so the workflow can still produce
                # a video in environments with ffmpeg but without compose support.
                fallback_result = await self.run_agent(
                    AgentName.RENDER, "create_render_job",
                    {
                        "project_id": project_id,
                        "output_filename": f"{project_id}_fallback.mp4",
                        "duration_sec": duration,
                    },
                )
                if fallback_result.success and fallback_result.output:
                    job_id = fallback_result.output.get("job_id")
                    render_result = await self.run_agent(AgentName.RENDER, "render_video", {"job_id": job_id})
                    results["render_fallback"] = render_result
                    if render_result.success and render_result.output:
                        render_output_path = render_result.output.get("output_path")
        state = self._advance_state(state, WorkflowStateEnum.RENDERING)

        # Step 10: QA MCP — create QA report.
        qa_result = await self.run_agent(
            AgentName.QA, "create_qa_report",
            {
                "project_id": project_id,
                "scenes": context["scenes"],
                "timeline_events": [],
                "audio_duration_sec": context["audio_duration_sec"],
                "video_duration_sec": context["audio_duration_sec"],
                "render_output_path": render_output_path,
            },
        )
        results["qa"] = qa_result
        state = self._advance_state(state, WorkflowStateEnum.QUALITY_CHECK)

        # Step 11: Decide COMPLETED / FAILED.
        qa_passed = False
        if qa_result.success and qa_result.output:
            qa_passed = qa_result.output.get("passed", False)
        if qa_passed:
            state = state.model_copy(update={
                "previous_state": state.current_state,
                "current_state": WorkflowStateEnum.COMPLETED,
            })
            log_event(logger, "workflow.completed", project_id=project_id)
        else:
            state = state.model_copy(update={
                "previous_state": state.current_state,
                "current_state": WorkflowStateEnum.FAILED,
            })
            log_event(logger, "workflow.failed", project_id=project_id, reason="qa_not_passed")

        return self._finalize(state, results, context, failed=not qa_passed)

    # --- helpers ------------------------------------------------------------

    def _advance_state(self, state: WorkflowState, target: WorkflowStateEnum) -> WorkflowState:
        """Advance the workflow state (best-effort; does not raise on invalid)."""
        try:
            nxt = _next_workflow_state(state.current_state)
            if nxt is not None:
                return state.model_copy(update={
                    "previous_state": state.current_state,
                    "current_state": nxt,
                    "previous_phase": state.current_phase,
                    "current_phase": _next_phase(state.current_phase),
                })
        except Exception:  # noqa: BLE001
            pass
        return state.model_copy(update={
            "previous_state": state.current_state,
            "current_state": target,
        })

    def _synchronize_timing(self, context: dict[str, Any]) -> dict[str, Any]:
        """Synchronize scene timing with audio duration."""
        audio_dur = context.get("audio_duration_sec")
        scenes = context.get("scenes", [])
        if audio_dur and scenes:
            per = audio_dur / len(scenes)
            for i, scene in enumerate(scenes):
                scene["start_time"] = round(i * per, 3)
                scene["end_time"] = round((i + 1) * per, 3)
        return context

    def _finalize(
        self, state: WorkflowState, results: dict[str, AgentResult], context: dict[str, Any], *, failed: bool
    ) -> dict[str, Any]:
        """Build the final workflow result dict and persist to the database."""
        # Persist workflow state, render job, and QA report to the DB.
        # Best-effort: DB errors do not fail the workflow result.
        try:
            from app.services.projects import ProjectService
            svc = ProjectService()
            project_id = context.get("project_id") or "unknown"
            # Ensure the project row exists (FK target for all child tables).
            if svc.get_project(project_id) is None:
                svc.create_project(
                    name=project_id,
                    script_text=context.get("script_text"),
                    project_id=project_id,
                )
            svc.save_workflow_state(
                project_id,
                current_state=state.current_state.value,
                previous_state=state.previous_state.value if state.previous_state else None,
                current_phase=state.current_phase.value if state.current_phase else None,
                agent_statuses={k: v.status for k, v in results.items()},
                retries={k: v.attempt - 1 for k, v in results.items()},
            )
            render_result = results.get("render")
            if render_result and render_result.success and render_result.output:
                output_path = render_result.output.get("output_path")
                if output_path:
                    svc.save_render_job(
                        project_id,
                        output_path=output_path,
                        status="completed",
                        duration_sec=context.get("audio_duration_sec"),
                    )
            qa_result = results.get("qa")
            if qa_result and qa_result.success and qa_result.output:
                svc.save_qa_report(
                    project_id,
                    passed=qa_result.output.get("passed", False),
                    findings=qa_result.output.get("findings", []),
                    summary=qa_result.output.get("summary", ""),
                )
        except Exception:  # noqa: BLE001
            # DB persistence is best-effort; never fail the workflow due to DB.
            pass
        return {
            "project_id": context.get("project_id"),
            "workflow_state": state.model_dump(mode="json"),
            "final_state": state.current_state.value,
            "failed": failed,
            "scenes": context.get("scenes", []),
            "audio_duration_sec": context.get("audio_duration_sec"),
            "resolved_locations": context.get("resolved_locations", []),
            "map_images": context.get("map_images", []),
            "scene_map_paths": context.get("scene_map_paths", {}),
            "text_overlays": context.get("text_overlays", []),
            "transitions": context.get("transitions", []),
            "sound_events": context.get("sound_events", []),
            "mixed_audio_path": context.get("mixed_audio_path"),
            "sound_mix": context.get("sound_mix"),
            "results": {k: v.model_dump(mode="json") for k, v in results.items()},
            "qa_report": results.get("qa", AgentResult(
                agent=AgentName.QA, status="failed", success=False, errors=["QA not run"]
            )).output,
        }


def _map_transition_kind(raw: str) -> str:
    """Map a scene's ``transition_type`` to a renderable FFmpeg xfade kind.

    The Script MCP emits narrative kinds (fade/dissolve); this maps them (and
    any synonyms) to the kinds understood by the Transitions/Render MCP
    servers. Unknown kinds fall back to ``fade`` so rendering always proceeds.
    """
    mapping = {
        "fade": "fade",
        "dissolve": "dissolve",
        "crossfade": "fade",
        "cut": "cut",
        "slide": "slide",
        "wipe": "wipe",
        "zoom": "zoom",
        "fade_to_black": "fade_to_black",
        "fadeblack": "fade_to_black",
        "map_zoom": "map_zoom",
        "map_to_map": "map_to_map",
    }
    return mapping.get(str(raw).strip().lower(), "fade")


def _next_phase(current: WorkflowPhase) -> WorkflowPhase:
    """Return the next phase in the canonical pipeline order."""
    order = [
        WorkflowPhase.INIT,
        WorkflowPhase.SCRIPT_UNDERSTANDING,
        WorkflowPhase.AUDIO_ANALYSIS,
        WorkflowPhase.GEO_RESOLUTION,
        WorkflowPhase.ASSET_SELECTION,
        WorkflowPhase.TEXT_GENERATION,
        WorkflowPhase.TRANSITION_SELECTION,
        WorkflowPhase.SOUND_DESIGN,
        WorkflowPhase.RENDERING,
        WorkflowPhase.QA,
        WorkflowPhase.DONE,
    ]
    try:
        idx = order.index(current)
    except ValueError:
        return current
    return order[min(idx + 1, len(order) - 1)]


__all__ = ["SupervisorAgent", "DEFAULT_MAX_RETRIES"]
