"""Unit tests for the MCP architecture."""

from __future__ import annotations

import pytest

from app.core.enums import AgentName


def test_mcp_client_registers_all_default_servers(mcp_client) -> None:
    names = set(mcp_client.list_servers())
    expected = {
        "script", "audio", "geo", "asset", "text",
        "transition", "sound", "qa", "render",
    }
    assert expected.issubset(names)


def test_mcp_client_available_tools(mcp_client) -> None:
    tools = mcp_client.available_tools()
    assert "split_scenes" in tools["script"]
    assert "run_qa" in tools["qa"]


def test_mcp_client_missing_server_raises() -> None:
    from app.core.exceptions import McpError

    from app.mcp.client import McpClient

    client = McpClient(servers={})
    with pytest.raises(McpError):
        client.get_server(AgentName.SCRIPT)


@pytest.mark.asyncio
async def test_script_server_splits_scenes(mcp_client) -> None:
    result = await mcp_client.call(
        AgentName.SCRIPT,
        "split_scenes",
        {"script_text": "Para one.\n\nPara two.", "total_duration_sec": 20.0, "project_id": "p1"},
    )
    assert result.success
    assert len(result.output) == 2
    assert result.output[0]["start_time"] == 0.0
    assert result.output[1]["end_time"] == 20.0


@pytest.mark.asyncio
async def test_script_server_rejects_empty_script(mcp_client) -> None:
    result = await mcp_client.call(
        AgentName.SCRIPT, "split_scenes", {"script_text": "", "total_duration_sec": 10.0}
    )
    assert not result.success


@pytest.mark.asyncio
async def test_geo_server_refuses_to_invent_coordinates(mcp_client) -> None:
    """Critical guardrail: geocoding must fail in Phase 1, never fabricate."""
    result = await mcp_client.call(AgentName.GEO, "geocode", {"query": "Paris"})
    assert not result.success
    assert any("not implemented" in e or "refusing" in e for e in result.errors)


@pytest.mark.asyncio
async def test_geo_server_build_animation_requires_traceable_source(mcp_client) -> None:
    result = await mcp_client.call(
        AgentName.GEO,
        "build_map_animation",
        {"location": {"latitude": 48.85, "longitude": 2.35, "source": "unknown"}},
    )
    assert not result.success


@pytest.mark.asyncio
async def test_geo_server_build_animation_accepts_verified(mcp_client) -> None:
    result = await mcp_client.call(
        AgentName.GEO,
        "build_map_animation",
        {"location": {"latitude": 48.85, "longitude": 2.35, "source": "nominatim"}},
    )
    assert result.success


@pytest.mark.asyncio
async def test_render_server_refuses_in_phase1(mcp_client) -> None:
    result = await mcp_client.call(AgentName.RENDER, "render_video", {"project_id": "p1"})
    assert not result.success


@pytest.mark.asyncio
async def test_qa_server_runs_structural_checks(mcp_client) -> None:
    result = await mcp_client.call(
        AgentName.QA,
        "run_qa",
        {
            "project_id": "p1",
            "scenes": [
                {"id": "s1", "project_id": "p1", "index": 0, "title": "A", "start_time": 0.0, "end_time": 5.0},
                {"id": "s2", "project_id": "p1", "index": 1, "title": "B", "start_time": 5.0, "end_time": 10.0},
            ],
            "timeline_events": [],
            "audio_duration_sec": 10.0,
            "video_duration_sec": 10.0,
        },
    )
    assert result.success
    assert result.output["passed"] is True


@pytest.mark.asyncio
async def test_qa_server_detects_audio_video_mismatch(mcp_client) -> None:
    result = await mcp_client.call(
        AgentName.QA,
        "run_qa",
        {
            "project_id": "p1",
            "scenes": [
                {"id": "s1", "project_id": "p1", "index": 0, "title": "A", "start_time": 0.0, "end_time": 10.0},
            ],
            "timeline_events": [],
            "audio_duration_sec": 10.0,
            "video_duration_sec": 12.0,
        },
    )
    assert result.success
    assert result.output["passed"] is False
    cats = [f["category"] for f in result.output["findings"]]
    assert "audio_video_mismatch" in cats


@pytest.mark.asyncio
async def test_unknown_tool_fails(mcp_client) -> None:
    result = await mcp_client.call(AgentName.SCRIPT, "bogus", {})
    assert not result.success
