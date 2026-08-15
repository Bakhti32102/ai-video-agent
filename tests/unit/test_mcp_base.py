"""Tests for the Phase 3 MCP base architecture: ToolDefinition, health_check,
execute_tool, input/output schema validation."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from app.mcp.servers.base import BaseMcpServer, ToolDefinition
from app.core.result import Result
from app.core.enums import AgentName


class _DummyInput(BaseModel):
    text: str
    count: int = 1


class _DummyOutput(BaseModel):
    echo: str
    doubled: int


class _DummyServer(BaseMcpServer):
    name = AgentName.SCRIPT
    version = "9.9.9"
    description = "dummy server for testing"

    def __init__(self) -> None:
        super().__init__()
        self._register_tool(ToolDefinition(
            name="echo",
            description="Echo text and double count.",
            input_schema=_DummyInput,
            output_schema=_DummyOutput,
            handler=self._echo,
        ))

    async def handle(self, tool: str, arguments: dict) -> Result:
        return self._fail(f"legacy handle not used for '{tool}'")

    async def _echo(self, inp: _DummyInput) -> Result[_DummyOutput]:
        return Result.ok(_DummyOutput(echo=inp.text, doubled=inp.count * 2))


@pytest.mark.asyncio
async def test_server_name_version_description() -> None:
    s = _DummyServer()
    assert s.server_name == "script"
    assert s.version == "9.9.9"
    assert s.description == "dummy server for testing"


@pytest.mark.asyncio
async def test_health_check_returns_status() -> None:
    s = _DummyServer()
    health = await s.health_check()
    assert health["status"] == "healthy"
    assert health["server"] == "script"
    assert health["version"] == "9.9.9"
    assert health["tools"] == 1


@pytest.mark.asyncio
async def test_list_tools_and_definitions() -> None:
    s = _DummyServer()
    assert s.list_tools() == ["echo"]
    defs = s.list_tool_definitions()
    assert len(defs) == 1
    assert defs[0].name == "echo"
    assert defs[0].description == "Echo text and double count."


@pytest.mark.asyncio
async def test_execute_tool_validates_input_and_output() -> None:
    s = _DummyServer()
    result = await s.execute_tool("echo", {"text": "hello", "count": 5})
    assert result.success
    assert result.data["echo"] == "hello"
    assert result.data["doubled"] == 10


@pytest.mark.asyncio
async def test_execute_tool_rejects_invalid_input() -> None:
    s = _DummyServer()
    result = await s.execute_tool("echo", {"text": "hello", "count": "not-a-number"})
    assert not result.success
    assert any("input validation failed" in e for e in result.errors)


@pytest.mark.asyncio
async def test_execute_tool_rejects_missing_input() -> None:
    s = _DummyServer()
    result = await s.execute_tool("echo", {"count": 5})
    assert not result.success
    assert any("input validation failed" in e for e in result.errors)


@pytest.mark.asyncio
async def test_execute_tool_unknown_tool_falls_back_to_handle() -> None:
    s = _DummyServer()
    result = await s.execute_tool("bogus", {})
    assert not result.success
    assert any("legacy handle not used" in e for e in result.errors)


@pytest.mark.asyncio
async def test_tool_schemas_returns_json_schema() -> None:
    s = _DummyServer()
    schemas = s.tool_schemas()
    assert "echo" in schemas
    assert schemas["echo"]["name"] == "echo"
    assert "input_schema" in schemas["echo"]
    assert "output_schema" in schemas["echo"]


@pytest.mark.asyncio
async def test_duplicate_tool_name_rejected() -> None:
    s = _DummyServer()
    with pytest.raises(ValueError, match="duplicate tool name"):
        s._register_tool(ToolDefinition(
            name="echo", description="dup", input_schema=_DummyInput, handler=None,
        ))


@pytest.mark.asyncio
async def test_handler_returning_result_unwraps() -> None:
    """If a handler returns a Result, execute_tool unwraps it correctly."""

    class _FailServer(BaseMcpServer):
        name = AgentName.AUDIO
        def __init__(self):
            super().__init__()
            self._register_tool(ToolDefinition(
                name="fail_tool", description="always fails",
                input_schema=_DummyInput, output_schema=None, handler=self._fail_tool,
            ))
        async def handle(self, tool, arguments):
            return self._fail("nope")
        async def _fail_tool(self, inp):
            return Result.fail("intentional failure")

    s = _FailServer()
    result = await s.execute_tool("fail_tool", {"text": "x"})
    assert not result.success
    assert "intentional failure" in result.errors
