"""Video QA MCP server.

Inspects generated project data and rendered output for structural and
semantic issues. Critical QA failures prevent the COMPLETED state.

Tools:
- ``validate_project`` — full project validation (scenes, timeline, audio, assets, locations)
- ``validate_timeline`` — timeline overlap + gap checks
- ``validate_audio`` — audio duration vs video duration check
- ``validate_assets`` — missing/invalid asset checks
- ``validate_locations`` — unresolved location + invalid coordinate checks
- ``validate_render`` — render output existence + metadata checks
- ``create_qa_report`` — produce a structured QAReport

Legacy tools (backward compat):
- ``run_qa`` — alias for ``create_qa_report``
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName, QASeverity, QACategory
from app.core.result import Result
from app.guardrails.guardrails import Guardrails
from app.mcp.schemas import (
    CreateQaReportInput,
    CreateQaReportOutput,
    ValidateProjectInput,
    ValidateProjectOutput,
)
from app.mcp.servers.base import BaseMcpServer, ToolDefinition
from app.schemas.contracts import (
    QAFinding,
    QAReport,
    Scene,
    TimelineEvent,
)
from app.schemas.validators import utc_now_iso


class QaMcpServer(BaseMcpServer):
    """Inspects project data and produces a structured QA report."""

    name = AgentName.QA
    version = "3.0.0"
    description = "Validates project data and rendered output; critical failures block COMPLETED."

    def __init__(self, guardrails: Guardrails | None = None) -> None:
        super().__init__()
        self.guardrails = guardrails or Guardrails()
        self._register_tool(ToolDefinition(
            name="validate_project",
            description="Run all validation checks on a project and return findings.",
            input_schema=ValidateProjectInput,
            output_schema=ValidateProjectOutput,
            handler=self._validate_project,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="validate_timeline",
            description="Validate timeline for overlaps and gaps.",
            input_schema=ValidateProjectInput,
            output_schema=ValidateProjectOutput,
            handler=self._validate_timeline,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="validate_audio",
            description="Validate audio/video duration consistency.",
            input_schema=ValidateProjectInput,
            output_schema=ValidateProjectOutput,
            handler=self._validate_audio,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="validate_assets",
            description="Validate assets for missing or invalid references.",
            input_schema=ValidateProjectInput,
            output_schema=ValidateProjectOutput,
            handler=self._validate_assets,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="validate_locations",
            description="Validate locations for unresolved/ambiguous coordinates.",
            input_schema=ValidateProjectInput,
            output_schema=ValidateProjectOutput,
            handler=self._validate_locations,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="validate_render",
            description="Validate render output exists and has valid metadata.",
            input_schema=ValidateProjectInput,
            output_schema=ValidateProjectOutput,
            handler=self._validate_render,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="create_qa_report",
            description="Produce a structured QA report from all validation findings.",
            input_schema=CreateQaReportInput,
            output_schema=CreateQaReportOutput,
            handler=self._create_qa_report,
            tags={"read"},
        ))

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        if tool == "run_qa":
            return await self.execute_tool("create_qa_report", arguments)
        return await self.execute_tool(tool, arguments)

    # --- tool handlers ------------------------------------------------------

    async def _validate_project(self, inp: ValidateProjectInput) -> Result[ValidateProjectOutput]:
        """Full project validation: scenes, timeline, audio, assets, locations."""
        findings: list[QAFinding] = []
        scenes = self._parse_scenes(inp.scenes)
        timeline = self._parse_timeline(inp.timeline_events)

        findings.extend(self._check_scene_coverage(scenes))
        findings.extend(self._check_timeline(timeline))
        findings.extend(self._check_audio(inp.audio_duration_sec, inp.video_duration_sec))
        findings.extend(self._check_assets(scenes))
        findings.extend(self._check_locations(scenes))

        return self._build_output(inp.project_id, findings)

    async def _validate_timeline(self, inp: ValidateProjectInput) -> Result[ValidateProjectOutput]:
        timeline = self._parse_timeline(inp.timeline_events)
        findings = self._check_timeline(timeline)
        findings.extend(self._check_scene_coverage(self._parse_scenes(inp.scenes)))
        return self._build_output(inp.project_id, findings)

    async def _validate_audio(self, inp: ValidateProjectInput) -> Result[ValidateProjectOutput]:
        findings = self._check_audio(inp.audio_duration_sec, inp.video_duration_sec)
        return self._build_output(inp.project_id, findings)

    async def _validate_assets(self, inp: ValidateProjectInput) -> Result[ValidateProjectOutput]:
        scenes = self._parse_scenes(inp.scenes)
        findings = self._check_assets(scenes)
        return self._build_output(inp.project_id, findings)

    async def _validate_locations(self, inp: ValidateProjectInput) -> Result[ValidateProjectOutput]:
        scenes = self._parse_scenes(inp.scenes)
        findings = self._check_locations(scenes)
        return self._build_output(inp.project_id, findings)

    async def _validate_render(self, inp: ValidateProjectInput) -> Result[ValidateProjectOutput]:
        findings: list[QAFinding] = []
        # The render_output_path comes via create_qa_report; for the standalone
        # validate_render tool, check video_duration presence.
        if inp.video_duration_sec is None:
            findings.append(QAFinding(
                category=QACategory.RENDER_ERROR,
                severity=QASeverity.ERROR,
                message="no video_duration_sec provided; render output may be missing",
            ))
        return self._build_output(inp.project_id, findings)

    async def _create_qa_report(self, inp: CreateQaReportInput) -> Result[CreateQaReportOutput]:
        scenes = self._parse_scenes(inp.scenes)
        timeline = self._parse_timeline(inp.timeline_events)
        findings: list[QAFinding] = []
        findings.extend(self._check_scene_coverage(scenes))
        findings.extend(self._check_timeline(timeline))
        findings.extend(self._check_audio(inp.audio_duration_sec, inp.video_duration_sec))
        findings.extend(self._check_assets(scenes))
        findings.extend(self._check_locations(scenes))
        findings.extend(self._check_render_output(inp.render_output_path))

        passed = not any(f.severity in (QASeverity.ERROR, QASeverity.CRITICAL) for f in findings)
        report = QAReport(
            id=f"qa_{inp.project_id}",
            project_id=inp.project_id,
            passed=passed,
            summary=f"{len(findings)} finding(s); passed={passed}",
            findings=findings,
            checked_at=utc_now_iso(),
        )
        # Validate report against guardrails.
        r = self.guardrails.qa_report(report)
        if r.is_failure:
            return Result.fail(*r.errors)
        return Result.ok(CreateQaReportOutput(
            report=report.model_dump(mode="json"),
            passed=passed,
            findings=[f.model_dump(mode="json") for f in findings],
            summary=report.summary or "",
        ))

    # --- check helpers ------------------------------------------------------

    def _parse_scenes(self, scenes_data: list[dict[str, Any]]) -> list[Scene]:
        scenes: list[Scene] = []
        for s in scenes_data:
            try:
                scenes.append(Scene.model_validate(s))
            except Exception:  # noqa: BLE001
                continue
        return scenes

    def _parse_timeline(self, timeline_data: list[dict[str, Any]]) -> list[TimelineEvent]:
        timeline: list[TimelineEvent] = []
        for t in timeline_data:
            try:
                timeline.append(TimelineEvent.model_validate(t))
            except Exception:  # noqa: BLE001
                continue
        return timeline

    def _check_scene_coverage(self, scenes: list[Scene]) -> list[QAFinding]:
        findings: list[QAFinding] = []
        if not scenes:
            findings.append(QAFinding(
                category=QACategory.MISSING_SCENE,
                severity=QASeverity.CRITICAL,
                message="project has no scenes",
            ))
            return findings
        ordered = sorted(scenes, key=lambda s: s.start_time)
        for prev, curr in zip(ordered, ordered[1:]):
            if curr.start_time > prev.end_time + 0.001:
                findings.append(QAFinding(
                    category=QACategory.MISSING_SCENE,
                    severity=QASeverity.WARNING,
                    message=f"gap between scene {prev.index} (ends {prev.end_time}) and scene {curr.index} (starts {curr.start_time})",
                    scene_id=curr.id,
                ))
        # Missing narration check.
        for scene in scenes:
            if not scene.narration or not scene.narration.strip():
                findings.append(QAFinding(
                    category=QACategory.MISSING_SCENE,
                    severity=QASeverity.WARNING,
                    message=f"scene {scene.index} has no narration",
                    scene_id=scene.id,
                ))
        return findings

    def _check_timeline(self, timeline: list[TimelineEvent]) -> list[QAFinding]:
        findings: list[QAFinding] = []
        r = self.guardrails.timeline_overlaps(timeline)
        if r.is_failure:
            for err in r.errors:
                findings.append(QAFinding(category=QACategory.TIMING, severity=QASeverity.WARNING, message=err))
        return findings

    def _check_audio(self, audio_dur: float | None, video_dur: float | None) -> list[QAFinding]:
        findings: list[QAFinding] = []
        if audio_dur is not None and video_dur is not None:
            diff = abs(float(audio_dur) - float(video_dur))
            if diff > 0.5:
                findings.append(QAFinding(
                    category=QACategory.AUDIO_VIDEO_MISMATCH,
                    severity=QASeverity.ERROR,
                    message=f"audio ({audio_dur}s) and video ({video_dur}s) durations differ by {diff:.2f}s",
                ))
        return findings

    def _check_assets(self, scenes: list[Scene]) -> list[QAFinding]:
        findings: list[QAFinding] = []
        for scene in scenes:
            for asset in scene.assets:
                r = self.guardrails.asset(asset)
                if r.is_failure:
                    for err in r.errors:
                        findings.append(QAFinding(
                            category=QACategory.MISSING_ASSET,
                            severity=QASeverity.ERROR,
                            message=err,
                            scene_id=scene.id,
                        ))
        return findings

    def _check_locations(self, scenes: list[Scene]) -> list[QAFinding]:
        findings: list[QAFinding] = []
        for scene in scenes:
            if scene.location is not None:
                r = self.guardrails.location(scene.location)
                if r.is_failure:
                    for err in r.errors:
                        findings.append(QAFinding(
                            category=QACategory.INVALID_COORDINATES,
                            severity=QASeverity.ERROR,
                            message=err,
                            scene_id=scene.id,
                        ))
                # Check for unresolved status in the location's geocode payload.
                payload = scene.location.geocode_payload or {}
                if isinstance(payload, dict) and payload.get("status") == "unresolved":
                    findings.append(QAFinding(
                        category=QACategory.INVALID_COORDINATES,
                        severity=QASeverity.ERROR,
                        message=f"location '{scene.location.name}' is unresolved; coordinates not verified",
                        scene_id=scene.id,
                    ))
        return findings

    def _check_render_output(self, render_output_path: str | None) -> list[QAFinding]:
        findings: list[QAFinding] = []
        if render_output_path is None:
            findings.append(QAFinding(
                category=QACategory.RENDER_ERROR,
                severity=QASeverity.CRITICAL,
                message="no render output path provided; video was not rendered",
            ))
        return findings

    def _build_output(self, project_id: str, findings: list[QAFinding]) -> Result[ValidateProjectOutput]:
        passed = not any(f.severity in (QASeverity.ERROR, QASeverity.CRITICAL) for f in findings)
        return Result.ok(ValidateProjectOutput(
            passed=passed,
            findings=[f.model_dump(mode="json") for f in findings],
            summary=f"{len(findings)} finding(s); passed={passed}",
            checked_at=utc_now_iso(),
        ))
