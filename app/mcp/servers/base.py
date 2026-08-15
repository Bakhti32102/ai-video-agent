"""Abstract base class for all MCP servers in the AI Video Agent pipeline.

Each specialized server (Script, Audio, Geo, ...) implements this interface.
The implementation is intentionally transport-agnostic in Phase 1: servers are
in-process objects so they can be unit-tested independently. A real MCP
transport (stdio/SSE) will be layered on top in Phase 2 without changing the
contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.core.enums import AgentName
from app.core.logging import get_logger
from app.core.result import Result
from app.schemas.contracts import AgentResult


class BaseMcpServer(ABC):
    """Common interface every MCP server implements.

    Subclasses must set :attr:`name` and implement :meth:`handle`.
    """

    name: AgentName = AgentName.SUPERVISOR  # overridden by subclasses

    def __init__(self) -> None:
        self.logger = get_logger(f"mcp.{self.name.value}")

    @property
    def server_id(self) -> str:
        return self.name.value

    @abstractmethod
    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        """Dispatch a named tool call to this server.

        Args:
            tool: The tool/capability name to invoke.
            arguments: Structured arguments for the tool.

        Returns:
            A :class:`Result` whose data is tool-specific.
        """

    def list_tools(self) -> list[str]:
        """Return the tool names this server exposes (default: none declared)."""
        return []

    def _ok(self, data: Any, *, warnings: list[str] | None = None) -> Result[Any]:
        return Result.ok(data, warnings=warnings)

    def _fail(self, *errors: str) -> Result[Any]:
        return Result.fail(*errors)

    def _to_agent_result(self, result: Result[Any], attempt: int = 1) -> AgentResult:
        """Convert an internal Result into a serializable AgentResult."""
        data = result.data
        output: dict | list | None
        if isinstance(data, (dict, list)):
            output = data
        elif data is None:
            output = None
        else:
            output = {"value": data}
        return AgentResult(
            agent=self.name,
            status="success" if result.success else "failed",
            success=result.success,
            output=output,
            errors=result.errors,
            warnings=result.warnings,
            attempt=attempt,
        )


__all__ = ["BaseMcpServer"]
