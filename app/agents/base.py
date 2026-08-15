"""Abstract base class for all specialized agents.

An agent wraps an MCP server, adds guardrail validation around its outputs,
and returns structured :class:`AgentResult` objects. The supervisor
coordinates agents; agents never talk to each other directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.core.enums import AgentName
from app.core.logging import get_logger
from app.core.result import Result
from app.guardrails.guardrails import Guardrails
from app.mcp.client import McpClient
from app.schemas.contracts import AgentResult


class BaseAgent(ABC):
    """Common agent lifecycle and guardrail integration."""

    name: AgentName = AgentName.SUPERVISOR

    def __init__(self, client: McpClient, guardrails: Guardrails | None = None) -> None:
        self.client = client
        self.guardrails = guardrails or Guardrails()
        self.logger = get_logger(f"agent.{self.name.value}")

    async def run(self, tool: str, arguments: dict[str, Any], *, attempt: int = 1) -> AgentResult:
        """Execute a tool via the MCP client and validate the result."""
        self.logger.info("agent %s running tool %s", self.name.value, tool)
        result = await self.client.call(self.name, tool, arguments, attempt=attempt)
        return await self.validate(result)

    @abstractmethod
    async def validate(self, result: AgentResult) -> AgentResult:
        """Apply agent-specific guardrails to a raw AgentResult.

        Implementations may downgrade, augment, or fail an otherwise-successful
        result. They must never silently fabricate missing data.
        """

    def _fail(self, result: AgentResult, *errors: str) -> AgentResult:
        return AgentResult(
            agent=result.agent,
            status="failed",
            success=False,
            errors=list(errors),
            warnings=result.warnings,
            attempt=result.attempt,
            started_at=result.started_at,
        )


__all__ = ["BaseAgent"]
