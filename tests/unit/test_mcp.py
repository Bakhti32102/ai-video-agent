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
    # Phase 3 renamed split_scenes -> split_into_scenes; both callable.
    assert "split_into_scenes" in tools["script"]
    assert "create_qa_report" in tools["qa"]


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
    # Phase 3: split_scenes delegates to split_into_scenes, returning a dict
    # with a "scenes" key rather than a bare list.
    scenes = result.output["scenes"] if isinstance(result.output, dict) else result.output
    assert len(scenes) == 2
    assert scenes[0]["start_time"] == 0.0
    assert scenes[1]["end_time"] == 20.0


@pytest.mark.asyncio
async def test_script_server_rejects_empty_script(mcp_client) -> None:
    result = await mcp_client.call(
        AgentName.SCRIPT, "split_scenes", {"script_text": "", "total_duration_sec": 10.0}
    )
    assert not result.success


@pytest.mark.asyncio
async def test_geo_server_refuses_to_invent_coordinates(mcp_client) -> None:
    """Critical guardrail: with no provider configured, geocoding returns
    unresolved (never fabricates coordinates)."""
    result = await mcp_client.call(AgentName.GEO, "geocode", {"query": "Paris"})
    assert result.success  # the call itself succeeds
    out = result.output
    # But the location is explicitly unresolved — no fabricated coordinates.
    assert out["status"] == "unresolved"
    assert out["latitude"] is None
    assert out["longitude"] is None


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
    # Phase 3: render_video requires a job_id; calling without one fails validation.
    result = await mcp_client.call(AgentName.RENDER, "render_video", {"job_id": "nonexistent_job"})
    assert not result.success


@pytest.mark.asyncio
async def test_qa_server_runs_structural_checks(mcp_client) -> None:
    result = await mcp_client.call(
        AgentName.QA,
        "run_qa",
        {
            "project_id": "p1",
            "scenes": [
                {"id": "s1", "project_id": "p1", "index": 0, "title": "A", "start_time": 0.0, "end_time": 5.0, "narration": "narration A"},
                {"id": "s2", "project_id": "p1", "index": 1, "title": "B", "start_time": 5.0, "end_time": 10.0, "narration": "narration B"},
            ],
            "timeline_events": [],
            "audio_duration_sec": 10.0,
            "video_duration_sec": 10.0,
            "render_output_path": "output/test.mp4",
        },
    )
    assert result.success
    # Phase 3: run_qa delegates to create_qa_report, returning {report, passed, ...}
    passed = result.output.get("passed", result.output.get("report", {}).get("passed"))
    assert passed is True


@pytest.mark.asyncio
async def test_qa_server_detects_audio_video_mismatch(mcp_client) -> None:
    result = await mcp_client.call(
        AgentName.QA,
        "run_qa",
        {
            "project_id": "p1",
            "scenes": [
                {"id": "s1", "project_id": "p1", "index": 0, "title": "A", "start_time": 0.0, "end_time": 10.0, "narration": "narration"},
            ],
            "timeline_events": [],
            "audio_duration_sec": 10.0,
            "video_duration_sec": 12.0,
            "render_output_path": "output/test.mp4",
        },
    )
    assert result.success
    findings = result.output.get("findings", result.output.get("report", {}).get("findings", []))
    passed = result.output.get("passed", result.output.get("report", {}).get("passed"))
    assert passed is False
    cats = [f["category"] for f in findings]
    assert "audio_video_mismatch" in cats


@pytest.mark.asyncio
async def test_unknown_tool_fails(mcp_client) -> None:
    result = await mcp_client.call(AgentName.SCRIPT, "bogus", {})
    assert not result.success
