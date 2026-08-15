"""Tests for the workflow state machine (app/core/workflow.py)."""

from __future__ import annotations

import pytest

from app.core.enums import WorkflowState
from app.core.exceptions import WorkflowError
from app.core.workflow import (
    WorkflowStateMachine,
    allowed_transitions,
    can_transition_to,
    is_terminal,
    is_valid_transition,
    next_state,
)


class TestTransitionValidation:
    def test_valid_forward_transition(self) -> None:
        assert is_valid_transition(WorkflowState.CREATED, WorkflowState.ANALYZING_SCRIPT)

    def test_invalid_skip_transition(self) -> None:
        # CREATED -> GENERATING_ASSETS skips phases; must be rejected.
        assert not is_valid_transition(WorkflowState.CREATED, WorkflowState.GENERATING_ASSETS)

    def test_any_state_can_fail(self) -> None:
        for state in WorkflowState:
            if state not in (WorkflowState.COMPLETED, WorkflowState.FAILED, WorkflowState.CANCELLED):
                assert is_valid_transition(state, WorkflowState.FAILED), f"{state} should go to FAILED"

    def test_any_state_can_cancel(self) -> None:
        for state in WorkflowState:
            if state not in (WorkflowState.COMPLETED, WorkflowState.FAILED, WorkflowState.CANCELLED):
                assert is_valid_transition(state, WorkflowState.CANCELLED)

    def test_terminal_cannot_transition(self) -> None:
        for terminal in (WorkflowState.COMPLETED, WorkflowState.FAILED, WorkflowState.CANCELLED):
            assert not is_valid_transition(terminal, WorkflowState.CREATED)

    def test_same_state_allowed_for_retries(self) -> None:
        # A state can transition to itself (retry within the same phase).
        assert is_valid_transition(WorkflowState.ANALYZING_SCRIPT, WorkflowState.ANALYZING_SCRIPT)

    def test_can_transition_to_ok(self) -> None:
        result = can_transition_to(WorkflowState.CREATED, WorkflowState.ANALYZING_SCRIPT)
        assert result.success
        assert result.data == WorkflowState.ANALYZING_SCRIPT

    def test_can_transition_to_fail(self) -> None:
        result = can_transition_to(WorkflowState.COMPLETED, WorkflowState.CREATED)
        assert result.is_failure
        assert any("terminal" in e for e in result.errors)


class TestNextState:
    def test_next_state_returns_forward(self) -> None:
        assert next_state(WorkflowState.CREATED) == WorkflowState.ANALYZING_SCRIPT
        assert next_state(WorkflowState.ANALYZING_SCRIPT) == WorkflowState.ANALYZING_AUDIO

    def test_next_state_completed_is_none(self) -> None:
        assert next_state(WorkflowState.COMPLETED) is None

    def test_next_state_failed_is_none(self) -> None:
        assert next_state(WorkflowState.FAILED) is None


class TestTerminal:
    def test_completed_is_terminal(self) -> None:
        assert is_terminal(WorkflowState.COMPLETED)

    def test_failed_is_terminal(self) -> None:
        assert is_terminal(WorkflowState.FAILED)

    def test_cancelled_is_terminal(self) -> None:
        assert is_terminal(WorkflowState.CANCELLED)

    def test_created_is_not_terminal(self) -> None:
        assert not is_terminal(WorkflowState.CREATED)


class TestAllowedTransitions:
    def test_created_can_go_to_analyzing_or_fail_or_cancel(self) -> None:
        allowed = allowed_transitions(WorkflowState.CREATED)
        assert WorkflowState.ANALYZING_SCRIPT in allowed
        assert WorkflowState.FAILED in allowed
        assert WorkflowState.CANCELLED in allowed

    def test_terminal_has_no_transitions(self) -> None:
        for terminal in (WorkflowState.COMPLETED, WorkflowState.FAILED, WorkflowState.CANCELLED):
            assert allowed_transitions(terminal) == []


class TestStateMachine:
    def test_initial_state_is_created(self) -> None:
        sm = WorkflowStateMachine(project_id="proj_1")
        assert sm.state == WorkflowState.CREATED
        assert sm.retries == 0
        assert not sm.is_terminal

    def test_advance_moves_forward(self) -> None:
        sm = WorkflowStateMachine(project_id="proj_1")
        sm.advance()
        assert sm.state == WorkflowState.ANALYZING_SCRIPT

    def test_full_pipeline_to_completed(self) -> None:
        sm = WorkflowStateMachine(project_id="proj_1")
        while not sm.is_terminal:
            nxt = sm.advance()
            if nxt is None:
                break
        assert sm.state == WorkflowState.COMPLETED
        assert sm.is_terminal

    def test_invalid_transition_raises(self) -> None:
        sm = WorkflowStateMachine(project_id="proj_1")
        with pytest.raises(WorkflowError):
            sm.transition(WorkflowState.GENERATING_ASSETS)

    def test_same_state_increments_retries(self) -> None:
        sm = WorkflowStateMachine(project_id="proj_1")
        sm.advance()  # -> ANALYZING_SCRIPT
        sm.transition(WorkflowState.ANALYZING_SCRIPT)  # retry
        sm.transition(WorkflowState.ANALYZING_SCRIPT)  # retry
        assert sm.retries == 2

    def test_forward_transition_resets_retries(self) -> None:
        sm = WorkflowStateMachine(project_id="proj_1")
        sm.advance()
        sm.transition(WorkflowState.ANALYZING_SCRIPT)  # retry, retries=1
        sm.advance()  # -> ANALYZING_AUDIO, retries reset
        assert sm.retries == 0

    def test_fail_transitions_to_failed(self) -> None:
        sm = WorkflowStateMachine(project_id="proj_1")
        sm.fail()
        assert sm.state == WorkflowState.FAILED
        assert sm.is_terminal

    def test_cancel_transitions_to_cancelled(self) -> None:
        sm = WorkflowStateMachine(project_id="proj_1")
        sm.cancel()
        assert sm.state == WorkflowState.CANCELLED
        assert sm.is_terminal

    def test_complete_from_quality_check(self) -> None:
        sm = WorkflowStateMachine(project_id="proj_1")
        # Advance through the pipeline to QUALITY_CHECK.
        while sm.state != WorkflowState.QUALITY_CHECK and not sm.is_terminal:
            sm.advance()
        sm.complete()
        assert sm.state == WorkflowState.COMPLETED

    def test_terminal_cannot_transition(self) -> None:
        sm = WorkflowStateMachine(project_id="proj_1")
        sm.fail()
        with pytest.raises(WorkflowError):
            sm.transition(WorkflowState.CREATED)

    def test_reset_to_created(self) -> None:
        sm = WorkflowStateMachine(project_id="proj_1")
        sm.fail()
        sm.reset()
        assert sm.state == WorkflowState.CREATED
        assert sm.retries == 0

    def test_reset_to_terminal_raises(self) -> None:
        sm = WorkflowStateMachine(project_id="proj_1")
        with pytest.raises(WorkflowError):
            sm.reset(WorkflowState.FAILED)

    def test_history_records_transitions(self) -> None:
        sm = WorkflowStateMachine(project_id="proj_1")
        sm.advance()
        sm.advance()
        assert len(sm.history) == 2
        assert sm.history[0] == (WorkflowState.CREATED, WorkflowState.ANALYZING_SCRIPT)

    def test_to_dict_serializable(self) -> None:
        sm = WorkflowStateMachine(project_id="proj_1")
        sm.advance()
        d = sm.to_dict()
        assert d["project_id"] == "proj_1"
        assert d["current_state"] == "analyzing_script"
        assert d["is_terminal"] is False
