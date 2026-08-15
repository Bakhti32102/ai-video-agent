"""End-to-end mocked workflow test.

Runs the full Supervisor orchestration with a deterministic script (the
Gadsden Purchase example) and a mock geo provider. No paid APIs are called.
"""

from __future__ import annotations

import pytest

from app.agents.supervisor import SupervisorAgent
from app.core.enums import AgentName, WorkflowState as WorkflowStateEnum
from app.mcp.client import McpClient
from app.mcp.servers.geo.server import GeoMcpServer
from app.mcp.servers.audio.server import AudioMcpServer
from app.services.geo import GeocodeResult, GeoProvider, NoneGeoProvider
from app.schemas.contracts import GeoProvenance
from app.services.ffmpeg import StubFFmpegService


GADSDEN_SCRIPT = (
    "James Gadsden negotiated with Mexico. "
    "The final agreement transferred approximately 29,600 square miles "
    "for ten million dollars."
)


class _MockGeoProvider(GeoProvider):
    """Deterministic mock geocoder for the Gadsden test."""

    name = "mock"

    async def geocode(self, query: str) -> GeocodeResult:
        if "mexico" in query.lower():
            return GeocodeResult(
                query=query, status="resolved", latitude=23.63, longitude=-102.55,
                display_name="Mexico", confidence=0.85, provider="mock",
                provenance=GeoProvenance(
                    provider="mock", source="test-fixture",
                    query=query, latitude=23.63, longitude=-102.55,
                ),
            )
        return GeocodeResult(query=query, status="unresolved", provider="mock", error="not found")


def _build_mock_client() -> McpClient:
    """Build an MCP client with mocked external dependencies."""
    # Start from a default registry, then override geo + audio + render.
    base_client = McpClient(validate_results=False)
    geo_server = GeoMcpServer(provider=_MockGeoProvider())
    audio_server = AudioMcpServer(ffmpeg_service=StubFFmpegService())
    # Inject StubFFmpegService into the render server so no real ffmpeg call
    # is made during the e2e test (keeps tests hermetic and fast).
    from app.mcp.servers.render.server import RenderMcpServer
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


@pytest.mark.asyncio
async def test_end_to_end_workflow_produces_structured_data() -> None:
    """The full workflow produces scenes, timing, locations, text, transitions,
    sound, render job, and QA report."""
    client = _build_mock_client()
    sup = SupervisorAgent(client, max_retries=1)
    result = await sup.run_project(
        project_id="gadsden_test",
        script_text=GADSDEN_SCRIPT,
        total_duration_sec=20.0,
    )

    # The workflow should complete all steps (render fails due to no ffmpeg,
    # so QA will fail, but all other stages produce data).
    assert result["project_id"] == "gadsden_test"
    assert len(result["scenes"]) >= 1
    assert result["audio_duration_sec"] is not None
    assert len(result["text_overlays"]) >= 1
    assert len(result["sound_events"]) >= 1
    # Geo resolution should have resolved "mexico".
    resolved = result["resolved_locations"]
    assert any(loc.get("status") == "resolved" for loc in resolved)
    # QA report should exist.
    assert result["qa_report"] is not None
    assert "findings" in result["qa_report"]


@pytest.mark.asyncio
async def test_end_to_end_workflow_final_state_is_failed_without_render() -> None:
    """Without ffmpeg, render fails, so QA fails, so final state is FAILED."""
    client = _build_mock_client()
    sup = SupervisorAgent(client, max_retries=1)
    result = await sup.run_project(
        project_id="gadsden_test",
        script_text=GADSDEN_SCRIPT,
        total_duration_sec=20.0,
    )
    # Render failure -> QA failure -> FAILED state.
    assert result["failed"] is True
    assert result["final_state"] == WorkflowStateEnum.FAILED.value


@pytest.mark.asyncio
async def test_end_to_end_workflow_results_contain_all_agents() -> None:
    """Every agent in the pipeline should have a result entry."""
    client = _build_mock_client()
    sup = SupervisorAgent(client, max_retries=1)
    result = await sup.run_project(
        project_id="gadsden_test",
        script_text=GADSDEN_SCRIPT,
        total_duration_sec=20.0,
    )
    results = result["results"]
    assert "script" in results
    assert "audio" in results
    assert "geo" in results
    assert "text" in results
    assert "transitions" in results
    assert "sound" in results
    assert "qa" in results


@pytest.mark.asyncio
async def test_end_to_end_workflow_script_analysis_extracts_gadsden() -> None:
    """The script analysis should detect 'gadsden' and 'mexico'."""
    client = _build_mock_client()
    sup = SupervisorAgent(client, max_retries=1)
    result = await sup.run_project(
        project_id="gadsden_test",
        script_text=GADSDEN_SCRIPT,
        total_duration_sec=20.0,
    )
    script_output = result["results"]["script"]["output"]
    entities = script_output.get("entities", {})
    assert "gadsden" in entities.get("people", [])
    assert "mexico" in entities.get("locations", [])


@pytest.mark.asyncio
async def test_end_to_end_workflow_locations_have_provenance() -> None:
    """Resolved locations must carry provenance (never fabricated)."""
    client = _build_mock_client()
    sup = SupervisorAgent(client, max_retries=1)
    result = await sup.run_project(
        project_id="gadsden_test",
        script_text=GADSDEN_SCRIPT,
        total_duration_sec=20.0,
    )
    for loc in result["resolved_locations"]:
        if loc.get("status") == "resolved":
            assert loc.get("provenance") is not None
            assert loc["provenance"]["provider"] == "mock"


@pytest.mark.asyncio
async def test_end_to_end_real_video_pipeline() -> None:
    """Full pipeline with real ffmpeg: script → scenes → text overlays → real MP4 → QA pass.

    This is the Phase 4 deliverable test: produces a real 1920x1080 H.264 MP4
    and verifies QA passes on it. Uses real ffmpeg (no stub).
    """
    import os
    from app.database import init_db
    init_db()  # ensure tables exist for DB persistence verification
    client = McpClient()  # real ffmpeg, real services
    sup = SupervisorAgent(client, max_retries=1)
    result = await sup.run_project(
        project_id="real_video_e2e",
        script_text=GADSDEN_SCRIPT,
        total_duration_sec=5.0,
    )
    # Pipeline should complete successfully.
    assert result["failed"] is False
    assert result["final_state"] == WorkflowStateEnum.COMPLETED.value
    # Render output should be a real file.
    render_output = result["results"]["render"]["output"]["output_path"]
    assert render_output is not None
    assert os.path.exists(render_output)
    assert os.path.getsize(render_output) > 1024
    # QA should pass.
    assert result["qa_report"]["passed"] is True
    # Verify DB persistence.
    from app.services.projects import ProjectService
    svc = ProjectService()
    jobs = svc.get_render_jobs("real_video_e2e")
    assert len(jobs) >= 1
    reports = svc.get_qa_reports("real_video_e2e")
    assert len(reports) >= 1
    assert reports[0].passed is True
    # Cleanup.
    if os.path.exists(render_output):
        os.remove(render_output)
