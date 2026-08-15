"""Tests for the MCP server registry."""

from __future__ import annotations

import pytest

from app.core.exceptions import McpError
from app.core.enums import AgentName
from app.mcp.registry import CANONICAL_SERVERS, McpServerRegistry, default_registry
from app.mcp.servers import ScriptMcpServer, AudioMcpServer


def test_default_registry_registers_all_9_servers() -> None:
    reg = default_registry()
    assert len(reg) == 9
    names = reg.list_servers()
    expected = [n.value for n in CANONICAL_SERVERS]
    assert names == expected


def test_registry_registers_in_canonical_order() -> None:
    reg = default_registry()
    names = reg.list_servers()
    assert names[0] == "script"
    assert names[-1] == "qa"


def test_register_and_get_server() -> None:
    reg = McpServerRegistry()
    server = ScriptMcpServer()
    reg.register_server(server)
    assert reg.has_server("script")
    assert reg.get_server("script") is server


def test_register_duplicate_raises() -> None:
    reg = McpServerRegistry()
    reg.register_server(ScriptMcpServer())
    with pytest.raises(McpError, match="already registered"):
        reg.register_server(ScriptMcpServer())


def test_unregister_server() -> None:
    reg = McpServerRegistry()
    reg.register_server(ScriptMcpServer())
    reg.unregister_server("script")
    assert not reg.has_server("script")


def test_unregister_unknown_raises() -> None:
    reg = McpServerRegistry()
    with pytest.raises(McpError, match="not found"):
        reg.unregister_server("bogus")


def test_get_server_unknown_raises() -> None:
    reg = McpServerRegistry()
    with pytest.raises(McpError):
        reg.get_server("bogus")


def test_get_server_accepts_enum() -> None:
    reg = McpServerRegistry()
    reg.register_server(ScriptMcpServer())
    assert reg.get_server(AgentName.SCRIPT) is not None


@pytest.mark.asyncio
async def test_health_check_all() -> None:
    reg = McpServerRegistry()
    reg.register_server(ScriptMcpServer())
    reg.register_server(AudioMcpServer())
    health = await reg.health_check_all()
    assert "script" in health
    assert health["script"]["status"] == "healthy"
    assert "audio" in health
    assert health["audio"]["status"] == "healthy"


def test_discover_tools() -> None:
    reg = default_registry()
    tools = reg.discover_tools()
    assert "script" in tools
    script_tools = [t.name for t in tools["script"]]
    assert "analyze_script" in script_tools
    assert "split_into_scenes" in script_tools


def test_discover_tool_names() -> None:
    reg = default_registry()
    names = reg.discover_tool_names()
    assert "qa" in names
    assert "create_qa_report" in names["qa"]


def test_tool_schemas() -> None:
    reg = default_registry()
    schemas = reg.tool_schemas()
    assert "script" in schemas
    assert "analyze_script" in schemas["script"]
    assert "input_schema" in schemas["script"]["analyze_script"]


def test_canonical_servers_count() -> None:
    assert len(CANONICAL_SERVERS) == 9
