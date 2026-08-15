"""Tests for the FFmpeg renderer service: safe subprocess, path validation,
shell-injection prevention."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from app.config import Settings, get_settings
from app.core.exceptions import AppError, FileSafetyError, RenderError
from app.services.ffmpeg import (
    FFmpegRenderer,
    FFmpegService,
    MediaInfo,
    RenderJobParams,
    StubFFmpegService,
    get_ffmpeg_service,
)


def test_stub_service_refuses_probe() -> None:
    stub = StubFFmpegService()
    import asyncio
    with pytest.raises(AppError, match="not available"):
        asyncio.run(stub.probe("foo.wav"))


def test_stub_service_refuses_render() -> None:
    stub = StubFFmpegService()
    import asyncio
    with pytest.raises(AppError, match="not available"):
        asyncio.run(stub.render(RenderJobParams(output_path="foo.mp4")))


def test_get_ffmpeg_service_returns_stub_or_renderer() -> None:
    service = get_ffmpeg_service()
    assert isinstance(service, (FFmpegRenderer, StubFFmpegService))


def test_render_job_params_defaults() -> None:
    params = RenderJobParams(output_path="test.mp4")
    assert params.width == 1920
    assert params.height == 1080
    assert params.fps == 30.0
    assert params.format == "mp4"


def test_renderer_validates_output_path_traversal(tmp_path) -> None:
    settings = Settings(output_dir=str(tmp_path), data_dir=str(tmp_path), assets_dir=str(tmp_path), logs_dir=str(tmp_path))
    renderer = FFmpegRenderer(settings, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe")
    params = RenderJobParams(output_path="../../../etc/passwd")
    import asyncio
    with pytest.raises(FileSafetyError, match="traversal|outside approved"):
        asyncio.run(renderer.render(params))


def test_renderer_rejects_unsafe_args(tmp_path) -> None:
    settings = Settings(output_dir=str(tmp_path), data_dir=str(tmp_path), assets_dir=str(tmp_path), logs_dir=str(tmp_path))
    renderer = FFmpegRenderer(settings, ffmpeg_bin="echo", ffprobe_bin="ffprobe")
    params = RenderJobParams(
        output_path="safe.mp4",
        extra_args=["; rm -rf /"],  # shell injection attempt
    )
    import asyncio
    with pytest.raises(RenderError, match="unsafe ffmpeg argument"):
        asyncio.run(renderer.render(params))


def test_renderer_is_safe_arg_rejects_metachars() -> None:
    assert not FFmpegRenderer._is_safe_arg("; rm -rf /")
    assert not FFmpegRenderer._is_safe_arg("$(whoami)")
    assert not FFmpegRenderer._is_safe_arg("`cat /etc/passwd`")
    assert not FFmpegRenderer._is_safe_arg("")
    assert FFmpegRenderer._is_safe_arg("-preset")
    assert FFmpegRenderer._is_safe_arg("fast")
    assert FFmpegRenderer._is_safe_arg("libx264")


def test_renderer_output_restricted_to_output_dir(tmp_path) -> None:
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    settings = Settings(
        output_dir=str(out_dir), data_dir=str(tmp_path),
        assets_dir=str(tmp_path), logs_dir=str(tmp_path),
    )
    renderer = FFmpegRenderer(settings, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe")
    # Output path trying to escape output dir via absolute path.
    params = RenderJobParams(output_path=str(other_dir / "hack.mp4"))
    import asyncio
    with pytest.raises(FileSafetyError):
        asyncio.run(renderer.render(params))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_renderer_real_render(tmp_path) -> None:
    """If ffmpeg is installed, actually render a 1-second test video."""
    import asyncio
    settings = Settings(
        output_dir=str(tmp_path), data_dir=str(tmp_path),
        assets_dir=str(tmp_path), logs_dir=str(tmp_path),
    )
    renderer = FFmpegRenderer(settings)
    params = RenderJobParams(output_path="test_output.mp4", duration_sec=1.0)
    output = asyncio.run(renderer.render(params))
    assert Path(output).exists()
    assert Path(output).stat().st_size > 0


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")
def test_renderer_real_probe(tmp_path) -> None:
    """If ffprobe is installed, probe a generated test file."""
    import asyncio
    settings = Settings(
        output_dir=str(tmp_path), data_dir=str(tmp_path),
        assets_dir=str(tmp_path), logs_dir=str(tmp_path),
    )
    renderer = FFmpegRenderer(settings)
    # First render, then probe.
    params = RenderJobParams(output_path="probe_target.mp4", duration_sec=1.0)
    output = asyncio.run(renderer.render(params))
    info = asyncio.run(renderer.probe(output))
    assert info.duration_sec is not None
    assert info.duration_sec > 0
