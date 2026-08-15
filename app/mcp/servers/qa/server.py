"""Video QA MCP server (Phase 1: structural checks implemented).

Inspects generated project data (and, in Phase 2, rendered output) for:
missing scenes, timing issues, audio/video duration mismatch, invalid
coordinates, missing assets, text overflow, and rendering errors.
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName, QASeverity, QACategory
from app.core.result import Result
from app.guardrails.guardrails import Guardrails
from app.mcp.servers.base import BaseMcpServer
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

    def __init__(self, guardrails: Guardrails | None = None) -> None:
        super().__init__()
        self.guardrails = guardrails or Guardrails()

    def list_tools(self) -> list[str]:
        return ["run_qa"]

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        if tool == "run_qa":
            return await self.run_qa(arguments)
        return self._fail(f"unknown tool '{tool}' for QA MCP server")

    async def run_qa(self, arguments: dict[str, Any]) -> Result[QAReport]:
        project_id = arguments.get("project_id", "proj")
        scenes_data = arguments.get("scenes", [])
        timeline_data = arguments.get("timeline_events", [])
        audio_duration = arguments.get("audio_duration_sec")
        video_duration = arguments.get("video_duration_sec")

        findings: list[QAFinding] = []

        scenes = [Scene.model_validate(s) for s in scenes_data]
        timeline = [TimelineEvent.model_validate(t) for t in timeline_data]

        # Missing scenes: gaps in the timeline coverage.
        findings.extend(self._check_scene_coverage(scenes, timeline, project_id))

        # Timing: overlaps on same layer.
        r = self.guardrails.timeline_overlaps(timeline)
        if r.is_failure:
            for err in r.errors:
                findings.append(QAFinding(category=QACategory.TIMING, severity=QASeverity.WARNING, message=err))

        # Audio/video mismatch.
        if audio_duration is not None and video_duration is not None:
            diff = abs(float(audio_duration) - float(video_duration))
            if diff > 0.5:
                findings.append(
                    QAFinding(
                        category=QACategory.AUDIO_VIDEO_MISMATCH,
                        severity=QASeverity.ERROR,
                        message=f"audio ({audio_duration}s) and video ({video_duration}s) durations differ by {diff:.2f}s",
                    )
                )

        # Invalid coordinates + missing assets per scene.
        for scene in scenes:
            if scene.location is not None:
                r = self.guardrails.location(scene.location)
                if r.is_failure:
                    for err in r.errors:
                        findings.append(
                            QAFinding(
                                category=QACategory.INVALID_COORDINATES,
                                severity=QASeverity.ERROR,
                                message=err,
                                scene_id=scene.id,
                            )
                        )
            for asset in scene.assets:
                r = self.guardrails.asset(asset)
                if r.is_failure:
                    for err in r.errors:
                        findings.append(
                            QAFinding(
                                category=QACategory.MISSING_ASSET,
                                severity=QASeverity.ERROR,
                                message=err,
                                scene_id=scene.id,
                            )
                        )

        passed = not any(f.severity in (QASeverity.ERROR, QASeverity.CRITICAL) for f in findings)
        report = QAReport(
            id=f"qa_{project_id}",
            project_id=project_id,
            passed=passed,
            summary=f"{len(findings)} finding(s); passed={passed}",
            findings=findings,
            checked_at=utc_now_iso(),
        )
        # Validate report against guardrails (passed + error/critical consistency).
        r = self.guardrails.qa_report(report)
        if r.is_failure:
            return Result.fail(*r.errors)
        return Result.ok(report.model_dump(mode="json"))

    def _check_scene_coverage(
        self, scenes: list[Scene], timeline: list[TimelineEvent], project_id: str
    ) -> list[QAFinding]:
        findings: list[QAFinding] = []
        if not scenes:
            findings.append(
                QAFinding(
                    category=QACategory.MISSING_SCENE,
                    severity=QASeverity.CRITICAL,
                    message="project has no scenes",
                )
            )
            return findings
        # Detect gaps between consecutive scenes.
        ordered = sorted(scenes, key=lambda s: s.start_time)
        for prev, curr in zip(ordered, ordered[1:]):
            if curr.start_time > prev.end_time + 0.001:
                findings.append(
                    QAFinding(
                        category=QACategory.MISSING_SCENE,
                        severity=QASeverity.WARNING,
                        message=f"gap between scene {prev.index} (ends {prev.end_time}) and scene {curr.index} (starts {curr.start_time})",
                        scene_id=curr.id,
                    )
                )
        return findings
