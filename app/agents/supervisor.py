"""Supervisor agent (Phase 1 stub).

Coordinates all specialized agents, maintains workflow state, validates
outputs, and decides when an agent must retry. The full orchestration loop is
a Phase 2 deliverable; Phase 1 provides the interface and a minimal,
deterministic single-step runner that is independently testable.
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName, WorkflowPhase
from app.core.logging import get_logger
from app.core.result import Result
from app.agents.base import BaseAgent
from app.guardrails.guardrails import Guardrails
from app.mcp.client import McpClient
from app.schemas.contracts import AgentResult, WorkflowState

logger = get_logger("agent.supervisor")

# Maximum retry attempts per agent before the supervisor gives up.
DEFAULT_MAX_RETRIES = 2


class SupervisorAgent:
    """Top-level coordinator. Not a BaseAgent (it has no upstream caller)."""

    def __init__(
        self,
        client: McpClient,
        guardrails: Guardrails | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self.client = client
        self.guardrails = guardrails or Guardrails()
        self.max_retries = max_retries

    async def run_agent(
        self, name: AgentName, tool: str, arguments: dict[str, Any]
    ) -> AgentResult:
        """Run a single agent tool with bounded retries.

        Retries on failure up to :attr:`max_retries`. Never silently accepts
        an invalid result: a final failure is returned as a failed AgentResult.
        """
        result: AgentResult | None = None
        for attempt in range(1, self.max_retries + 2):
            result = await self.client.call(name, tool, arguments, attempt=attempt)
            if result.success:
                logger.info("agent %s succeeded on attempt %d", name.value, attempt)
                return result
            logger.warning("agent %s failed attempt %d: %s", name.value, attempt, "; ".join(result.errors))
        assert result is not None
        return result

    async def run_step(
        self, state: WorkflowState, name: AgentName, tool: str, arguments: dict[str, Any]
    ) -> Result[WorkflowState]:
        """Run one workflow step and update workflow state.

        TODO(Phase 2): implement the full phase-ordered pipeline (script ->
        audio -> geo -> assets -> text -> transitions -> sound -> render ->
        qa) with cross-agent validation and rollback.
        """
        result = await self.run_agent(name, tool, arguments)
        new_statuses = dict(state.agent_statuses)
        new_statuses[name.value] = result.status
        new_retries = dict(state.retries)
        new_retries[name.value] = result.attempt - 1 if not result.success else 0

        updated = state.model_copy(
            update={
                "agent_statuses": new_statuses,
                "retries": new_retries,
                "previous_phase": state.current_phase,
                "current_phase": _next_phase(state.current_phase) if result.success else state.current_phase,
            }
        )
        if result.success:
            return Result.ok(updated)
        return Result.fail(*result.errors, data=updated)


def _next_phase(current: WorkflowPhase) -> WorkflowPhase:
    """Return the next phase in the canonical pipeline order."""
    order = [
        WorkflowPhase.INIT,
        WorkflowPhase.SCRIPT_UNDERSTANDING,
        WorkflowPhase.AUDIO_ANALYSIS,
        WorkflowPhase.GEO_RESOLUTION,
        WorkflowPhase.ASSET_SELECTION,
        WorkflowPhase.TEXT_GENERATION,
        WorkflowPhase.TRANSITION_SELECTION,
        WorkflowPhase.SOUND_DESIGN,
        WorkflowPhase.RENDERING,
        WorkflowPhase.QA,
        WorkflowPhase.DONE,
    ]
    try:
        idx = order.index(current)
    except ValueError:
        return current
    return order[min(idx + 1, len(order) - 1)]


__all__ = ["SupervisorAgent", "DEFAULT_MAX_RETRIES"]
