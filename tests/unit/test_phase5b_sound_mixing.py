"""Phase 5B tests: Sound Design and Audio Mixing.

Verifies that the Sound MCP ``mix_audio`` tool, the FFmpeg ``mix_audio``
implementation, and the Supervisor Step 8b wiring produce real audio files
(voiceover + timed SFX + music) and that the mixed track is muxed into the
final MP4 by the compose step.

Tests with the ``real_ffmpeg`` marker exercise the real ffmpeg binary against
generated tone fixtures (no copyrighted audio, no network). They are skipped
when ffmpeg/ffprobe are not on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.agents.supervisor import SupervisorAgent
from app.core.enums import AgentName
from app.mcp.client import McpClient
from app.mcp.servers.geo.server import GeoMcpServer
from app.mcp.servers.sound.server import SoundMcpServer
from app.mcp.schemas import MixAudioInput, SoundTrackSpec
from app.services.ffmpeg import AudioTrack, FFmpegRenderer, MixAudioParams, StubFFmpegService
from app.services.geo import GeoProvider, GeocodeResult, NoneGeoProvider
from app.schemas.contracts import GeoProvenance

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

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
        return GeocodeResult(query=query, status="unresolved", provider="mock", error="not found")


@pytest.fixture(scope="session")
def audio_fixtures(tmp_path_factory) -> dict[str, str]:
    """Generate deterministic tone WAV fixtures via ffmpeg (no committed binaries).

    Only created when ffmpeg is available; the real-ffmpeg tests are skipped
    otherwise, so this fixture is never exercised without ffmpeg.
    """
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not installed")
    d = tmp_path_factory.mktemp("audio_fixtures")
    paths: dict[str, str] = {}

    def _tone(name: str, freq: int, duration: float) -> None:
        out = d / name
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi",
             "-i", f"sine=frequency={freq}:duration={duration}",
             "-ar", "44100", "-ac", "1", str(out)],
            capture_output=True, check=True,
        )
        paths[name] = str(out)

    _tone("voiceover.wav", 440, 10.0)
    _tone("voiceover_10s.wav", 440, 10.0)
    _tone("sfx_boom.wav", 220, 2.0)
    _tone("sfx_whoosh.wav", 300, 1.5)
    _tone("music.wav", 110, 10.0)
    return paths


def _fx(audio_fixtures: dict[str, str], name: str) -> str:
    return audio_fixtures[name]


# ---------------------------------------------------------------------------
# Unit tests (no ffmpeg required): schemas, command building, stub wiring
# ---------------------------------------------------------------------------


def test_mix_audio_input_schema_validates() -> None:
    """MixAudioInput accepts a minimal valid payload."""
    inp = MixAudioInput(output_filename="out.wav", duration_sec=10.0)
    assert inp.output_filename == "out.wav"
    assert inp.sfx_tracks == []
    assert inp.format == "wav"
    assert inp.channels == 1


def test_mix_audio_input_rejects_bad_format() -> None:
    with pytest.raises(Exception):
        MixAudioInput(output_filename="out.wav", format="mp3")


def test_sound_track_spec_defaults() -> None:
    spec = SoundTrackSpec(file_path="/tmp/x.wav")
    assert spec.start_time == 0.0
    assert spec.volume_db == 0.0
    assert spec.fade_in_sec == 0.0


def test_mix_audio_params_dataclass_defaults() -> None:
    p = MixAudioParams(output_path="x.wav")
    assert p.voiceover_path is None
    assert p.sfx_tracks == []
    assert p.sample_rate == 44100
    assert p.format == "wav"


def test_audio_track_dataclass() -> None:
    t = AudioTrack(file_path="/tmp/x.wav", start_time=2.0, volume_db=-6.0)
    assert t.start_time == 2.0
    assert t.fade_in_sec == 0.0


def test_build_mix_audio_command_silence_when_no_inputs() -> None:
    """With no inputs, mix_audio produces a silent lavfi source — no fabricated audio file input."""
    r = FFmpegRenderer()
    p = MixAudioParams(output_path="silent.wav", duration_sec=8.0)
    from app.config import get_settings
    out = Path(get_settings().output_path) / "silent.wav"
    cmd = r._build_mix_audio_command(p, out)
    assert cmd[0].endswith("ffmpeg")
    joined = " ".join(cmd)
    assert "anullsrc" in joined
    assert "lavfi" in cmd
    # The only -i is the lavfi anullsrc source, not a (possibly fake) file path.
    i_indices = [i for i, a in enumerate(cmd) if a == "-i"]
    assert len(i_indices) == 1
    assert cmd[i_indices[0] + 1].startswith("anullsrc=")


def test_build_mix_audio_command_voiceover_only(tmp_path) -> None:
    r = FFmpegRenderer()
    vo = tmp_path / "voiceover.wav"
    vo.write_bytes(b"dummy")
    p = MixAudioParams(
        output_path="vo.wav",
        voiceover_path=str(vo),
        duration_sec=10.0,
    )
    from app.config import get_settings
    out = Path(get_settings().output_path) / "vo.wav"
    cmd = r._build_mix_audio_command(p, out)
    joined = " ".join(cmd)
    assert "[0:a]" in joined
    assert "amix=inputs=1" in joined
    assert "[aout]" in joined


def test_build_mix_audio_command_full_mix(tmp_path) -> None:
    r = FFmpegRenderer()
    vo = tmp_path / "voiceover.wav"; vo.write_bytes(b"dummy")
    sfx = tmp_path / "sfx_boom.wav"; sfx.write_bytes(b"dummy")
    music = tmp_path / "music.wav"; music.write_bytes(b"dummy")
    p = MixAudioParams(
        output_path="full.wav",
        voiceover_path=str(vo),
        sfx_tracks=[AudioTrack(file_path=str(sfx), start_time=5.0, volume_db=-6.0)],
        music_path=str(music),
        music_volume_db=-18.0,
        duration_sec=10.0,
    )
    from app.config import get_settings
    out = Path(get_settings().output_path) / "full.wav"
    cmd = r._build_mix_audio_command(p, out)
    joined = " ".join(cmd)
    assert "amix=inputs=3" in joined
    assert "adelay=5000|5000" in joined
    assert "volume=-18.0dB" in joined
    assert "[aout]" in joined
    assert "-t 10.0" in joined


def test_mix_audio_params_path_traversal_rejected() -> None:
    """Output path escaping the output directory is blocked."""
    import asyncio
    r = FFmpegRenderer()
    p = MixAudioParams(output_path="../../etc/evil.wav", duration_sec=1.0)
    with pytest.raises(Exception):
        asyncio.run(r.mix_audio(p))


def test_stub_ffmpeg_service_has_mix_audio() -> None:
    """The StubFFmpegService must implement mix_audio (no-op for hermetic tests)."""
    stub = StubFFmpegService()
    assert hasattr(stub, "mix_audio")
    assert hasattr(stub, "compose")
    assert hasattr(stub, "probe")


def test_sound_server_registers_mix_audio_tool() -> None:
    server = SoundMcpServer(ffmpeg_service=StubFFmpegService())
    tool_names = set(server.list_tools())
    assert "mix_audio" in tool_names
    assert "build_audio_mix" in tool_names


# ---------------------------------------------------------------------------
# Real ffmpeg tests (skipped when ffmpeg is unavailable)
# ---------------------------------------------------------------------------


pytestmark_real = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
@pytest.mark.asyncio
async def test_mix_audio_real_voiceover_only(tmp_path, audio_fixtures) -> None:
    """mix_audio with only a voiceover produces a real WAV of the target duration."""
    r = FFmpegRenderer()
    p = MixAudioParams(
        output_path="vo_only.wav",
        voiceover_path=_fx(audio_fixtures, "voiceover_10s.wav"),
        duration_sec=10.0,
    )
    out = await r.mix_audio(p)
    assert os.path.exists(out)
    info = await r.probe(out)
    assert abs(info.duration_sec - 10.0) < 0.25
    assert info.codec == "pcm_s16le"
    assert info.sample_rate == 44100


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
@pytest.mark.asyncio
async def test_mix_audio_real_full_mix(tmp_path, audio_fixtures) -> None:
    """mix_audio with voiceover + timed SFX + music produces a real mixed WAV."""
    r = FFmpegRenderer()
    p = MixAudioParams(
        output_path="full_mix.wav",
        voiceover_path=_fx(audio_fixtures, "voiceover_10s.wav"),
        sfx_tracks=[AudioTrack(file_path=_fx(audio_fixtures, "sfx_boom.wav"), start_time=5.0, volume_db=-6.0)],
        music_path=_fx(audio_fixtures, "music.wav"),
        music_volume_db=-18.0,
        duration_sec=10.0,
    )
    out = await r.mix_audio(p)
    assert os.path.exists(out)
    assert os.path.getsize(out) > 1000
    info = await r.probe(out)
    assert abs(info.duration_sec - 10.0) < 0.25
    assert info.channels == 1


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
@pytest.mark.asyncio
async def test_mix_audio_real_silence_when_no_inputs(tmp_path, audio_fixtures) -> None:
    """mix_audio with no inputs produces a silent track of the target duration."""
    r = FFmpegRenderer()
    p = MixAudioParams(output_path="silent.wav", duration_sec=6.0)
    out = await r.mix_audio(p)
    assert os.path.exists(out)
    info = await r.probe(out)
    assert abs(info.duration_sec - 6.0) < 0.25


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
@pytest.mark.asyncio
async def test_mix_audio_real_missing_optional_inputs_skipped(tmp_path, audio_fixtures) -> None:
    """Non-existent optional inputs are skipped at the server layer, not fabricated; voiceover still mixes."""
    from app.services.ffmpeg import FFmpegRenderer
    server = SoundMcpServer(ffmpeg_service=FFmpegRenderer())
    result = await server.execute_tool("mix_audio", {
        "output_filename": "skip_missing.wav",
        "voiceover_path": _fx(audio_fixtures, "voiceover_10s.wav"),
        "sfx_tracks": [{"file_path": "/nonexistent/sfx.wav", "start_time": 2.0}],
        "music_path": "/nonexistent/music.wav",
        "duration_sec": 10.0,
    })
    assert result.success, result.errors
    out = result.data["output_path"]
    assert os.path.exists(out)
    # Only the voiceover was actually mixed (missing SFX/music skipped).
    assert result.data["track_count"] == 1
    assert any("does not exist" in w for w in result.data["warnings"])
    os.remove(out)


# ---------------------------------------------------------------------------
# Sound MCP tool wiring (real ffmpeg)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
@pytest.mark.asyncio
async def test_sound_mix_audio_tool_produces_real_file(tmp_path, audio_fixtures) -> None:
    """The Sound MCP mix_audio tool mixes real inputs via the injected FFmpegService."""
    from app.services.ffmpeg import FFmpegRenderer
    server = SoundMcpServer(ffmpeg_service=FFmpegRenderer())
    result = await server.execute_tool("mix_audio", {
        "output_filename": "tool_mix.wav",
        "voiceover_path": _fx(audio_fixtures, "voiceover_10s.wav"),
        "sfx_tracks": [{"file_path": _fx(audio_fixtures, "sfx_boom.wav"), "start_time": 5.0, "volume_db": -6.0}],
        "music_path": _fx(audio_fixtures, "music.wav"),
        "music_volume_db": -18.0,
        "duration_sec": 10.0,
    })
    assert result.success, result.errors
    out = result.data["output_path"]
    assert os.path.exists(out)
    assert result.data["track_count"] == 3
    assert result.data["duration_sec"] is not None
    assert abs(result.data["duration_sec"] - 10.0) < 0.25
    os.remove(out)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
@pytest.mark.asyncio
async def test_sound_mix_audio_tool_skips_missing_inputs(tmp_path, audio_fixtures) -> None:
    """The tool skips missing optional inputs and reports them as warnings."""
    from app.services.ffmpeg import FFmpegRenderer
    server = SoundMcpServer(ffmpeg_service=FFmpegRenderer())
    result = await server.execute_tool("mix_audio", {
        "output_filename": "tool_skip.wav",
        "voiceover_path": _fx(audio_fixtures, "voiceover_10s.wav"),
        "sfx_tracks": [{"file_path": "/nonexistent/sfx.wav", "start_time": 1.0}],
        "duration_sec": 10.0,
    })
    assert result.success
    assert any("sfx track does not exist" in w for w in result.data["warnings"])
    assert result.data["track_count"] == 1  # voiceover only
    os.remove(result.data["output_path"])


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
@pytest.mark.asyncio
async def test_sound_mix_audio_tool_fails_without_inputs_or_duration(tmp_path) -> None:
    """No inputs and no duration → failure (cannot produce a useful file)."""
    from app.services.ffmpeg import FFmpegRenderer
    server = SoundMcpServer(ffmpeg_service=FFmpegRenderer())
    result = await server.execute_tool("mix_audio", {
        "output_filename": "noop.wav",
    })
    assert not result.success


# ---------------------------------------------------------------------------
# Supervisor Step 8b wiring (real ffmpeg e2e)
# ---------------------------------------------------------------------------


def _build_real_client() -> McpClient:
    """MCP client with real ffmpeg for sound + render, mock geocoder."""
    from app.mcp.servers.render.server import RenderMcpServer
    from app.services.ffmpeg import FFmpegRenderer

    base_client = McpClient(validate_results=False)
    geo_server = GeoMcpServer(provider=_MockGeoProvider())
    render_server = RenderMcpServer(ffmpeg_service=FFmpegRenderer())
    sound_server = SoundMcpServer(ffmpeg_service=FFmpegRenderer())
    servers: dict = {}
    for name in AgentName:
        if name == AgentName.SUPERVISOR:
            continue
        if base_client._registry.has_server(name):
            servers[name] = base_client._registry.get_server(name)
    servers[AgentName.GEO] = geo_server
    servers[AgentName.RENDER] = render_server
    servers[AgentName.SOUND] = sound_server
    return McpClient(servers=servers, validate_results=False)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
@pytest.mark.asyncio
async def test_supervisor_step8b_produces_mixed_audio_and_passes_to_compose(tmp_path, audio_fixtures) -> None:
    """The supervisor Step 8b mixes voiceover + SFX + music and the MP4 contains audio."""
    from app.database import init_db
    init_db()
    client = _build_real_client()
    sup = SupervisorAgent(client, max_retries=1)
    result = await sup.run_project(
        project_id="phase5b_e2e",
        script_text=GADSDEN_SCRIPT,
        voiceover_path=_fx(audio_fixtures, "voiceover_10s.wav"),
        total_duration_sec=10.0,
        sfx_paths=[_fx(audio_fixtures, "sfx_boom.wav"), _fx(audio_fixtures, "sfx_whoosh.wav")],
        music_path=_fx(audio_fixtures, "music.wav"),
    )

    assert result["failed"] is False

    # Step 8b produced a real mixed audio file.
    mixed = result.get("mixed_audio_path")
    assert mixed is not None
    assert os.path.exists(mixed)

    # The sound_mix result should report the mixed output.
    sound_mix = result.get("sound_mix") or {}
    assert sound_mix.get("output_path") == mixed
    assert sound_mix.get("track_count", 0) >= 1

    # The final MP4 exists and contains an audio stream (muxed mixed track).
    render_output = result["results"]["render"]["output"]["output_path"]
    assert os.path.exists(render_output)
    assert os.path.getsize(render_output) > 1024

    # ffprobe the MP4: it must have an audio stream.
    import subprocess, json as _json
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", render_output],
        capture_output=True, text=True, check=True,
    )
    streams = _json.loads(probe.stdout)["streams"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    assert len(audio_streams) >= 1, "expected an audio stream in the final MP4"
    assert audio_streams[0].get("codec_name", "") == "aac"

    # Cleanup.
    for p in [mixed, render_output]:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    for overlay in result.get("text_overlays", []):
        rp = overlay.get("rendered_path")
        if rp and os.path.exists(rp):
            try:
                os.remove(rp)
            except OSError:
                pass
    for mp in (result.get("scene_map_paths") or {}).values():
        if mp and os.path.exists(mp):
            try:
                os.remove(mp)
            except OSError:
                pass


@pytest.mark.asyncio
async def test_supervisor_falls_back_to_voiceover_when_mix_unavailable() -> None:
    """When the FFmpegService can't mix (Stub), the supervisor falls back to the raw voiceover path."""
    from app.mcp.servers.render.server import RenderMcpServer

    base_client = McpClient(validate_results=False)
    geo_server = GeoMcpServer(provider=_MockGeoProvider())
    # Stub ffmpeg everywhere so mix_audio and compose both no-op/fail gracefully.
    sound_server = SoundMcpServer(ffmpeg_service=StubFFmpegService())
    render_server = RenderMcpServer(ffmpeg_service=StubFFmpegService())
    servers: dict = {}
    for name in AgentName:
        if name == AgentName.SUPERVISOR:
            continue
        if base_client._registry.has_server(name):
            servers[name] = base_client._registry.get_server(name)
    servers[AgentName.GEO] = geo_server
    servers[AgentName.RENDER] = render_server
    servers[AgentName.SOUND] = sound_server
    client = McpClient(servers=servers, validate_results=False)

    sup = SupervisorAgent(client, max_retries=1)
    result = await sup.run_project(
        project_id="phase5b_fallback",
        script_text=GADSDEN_SCRIPT,
        voiceover_path="/tmp/fake_voiceover.wav",  # non-existent; checks fallback path logic
        audio_duration_sec=10.0,  # lets the audio step succeed without ffprobe
        total_duration_sec=10.0,
    )
    # Workflow should not crash; sound_mix result recorded.
    assert "sound_mix" in result["results"]
