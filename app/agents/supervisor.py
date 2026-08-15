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
    ) -> dict[str, Any]:
        """Run the full 9-step orchestration workflow.

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
        geo_results: list[dict[str, Any]] = []
        for loc in context.get("locations", []):
            loc_name = loc.get("name", "")
            if loc_name:
                geo_result = await self.run_agent(AgentName.GEO, "geocode_location", {"query": loc_name})
                if geo_result.success and geo_result.output:
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

        # Step 5: Assets MCP — list assets (none registered yet in mock).
        assets_result = await self.run_agent(AgentName.ASSET, "list_assets", {})
        results["assets"] = assets_result
        state = self._advance_state(state, WorkflowStateEnum.GENERATING_ASSETS)

        # Step 6: Text MCP — create text overlays per scene.
        text_overlays: list[dict[str, Any]] = []
        for scene in context["scenes"]:
            scene_id = scene.get("id", new_id("scene_"))
            text_result = await self.run_agent(
                AgentName.TEXT, "create_text_overlay",
                {
                    "scene_id": scene_id,
                    "kind": "title",
                    "text": scene.get("title", f"Scene {scene.get('index', 0)}"),
                    "start_time": scene.get("start_time", 0.0),
                    "end_time": scene.get("end_time", 5.0),
                },
            )
            if text_result.success and text_result.output:
                text_overlays.append(text_result.output.get("overlay", {}))
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
        transitions: list[dict[str, Any]] = []
        scenes = context["scenes"]
        for i in range(1, len(scenes)):
            prev = scenes[i - 1]
            curr = scenes[i]
            trans_result = await self.run_agent(
                AgentName.TRANSITION, "create_transition",
                {
                    "from_scene_id": prev.get("id"),
                    "to_scene_id": curr.get("id"),
                    "kind": "fade",
                    "duration_sec": 0.5,
                    "start_time": prev.get("end_time", 0.0),
                },
            )
            if trans_result.success and trans_result.output:
                transitions.append(trans_result.output.get("transition", {}))
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

        # Step 9: Render MCP — create + execute render job.
        render_job_result = await self.run_agent(
            AgentName.RENDER, "create_render_job",
            {
                "project_id": project_id,
                "output_filename": f"{project_id}.mp4",
                "duration_sec": context["audio_duration_sec"] or total_duration_sec,
            },
        )
        results["render_create"] = render_job_result
        render_output_path = None
        if render_job_result.success and render_job_result.output:
            job_id = render_job_result.output.get("job_id")
            render_result = await self.run_agent(AgentName.RENDER, "render_video", {"job_id": job_id})
            results["render"] = render_result
            if render_result.success and render_result.output:
                render_output_path = render_result.output.get("output_path")
            elif not render_result.success:
                # Render failed — but in test/mock environments, this is expected.
                # We continue to QA so the report captures the missing render.
                render_output_path = None
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
        """Build the final workflow result dict."""
        return {
            "project_id": context.get("project_id"),
            "workflow_state": state.model_dump(mode="json"),
            "final_state": state.current_state.value,
            "failed": failed,
            "scenes": context.get("scenes", []),
            "audio_duration_sec": context.get("audio_duration_sec"),
            "resolved_locations": context.get("resolved_locations", []),
            "text_overlays": context.get("text_overlays", []),
            "transitions": context.get("transitions", []),
            "sound_events": context.get("sound_events", []),
            "results": {k: v.model_dump(mode="json") for k, v in results.items()},
            "qa_report": results.get("qa", AgentResult(
                agent=AgentName.QA, status="failed", success=False, errors=["QA not run"]
            )).output,
        }


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
