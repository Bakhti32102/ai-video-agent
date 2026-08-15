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
        if inp.video_duration_sec is None:
            findings.append(QAFinding(
                category=QACategory.RENDER_ERROR,
                severity=QASeverity.ERROR,
                message="no video_duration_sec provided; render output may be missing",
            ))
        # Check render_output_path if provided (via extra fields).
        render_path = getattr(inp, "render_output_path", None)
        if render_path:
            findings.extend(await self._check_render_output(render_path))
        elif inp.video_duration_sec is not None:
            findings.extend(await self._check_render_output(None, video_duration_sec=inp.video_duration_sec))
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
        findings.extend(await self._check_render_output(inp.render_output_path, video_duration_sec=inp.video_duration_sec))

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
                # The script server returns rich metadata fields not in the
                # Scene contract. Extract only the fields Scene needs.
                scene_kwargs = {
                    "id": s.get("id", ""),
                    "project_id": s.get("project_id", ""),
                    "index": s.get("index", 0),
                    "title": s.get("title", ""),
                    "start_time": float(s.get("start_time", 0.0)),
                    "end_time": float(s.get("end_time", 1.0)),
                    "narration": s.get("narration"),
                    "location": s.get("location"),
                    "assets": s.get("assets", []),
                    "status": s.get("status", "pending"),
                }
                # Remove None values for optional fields.
                scene_kwargs = {k: v for k, v in scene_kwargs.items() if v is not None}
                scenes.append(Scene.model_validate(scene_kwargs))
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

    async def _check_render_output(self, render_output_path: str | None, *, video_duration_sec: float | None = None) -> list[QAFinding]:
        findings: list[QAFinding] = []
        # If a video duration was expected but no render output path is provided,
        # the render step failed or was skipped.
        if render_output_path is None:
            if video_duration_sec is not None:
                findings.append(QAFinding(
                    category=QACategory.RENDER_ERROR,
                    severity=QASeverity.CRITICAL,
                    message="no render output path provided; video was not rendered",
                ))
            return findings
        # Check file existence.
        import os
        if not os.path.exists(render_output_path):
            findings.append(QAFinding(
                category=QACategory.RENDER_ERROR,
                severity=QASeverity.CRITICAL,
                message=f"render output file does not exist: {render_output_path}",
            ))
            return findings
        # Check file size is non-trivial (>1KB).
        size = os.path.getsize(render_output_path)
        if size < 1024:
            findings.append(QAFinding(
                category=QACategory.RENDER_ERROR,
                severity=QASeverity.ERROR,
                message=f"render output file is suspiciously small ({size} bytes): {render_output_path}",
            ))
        # Probe the video file for codec/dimension/duration validation.
        try:
            from app.services.ffmpeg import get_ffmpeg_service
            ffmpeg = get_ffmpeg_service()
            info = await ffmpeg.probe(render_output_path)
            if info.duration_sec is not None and info.duration_sec < 0.5:
                findings.append(QAFinding(
                    category=QACategory.RENDER_ERROR,
                    severity=QASeverity.ERROR,
                    message=f"rendered video duration is too short: {info.duration_sec:.2f}s",
                ))
            if info.width and info.height:
                if info.width != 1920 or info.height != 1080:
                    findings.append(QAFinding(
                        category=QACategory.RENDER_ERROR,
                        severity=QASeverity.WARNING,
                        message=f"rendered video is {info.width}x{info.height}; expected 1920x1080",
                    ))
            if info.codec and info.codec not in ("h264", "hevc", "vp9", "av1"):
                findings.append(QAFinding(
                    category=QACategory.RENDER_ERROR,
                    severity=QASeverity.WARNING,
                    message=f"rendered video codec is {info.codec}; h264 recommended",
                ))
        except Exception as exc:  # noqa: BLE001
            findings.append(QAFinding(
                category=QACategory.RENDER_ERROR,
                severity=QASeverity.WARNING,
                message=f"could not probe render output: {exc}",
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
