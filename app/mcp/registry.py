"""MCP server registry.

Central registry for the 9 specialized MCP servers. Supports dynamic
registration, discovery, health-checking, and tool discovery.

The registry is the authoritative source of which servers are available. The
:class:`~app.mcp.client.McpClient` delegates to it for server lookup.
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName
from app.core.exceptions import McpError
from app.core.logging import get_logger, log_event
from app.mcp.servers import BaseMcpServer, ToolDefinition
from app.mcp.servers.assets.server import AssetMcpServer
from app.mcp.servers.audio.server import AudioMcpServer
from app.mcp.servers.geo.server import GeoMcpServer
from app.mcp.servers.qa.server import QaMcpServer
from app.mcp.servers.render.server import RenderMcpServer
from app.mcp.servers.script.server import ScriptMcpServer
from app.mcp.servers.sound.server import SoundMcpServer
from app.mcp.servers.text.server import TextMcpServer
from app.mcp.servers.transitions.server import TransitionMcpServer

logger = get_logger("mcp.registry")

# The canonical 9 server names in pipeline order.
CANONICAL_SERVERS: tuple[AgentName, ...] = (
    AgentName.SCRIPT,
    AgentName.AUDIO,
    AgentName.GEO,
    AgentName.ASSET,
    AgentName.TEXT,
    AgentName.TRANSITION,
    AgentName.SOUND,
    AgentName.RENDER,
    AgentName.QA,
)


class McpServerRegistry:
    """Registry of available MCP servers.

    Provides:
    - ``register_server`` / ``unregister_server``
    - ``get_server`` (raises if not found)
    - ``list_servers`` (names)
    - ``health_check_all`` (aggregated health)
    - ``discover_tools`` (all tools across all servers)
    """

    def __init__(self) -> None:
        self._servers: dict[str, BaseMcpServer] = {}

    def register_server(self, server: BaseMcpServer) -> None:
        """Register an MCP server instance under its name."""
        name = server.server_name
        if name in self._servers:
            raise McpError(f"server '{name}' is already registered")
        self._servers[name] = server
        log_event(logger, "registry.registered", server=name, tools=server.list_tools())

    def unregister_server(self, name: str | AgentName) -> None:
        """Remove a server from the registry."""
        key = name.value if isinstance(name, AgentName) else name
        if key not in self._servers:
            raise McpError(f"cannot unregister: server '{key}' not found")
        del self._servers[key]
        log_event(logger, "registry.unregistered", server=key)

    def get_server(self, name: str | AgentName) -> BaseMcpServer:
        """Return the server for ``name``; raises McpError if not found."""
        key = name.value if isinstance(name, AgentName) else name
        server = self._servers.get(key)
        if server is None:
            raise McpError(f"no MCP server registered for '{key}'")
        return server

    def list_servers(self) -> list[str]:
        """Return the names of all registered servers (sorted by canonical order)."""
        registered = set(self._servers.keys())
        # Return in canonical pipeline order, then any extras alphabetically.
        ordered = [n.value for n in CANONICAL_SERVERS if n.value in registered]
        extras = sorted(registered - set(ordered))
        return ordered + extras

    def has_server(self, name: str | AgentName) -> bool:
        key = name.value if isinstance(name, AgentName) else name
        return key in self._servers

    async def health_check_all(self) -> dict[str, dict[str, Any]]:
        """Run health_check on every registered server."""
        results: dict[str, dict[str, Any]] = {}
        for name, server in self._servers.items():
            try:
                results[name] = await server.health_check()
            except Exception as exc:  # noqa: BLE001
                results[name] = {
                    "server": name,
                    "status": "unhealthy",
                    "error": str(exc),
                }
        return results

    def discover_tools(self) -> dict[str, list[ToolDefinition]]:
        """Return tool definitions for every registered server."""
        return {name: server.list_tool_definitions() for name, server in self._servers.items()}

    def discover_tool_names(self) -> dict[str, list[str]]:
        """Return tool names for every registered server."""
        return {name: server.list_tools() for name, server in self._servers.items()}

    def tool_schemas(self) -> dict[str, dict[str, Any]]:
        """Return JSON schemas for all tools across all servers."""
        return {name: server.tool_schemas() for name, server in self._servers.items()}

    def __len__(self) -> int:
        return len(self._servers)


def default_registry() -> McpServerRegistry:
    """Create a registry pre-loaded with all 9 canonical servers."""
    registry = McpServerRegistry()
    for server_cls in (
        ScriptMcpServer,
        AudioMcpServer,
        GeoMcpServer,
        AssetMcpServer,
        TextMcpServer,
        TransitionMcpServer,
        SoundMcpServer,
        RenderMcpServer,
        QaMcpServer,
    ):
        registry.register_server(server_cls())
    return registry


__all__ = ["CANONICAL_SERVERS", "McpServerRegistry", "default_registry"]
