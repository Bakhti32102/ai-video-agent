"""Workflow state machine.

Defines the valid transitions between :class:`WorkflowState` values and
provides a small, deterministic state machine that the Supervisor uses to
track project lifecycle. Only explicitly-allowed transitions are accepted;
every other transition is rejected so the pipeline can never silently skip
or reorder phases.

Terminal states are COMPLETED, FAILED, and CANCELLED — once reached, no
further transitions are accepted (the machine must be explicitly reset).
"""

from __future__ import annotations

from app.core.enums import WorkflowState
from app.core.exceptions import WorkflowError
from app.core.logging import get_logger
from app.core.result import Result

logger = get_logger("workflow")

# Canonical forward pipeline order (excludes terminal states).
_PIPELINE: list[WorkflowState] = [
    WorkflowState.CREATED,
    WorkflowState.ANALYZING_SCRIPT,
    WorkflowState.ANALYZING_AUDIO,
    WorkflowState.BUILDING_SCENES,
    WorkflowState.GENERATING_MAPS,
    WorkflowState.GENERATING_ASSETS,
    WorkflowState.GENERATING_TEXT,
    WorkflowState.GENERATING_TRANSITIONS,
    WorkflowState.GENERATING_SOUND,
    WorkflowState.RENDERING,
    WorkflowState.QUALITY_CHECK,
    WorkflowState.COMPLETED,
]

_TERMINAL: frozenset[WorkflowState] = frozenset(
    {WorkflowState.COMPLETED, WorkflowState.FAILED, WorkflowState.CANCELLED}
)

# Explicitly-allowed transitions. Any non-terminal state may go to FAILED or
# CANCELLED. Otherwise only the next forward state (or itself for retries) is
# accepted.
_TRANSITIONS: dict[WorkflowState, frozenset[WorkflowState]] = {}
for _i, _state in enumerate(_PIPELINE[:-1]):
    _next = _PIPELINE[_i + 1]
    _TRANSITIONS[_state] = frozenset({_next, _state, WorkflowState.FAILED, WorkflowState.CANCELLED})
# QUALITY_CHECK can go to COMPLETED, stay, or fail/cancel.
_TRANSITIONS[WorkflowState.QUALITY_CHECK] = frozenset(
    {WorkflowState.COMPLETED, WorkflowState.QUALITY_CHECK, WorkflowState.FAILED, WorkflowState.CANCELLED}
)
# Terminal states have no outgoing transitions.
for _t in _TERMINAL:
    _TRANSITIONS[_t] = frozenset()


def _pipeline_index(state: WorkflowState) -> int:
    try:
        return _PIPELINE.index(state)
    except ValueError:
        return -1


def is_valid_transition(current: WorkflowState, target: WorkflowState) -> bool:
    """Return True if ``current -> target`` is an allowed transition."""
    return target in _TRANSITIONS.get(current, frozenset())


def can_transition_to(current: WorkflowState, target: WorkflowState) -> Result[WorkflowState]:
    """Validate a transition, returning a structured Result."""
    if current in _TERMINAL:
        return Result.fail(
            f"cannot transition from terminal state '{current.value}' to '{target.value}'"
        )
    if not is_valid_transition(current, target):
        return Result.fail(
            f"invalid workflow transition: '{current.value}' -> '{target.value}'"
        )
    return Result.ok(target)


def next_state(current: WorkflowState) -> WorkflowState | None:
    """Return the next forward pipeline state, or None if terminal/unknown."""
    idx = _pipeline_index(current)
    if idx < 0 or idx >= len(_PIPELINE) - 1:
        return None
    return _PIPELINE[idx + 1]


def is_terminal(state: WorkflowState) -> bool:
    """Return True if ``state`` is a terminal (no further transitions)."""
    return state in _TERMINAL


def allowed_transitions(state: WorkflowState) -> list[WorkflowState]:
    """Return all states reachable from ``state``."""
    return sorted(_TRANSITIONS.get(state, frozenset()), key=lambda s: s.value)


class WorkflowStateMachine:
    """A mutable, single-project workflow state machine.

    Holds the current state, retry count, and a short history of transitions.
    The Supervisor creates one per project and drives it through the pipeline.
    """

    def __init__(self, project_id: str, state: WorkflowState = WorkflowState.CREATED) -> None:
        self.project_id = project_id
        self._state = state
        self.retries: int = 0
        self.history: list[tuple[WorkflowState, WorkflowState]] = []
        logger.info("workflow state machine created for project %s at state %s", project_id, state.value)

    @property
    def state(self) -> WorkflowState:
        return self._state

    @property
    def is_terminal(self) -> bool:
        return is_terminal(self._state)

    def transition(self, target: WorkflowState) -> WorkflowState:
        """Attempt to move to ``target``; raise WorkflowError if invalid."""
        result = can_transition_to(self._state, target)
        if result.is_failure:
            logger.warning(
                "rejected transition %s -> %s for project %s: %s",
                self._state.value,
                target.value,
                self.project_id,
                "; ".join(result.errors),
            )
            raise WorkflowError(
                "; ".join(result.errors),
                details={
                    "project_id": self.project_id,
                    "current": self._state.value,
                    "target": target.value,
                },
            )
        if target == self._state:
            # Same-state transition = a retry within the current phase.
            self.retries += 1
            logger.debug("retry #%d in state %s for project %s", self.retries, self._state.value, self.project_id)
        else:
            self.history.append((self._state, target))
            self.retries = 0
            logger.info(
                "workflow transition %s -> %s for project %s",
                self._state.value,
                target.value,
                self.project_id,
            )
        self._state = target
        return self._state

    def advance(self) -> WorkflowState | None:
        """Move to the next forward state; returns None if already terminal."""
        nxt = next_state(self._state)
        if nxt is None:
            return None
        return self.transition(nxt)

    def fail(self) -> WorkflowState:
        """Transition to the FAILED terminal state."""
        return self.transition(WorkflowState.FAILED)

    def cancel(self) -> WorkflowState:
        """Transition to the CANCELLED terminal state."""
        return self.transition(WorkflowState.CANCELLED)

    def complete(self) -> WorkflowState:
        """Transition to COMPLETED (only valid from QUALITY_CHECK)."""
        return self.transition(WorkflowState.COMPLETED)

    def reset(self, state: WorkflowState = WorkflowState.CREATED) -> None:
        """Reset the machine to a non-terminal state (used for re-runs)."""
        if state in _TERMINAL:
            raise WorkflowError(
                f"cannot reset to terminal state '{state.value}'",
                details={"project_id": self.project_id, "target": state.value},
            )
        self._state = state
        self.retries = 0
        self.history.clear()
        logger.info("workflow state machine reset to %s for project %s", state.value, self.project_id)

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "current_state": self._state.value,
            "retries": self.retries,
            "is_terminal": self.is_terminal,
            "history": [(c.value, t.value) for c, t in self.history],
        }


__all__ = [
    "WorkflowStateMachine",
    "allowed_transitions",
    "can_transition_to",
    "is_terminal",
    "is_valid_transition",
    "next_state",
]
