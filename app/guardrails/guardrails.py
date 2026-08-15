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

    def agent_result(self, result: AgentResult) -> Result[AgentResult]:
        # Pydantic model_validator already enforces success/errors consistency.
        return Result.ok(result)


__all__ = ["Guardrails"]
