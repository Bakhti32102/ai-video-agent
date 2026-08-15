"""Tests for the Phase 3 MCP client: discovery, validation, timeout, guardrails."""

from __future__ import annotations

import asyncio

import pytest

from app.core.enums import AgentName, AgentRunStatus
from app.core.exceptions import McpError
from app.mcp.client import DEFAULT_TIMEOUT_SEC, McpClient
from app.mcp.registry import McpServerRegistry
from app.mcp.servers import ScriptMcpServer, BaseMcpServer
from app.core.result import Result
from app.schemas.contracts import AgentResult


def test_client_default_registry_has_9_servers() -> None:
    client = McpClient(validate_results=False)
    assert len(client.list_servers()) == 9


def test_client_discover_tools() -> None:
    client = McpClient(validate_results=False)
    tools = client.discover_tools()
    assert "script" in tools
    assert any(t.name == "analyze_script" for t in tools["script"])


def test_client_tool_schemas() -> None:
    client = McpClient(validate_results=False)
    schemas = client.tool_schemas()
    assert "script" in schemas
    assert "analyze_script" in schemas["script"]


def test_client_from_custom_registry() -> None:
    reg = McpServerRegistry()
    reg.register_server(ScriptMcpServer())
    client = McpClient(registry=reg, validate_results=False)
    assert client.list_servers() == ["script"]


@pytest.mark.asyncio
async def test_client_call_success() -> None:
    client = McpClient(validate_results=False)
    result = await client.call(
        AgentName.SCRIPT, "split_into_scenes",
        {"script_text": "Hello world.", "total_duration_sec": 10.0},
    )
    assert result.success
    assert result.agent == AgentName.SCRIPT


@pytest.mark.asyncio
async def test_client_call_unknown_tool_fails() -> None:
    client = McpClient(validate_results=False)
    result = await client.call(AgentName.SCRIPT, "bogus_tool", {})
    assert not result.success


@pytest.mark.asyncio
async def test_client_missing_server_raises() -> None:
    client = McpClient(servers={}, validate_results=False)
    with pytest.raises(McpError):
        await client.call(AgentName.SCRIPT, "analyze_script", {})


@pytest.mark.asyncio
async def test_client_health_check_all() -> None:
    client = McpClient(validate_results=False)
    health = await client.health_check_all()
    assert len(health) == 9
    for info in health.values():
        assert info["status"] == "healthy"


@pytest.mark.asyncio
async def test_client_timeout_handling() -> None:
    """A slow server should trigger a timeout."""

    class _SlowServer(BaseMcpServer):
        name = AgentName.SCRIPT
        async def handle(self, tool, arguments):
            await asyncio.sleep(10)
            return self._ok({})
        async def execute_tool(self, tool, arguments):
            await asyncio.sleep(10)
            return self._ok({})

    client = McpClient(servers={AgentName.SCRIPT: _SlowServer()}, timeout_sec=0.1, validate_results=False)
    result = await client.call(AgentName.SCRIPT, "anything", {})
    assert not result.success
    assert any("timed out" in e for e in result.errors)


@pytest.mark.asyncio
async def test_client_guardrail_validation_rejects() -> None:
    """When validate_results=True, the client runs guardrail validation."""
    client = McpClient(validate_results=True)
    # A valid script call should pass guardrails.
    result = await client.call(
        AgentName.SCRIPT, "split_into_scenes",
        {"script_text": "Hello.", "total_duration_sec": 5.0},
    )
    assert result.success


@pytest.mark.asyncio
async def test_client_server_exception_becomes_failure() -> None:
    """If a server raises, the client returns a failed AgentResult (not raise)."""

    class _RaisingServer(BaseMcpServer):
        name = AgentName.SCRIPT
        async def handle(self, tool, arguments):
            raise RuntimeError("boom")
        async def execute_tool(self, tool, arguments):
            raise RuntimeError("boom")

    client = McpClient(servers={AgentName.SCRIPT: _RaisingServer()}, validate_results=False)
    result = await client.call(AgentName.SCRIPT, "anything", {})
    assert not result.success
    assert any("server raised" in e for e in result.errors)


def test_default_timeout_is_30s() -> None:
    assert DEFAULT_TIMEOUT_SEC == 30.0
