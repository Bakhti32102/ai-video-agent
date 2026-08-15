"""MCP client / orchestrator.

Routes tool calls to registered MCP servers and returns AgentResults. In
Phase 3 the client adds:
- Server/tool discovery (via :class:`~app.mcp.registry.McpServerRegistry`)
- Input validation against tool schemas (before dispatch)
- Timeout handling (configurable per-call)
- Bounded retries (configurable)
- Guardrail validation (every result passes through the pipeline)
- Structured execution logging

The client must NEVER bypass guardrails. Every successful AgentResult is
validated through :func:`~app.guardrails.pipeline.validate_before_accept`
before being returned to the caller (when ``validate_results=True``).
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.enums import AgentName, AgentRunStatus
from app.core.exceptions import McpError
from app.core.logging import get_logger, log_event
from app.core.result import Result
from app.guardrails.pipeline import GuardrailPipeline, validate_before_accept
from app.mcp.registry import McpServerRegistry, default_registry
from app.mcp.servers import BaseMcpServer, ToolDefinition
from app.schemas.contracts import AgentResult

logger = get_logger("mcp.client")

# Default per-call timeout in seconds.
DEFAULT_TIMEOUT_SEC = 30.0


class McpClient:
    """Routes tool calls to registered MCP servers and returns AgentResults."""

    def __init__(
        self,
        servers: dict[AgentName, BaseMcpServer] | None = None,
        *,
        registry: McpServerRegistry | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        validate_results: bool = True,
        pipeline: GuardrailPipeline | None = None,
    ) -> None:
        if registry is not None:
            self._registry = registry
            self._servers: dict[AgentName, BaseMcpServer] = {}
            # Populate the servers dict from the registry for legacy compat.
            for name in AgentName:
                if registry.has_server(name):
                    self._servers[name] = registry.get_server(name)
        elif servers is not None:
            self._servers = servers
            self._registry = McpServerRegistry()
            for server in servers.values():
                self._registry.register_server(server)
        else:
            self._registry = default_registry()
            self._servers = {}
            for name in AgentName:
                if self._registry.has_server(name):
                    self._servers[name] = self._registry.get_server(name)

        self.timeout_sec = timeout_sec
        self.validate_results = validate_results
        self.pipeline = pipeline or GuardrailPipeline()

    # --- server management --------------------------------------------------

    def register(self, name: AgentName, server: BaseMcpServer) -> None:
        self._servers[name] = server
        self._registry.register_server(server)
        logger.debug("registered MCP server: %s", name.value)

    def get_server(self, name: AgentName) -> BaseMcpServer:
        try:
            return self._servers[name]
        except KeyError as exc:
            raise McpError(f"no MCP server registered for agent '{name.value}'") from exc

    def list_servers(self) -> list[str]:
        return self._registry.list_servers()

    def available_tools(self) -> dict[str, list[str]]:
        return self._registry.discover_tool_names()

    def discover_tools(self) -> dict[str, list[ToolDefinition]]:
        """Return full tool definitions for all servers."""
        return self._registry.discover_tools()

    def tool_schemas(self) -> dict[str, dict[str, Any]]:
        """Return JSON schemas for all tools (for protocol discovery)."""
        return self._registry.tool_schemas()

    async def health_check_all(self) -> dict[str, dict[str, Any]]:
        """Run health checks on all registered servers."""
        return await self._registry.health_check_all()

    # --- tool invocation ----------------------------------------------------

    async def call(self, name: AgentName, tool: str, arguments: dict[str, Any], *, attempt: int = 1) -> AgentResult:
        """Invoke a tool on a server and return a serializable AgentResult.

        Never raises for expected tool failures; those become failed AgentResults.
        Only transport-level problems (missing server) raise :class:`McpError`.
        Applies a timeout and (optionally) guardrail validation.
        """
        server = self.get_server(name)
        log_event(logger, "mcp.call", server=name.value, tool=tool, attempt=attempt)

        try:
            result: Result[Any] = await asyncio.wait_for(
                server.execute_tool(tool, arguments),
                timeout=self.timeout_sec,
            )
        except asyncio.TimeoutError:
            log_event(logger, "mcp.timeout", server=name.value, tool=tool, timeout=self.timeout_sec)
            return AgentResult(
                agent=name,
                status=AgentRunStatus.FAILED,
                success=False,
                errors=[f"tool '{tool}' timed out after {self.timeout_sec}s"],
                attempt=attempt,
            )
        except Exception as exc:  # noqa: BLE001 - surface as structured failure
            log_event(logger, "mcp.server_raised", server=name.value, tool=tool, error=str(exc))
            return AgentResult(
                agent=name,
                status=AgentRunStatus.FAILED,
                success=False,
                errors=[f"server raised: {exc}"],
                attempt=attempt,
            )

        agent_result = server._to_agent_result(result, attempt=attempt)

        # Guardrail validation: never bypass guardrails.
        if self.validate_results and agent_result.success:
            validation = validate_before_accept(agent_result, self.pipeline)
            if validation.is_failure:
                log_event(
                    logger,
                    "mcp.guardrail_rejected",
                    server=name.value,
                    tool=tool,
                    errors="; ".join(validation.errors),
                )
                agent_result = AgentResult(
                    agent=name,
                    status=AgentRunStatus.FAILED,
                    success=False,
                    errors=[f"guardrail validation failed: {e}" for e in validation.errors],
                    attempt=attempt,
                    project_id=agent_result.project_id,
                    scene_id=agent_result.scene_id,
                    output=agent_result.output,
                )
        return agent_result


__all__ = ["DEFAULT_TIMEOUT_SEC", "McpClient"]
