"""Tests for the centralized guardrail pipeline and new guardrail rules."""

from __future__ import annotations

import pytest

from app.core.enums import AgentName, AgentRunStatus, ProvenanceType, WorkflowState
from app.core.result import Result
from app.guardrails import Guardrails, GuardrailPipeline, validate_before_accept
from app.guardrails.rules import (
    check_agent_status,
    check_file_path_safe,
    check_id,
    check_path_traversal,
    check_provenance,
    check_workflow_transition,
)
from app.schemas.contracts import AgentResult, Provenance


class TestCheckPathTraversal:
    def test_safe_path(self) -> None:
        r = check_path_traversal("assets/photo.png")
        assert r.success

    def test_traversal_rejected(self) -> None:
        r = check_path_traversal("../../secret.txt")
        assert r.is_failure

    def test_empty_rejected(self) -> None:
        r = check_path_traversal("")
        assert r.is_failure

    def test_control_chars_rejected(self) -> None:
        r = check_path_traversal("file\x00.png")
        assert r.is_failure


class TestCheckId:
    def test_valid_id(self) -> None:
        r = check_id("proj_abc123")
        assert r.success

    def test_empty_rejected(self) -> None:
        r = check_id("")
        assert r.is_failure

    def test_none_rejected(self) -> None:
        r = check_id(None)
        assert r.is_failure

    def test_invalid_chars_rejected(self) -> None:
        r = check_id("proj with spaces")
        assert r.is_failure

    def test_too_long_rejected(self) -> None:
        r = check_id("a" * 65)
        assert r.is_failure

    def test_custom_field_name(self) -> None:
        r = check_id("", field="scene_id")
        assert r.is_failure
        assert any("scene_id" in e for e in r.errors)


class TestCheckWorkflowTransition:
    def test_valid_transition(self) -> None:
        r = check_workflow_transition("created", "analyzing_script")
        assert r.success

    def test_invalid_skip(self) -> None:
        r = check_workflow_transition("created", "rendering")
        assert r.is_failure

    def test_terminal_to_anything_fails(self) -> None:
        r = check_workflow_transition("completed", "created")
        assert r.is_failure

    def test_unknown_state_fails(self) -> None:
        r = check_workflow_transition("unknown", "created")
        assert r.is_failure


class TestCheckAgentStatus:
    def test_valid_status(self) -> None:
        r = check_agent_status("success")
        assert r.success

    def test_invalid_status(self) -> None:
        r = check_agent_status("bogus")
        assert r.is_failure


class TestCheckProvenance:
    def test_valid_dict_provenance(self) -> None:
        r = check_provenance({"provider": "wikimedia", "source": "commons"})
        assert r.success

    def test_none_rejected(self) -> None:
        r = check_provenance(None)
        assert r.is_failure

    def test_unknown_provider_rejected(self) -> None:
        r = check_provenance({"provider": "unknown", "source": "x"})
        assert r.is_failure

    def test_none_provider_rejected(self) -> None:
        r = check_provenance({"provider": "none", "source": "x"})
        assert r.is_failure

    def test_unknown_source_rejected(self) -> None:
        r = check_provenance({"provider": "x", "source": "unknown"})
        assert r.is_failure

    def test_pydantic_model_provenance(self) -> None:
        p = Provenance(
            provenance_type=ProvenanceType.ASSET, provider="wikimedia", source="commons"
        )
        r = check_provenance(p)
        assert r.success


class TestCheckFilePathSafe:
    def test_safe_path(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            r = check_file_path_safe("file.png", base_dir=td)
            assert r.success

    def test_traversal_rejected(self) -> None:
        r = check_file_path_safe("../../secret.txt")
        assert r.is_failure


class TestGuardrailPipeline:
    def test_valid_agent_result_passes(self) -> None:
        result = AgentResult(
            agent=AgentName.SCRIPT,
            status=AgentRunStatus.SUCCESS,
            success=True,
            project_id="proj_1",
        )
        report = GuardrailPipeline().validate_agent_result(result)
        assert report.passed

    def test_bad_provenance_in_output_fails(self) -> None:
        result = AgentResult(
            agent=AgentName.GEO,
            status=AgentRunStatus.SUCCESS,
            success=True,
            output={
                "locations": [
                    {"name": "Paris", "latitude": 48.85, "longitude": 2.35, "source": "unknown"}
                ]
            },
        )
        report = GuardrailPipeline().validate_agent_result(result)
        assert not report.passed
        assert any("provenance" in e.lower() for e in report.errors)

    def test_path_traversal_in_output_fails(self) -> None:
        result = AgentResult(
            agent=AgentName.ASSET,
            status=AgentRunStatus.SUCCESS,
            success=True,
            output={
                "assets": [
                    {"name": "icon", "file_path": "../../etc/passwd.png", "source": "wikimedia"}
                ]
            },
        )
        report = GuardrailPipeline().validate_agent_result(result)
        assert not report.passed
        assert any("traversal" in e.lower() for e in report.errors)

    def test_invalid_project_id_fails(self) -> None:
        # The IDField pattern rejects invalid IDs at construction time;
        # verify the guardrail also catches it via the check_id rule.
        from app.guardrails.rules import check_id

        r = check_id("invalid id with spaces!", field="project_id")
        assert r.is_failure

    def test_valid_provenance_passes(self) -> None:
        p = Provenance(
            provenance_type=ProvenanceType.ASSET, provider="wikimedia", source="commons"
        )
        result = AgentResult(
            agent=AgentName.ASSET,
            status=AgentRunStatus.SUCCESS,
            success=True,
            provenance=p,
        )
        report = GuardrailPipeline().validate_agent_result(result)
        assert report.passed

    def test_warnings_collected(self) -> None:
        # A valid result should have no warnings, but the mechanism works.
        result = AgentResult(
            agent=AgentName.SCRIPT,
            status=AgentRunStatus.SUCCESS,
            success=True,
        )
        report = GuardrailPipeline().validate_agent_result(result)
        assert report.passed
        assert isinstance(report.warnings, list)

    def test_report_to_dict(self) -> None:
        result = AgentResult(
            agent=AgentName.SCRIPT,
            status=AgentRunStatus.SUCCESS,
            success=True,
        )
        report = GuardrailPipeline().validate_agent_result(result)
        d = report.to_dict()
        assert "passed" in d
        assert "errors" in d
        assert "warnings" in d
        assert "rules_run" in d

    def test_validate_workflow_transition(self) -> None:
        pipeline = GuardrailPipeline()
        report = pipeline.validate_workflow_transition("created", "analyzing_script")
        assert report.passed

    def test_validate_workflow_transition_invalid(self) -> None:
        pipeline = GuardrailPipeline()
        report = pipeline.validate_workflow_transition("created", "rendering")
        assert not report.passed


class TestValidateBeforeAccept:
    def test_accepts_valid_result(self) -> None:
        result = AgentResult(
            agent=AgentName.SCRIPT,
            status=AgentRunStatus.SUCCESS,
            success=True,
            project_id="proj_1",
        )
        r = validate_before_accept(result)
        assert r.success

    def test_rejects_bad_result(self) -> None:
        result = AgentResult(
            agent=AgentName.GEO,
            status=AgentRunStatus.SUCCESS,
            success=True,
            output={
                "locations": [
                    {"name": "X", "latitude": 0, "longitude": 0, "source": "unknown"}
                ]
            },
        )
        r = validate_before_accept(result)
        assert r.is_failure

    def test_returns_warnings_in_metadata(self) -> None:
        result = AgentResult(
            agent=AgentName.SCRIPT,
            status=AgentRunStatus.SUCCESS,
            success=True,
        )
        r = validate_before_accept(result)
        assert r.success
        assert r.metadata is not None
        assert "rules_run" in r.metadata


class TestGuardrailsFacade:
    def test_id_check(self) -> None:
        g = Guardrails()
        assert g.id("proj_1").success
        assert g.id("").is_failure

    def test_path_traversal_check(self) -> None:
        g = Guardrails()
        assert g.path_traversal("safe/path.png").success
        assert g.path_traversal("../../evil").is_failure

    def test_workflow_transition_check(self) -> None:
        g = Guardrails()
        assert g.workflow_transition("created", "analyzing_script").success

    def test_agent_status_check(self) -> None:
        g = Guardrails()
        assert g.agent_status("success").success
        assert g.agent_status("bogus").is_failure

    def test_provenance_check(self) -> None:
        g = Guardrails()
        assert g.provenance({"provider": "x", "source": "y"}).success
        assert g.provenance(None).is_failure
