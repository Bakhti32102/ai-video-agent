"""Tests for the 9 MCP servers: tool registration, schemas, validation,
and core behaviour."""

from __future__ import annotations

import pytest

from app.core.enums import AgentName, AssetFormat, AssetType
from app.mcp.servers import (
    AssetMcpServer,
    AudioMcpServer,
    GeoMcpServer,
    QaMcpServer,
    RenderMcpServer,
    ScriptMcpServer,
    SoundMcpServer,
    TextMcpServer,
    TransitionMcpServer,
)
from app.services.geo import GeocodeResult, GeoProvider
from app.schemas.contracts import GeoProvenance


# === Script MCP ===


@pytest.mark.asyncio
async def test_script_analyze_script() -> None:
    server = ScriptMcpServer()
    result = await server.execute_tool("analyze_script", {
        "script_text": "James Gadsden negotiated with Mexico. The agreement transferred land.",
        "total_duration_sec": 20.0,
        "project_id": "p1",
    })
    assert result.success
    assert len(result.data["scenes"]) >= 1
    assert "entities" in result.data
    assert "requirements" in result.data


@pytest.mark.asyncio
async def test_script_split_into_scenes() -> None:
    server = ScriptMcpServer()
    result = await server.execute_tool("split_into_scenes", {
        "script_text": "Para one.\n\nPara two.",
        "total_duration_sec": 10.0,
    })
    assert result.success
    assert len(result.data["scenes"]) == 2
    assert result.data["scenes"][0]["start_time"] == 0.0


@pytest.mark.asyncio
async def test_script_extract_entities_detects_gadsden() -> None:
    server = ScriptMcpServer()
    result = await server.execute_tool("extract_entities", {
        "script_text": "James Gadsden negotiated with Mexico in 1853.",
    })
    assert result.success
    assert "gadsden" in result.data["people"]
    assert "mexico" in result.data["locations"]


@pytest.mark.asyncio
async def test_script_extract_locations_marks_unresolved() -> None:
    server = ScriptMcpServer()
    result = await server.execute_tool("extract_locations", {
        "script_text": "The treaty with Mexico was signed.",
    })
    assert result.success
    locs = result.data["locations"]
    assert any(l["name"] == "mexico" and l["status"] == "unresolved" for l in locs)


@pytest.mark.asyncio
async def test_script_rejects_empty_script() -> None:
    server = ScriptMcpServer()
    result = await server.execute_tool("analyze_script", {"script_text": "", "total_duration_sec": 10.0})
    assert not result.success


# === Audio MCP ===


@pytest.mark.asyncio
async def test_audio_inspect_with_supplied_duration() -> None:
    server = AudioMcpServer()
    result = await server.execute_tool("inspect_audio", {
        "file_path": "/tmp/test.wav", "duration_sec": 15.5,
    })
    assert result.success
    assert result.data["duration_sec"] == 15.5


@pytest.mark.asyncio
async def test_audio_inspect_rejects_traversal() -> None:
    server = AudioMcpServer()
    result = await server.execute_tool("inspect_audio", {
        "file_path": "../../../etc/passwd", "duration_sec": 10.0,
    })
    assert not result.success


@pytest.mark.asyncio
async def test_audio_create_audio_timeline() -> None:
    server = AudioMcpServer()
    result = await server.execute_tool("create_audio_timeline", {
        "duration_sec": 30.0, "scene_count": 3,
    })
    assert result.success
    assert len(result.data["timeline"]) == 3
    assert result.data["total_duration_sec"] == 30.0


@pytest.mark.asyncio
async def test_audio_detect_silence() -> None:
    # Use a stub ffmpeg service so no real ffmpeg call is made.
    from app.services.ffmpeg import StubFFmpegService
    server = AudioMcpServer(ffmpeg_service=StubFFmpegService())
    result = await server.execute_tool("detect_silence", {"file_path": "/tmp/test.wav"})
    assert result.success
    assert "silence_segments" in result.data


@pytest.mark.asyncio
async def test_audio_silence_log_parsing() -> None:
    """Verify the ffmpeg silencedetect log parser works correctly."""
    from app.mcp.servers.audio.server import AudioMcpServer
    log = (
        "[silencedetect @ 0x123] silence_start: 1.2300\n"
        "[silencedetect @ 0x123] silence_end: 2.4500 | silence_duration: 1.2200\n"
    )
    segs = AudioMcpServer._parse_silence_log(log)
    assert len(segs) == 1
    assert segs[0]["start_time"] == 1.23
    assert segs[0]["end_time"] == 2.45
    assert segs[0]["duration_sec"] == 1.22


# === Geo MCP ===


class _MockGeoProvider(GeoProvider):
    name = "mock"
    async def geocode(self, query: str) -> GeocodeResult:
        if query.lower() == "paris":
            return GeocodeResult(
                query=query, status="resolved", latitude=48.85, longitude=2.35,
                display_name="Paris, France", confidence=0.9, provider="mock",
                provenance=GeoProvenance(provider="mock", source="test", query=query, latitude=48.85, longitude=2.35),
            )
        return GeocodeResult(query=query, status="unresolved", provider="mock", error="not found")


@pytest.mark.asyncio
async def test_geo_geocode_location_resolved() -> None:
    server = GeoMcpServer(provider=_MockGeoProvider())
    result = await server.execute_tool("geocode_location", {"query": "Paris"})
    assert result.success
    assert result.data["status"] == "resolved"
    assert result.data["latitude"] == 48.85
    assert result.data["provenance"] is not None
    assert result.data["provenance"]["provider"] == "mock"


@pytest.mark.asyncio
async def test_geo_geocode_location_unresolved() -> None:
    server = GeoMcpServer(provider=_MockGeoProvider())
    result = await server.execute_tool("geocode_location", {"query": "Nowhere"})
    assert result.success
    assert result.data["status"] == "unresolved"
    assert result.data["latitude"] is None


@pytest.mark.asyncio
async def test_geo_batch_geocode() -> None:
    server = GeoMcpServer(provider=_MockGeoProvider())
    result = await server.execute_tool("batch_geocode", {"queries": ["Paris", "Nowhere"]})
    assert result.success
    assert result.data["resolved"] == 1
    assert result.data["unresolved"] == 1


@pytest.mark.asyncio
async def test_geo_validate_coordinates() -> None:
    server = GeoMcpServer(provider=_MockGeoProvider())
    result = await server.execute_tool("validate_coordinates", {"latitude": 48.85, "longitude": 2.35})
    assert result.success
    assert result.data["valid"] is True


@pytest.mark.asyncio
async def test_geo_reverse_geocode() -> None:
    server = GeoMcpServer(provider=_MockGeoProvider())
    result = await server.execute_tool("reverse_geocode", {"latitude": 48.85, "longitude": 2.35})
    assert result.success


# === Assets MCP ===


@pytest.mark.asyncio
async def test_assets_register_and_get() -> None:
    server = AssetMcpServer()
    reg = await server.execute_tool("register_asset", {
        "name": "test_icon", "asset_type": "icon", "format": "svg",
        "file_path": "icons/test.svg", "source": "test",
    })
    assert reg.success
    asset_id = reg.data["asset_id"]
    get = await server.execute_tool("get_asset", {"asset_id": asset_id})
    assert get.success
    assert get.data["found"] is True
    assert get.data["asset"]["name"] == "test_icon"


@pytest.mark.asyncio
async def test_assets_list() -> None:
    server = AssetMcpServer()
    await server.execute_tool("register_asset", {
        "name": "a1", "asset_type": "icon", "format": "svg",
        "file_path": "a.svg", "source": "s",
    })
    result = await server.execute_tool("list_assets", {})
    assert result.success
    assert result.data["count"] == 1


@pytest.mark.asyncio
async def test_assets_validate_asset() -> None:
    server = AssetMcpServer()
    result = await server.execute_tool("validate_asset", {"file_path": "icon.png"})
    assert result.success
    assert result.data["valid"] is True


@pytest.mark.asyncio
async def test_assets_validate_rejects_unsupported() -> None:
    server = AssetMcpServer()
    result = await server.execute_tool("validate_asset", {"file_path": "icon.xyz"})
    assert result.success
    assert result.data["valid"] is False


@pytest.mark.asyncio
async def test_assets_find() -> None:
    server = AssetMcpServer()
    await server.execute_tool("register_asset", {
        "name": "currency_icon", "asset_type": "icon", "format": "svg",
        "file_path": "currency.svg", "source": "s",
    })
    result = await server.execute_tool("find_asset", {"query": "currency"})
    assert result.success
    assert result.data["count"] == 1


# === Text MCP ===


@pytest.mark.asyncio
async def test_text_create_overlay() -> None:
    server = TextMcpServer()
    result = await server.execute_tool("create_text_overlay", {
        "scene_id": "s1", "kind": "title", "text": "Hello",
        "start_time": 0.0, "end_time": 5.0,
    })
    assert result.success
    assert result.data["overlay"]["text"] == "Hello"
    assert result.data["overlay"]["kind"] == "title"


@pytest.mark.asyncio
async def test_text_rejects_invalid_kind() -> None:
    server = TextMcpServer()
    result = await server.execute_tool("create_text_overlay", {
        "scene_id": "s1", "kind": "bogus", "text": "x",
        "start_time": 0.0, "end_time": 5.0,
    })
    assert not result.success


@pytest.mark.asyncio
async def test_text_rejects_invalid_animation() -> None:
    server = TextMcpServer()
    result = await server.execute_tool("create_text_overlay", {
        "scene_id": "s1", "kind": "title", "text": "x",
        "start_time": 0.0, "end_time": 5.0, "animation": "explode",
    })
    assert not result.success


@pytest.mark.asyncio
async def test_text_normalizes_color() -> None:
    server = TextMcpServer()
    result = await server.execute_tool("create_text_overlay", {
        "scene_id": "s1", "kind": "title", "text": "x",
        "start_time": 0.0, "end_time": 5.0, "color": "FF0000",
    })
    assert result.success
    assert result.data["overlay"]["color"] == "#FF0000"


# === Transitions MCP ===


@pytest.mark.asyncio
async def test_transitions_create() -> None:
    server = TransitionMcpServer()
    result = await server.execute_tool("create_transition", {
        "from_scene_id": "s1", "to_scene_id": "s2",
        "kind": "fade", "duration_sec": 0.5, "start_time": 5.0,
    })
    assert result.success
    assert result.data["transition"]["kind"] == "fade"


@pytest.mark.asyncio
async def test_transitions_rejects_invalid_kind() -> None:
    server = TransitionMcpServer()
    result = await server.execute_tool("create_transition", {
        "from_scene_id": "s1", "to_scene_id": "s2", "kind": "explode",
    })
    assert not result.success


@pytest.mark.asyncio
async def test_transitions_map_zoom_requires_from_scene() -> None:
    server = TransitionMcpServer()
    result = await server.execute_tool("create_transition", {
        "to_scene_id": "s2", "kind": "map_zoom",
    })
    assert not result.success


# === Sound MCP ===


@pytest.mark.asyncio
async def test_sound_create_event() -> None:
    server = SoundMcpServer()
    result = await server.execute_tool("create_sound_event", {
        "asset_id": "asset_1", "kind": "ambience",
        "start_time": 0.0, "duration_sec": 10.0,
    })
    assert result.success
    assert result.data["sound_event"]["kind"] == "ambience"


@pytest.mark.asyncio
async def test_sound_create_design_plan() -> None:
    server = SoundMcpServer()
    result = await server.execute_tool("create_sound_design_plan", {
        "scenes": [{"id": "s1", "start_time": 0, "end_time": 5}],
        "total_duration_sec": 10.0,
    })
    assert result.success
    assert len(result.data["events"]) == 1


@pytest.mark.asyncio
async def test_sound_validate_event() -> None:
    server = SoundMcpServer()
    await server.execute_tool("create_sound_event", {
        "asset_id": "asset_1", "kind": "ambience",
        "start_time": 0.0, "duration_sec": 5.0,
    })
    result = await server.execute_tool("validate_sound_event", {
        "asset_id": "asset_1", "start_time": 0.0, "duration_sec": 5.0,
    })
    assert result.success
    assert result.data["valid"] is True


# === Render MCP ===


@pytest.mark.asyncio
async def test_render_create_job() -> None:
    server = RenderMcpServer()
    result = await server.execute_tool("create_render_job", {
        "project_id": "p1", "output_filename": "out",
    })
    assert result.success
    assert result.data["status"] == "queued"
    assert result.data["output_path"].endswith(".mp4")


@pytest.mark.asyncio
async def test_render_validate_job() -> None:
    server = RenderMcpServer()
    result = await server.execute_tool("validate_render_job", {
        "project_id": "p1", "output_path": "safe.mp4",
    })
    assert result.success
    assert result.data["valid"] is True


@pytest.mark.asyncio
async def test_render_validate_rejects_traversal() -> None:
    server = RenderMcpServer()
    result = await server.execute_tool("validate_render_job", {
        "project_id": "p1", "output_path": "../../../etc/passwd",
    })
    assert result.success
    assert result.data["valid"] is False


@pytest.mark.asyncio
async def test_render_get_status_unknown() -> None:
    server = RenderMcpServer()
    result = await server.execute_tool("get_render_status", {"job_id": "nonexistent"})
    assert not result.success


# === QA MCP ===


@pytest.mark.asyncio
async def test_qa_validate_project_passes() -> None:
    server = QaMcpServer()
    result = await server.execute_tool("validate_project", {
        "project_id": "p1",
        "scenes": [
            {"id": "s1", "project_id": "p1", "index": 0, "title": "A", "start_time": 0.0, "end_time": 5.0, "narration": "n"},
            {"id": "s2", "project_id": "p1", "index": 1, "title": "B", "start_time": 5.0, "end_time": 10.0, "narration": "n"},
        ],
        "audio_duration_sec": 10.0, "video_duration_sec": 10.0,
    })
    assert result.success
    assert result.data["passed"] is True


@pytest.mark.asyncio
async def test_qa_validate_locations_detects_unresolved() -> None:
    server = QaMcpServer()
    result = await server.execute_tool("validate_locations", {
        "project_id": "p1",
        "scenes": [{
            "id": "s1", "project_id": "p1", "index": 0, "title": "A",
            "start_time": 0.0, "end_time": 5.0, "narration": "n",
            "location": {
                "id": "l1", "name": "Paris", "latitude": 48.85, "longitude": 2.35,
                "source": "nominatim",
                "geocode_payload": {"status": "unresolved"},
            },
        }],
    })
    assert result.success
    cats = [f["category"] for f in result.data["findings"]]
    assert "invalid_coordinates" in cats


@pytest.mark.asyncio
async def test_qa_create_report_no_render() -> None:
    server = QaMcpServer()
    result = await server.execute_tool("create_qa_report", {
        "project_id": "p1",
        "scenes": [{"id": "s1", "project_id": "p1", "index": 0, "title": "A", "start_time": 0.0, "end_time": 5.0, "narration": "n"}],
    })
    assert result.success
    # No render_output_path means QA runs pre-render; no render_error finding.
    assert result.data["passed"] is True
    cats = [f["category"] for f in result.data["findings"]]
    assert "render_error" not in cats


@pytest.mark.asyncio
async def test_qa_create_report_missing_render_file() -> None:
    server = QaMcpServer()
    result = await server.execute_tool("create_qa_report", {
        "project_id": "p1",
        "scenes": [{"id": "s1", "project_id": "p1", "index": 0, "title": "A", "start_time": 0.0, "end_time": 5.0, "narration": "n"}],
        "render_output_path": "output/nonexistent.mp4",
    })
    assert result.success
    assert result.data["passed"] is False
    cats = [f["category"] for f in result.data["findings"]]
    assert "render_error" in cats

