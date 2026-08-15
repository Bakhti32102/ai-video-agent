"""Centralized guardrail pipeline.

The :class:`GuardrailPipeline` runs all applicable guardrail checks against an
agent output *before* the workflow accepts it. It is the single entry point
the Supervisor uses to decide whether an :class:`AgentResult` is acceptable.

Design:
- The pipeline is composable: each check is a named rule that appends
  structured errors/warnings to a collected report.
- Critical failures (path traversal, missing provenance, schema violations)
  cause the pipeline to STOP and return a failed Result.
- Non-critical warnings are collected but do not block acceptance.
- The pipeline never silently repairs data.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.core.enums import AgentName
from app.core.logging import get_logger
from app.core.result import Result
from app.guardrails.guardrails import Guardrails
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

logger = get_logger("guardrail_pipeline")


class GuardrailReport:
    """Accumulated guardrail findings for a single validation run."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.rules_run: list[str] = []

    @property
    def passed(self) -> bool:
        return not self.errors

    def add_error(self, rule: str, message: str) -> None:
        self.errors.append(f"[{rule}] {message}")
        self.rules_run.append(rule)

    def add_warning(self, rule: str, message: str) -> None:
        self.warnings.append(f"[{rule}] {message}")
        self.rules_run.append(rule)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "errors": self.errors,
            "warnings": self.warnings,
            "rules_run": self.rules_run,
        }


class GuardrailPipeline:
    """Centralized validation pipeline for agent outputs.

    Usage::

        pipeline = GuardrailPipeline()
        report = pipeline.validate_agent_result(result)
        if not report.passed:
            # reject the result, trigger retry
    """

    def __init__(self, guardrails: Guardrails | None = None) -> None:
        self.guardrails = guardrails or Guardrails()

    # --- AgentResult validation (the main entry point) ----------------------

    def validate_agent_result(self, result: AgentResult) -> GuardrailReport:
        """Run all checks on an AgentResult before accepting it."""
        report = GuardrailReport()

        # A. Schema validation (already done by Pydantic on construction, but
        #    we re-check the status/ID consistency here).
        self._run(report, "schema.agent_status", self.guardrails.agent_status, result.status.value)
        if result.project_id is not None:
            self._run(report, "schema.project_id", self.guardrails.id, result.project_id, field="project_id")
        if result.scene_id is not None:
            self._run(report, "schema.scene_id", self.guardrails.id, result.scene_id, field="scene_id")

        # O. Provenance: if the agent claims external data, it must be traceable.
        if result.provenance is not None:
            self._run(report, "provenance", self.guardrails.provenance, result.provenance)

        # Validate output payload structure if it contains known entities.
        if result.success and result.output is not None:
            self._validate_output_payload(report, result)

        if not report.passed:
            logger.warning(
                "guardrail pipeline rejected agent %s result: %s",
                result.agent.value,
                "; ".join(report.errors),
            )
        elif report.warnings:
            logger.info(
                "guardrail pipeline passed agent %s with warnings: %s",
                result.agent.value,
                "; ".join(report.warnings),
            )
        return report

    # --- Output payload validation -------------------------------------------

    def _validate_output_payload(self, report: GuardrailReport, result: AgentResult) -> None:
        """Inspect the output payload for known entity types and validate them."""
        output = result.output
        if not isinstance(output, dict):
            return

        # Locations: validate coordinates + provenance.
        locations = output.get("locations", [])
        if isinstance(locations, list):
            for loc_data in locations:
                if isinstance(loc_data, dict):
                    self._validate_location_dict(report, loc_data)

        # Assets: validate format + source.
        assets = output.get("assets", [])
        if isinstance(assets, list):
            for asset_data in assets:
                if isinstance(asset_data, dict):
                    self._validate_asset_dict(report, asset_data)

        # File paths: check for traversal.
        for path_key in ("file_path", "voiceover_path", "output_path"):
            val = output.get(path_key)
            if isinstance(val, str):
                self._run(report, f"path_traversal.{path_key}", self.guardrails.path_traversal, val)

    def _validate_location_dict(self, report: GuardrailReport, loc: dict) -> None:
        """Validate a location dict from an agent output."""
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        if lat is not None and lon is not None:
            self._run(report, "geo.coordinates", self.guardrails.coordinates, float(lat), float(lon))
        source = loc.get("source")
        if source is not None and str(source).strip().lower() in {"unknown", "none", ""}:
            report.add_error("geo.provenance", f"location '{loc.get('name', '?')}' has unverifiable source")

    def _validate_asset_dict(self, report: GuardrailReport, asset: dict) -> None:
        """Validate an asset dict from an agent output."""
        path = asset.get("file_path")
        if isinstance(path, str):
            self._run(report, "asset.path_traversal", self.guardrails.path_traversal, path)
        source = asset.get("source")
        if source is not None and str(source).strip().lower() in {"unknown", "none", ""}:
            report.add_error("asset.provenance", f"asset '{asset.get('name', '?')}' has no traceable source")

    # --- Project validation --------------------------------------------------

    def validate_project(self, project: Project) -> GuardrailReport:
        """Run all cross-entity checks on a full project."""
        report = GuardrailReport()
        self._run(report, "project", self.guardrails.project, project)
        return report

    # --- QA report validation ------------------------------------------------

    def validate_qa_report(self, report_data: QAReport) -> GuardrailReport:
        """Validate a QA report."""
        rep = GuardrailReport()
        self._run(rep, "qa_report", self.guardrails.qa_report, report_data)
        return rep

    # --- Workflow transition validation --------------------------------------

    def validate_workflow_transition(self, current: str, target: str) -> GuardrailReport:
        """Validate a workflow state transition."""
        report = GuardrailReport()
        self._run(report, "workflow.transition", self.guardrails.workflow_transition, current, target)
        return report

    # --- File path validation ------------------------------------------------

    def validate_file_path(self, path: str, *, base_dir: str | None = None, must_exist: bool = False) -> GuardrailReport:
        """Full file-path safety validation."""
        report = GuardrailReport()
        self._run(report, "file.safety", self.guardrails.file_path_safe, path, base_dir=base_dir, must_exist=must_exist)
        return report

    # --- Helper --------------------------------------------------------------

    def _run(self, report: GuardrailReport, rule_name: str, fn: Any, *args: Any, **kwargs: Any) -> None:
        """Execute a guardrail function and collect its result."""
        result = fn(*args, **kwargs)
        if result.is_failure:
            for err in result.errors:
                report.add_error(rule_name, err)
        for warn in result.warnings:
            report.add_warning(rule_name, warn)


def validate_before_accept(result: AgentResult, pipeline: GuardrailPipeline | None = None) -> Result[AgentResult]:
    """Convenience function: validate an AgentResult and return a Result.

    This is the function the Supervisor calls before accepting an agent's
    output into the workflow. Returns ``Result.ok(result)`` if all guardrails
    pass, or ``Result.fail(...)`` with structured errors if not.
    """
    pipeline = pipeline or GuardrailPipeline()
    report = pipeline.validate_agent_result(result)
    if report.passed:
        return Result.ok(result, warnings=report.warnings or None, metadata={"rules_run": report.rules_run})
    return Result.fail(
        *report.errors,
        warnings=report.warnings or None,
        metadata={"rules_run": report.rules_run},
    )


__all__ = [
    "GuardrailPipeline",
    "GuardrailReport",
    "validate_before_accept",
]
