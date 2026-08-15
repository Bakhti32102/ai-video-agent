"""High-level guardrail facade.

Provides a single :class:`Guardrails` object that runs all relevant rules for
a given payload type. This keeps agent call sites simple while centralising
the validation policy.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.core.result import Result
from app.guardrails import rules
from app.schemas.contracts import (
    AgentResult,
    Asset,
    Location,
    Project,
    Provenance,
    QAReport,
    Scene,
    TimelineEvent,
)


class Guardrails:
    """Application-level guardrail facade (dependency-injectable)."""

    def required_fields(self, payload: dict, required: list[str]) -> Result[dict]:
        return rules.check_required_fields(payload, required)

    def file_path(self, path: str, *, must_exist: bool = False) -> Result[str]:
        return rules.check_file_path(path, must_exist=must_exist)

    def file_path_safe(self, path: str, *, base_dir: str | None = None, must_exist: bool = False) -> Result[str]:
        return rules.check_file_path_safe(path, base_dir=base_dir, must_exist=must_exist)

    def path_traversal(self, path: str) -> Result[str]:
        return rules.check_path_traversal(path)

    def supported_media(self, path: str, allowed: frozenset[str] | None = None) -> Result[str]:
        return rules.check_supported_media(path, allowed)

    def duration(self, value: float, *, min_value: float = 0.0, max_value: float | None = None) -> Result[float]:
        return rules.check_duration(value, min_value=min_value, max_value=max_value)

    def time_range(self, start: float, end: float) -> Result[tuple[float, float]]:
        return rules.check_time_range(start, end)

    def scene_timing(self, scene: Scene, project_duration: float | None = None) -> Result[Scene]:
        return rules.check_scene_timing(scene, project_duration)

    def timeline_overlaps(self, events: list[TimelineEvent]) -> Result[list[TimelineEvent]]:
        return rules.check_timeline_overlaps(events)

    def coordinates(self, latitude: float, longitude: float) -> Result[tuple[float, float]]:
        return rules.check_coordinates(latitude, longitude)

    def location(self, location: Location) -> Result[Location]:
        return rules.check_location(location)

    def asset(self, asset: Asset) -> Result[Asset]:
        return rules.check_asset(asset)

    def missing_assets(self, assets: list[Asset], required_ids: list[str]) -> Result[list[str]]:
        return rules.check_missing_assets(assets, required_ids)

    def api_configuration(self, config: dict) -> Result[dict]:
        return rules.check_api_configuration(config)

    def agent_output(self, model_cls: type[BaseModel], payload: Any) -> Result[BaseModel]:
        return rules.check_agent_output(model_cls, payload)

    def id(self, value: str | None, *, field: str = "id") -> Result[str]:
        return rules.check_id(value, field=field)

    def workflow_transition(self, current: str, target: str) -> Result[str]:
        return rules.check_workflow_transition(current, target)

    def agent_status(self, status: str) -> Result[str]:
        return rules.check_agent_status(status)

    def provenance(self, provenance: Any) -> Result[Any]:
        return rules.check_provenance(provenance)

    def project(self, project: Project) -> Result[Project]:
        """Run cross-entity checks for a full project."""
        errors: list[str] = []
        duration = project.target_duration_sec
        for scene in project.scenes:
            r = self.scene_timing(scene, duration)
            if r.is_failure:
                errors.extend(r.errors)
            if scene.location is not None:
                r = self.location(scene.location)
                if r.is_failure:
                    errors.extend(r.errors)
            for asset in scene.assets:
                r = self.asset(asset)
                if r.is_failure:
                    errors.extend(r.errors)
        if errors:
            return Result.fail(*errors)
        return Result.ok(project)

    def qa_report(self, report: QAReport) -> Result[QAReport]:
        """A QA report is structurally validated by Pydantic already; here we
        add the semantic rule that a passing report must not contain
        error/critical findings."""
        if report.passed:
            blocking = [
                f for f in report.findings if f.severity in ("error", "critical")
            ]
            if blocking:
                return Result.fail(
                    "QA report marked passed but contains error/critical findings: "
                    + "; ".join(f.message for f in blocking)
                )
        return Result.ok(report)

    def video_output(
        self,
        *,
        width: int = 1920,
        height: int = 1080,
        fps: float = 30.0,
        codec: str = "h264",
        duration_sec: float | None = None,
    ) -> Result[dict[str, Any]]:
        """Validate video output parameters meet documentary standards."""
        errors: list[str] = []
        if width < 1280 or height < 720:
            errors.append(f"video resolution {width}x{height} is below minimum 1280x720")
        if width != 1920 or height != 1080:
            errors.append(f"video resolution {width}x{height}; expected 1920x1080 for 16:9 documentary")
        if fps < 24.0 or fps > 60.0:
            errors.append(f"fps {fps} is outside acceptable range 24-60")
        if codec not in ("h264", "hevc", "vp9", "av1"):
            errors.append(f"codec {codec} is not a recommended video codec (h264/hevc/vp9/av1)")
        if duration_sec is not None and duration_sec < 1.0:
            errors.append(f"video duration {duration_sec}s is too short (minimum 1.0s)")
        if errors:
            return Result.fail(*errors)
        return Result.ok({
            "width": width, "height": height, "fps": fps, "codec": codec, "duration_sec": duration_sec,
        })

    def agent_result(self, result: AgentResult) -> Result[AgentResult]:
        """Validate a full AgentResult, including provenance when present."""
        errors: list[str] = []
        r = self.agent_status(result.status.value)
        if r.is_failure:
            errors.extend(r.errors)
        if result.project_id is not None:
            r = self.id(result.project_id, field="project_id")
            if r.is_failure:
                errors.extend(r.errors)
        if result.scene_id is not None:
            r = self.id(result.scene_id, field="scene_id")
            if r.is_failure:
                errors.extend(r.errors)
        if result.provenance is not None:
            r = self.provenance(result.provenance)
            if r.is_failure:
                errors.extend(r.errors)
        if errors:
            return Result.fail(*errors)
        return Result.ok(result)


__all__ = ["Guardrails"]
