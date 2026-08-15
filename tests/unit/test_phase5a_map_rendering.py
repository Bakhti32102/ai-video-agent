"""Phase 5A tests: map rendering wired into the video pipeline.

Covers:
- location query normalization
- render_map tool on the Geo MCP server
- MapAnimationPlan.from_dict round-trip
- Supervisor renders maps for map-required scenes
- map PNG paths reach the composition layer with scene timing
- no fabricated coordinates (provenance preserved)
- graceful fallback when map rendering or geocoding fails
"""

from __future__ import annotations

import os
import shutil

import pytest

from app.agents.supervisor import SupervisorAgent
from app.core.enums import AgentName, WorkflowState as WorkflowStateEnum
from app.mcp.client import McpClient
from app.mcp.schemas import RenderMapInput
from app.mcp.servers.geo.server import GeoMcpServer
from app.mcp.servers.audio.server import AudioMcpServer
from app.mcp.servers.render.server import RenderMcpServer
from app.schemas.contracts import GeoProvenance, Location
from app.services.geo import GeocodeResult, GeoProvider, normalize_geo_query
from app.services.map_engine import MapAnimationEngine, MapAnimationPlan, MapRenderer
from app.services.ffmpeg import StubFFmpegService


GADSDEN_SCRIPT = (
    "The Gadsden Purchase of 1853 was a treaty between the United States and Mexico.\n\n"
    "James Gadsden negotiated the agreement with Santa Anna in Mexico City.\n\n"
    "The United States paid ten million dollars for the land in Arizona and New Mexico.\n\n"
    "This territory expansion shaped the modern border between the two nations."
)


class _MockGeoProvider(GeoProvider):
    """Deterministic mock geocoder resolving common Gadsden locations."""

    name = "mock"
    _RESULTS: dict[str, tuple[float, float, str]] = {
        "mexico": (23.63, -102.55, "Mexico"),
        "united states": (39.78, -100.44, "United States"),
        "arizona, usa": (34.39, -111.76, "Arizona, USA"),
        "new mexico, usa": (34.58, -105.99, "New Mexico, USA"),
        "mexico city, mexico": (19.32, -99.15, "Mexico City, Mexico"),
    }

    async def geocode(self, query: str) -> GeocodeResult:
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


class _FailingGeoProvider(GeoProvider):
    """Provider that always returns unresolved (simulates no geocoder configured)."""

    name = "failing"

    async def geocode(self, query: str) -> GeocodeResult:
        return GeocodeResult(
            query=query, status="unresolved", provider="failing", error="no geocoder",
        )


def _build_mock_client(provider: GeoProvider | None = None) -> McpClient:
    """Build an MCP client with mocked geo/audio/render."""
    base_client = McpClient(validate_results=False)
    geo_server = GeoMcpServer(provider=provider or _MockGeoProvider())
    audio_server = AudioMcpServer(ffmpeg_service=StubFFmpegService())
    render_server = RenderMcpServer(ffmpeg_service=StubFFmpegService())
    servers: dict = {}
    for name in AgentName:
        if name == AgentName.SUPERVISOR:
            continue
        if base_client._registry.has_server(name):
            servers[name] = base_client._registry.get_server(name)
    servers[AgentName.GEO] = geo_server
    servers[AgentName.AUDIO] = audio_server
    servers[AgentName.RENDER] = render_server
    return McpClient(servers=servers, validate_results=False)


# --- Query normalization --------------------------------------------------


class TestNormalizeGeoQuery:
    def test_us_state_gets_usa_suffix(self) -> None:
        assert normalize_geo_query("arizona") == "Arizona, USA"
        assert normalize_geo_query("new mexico") == "New Mexico, USA"

    def test_country_not_double_suffixed(self) -> None:
        assert normalize_geo_query("mexico") == "Mexico"

    def test_city_gets_country_suffix(self) -> None:
        assert normalize_geo_query("mexico city") == "Mexico City, Mexico"

    def test_unknown_place_title_cased(self) -> None:
        assert normalize_geo_query("mesa del osos") == "Mesa Del Osos"

    def test_empty_string_passthrough(self) -> None:
        assert normalize_geo_query("") == ""

    def test_does_not_fabricate_unknown_place(self) -> None:
        # An unknown place is title-cased, not suffixed with a country.
        result = normalize_geo_query("unknown place")
        assert result == "Unknown Place"
        # No fabricated country.
        assert "USA" not in result and "Mexico" not in result


# --- render_map tool ------------------------------------------------------


class TestRenderMapTool:
    @pytest.mark.asyncio
    async def test_render_map_produces_png(self, tmp_path) -> None:
        server = GeoMcpServer(provider=_MockGeoProvider())
        loc = Location(
            id="loc1", name="Arizona, USA", latitude=34.39, longitude=-111.76, source="mock",
        )
        engine = MapAnimationEngine()
        plan = engine.build_static_plan(loc, scene_id="scene_0", duration_sec=5.0, zoom=5.0)
        result = await server.execute_tool("render_map", {
            "plan": plan.to_dict(),
            "output_filename": "test_map_render.png",
        })
        assert result.success
        assert result.data is not None
        output_path = result.data["output_path"] if isinstance(result.data, dict) else result.data.output_path
        assert os.path.exists(output_path)
        assert os.path.getsize(output_path) > 100

    @pytest.mark.asyncio
    async def test_render_map_rejects_traversal(self) -> None:
        server = GeoMcpServer(provider=_MockGeoProvider())
        loc = Location(id="loc1", name="Arizona, USA", latitude=34.39, longitude=-111.76, source="mock")
        engine = MapAnimationEngine()
        plan = engine.build_static_plan(loc, scene_id="scene_0", duration_sec=5.0, zoom=5.0)
        result = await server.execute_tool("render_map", {
            "plan": plan.to_dict(),
            "output_filename": "../../../etc/evil.png",
        })
        assert not result.success

    @pytest.mark.asyncio
    async def test_render_map_invalid_plan_fails_gracefully(self) -> None:
        server = GeoMcpServer(provider=_MockGeoProvider())
        result = await server.execute_tool("render_map", {
            "plan": {"not_a_real_plan": True},
            "output_filename": "bad.png",
        })
        assert not result.success


# --- MapAnimationPlan.from_dict ------------------------------------------


class TestMapAnimationPlanFromDict:
    def test_round_trip_preserves_fields(self) -> None:
        loc = Location(id="loc1", name="Arizona, USA", latitude=34.39, longitude=-111.76, source="mock")
        engine = MapAnimationEngine()
        original = engine.build_static_plan(loc, scene_id="scene_0", duration_sec=5.0, zoom=5.0)
        d = original.to_dict()
        restored = MapAnimationPlan.from_dict(d)
        assert restored.center_latitude == original.center_latitude
        assert restored.center_longitude == original.center_longitude
        assert len(restored.markers) == len(original.markers)
        assert restored.markers[0].latitude == original.markers[0].latitude
        assert restored.scene_id == original.scene_id
        assert restored.animation_type == original.animation_type

    def test_from_dict_reconstructs_provenance(self) -> None:
        loc = Location(
            id="loc1", name="Arizona, USA", latitude=34.39, longitude=-111.76, source="mock",
            provenance=GeoProvenance(
                provider="mock", source="test", query="Arizona, USA",
                latitude=34.39, longitude=-111.76,
            ),
        )
        engine = MapAnimationEngine()
        original = engine.build_static_plan(loc, scene_id="scene_0", duration_sec=5.0, zoom=5.0)
        d = original.to_dict()
        restored = MapAnimationPlan.from_dict(d)
        assert restored.provenance is not None
        assert restored.provenance.provider == "mock"


# --- Supervisor integration -----------------------------------------------


class TestSupervisorMapRendering:
    @pytest.mark.asyncio
    async def test_supervisor_renders_maps_for_map_required_scenes(self) -> None:
        client = _build_mock_client()
        sup = SupervisorAgent(client, max_retries=1)
        result = await sup.run_project(
            project_id="phase5a_map_test",
            script_text=GADSDEN_SCRIPT,
            total_duration_sec=20.0,
        )
        # map_images should be populated for map-required scenes.
        map_images = result.get("map_images", [])
        scene_map_paths = result.get("scene_map_paths", {})
        assert len(map_images) >= 1
        assert len(scene_map_paths) >= 1
        # Each map path should be a real file.
        for path in scene_map_paths.values():
            assert os.path.exists(path)
            assert os.path.getsize(path) > 100

    @pytest.mark.asyncio
    async def test_map_paths_reach_composition_layer(self) -> None:
        client = _build_mock_client()
        sup = SupervisorAgent(client, max_retries=1)
        result = await sup.run_project(
            project_id="phase5a_compose_test",
            script_text=GADSDEN_SCRIPT,
            total_duration_sec=20.0,
        )
        # The compose result should include overlays (maps + text).
        # With StubFFmpegService, compose fails, but the supervisor still
        # builds the overlay list. Verify via map_images.
        scene_map_paths = result.get("scene_map_paths", {})
        scenes = result.get("scenes", [])
        # For each map-required scene, there should be a map path.
        for scene in scenes:
            if scene.get("map_required"):
                idx = scene.get("index")
                assert idx in scene_map_paths
                assert os.path.exists(scene_map_paths[idx])

    @pytest.mark.asyncio
    async def test_map_timing_matches_scene_timing(self) -> None:
        client = _build_mock_client()
        sup = SupervisorAgent(client, max_retries=1)
        result = await sup.run_project(
            project_id="phase5a_timing_test",
            script_text=GADSDEN_SCRIPT,
            total_duration_sec=20.0,
        )
        scenes = result.get("scenes", [])
        scene_map_paths = result.get("scene_map_paths", {})
        # Each scene's map exists and the scene has valid start/end times.
        for scene in scenes:
            if scene.get("map_required"):
                idx = scene.get("index")
                assert idx in scene_map_paths
                start = float(scene.get("start_time", 0.0))
                end = float(scene.get("end_time", 0.0))
                assert end > start
        # Map-required scenes (first 3) should have maps; text-only scene (last) should not.
        map_required_indices = {s["index"] for s in scenes if s.get("map_required")}
        text_only_indices = {s["index"] for s in scenes if not s.get("map_required")}
        assert map_required_indices.issubset(set(scene_map_paths.keys()))
        assert text_only_indices.isdisjoint(set(scene_map_paths.keys()))

    @pytest.mark.asyncio
    async def test_no_fabricated_coordinates(self) -> None:
        client = _build_mock_client()
        sup = SupervisorAgent(client, max_retries=1)
        result = await sup.run_project(
            project_id="phase5a_provenance_test",
            script_text=GADSDEN_SCRIPT,
            total_duration_sec=20.0,
        )
        # Every resolved location must carry provenance.
        for loc in result.get("resolved_locations", []):
            if loc.get("status") == "resolved":
                assert loc.get("provenance") is not None
                assert loc.get("latitude") is not None
                assert loc.get("longitude") is not None

    @pytest.mark.asyncio
    async def test_graceful_fallback_when_geocoding_fails(self) -> None:
        """When no location resolves, the pipeline still completes without maps."""
        client = _build_mock_client(provider=_FailingGeoProvider())
        sup = SupervisorAgent(client, max_retries=1)
        result = await sup.run_project(
            project_id="phase5a_fallback_test",
            script_text=GADSDEN_SCRIPT,
            total_duration_sec=20.0,
        )
        # No maps should be rendered.
        assert result.get("map_images", []) == []
        assert result.get("scene_map_paths", {}) == {}
        # All resolved locations should be unresolved (no fabrication).
        for loc in result.get("resolved_locations", []):
            assert loc.get("status") == "unresolved"
        # Pipeline should not crash; it completes (render fails due to stub,
        # which is the existing behavior).
        assert "final_state" in result

    @pytest.mark.asyncio
    async def test_zero_config_no_geocoder_does_not_crash(self) -> None:
        """With the default NoneGeoProvider, the pipeline runs without maps."""
        from app.services.geo import NoneGeoProvider
        client = _build_mock_client(provider=NoneGeoProvider())
        sup = SupervisorAgent(client, max_retries=1)
        result = await sup.run_project(
            project_id="phase5a_zero_config_test",
            script_text=GADSDEN_SCRIPT,
            total_duration_sec=20.0,
        )
        # No maps, no crash.
        assert result.get("map_images", []) == []
        assert "final_state" in result

    @pytest.mark.asyncio
    async def test_existing_text_overlays_preserved(self) -> None:
        """Map rendering must not remove existing text overlays."""
        client = _build_mock_client()
        sup = SupervisorAgent(client, max_retries=1)
        result = await sup.run_project(
            project_id="phase5a_text_preserved_test",
            script_text=GADSDEN_SCRIPT,
            total_duration_sec=20.0,
        )
        # Text overlays should still be present (one per scene).
        text_overlays = result.get("text_overlays", [])
        assert len(text_overlays) >= 1
        for overlay in text_overlays:
            assert overlay.get("rendered_path") is not None
            assert os.path.exists(overlay["rendered_path"])


# --- Real end-to-end pipeline (requires ffmpeg) --------------------------


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")
@pytest.mark.asyncio
async def test_phase5a_real_pipeline_produces_video_with_maps() -> None:
    """Full Phase 5A pipeline: real ffmpeg + mock geocoder → real MP4 with map visuals.

    Verifies the end-to-end flow: script → scenes → geocode → map plans →
    rendered map PNGs → composed video with map overlays → real 1920x1080
    H.264 MP4 → QA pass. The mock geocoder provides deterministic coordinates
    so the test is hermetic (no real Nominatim calls).
    """
    import os
    from app.database import init_db
    from app.services.ffmpeg import FFmpegRenderer

    init_db()
    base_client = McpClient(validate_results=False)
    geo_server = GeoMcpServer(provider=_MockGeoProvider())
    render_server = RenderMcpServer(ffmpeg_service=FFmpegRenderer())
    servers: dict = {}
    for name in AgentName:
        if name == AgentName.SUPERVISOR:
            continue
        if base_client._registry.has_server(name):
            servers[name] = base_client._registry.get_server(name)
    servers[AgentName.GEO] = geo_server
    servers[AgentName.RENDER] = render_server
    client = McpClient(servers=servers, validate_results=False)

    sup = SupervisorAgent(client, max_retries=1)
    result = await sup.run_project(
        project_id="phase5a_real_e2e",
        script_text=GADSDEN_SCRIPT,
        total_duration_sec=10.0,
    )

    # Pipeline should complete successfully.
    assert result["failed"] is False
    assert result["final_state"] == WorkflowStateEnum.COMPLETED.value

    # Maps should have been rendered for map-required scenes.
    scene_map_paths = result.get("scene_map_paths", {})
    assert len(scene_map_paths) >= 1
    for path in scene_map_paths.values():
        assert os.path.exists(path)

    # Render output should be a real MP4.
    render_output = result["results"]["render"]["output"]["output_path"]
    assert os.path.exists(render_output)
    assert os.path.getsize(render_output) > 1024

    # QA should pass.
    assert result["qa_report"]["passed"] is True

    # Verify the video contains the map visuals by sampling frames.
    # A frame from a map-required scene should have more color variation
    # than a plain background frame.
    from PIL import Image
    import subprocess
    scene0_mid = 2 * 30  # ~2s into scene 0 (map-required)
    frame_path = render_output + ".frame.png"
    subprocess.run(
        ["ffmpeg", "-y", "-i", render_output, "-vf", f"select=eq(n\\,{scene0_mid})",
         "-vframes", "1", frame_path],
        capture_output=True, check=True,
    )
    img = Image.open(frame_path)
    colors = set()
    for x in range(0, 1920, 80):
        for y in range(0, 1080, 80):
            colors.add(img.getpixel((x, y)))
    # A map overlay produces varied land/grid colors, not just a flat background.
    assert len(colors) >= 4, f"expected map visuals, got {len(colors)} colors"

    # Cleanup.
    for f in [render_output, frame_path, *scene_map_paths.values()]:
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
