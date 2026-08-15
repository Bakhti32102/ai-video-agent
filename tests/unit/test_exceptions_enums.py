"""Tests for the enhanced exception hierarchy and new enums (app/core)."""

from __future__ import annotations

import pytest

from app.core.enums import ProvenanceType, WorkflowState
from app.core.exceptions import (
    AgentError,
    AppError,
    DatabaseError,
    FileSafetyError,
    GuardrailError,
    RenderError,
    WorkflowError,
)


class TestAppErrorCode:
    def test_app_error_has_code(self) -> None:
        err = AppError("something failed", code="E001")
        assert err.code == "E001"
        assert "something failed" in str(err)

    def test_app_error_default_code(self) -> None:
        err = AppError("failed")
        assert err.code == "APP_ERROR"

    def test_app_error_carries_details(self) -> None:
        err = AppError("failed", code="E002", details={"key": "value"})
        assert err.details == {"key": "value"}


class TestExceptionHierarchy:
    def test_all_inherit_from_app_error(self) -> None:
        for exc_cls in [
            AgentError,
            DatabaseError,
            FileSafetyError,
            GuardrailError,
            RenderError,
            WorkflowError,
        ]:
            assert issubclass(exc_cls, AppError), f"{exc_cls.__name__} should inherit AppError"

    def test_agent_error_message_and_code(self) -> None:
        err = AgentError("agent failed", agent="script")
        assert "agent failed" in str(err)
        assert err.code == "AGENT_ERROR"
        assert err.agent == "script"

    def test_workflow_error_carries_details(self) -> None:
        err = WorkflowError("bad transition", details={"from": "a", "to": "b"})
        assert err.details == {"from": "a", "to": "b"}
        assert err.code == "WORKFLOW_ERROR"

    def test_file_safety_error_is_subclass(self) -> None:
        err = FileSafetyError("traversal")
        assert isinstance(err, AppError)
        assert err.code == "FILE_SAFETY_ERROR"

    def test_to_dict_serializable(self) -> None:
        err = AgentError("failed", agent="script", details={"extra": "info"})
        d = err.to_dict()
        assert d["code"] == "AGENT_ERROR"
        assert d["message"] == "failed"
        assert "agent" in d["details"]


class TestProvenanceType:
    def test_has_geocoding(self) -> None:
        assert ProvenanceType.GEOCODING.value == "geocoding"

    def test_has_asset(self) -> None:
        assert ProvenanceType.ASSET.value == "asset"

    def test_has_ai_generated(self) -> None:
        assert ProvenanceType.AI_GENERATED.value == "ai_generated"

    def test_all_values_are_strings(self) -> None:
        for pt in ProvenanceType:
            assert isinstance(pt.value, str)


class TestWorkflowStateEnum:
    def test_has_created(self) -> None:
        assert WorkflowState.CREATED.value == "created"

    def test_has_completed(self) -> None:
        assert WorkflowState.COMPLETED.value == "completed"

    def test_has_failed(self) -> None:
        assert WorkflowState.FAILED.value == "failed"

    def test_has_cancelled(self) -> None:
        assert WorkflowState.CANCELLED.value == "cancelled"

    def test_all_values_are_strings(self) -> None:
        for ws in WorkflowState:
            assert isinstance(ws.value, str)
