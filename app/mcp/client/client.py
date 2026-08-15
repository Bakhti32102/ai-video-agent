"""MCP client / orchestrator.

In Phase 1 the "client" is an in-process registry that routes tool calls to
the appropriate specialized server and collects structured AgentResults. The
routing contract mirrors a real MCP client so that swapping in a networked
transport in Phase 2 requires no changes to callers.
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName
from app.core.exceptions import McpError
from app.core.logging import get_logger
from app.core.result import Result
from app.mcp.servers import (
    AssetMcpServer,
    AudioMcpServer,
    BaseMcpServer,
    GeoMcpServer,
    QaMcpServer,
    RenderMcpServer,
    ScriptMcpServer,
    SoundMcpServer,
    TextMcpServer,
    TransitionMcpServer,
)
from app.schemas.contracts import AgentResult

logger = get_logger("mcp.client")


class McpClient:
    """Routes tool calls to registered MCP servers and returns AgentResults."""

    def __init__(self, servers: dict[AgentName, BaseMcpServer] | None = None) -> None:
        self._servers: dict[AgentName, BaseMcpServer] = servers if servers is not None else self._default_servers()

    @staticmethod
    def _default_servers() -> dict[AgentName, BaseMcpServer]:
        return {
            AgentName.SCRIPT: ScriptMcpServer(),
            AgentName.AUDIO: AudioMcpServer(),
            AgentName.GEO: GeoMcpServer(),
            AgentName.ASSET: AssetMcpServer(),
            AgentName.TEXT: TextMcpServer(),
            AgentName.TRANSITION: TransitionMcpServer(),
            AgentName.SOUND: SoundMcpServer(),
            AgentName.QA: QaMcpServer(),
            AgentName.RENDER: RenderMcpServer(),
        }

    def register(self, name: AgentName, server: BaseMcpServer) -> None:
        self._servers[name] = server
        logger.debug("registered MCP server: %s", name.value)

    def get_server(self, name: AgentName) -> BaseMcpServer:
        try:
            return self._servers[name]
        except KeyError as exc:
            raise McpError(f"no MCP server registered for agent '{name.value}'") from exc

    def list_servers(self) -> list[str]:
        return [n.value for n in self._servers]

    def available_tools(self) -> dict[str, list[str]]:
        return {name.value: server.list_tools() for name, server in self._servers.items()}

    async def call(self, name: AgentName, tool: str, arguments: dict[str, Any], *, attempt: int = 1) -> AgentResult:
        """Invoke a tool on a server and return a serializable AgentResult.

        Never raises for expected tool failures; those become failed AgentResults.
        Only transport-level problems (missing server) raise :class:`McpError`.
        """
        server = self.get_server(name)
        logger.info("MCP call: %s.%s (attempt %d)", name.value, tool, attempt)
        try:
            result: Result[Any] = await server.handle(tool, arguments)
        except Exception as exc:  # noqa: BLE001 - surface as structured failure
            logger.exception("MCP server %s raised unexpectedly", name.value)
            return AgentResult(
                agent=name,
                status="failed",
                success=False,
                errors=[f"server raised: {exc}"],
                attempt=attempt,
            )
        return server._to_agent_result(result, attempt=attempt)


__all__ = ["McpClient"]
