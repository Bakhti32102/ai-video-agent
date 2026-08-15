"""Supervisor agent.

Coordinates all specialized agents, maintains workflow state, validates
outputs through the centralized guardrail pipeline, and decides when an agent
must retry.

Phase 2 enhancements:
- Uses :class:`app.core.workflow.WorkflowStateMachine` for state transitions.
- Runs :func:`app.guardrails.pipeline.validate_before_accept` on every
  agent result *before* accepting it into the workflow. Results that fail
  guardrails are treated as failures and trigger a retry.
- Emits structured event logs for observability.
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName, WorkflowPhase, WorkflowState as WorkflowStateEnum
from app.core.logging import get_logger, log_event
from app.core.result import Result
from app.core.workflow import next_state as _next_workflow_state
from app.agents.base import BaseAgent
from app.guardrails.guardrails import Guardrails
from app.guardrails.pipeline import GuardrailPipeline, validate_before_accept
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
        pipeline: GuardrailPipeline | None = None,
    ) -> None:
        self.client = client
        self.guardrails = guardrails or Guardrails()
        self.max_retries = max_retries
        self.pipeline = pipeline or GuardrailPipeline(guardrails=self.guardrails)

    async def run_agent(
        self, name: AgentName, tool: str, arguments: dict[str, Any]
    ) -> AgentResult:
        """Run a single agent tool with bounded retries.

        Retries on failure up to :attr:`max_retries`. Never silently accepts
        an invalid result: every successful result is validated through the
        guardrail pipeline before being accepted. A final failure is returned
        as a failed AgentResult.
        """
        result: AgentResult | None = None
        for attempt in range(1, self.max_retries + 2):
            result = await self.client.call(name, tool, arguments, attempt=attempt)
            if result.success:
                # Validate through guardrail pipeline before accepting.
                validation = validate_before_accept(result, self.pipeline)
                if validation.is_failure:
                    log_event(
                        logger,
                        "agent.guardrail_rejected",
                        level=30,  # WARNING
                        agent=name.value,
                        attempt=attempt,
                        errors="; ".join(validation.errors),
                    )
                    # Convert to a failed result so it triggers a retry.
                    result = AgentResult(
                        agent=name,
                        status="failed",
                        success=False,
                        errors=[f"guardrail validation failed: {e}" for e in validation.errors],
                        attempt=attempt,
                        project_id=result.project_id,
                        scene_id=result.scene_id,
                        output=result.output,
                    )
                    if attempt <= self.max_retries:
                        continue
                    return result
                log_event(
                    logger,
                    "agent.succeeded",
                    agent=name.value,
                    attempt=attempt,
                    run_id=result.run_id,
                )
                return result
            log_event(
                logger,
                "agent.failed",
                level=30,  # WARNING
                agent=name.value,
                attempt=attempt,
                errors="; ".join(result.errors),
            )
        assert result is not None
        return result

    async def run_step(
        self, state: WorkflowState, name: AgentName, tool: str, arguments: dict[str, Any]
    ) -> Result[WorkflowState]:
        """Run one workflow step and update workflow state.

        Uses the :class:`WorkflowStateMachine` to validate state transitions.
        On success, advances the workflow state; on failure, tracks retries
        and may transition to ``FAILED`` if retries are exhausted.
        """
        result = await self.run_agent(name, tool, arguments)
        new_statuses = dict(state.agent_statuses)
        new_statuses[name.value] = result.status
        new_retries = dict(state.retries)
        new_retries[name.value] = result.attempt - 1 if not result.success else 0

        # Determine the next workflow state using the state machine.
        current_enum = state.current_state
        if result.success:
            nxt = _next_workflow_state(current_enum)
            target_enum = nxt if nxt is not None else current_enum
        else:
            # If retries exhausted, transition to FAILED.
            if new_retries[name.value] >= self.max_retries:
                target_enum = WorkflowStateEnum.FAILED
            else:
                target_enum = current_enum

        updated = state.model_copy(
            update={
                "agent_statuses": new_statuses,
                "retries": new_retries,
                "previous_state": state.current_state,
                "previous_phase": state.current_phase,
                "current_state": target_enum,
                "current_phase": _next_phase(state.current_phase) if result.success else state.current_phase,
            }
        )
        if result.success:
            log_event(
                logger,
                "workflow.advanced",
                agent=name.value,
                state=current_enum.value,
                next_state=target_enum.value,
            )
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
