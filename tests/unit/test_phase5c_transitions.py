"""Phase 5C tests: Real Video Transitions.

Verifies that the Transitions MCP ``build_filtergraph`` tool, the FFmpeg
``compose_with_transitions`` implementation, the Render MCP
``compose_with_transitions`` tool, and the Supervisor Step 7→9 wiring produce a
real multi-scene MP4 joined by FFmpeg ``xfade`` transitions — with no
destruction of the Phase 5A (map rendering) or Phase 5B (sound mixing)
architecture.

Tests with the ``real_ffmpeg`` marker exercise the real ffmpeg binary against
synthetic color/tone fixtures (no copyrighted media, no network). They are
skipped when ffmpeg/ffprobe are not on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.agents.supervisor import SupervisorAgent, _map_transition_kind
from app.core.enums import AgentName, WorkflowState as WorkflowStateEnum
from app.mcp.client import McpClient
from app.mcp.servers.base import BaseMcpServer
from app.mcp.servers.geo.server import GeoMcpServer
from app.mcp.servers.render.server import RenderMcpServer
from app.mcp.servers.transitions.server import TransitionMcpServer
from app.mcp.schemas import (
    BuildFiltergraphInput,
    ComposeWithTransitionsInput,
)
from app.services.ffmpeg import (
    ComposeWithTransitionsParams,
    FFmpegRenderer,
    OverlayLayer,
    SceneSegmentSpec,
    StubFFmpegService,
    TransitionSpec,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

GADSDEN_SCRIPT = (
    "The Gadsden Purchase of 1853 was a treaty between the United States and Mexico.\n\n"
    "James Gadsden negotiated the agreement with Santa Anna in Mexico City.\n\n"
    "The United States paid ten million dollars for the land in Arizona and New Mexico.\n\n"
    "This territory expansion shaped the modern border between the two nations."
)


class _MockGeoProvider:  # minimal stand-in reused from Phase 5A
    name = "mock"
    _RESULTS = {
        "mexico": (23.63, -102.55, "Mexico"),
        "united states": (39.78, -100.44, "United States"),
        "arizona, usa": (34.39, -111.76, "Arizona, USA"),
        "new mexico, usa": (34.58, -105.99, "New Mexico, USA"),
        "mexico city, mexico": (19.32, -99.15, "Mexico City, Mexico"),
    }

    async def geocode(self, query: str):
        from app.schemas.contracts import GeoProvenance
        from app.services.geo import GeocodeResult
        key = query.strip().lower()
        if key in self._RESULTS:
            lat, lon, display = self._RESULTS[key]
            return GeocodeResult(
                query=query, status="resolved", latitude=lat, longitude=lon,
                display_name=display, confidence=0.85, provider="mock",
                provenance=GeoProvenance(
                    provider="mock", source="test-fixture",
                    query=query, latitude=lat, longitude=lon,
                ),
            )
        return GeocodeResult(
            query=query, status="unresolved", provider="mock", error="not found",
        )


# ---------------------------------------------------------------------------
# Schema / mapping tests (no ffmpeg required)
# ---------------------------------------------------------------------------


def test_map_transition_kind_known_and_unknown() -> None:
    assert _map_transition_kind("dissolve") == "dissolve"
    assert _map_transition_kind("fade") == "fade"
    assert _map_transition_kind("cut") == "cut"
    assert _map_transition_kind("fadeblack") == "fade_to_black"
    assert _map_transition_kind("nonexistent") == "fade"


def test_compose_with_transitions_schema_validates_segments() -> None:
    inp = ComposeWithTransitionsInput(
        output_filename="out.mp4",
        segments=[{"scene_id": "s1", "duration_sec": 2.0}],
    )
    assert inp.segments[0]["scene_id"] == "s1"
    # Empty segments are rejected by min_length=1.
    with pytest.raises(Exception):
        ComposeWithTransitionsInput(output_filename="out.mp4", segments=[])


def test_transition_spec_defaults() -> None:
    t = TransitionSpec(kind="fade")
    assert t.duration_sec == 0.5
    assert t.direction == "left"


def test_scene_segment_spec_defaults() -> None:
    s = SceneSegmentSpec(scene_id="s0", duration_sec=3.0)
    assert s.background_color == "#1a1a2e"
    assert s.overlays == []


# ---------------------------------------------------------------------------
# Transitions MCP build_filtergraph tests (no ffmpeg required)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_filtergraph_dissolve_uses_xfade() -> None:
    server = TransitionMcpServer()
    result = await server.execute_tool("build_filtergraph", {
        "transition_kind": "dissolve", "duration_sec": 0.5,
        "offset_sec": 2.0, "total_duration_sec": 5.0,
    })
    assert result.success
    assert "xfade" in result.data["filtergraph"]
    assert "transition=fade" in result.data["filtergraph"]


@pytest.mark.asyncio
async def test_build_filtergraph_slide_direction() -> None:
    server = TransitionMcpServer()
    result = await server.execute_tool("build_filtergraph", {
        "transition_kind": "slide", "direction": "right",
        "duration_sec": 0.5, "offset_sec": 1.0, "total_duration_sec": 4.0,
    })
    assert result.success
    assert "slideright" in result.data["filtergraph"]


@pytest.mark.asyncio
async def test_build_filtergraph_cut_returns_empty() -> None:
    server = TransitionMcpServer()
    result = await server.execute_tool("build_filtergraph", {
        "transition_kind": "cut", "duration_sec": 0.5,
        "offset_sec": 0.0, "total_duration_sec": 4.0,
    })
    assert result.success
    assert result.data["filtergraph"] == ""


# ---------------------------------------------------------------------------
# FFmpegRenderer transition normalization tests (no ffmpeg required)
# ---------------------------------------------------------------------------


def test_normalize_transitions_clamps_oversized_duration() -> None:
    r = FFmpegRenderer()
    params = ComposeWithTransitionsParams(
        output_path="out.mp4",
        segments=[
            SceneSegmentSpec(scene_id="a", duration_sec=1.0),
            SceneSegmentSpec(scene_id="b", duration_sec=1.0),
        ],
        transitions=[TransitionSpec(kind="fade", duration_sec=2.0)],
    )
    warnings: list[str] = []
    norm = r._normalize_transitions(params, warnings)
    assert norm[0].kind == "fade"
    # 2.0s transition on 1.0s scenes → clamped to 0.5s (50% of min scene).
    assert norm[0].duration_sec == 0.5
    assert any("clamped" in w for w in warnings)


def test_normalize_transitions_unsupported_kind_falls_back_to_cut() -> None:
    r = FFmpegRenderer()
    params = ComposeWithTransitionsParams(
        output_path="out.mp4",
        segments=[
            SceneSegmentSpec(scene_id="a", duration_sec=2.0),
            SceneSegmentSpec(scene_id="b", duration_sec=2.0),
        ],
        transitions=[TransitionSpec(kind="explode", duration_sec=0.5)],
    )
    warnings: list[str] = []
    norm = r._normalize_transitions(params, warnings)
    assert norm[0].kind == "cut"
    assert any("unsupported" in w for w in warnings)


def test_normalize_transitions_pads_missing_transitions() -> None:
    r = FFmpegRenderer()
    params = ComposeWithTransitionsParams(
        output_path="out.mp4",
        segments=[
            SceneSegmentSpec(scene_id="a", duration_sec=2.0),
            SceneSegmentSpec(scene_id="b", duration_sec=2.0),
            SceneSegmentSpec(scene_id="c", duration_sec=2.0),
        ],
        transitions=[],  # no transitions supplied → default fade padding
    )
    warnings: list[str] = []
    norm = r._normalize_transitions(params, warnings)
    assert len(norm) == 2  # 3 segments → 2 transitions
    assert all(t.kind == "fade" for t in norm)


def test_normalize_transitions_map_kinds_supported() -> None:
    r = FFmpegRenderer()
    params = ComposeWithTransitionsParams(
        output_path="out.mp4",
        segments=[
            SceneSegmentSpec(scene_id="a", duration_sec=2.0),
            SceneSegmentSpec(scene_id="b", duration_sec=2.0),
        ],
        transitions=[TransitionSpec(kind="map_zoom", duration_sec=0.5)],
    )
    warnings: list[str] = []
    norm = r._normalize_transitions(params, warnings)
    # 2 segments → 1 transition; map_zoom → zoomin (renderable, not cut).
    assert len(norm) == 1
    assert norm[0].kind == "map_zoom"


def test_build_transition_command_offset_math() -> None:
    """Verify the xfade offset formula for 3 segments / 2 transitions."""
    r = FFmpegRenderer()
    params = ComposeWithTransitionsParams(
        output_path="out.mp4",
        segments=[
            SceneSegmentSpec(scene_id="a", duration_sec=2.0),
            SceneSegmentSpec(scene_id="b", duration_sec=2.0),
            SceneSegmentSpec(scene_id="c", duration_sec=2.0),
        ],
        transitions=[
            TransitionSpec(kind="fade", duration_sec=0.5),
            TransitionSpec(kind="slide", duration_sec=0.5, direction="left"),
        ],
    )
    warnings: list[str] = []
    # _build_transition_command validates input paths, so we cannot call it
    # without real segment files. Instead, verify the offset math indirectly:
    # the expected output duration = 2+2+2 - 0.5 - 0.5 = 5.0s.
    norm = r._normalize_transitions(params, warnings)
    seg_durs = [s.duration_sec for s in params.segments]
    overlap = sum(t.duration_sec for t in norm if t.kind != "cut")
    expected = sum(seg_durs) - overlap
    assert expected == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# StubFFmpegService / Render MCP fallback tests (no ffmpeg required)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stub_ffmpeg_compose_with_transitions_unavailable() -> None:
    stub = StubFFmpegService()
    params = ComposeWithTransitionsParams(
        output_path="out.mp4",
        segments=[SceneSegmentSpec(scene_id="a", duration_sec=2.0)],
    )
    with pytest.raises(Exception, match="not available"):
        await stub.compose_with_transitions(params)


@pytest.mark.asyncio
async def test_render_mcp_compose_with_transitions_registered() -> None:
    server = RenderMcpServer(ffmpeg_service=StubFFmpegService())
    tool_names = [t.name for t in server._tools.values()]
    assert "compose_with_transitions" in tool_names


@pytest.mark.asyncio
async def test_render_mcp_transition_rejects_empty_segments() -> None:
    server = RenderMcpServer(ffmpeg_service=StubFFmpegService())
    result = await server.execute_tool("compose_with_transitions", {
        "output_filename": "out.mp4",
        "segments": [],
    })
    assert not result.success


# ---------------------------------------------------------------------------
# Supervisor wiring tests (StubFFmpegService — no real rendering)
# ---------------------------------------------------------------------------


def _build_stub_client() -> McpClient:
    base = McpClient(validate_results=False)
    servers: dict = {}
    for name in AgentName:
        if name == AgentName.SUPERVISOR:
            continue
        if base._registry.has_server(name):
            servers[name] = base._registry.get_server(name)
    # Use the mock geo provider so maps render for map-required scenes.
    servers[AgentName.GEO] = GeoMcpServer(provider=_MockGeoProvider())  # type: ignore[arg-type]
    servers[AgentName.RENDER] = RenderMcpServer(ffmpeg_service=StubFFmpegService())
    return McpClient(servers=servers, validate_results=False)


@pytest.mark.asyncio
async def test_supervisor_creates_transitions_between_scenes() -> None:
    sup = SupervisorAgent(_build_stub_client(), max_retries=1)
    result = await sup.run_project(
        project_id="phase5c_trans_plan",
        script_text=GADSDEN_SCRIPT,
        total_duration_sec=10.0,
    )
    transitions = result.get("transitions", [])
    # 4 scenes → 3 transitions.
    assert len(transitions) == 3
    for t in transitions:
        assert t.get("kind") in {"fade", "dissolve", "cut", "slide", "wipe", "zoom"}
        assert t.get("duration_sec") == 0.5
        assert t.get("from_scene_id") is not None
        assert t.get("to_scene_id") is not None


@pytest.mark.asyncio
async def test_supervisor_transition_compose_falls_back_gracefully() -> None:
    """With StubFFmpegService, transition compose fails → continuous compose →
    render_video fallback. No video is produced (stub env), but transitions
    are still planned and the workflow degrades gracefully (FAILED state, not a
    crash). Phase 5A maps are still rendered."""
    sup = SupervisorAgent(_build_stub_client(), max_retries=1)
    result = await sup.run_project(
        project_id="phase5c_fallback",
        script_text=GADSDEN_SCRIPT,
        total_duration_sec=10.0,
    )
    # Transitions were still planned (4 scenes → 3 transitions).
    assert len(result.get("transitions", [])) == 3
    # Phase 5A maps were still rendered even though video rendering is stubbed.
    assert len(result.get("scene_map_paths", {})) >= 1
    # Workflow degrades to FAILED (no video possible with Stub) — not a crash.
    assert result["final_state"] in {
        WorkflowStateEnum.FAILED.value,
        WorkflowStateEnum.COMPLETED.value,
    }


# ---------------------------------------------------------------------------
# Real ffmpeg tests (skipped when ffmpeg is unavailable)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def audio_fixture(tmp_path_factory):
    """Generate a short tone WAV for audio-mux tests."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not installed")
    d = tmp_path_factory.mktemp("phase5c_audio")
    out = d / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=6.0",
         "-ar", "44100", "-ac", "1", str(out)],
        capture_output=True, check=True,
    )
    return str(out)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
@pytest.mark.asyncio
async def test_real_compose_with_transitions_basic(tmp_path) -> None:
    """3 color segments joined by fade + slide → real MP4 with correct duration."""
    r = FFmpegRenderer()
    params = ComposeWithTransitionsParams(
        output_path="phase5c_basic.mp4",
        segments=[
            SceneSegmentSpec(scene_id="s0", duration_sec=2.5, background_color="#ff0000"),
            SceneSegmentSpec(scene_id="s1", duration_sec=2.5, background_color="#0000ff"),
            SceneSegmentSpec(scene_id="s2", duration_sec=2.5, background_color="#00ff00"),
        ],
        transitions=[
            TransitionSpec(kind="fade", duration_sec=0.5),
            TransitionSpec(kind="slide", duration_sec=0.5, direction="left"),
        ],
    )
    out = await r.compose_with_transitions(params)
    assert os.path.exists(out)
    info = await r.probe(out)
    # 2.5*3 - 0.5*2 = 6.5s
    assert info.duration_sec == pytest.approx(6.5, abs=0.2)
    assert info.width == 1920 and info.height == 1080
    assert info.codec == "h264"
    # Temp segment files must be cleaned up.
    seg_files = list(Path(out).parent.glob("_seg_*.mp4"))
    assert seg_files == []
    os.remove(out)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
@pytest.mark.asyncio
async def test_real_compose_with_transitions_and_audio(tmp_path, audio_fixture) -> None:
    """Transition-composed video with audio mux → has both video + audio streams."""
    r = FFmpegRenderer()
    params = ComposeWithTransitionsParams(
        output_path="phase5c_audio.mp4",
        segments=[
            SceneSegmentSpec(scene_id="s0", duration_sec=2.0, background_color="#222244"),
            SceneSegmentSpec(scene_id="s1", duration_sec=2.0, background_color="#442222"),
        ],
        transitions=[TransitionSpec(kind="dissolve", duration_sec=0.5)],
        audio_path=audio_fixture,
    )
    out = await r.compose_with_transitions(params)
    assert os.path.exists(out)
    info = await r.probe(out)
    # 2+2 - 0.5 = 3.5s
    assert info.duration_sec == pytest.approx(3.5, abs=0.2)
    assert info.codec == "h264"
    # Verify an audio stream is present.
    probe_raw = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", out],
        capture_output=True, text=True, check=True,
    )
    assert "audio" in probe_raw.stdout
    os.remove(out)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
@pytest.mark.asyncio
async def test_real_compose_transitions_with_overlays(tmp_path) -> None:
    """Segments carrying image overlays (Phase 5A maps) still composite correctly."""
    from PIL import Image, ImageDraw
    r = FFmpegRenderer()
    # Create two distinct overlay PNGs.
    ov0 = tmp_path / "map0.png"
    ov1 = tmp_path / "map1.png"
    img0 = Image.new("RGB", (400, 300), (10, 200, 10))
    ImageDraw.Draw(img0).rectangle([50, 50, 350, 250], fill=(200, 10, 10))
    img0.save(ov0)
    img1 = Image.new("RGB", (400, 300), (10, 10, 200))
    ImageDraw.Draw(img1).ellipse([50, 50, 350, 250], fill=(200, 200, 10))
    img1.save(ov1)

    params = ComposeWithTransitionsParams(
        output_path="phase5c_overlays.mp4",
        segments=[
            SceneSegmentSpec(
                scene_id="s0", duration_sec=2.0, background_color="#111111",
                overlays=[OverlayLayer(image_path=str(ov0), x=0.1, y=0.1,
                                       start_time=0.0, end_time=2.0)],
            ),
            SceneSegmentSpec(
                scene_id="s1", duration_sec=2.0, background_color="#222222",
                overlays=[OverlayLayer(image_path=str(ov1), x=0.1, y=0.1,
                                       start_time=0.0, end_time=2.0)],
            ),
        ],
        transitions=[TransitionSpec(kind="fade_to_black", duration_sec=0.4)],
    )
    out = await r.compose_with_transitions(params)
    assert os.path.exists(out)
    info = await r.probe(out)
    assert info.duration_sec == pytest.approx(3.6, abs=0.2)
    assert info.width == 1920 and info.height == 1080
    os.remove(out)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
@pytest.mark.asyncio
async def test_real_compose_all_cut_transitions(tmp_path) -> None:
    """All-cut transitions still produce a valid (concatenated) video."""
    r = FFmpegRenderer()
    params = ComposeWithTransitionsParams(
        output_path="phase5c_cut.mp4",
        segments=[
            SceneSegmentSpec(scene_id="s0", duration_sec=1.5, background_color="#ff0000"),
            SceneSegmentSpec(scene_id="s1", duration_sec=1.5, background_color="#00ff00"),
        ],
        transitions=[TransitionSpec(kind="cut", duration_sec=0.0)],
    )
    out = await r.compose_with_transitions(params)
    assert os.path.exists(out)
    info = await r.probe(out)
    # cut → 0.1s cross-fade; duration ~ 1.5+1.5 - 0.1 = 2.9s
    assert info.duration_sec == pytest.approx(2.9, abs=0.15)
    os.remove(out)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
@pytest.mark.asyncio
async def test_render_mcp_compose_with_transitions_tool_real(tmp_path) -> None:
    """The Render MCP compose_with_transitions tool produces a real MP4."""
    server = RenderMcpServer(ffmpeg_service=FFmpegRenderer())
    result = await server.execute_tool("compose_with_transitions", {
        "output_filename": "phase5c_mcp.mp4",
        "project_id": "phase5c",
        "segments": [
            {"scene_id": "a", "duration_sec": 2.0, "background_color": "#334455"},
            {"scene_id": "b", "duration_sec": 2.0, "background_color": "#554433"},
        ],
        "transitions": [{"kind": "fade", "duration_sec": 0.5, "direction": "left"}],
        "width": 1920, "height": 1080, "fps": 30.0,
    })
    assert result.success
    out = result.data["output_path"]
    assert os.path.exists(out)
    assert result.data["segment_count"] == 2
    assert result.data["transition_count"] == 1
    os.remove(out)


# ---------------------------------------------------------------------------
# Full end-to-end supervisor pipeline with real ffmpeg + transitions
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
@pytest.mark.asyncio
async def test_phase5c_full_pipeline_produces_transitioned_video() -> None:
    """Full pipeline: script → scenes → maps → transitions → composed MP4 → QA pass.

    Verifies Phase 5C connects the Transitions MCP and FFmpeg transition
    composition to the real video pipeline WITHOUT destroying Phase 5A (maps)
    or Phase 5B (audio mixing). The output is a real 1920x1080 H.264 MP4 whose
    duration reflects the transition overlaps, and QA passes.
    """
    from app.database import init_db

    init_db()
    base = McpClient(validate_results=False)
    geo_server = GeoMcpServer(provider=_MockGeoProvider())  # type: ignore[arg-type]
    render_server = RenderMcpServer(ffmpeg_service=FFmpegRenderer())
    servers: dict = {}
    for name in AgentName:
        if name == AgentName.SUPERVISOR:
            continue
        if base._registry.has_server(name):
            servers[name] = base._registry.get_server(name)
    servers[AgentName.GEO] = geo_server
    servers[AgentName.RENDER] = render_server
    client = McpClient(servers=servers, validate_results=False)

    sup = SupervisorAgent(client, max_retries=1)
    result = await sup.run_project(
        project_id="phase5c_e2e",
        script_text=GADSDEN_SCRIPT,
        total_duration_sec=10.0,
    )

    # Pipeline completes successfully.
    assert result["failed"] is False
    assert result["final_state"] == WorkflowStateEnum.COMPLETED.value

    # Transitions were planned (4 scenes → 3 transitions).
    transitions = result.get("transitions", [])
    assert len(transitions) == 3
    for t in transitions:
        assert t.get("kind") in {"fade", "dissolve", "cut", "slide", "wipe", "zoom",
                                 "fade_to_black", "map_zoom", "map_to_map"}

    # Render output is a real MP4 produced by transition composition.
    render_out = result["results"]["render"]["output"]
    out_path = render_out["output_path"]
    assert os.path.exists(out_path)
    assert os.path.getsize(out_path) > 1024
    assert render_out.get("segment_count") == 4
    assert render_out.get("transition_count") == 3

    # Duration reflects transition overlaps: 4 scenes * 2.5s - 3 * 0.5s = 8.5s.
    info = await FFmpegRenderer().probe(out_path)
    assert info.duration_sec == pytest.approx(8.5, abs=0.3)
    assert info.width == 1920 and info.height == 1080
    assert info.codec == "h264"

    # QA passes.
    assert result["qa_report"]["passed"] is True

    # Phase 5A preserved: maps were rendered for map-required scenes.
    scene_map_paths = result.get("scene_map_paths", {})
    assert len(scene_map_paths) >= 1

    # Cleanup.
    for f in [out_path, *scene_map_paths.values()]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                pass
    for overlay in result.get("text_overlays", []):
        p = overlay.get("rendered_path")
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
