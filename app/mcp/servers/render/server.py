"""Render MCP server.

Manages video render jobs via the :class:`~app.services.ffmpeg.FFmpegRenderer`.
Security:
- Safe subprocess execution (no ``shell=True``).
- Input paths validated against traversal.
- Output path restricted to the project output directory.
- Render status tracked in-memory (persistable to DB in future).

Tools:
- ``create_render_job`` — create and queue a render job
- ``validate_render_job`` — validate job params before rendering
- ``render_video`` — execute the render
- ``get_render_status`` — query job status

Legacy tools (backward compat):
- ``render_video`` — now uses the new schema (requires job_id)
- ``probe_media`` — probes media via ffprobe
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName, RenderJobStatus
from app.core.exceptions import AppError, RenderError
from app.core.logging import get_logger, log_event
from app.core.result import Result
from app.mcp.schemas import (
    ComposeVideoInput,
    ComposeVideoOutput,
    CreateRenderJobInput,
    CreateRenderJobOutput,
    GetRenderStatusInput,
    GetRenderStatusOutput,
    RenderVideoInput,
    RenderVideoOutput,
    ValidateRenderJobInput,
    ValidateRenderJobOutput,
)
from app.mcp.servers.base import BaseMcpServer, ToolDefinition
from app.services.ffmpeg import (
    ComposeVideoParams,
    FFmpegService,
    OverlayLayer,
    RenderJobParams,
    get_ffmpeg_service,
)
from app.utils.ids import new_id

logger = get_logger("mcp.render")


class RenderMcpServer(BaseMcpServer):
    """Renders the final video from project/timeline data via FFmpeg."""

    name = AgentName.RENDER
    version = "4.0.0"
    description = "Creates, composes, and executes render jobs via safe FFmpeg subprocess."

    def __init__(self, ffmpeg_service: FFmpegService | None = None) -> None:
        super().__init__()
        self.ffmpeg = ffmpeg_service or get_ffmpeg_service()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._register_tool(ToolDefinition(
            name="create_render_job",
            description="Create and queue a render job with validated output path.",
            input_schema=CreateRenderJobInput,
            output_schema=CreateRenderJobOutput,
            handler=self._create_render_job,
            tags={"write"},
        ))
        self._register_tool(ToolDefinition(
            name="validate_render_job",
            description="Validate render job parameters (paths, dimensions, fps).",
            input_schema=ValidateRenderJobInput,
            output_schema=ValidateRenderJobOutput,
            handler=self._validate_render_job,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="render_video",
            description="Execute a queued render job via FFmpeg.",
            input_schema=RenderVideoInput,
            output_schema=RenderVideoOutput,
            handler=self._render_video,
            tags={"write"},
        ))
        self._register_tool(ToolDefinition(
            name="compose_video",
            description="Compose a video from background + image overlays + audio via FFmpeg filter_complex.",
            input_schema=ComposeVideoInput,
            output_schema=ComposeVideoOutput,
            handler=self._compose_video,
            tags={"write"},
        ))
        self._register_tool(ToolDefinition(
            name="get_render_status",
            description="Query the status of a render job.",
            input_schema=GetRenderStatusInput,
            output_schema=GetRenderStatusOutput,
            handler=self._get_render_status,
            tags={"read"},
        ))

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        if tool == "probe_media":
            return await self._probe_media(arguments)
        return await self.execute_tool(tool, arguments)

    # --- tool handlers ------------------------------------------------------

    async def _create_render_job(self, inp: CreateRenderJobInput) -> Result[CreateRenderJobOutput]:
        job_id = new_id("render_")
        # Build the output path relative to the output directory.
        output_path = inp.output_filename
        # Ensure it has the right extension.
        if not output_path.lower().endswith(f".{inp.format}"):
            output_path = f"{output_path}.{inp.format}"

        params = {
            "project_id": inp.project_id,
            "output_filename": output_path,
            "width": inp.width,
            "height": inp.height,
            "fps": inp.fps,
            "duration_sec": inp.duration_sec,
            "audio_path": inp.audio_path,
            "format": inp.format,
        }
        job = {
            "job_id": job_id,
            "project_id": inp.project_id,
            "status": RenderJobStatus.QUEUED.value,
            "output_path": output_path,
            "params": params,
            "error": None,
        }
        self._jobs[job_id] = job
        log_event(logger, "render_job.created", job_id=job_id, project_id=inp.project_id)
        return Result.ok(CreateRenderJobOutput(
            job_id=job_id,
            project_id=inp.project_id,
            status=RenderJobStatus.QUEUED.value,
            output_path=output_path,
            params=params,
        ))

    async def _validate_render_job(self, inp: ValidateRenderJobInput) -> Result[ValidateRenderJobOutput]:
        errors: list[str] = []
        warnings: list[str] = []
        # Check output path safety.
        if ".." in inp.output_path or "\x00" in inp.output_path:
            errors.append(f"output path contains traversal or control chars: {inp.output_path}")
        if inp.width < 1 or inp.height < 1:
            errors.append(f"invalid dimensions: {inp.width}x{inp.height}")
        if inp.fps <= 0 or inp.fps > 120:
            errors.append(f"fps must be in (0, 120]; got {inp.fps}")
        if inp.width != 1920 or inp.height != 1080:
            warnings.append(f"non-standard resolution {inp.width}x{inp.height}; 1920x1080 recommended")
        return Result.ok(ValidateRenderJobOutput(
            valid=not errors,
            output_path=inp.output_path,
            errors=errors,
            warnings=warnings,
        ))

    async def _render_video(self, inp: RenderVideoInput) -> Result[RenderVideoOutput]:
        job = self._jobs.get(inp.job_id)
        if job is None:
            return Result.fail(f"render job not found: {inp.job_id}")

        # Mark as running.
        job["status"] = RenderJobStatus.RUNNING.value
        params = job["params"]
        log_event(logger, "render_job.started", job_id=inp.job_id)

        try:
            render_params = RenderJobParams(
                output_path=params["output_filename"],
                width=params["width"],
                height=params["height"],
                fps=params["fps"],
                format=params["format"],
                duration_sec=params.get("duration_sec"),
                audio_path=params.get("audio_path"),
            )
            output_path = await self.ffmpeg.render(render_params)
            job["status"] = RenderJobStatus.COMPLETED.value
            job["output_path"] = output_path
            log_event(logger, "render_job.completed", job_id=inp.job_id, output=output_path)
            return Result.ok(RenderVideoOutput(
                job_id=inp.job_id,
                status=RenderJobStatus.COMPLETED.value,
                output_path=output_path,
            ))
        except (AppError, RenderError) as exc:
            job["status"] = RenderJobStatus.FAILED.value
            job["error"] = str(exc)
            log_event(logger, "render_job.failed", job_id=inp.job_id, error=str(exc))
            return Result.fail(f"render failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            job["status"] = RenderJobStatus.FAILED.value
            job["error"] = str(exc)
            log_event(logger, "render_job.failed", job_id=inp.job_id, error=str(exc))
            return Result.fail(f"render failed unexpectedly: {exc}")

    async def _get_render_status(self, inp: GetRenderStatusInput) -> Result[GetRenderStatusOutput]:
        job = self._jobs.get(inp.job_id)
        if job is None:
            return Result.fail(f"render job not found: {inp.job_id}")
        return Result.ok(GetRenderStatusOutput(
            job_id=inp.job_id,
            status=job["status"],
            output_path=job.get("output_path"),
            error=job.get("error"),
        ))

    async def _compose_video(self, inp: ComposeVideoInput) -> Result[ComposeVideoOutput]:
        """Compose a video from background + overlays + audio via FFmpeg."""
        job_id = new_id("compose_")
        warnings: list[str] = []
        output_filename = inp.output_filename
        if not output_filename.lower().endswith(f".{inp.format}"):
            output_filename = f"{output_filename}.{inp.format}"

        # Build overlay layers.
        overlays: list[OverlayLayer] = []
        if inp.overlays:
            for ov in inp.overlays:
                try:
                    overlays.append(OverlayLayer(
                        image_path=ov["image_path"],
                        x=float(ov.get("x", 0.0)),
                        y=float(ov.get("y", 0.0)),
                        start_time=float(ov.get("start_time", 0.0)),
                        end_time=float(ov["end_time"]) if ov.get("end_time") is not None else None,
                        opacity=float(ov.get("opacity", 1.0)),
                    ))
                except (KeyError, TypeError, ValueError) as exc:
                    return Result.fail(f"invalid overlay: {exc}")

        bg_color = inp.background_color if inp.background_color.startswith("#") else f"#{inp.background_color}"

        job = {
            "job_id": job_id,
            "project_id": inp.project_id,
            "status": RenderJobStatus.RUNNING.value,
            "output_path": output_filename,
            "error": None,
        }
        self._jobs[job_id] = job
        log_event(logger, "compose.started", job_id=job_id, overlays=len(overlays))

        try:
            params = ComposeVideoParams(
                output_path=output_filename,
                width=inp.width,
                height=inp.height,
                fps=inp.fps,
                duration_sec=inp.duration_sec,
                background_color=bg_color,
                background_image=inp.background_image,
                overlays=overlays,
                audio_path=inp.audio_path,
                video_filter=inp.video_filter,
                format=inp.format,
            )
            output_path = await self.ffmpeg.compose(params)
            job["status"] = RenderJobStatus.COMPLETED.value
            job["output_path"] = output_path
            log_event(logger, "compose.completed", job_id=job_id, output=output_path)
            return Result.ok(ComposeVideoOutput(
                job_id=job_id,
                status=RenderJobStatus.COMPLETED.value,
                output_path=output_path,
                duration_sec=inp.duration_sec,
                warnings=warnings,
            ))
        except (AppError, RenderError) as exc:
            job["status"] = RenderJobStatus.FAILED.value
            job["error"] = str(exc)
            log_event(logger, "compose.failed", job_id=job_id, error=str(exc))
            return Result.fail(f"compose failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            job["status"] = RenderJobStatus.FAILED.value
            job["error"] = str(exc)
            log_event(logger, "compose.failed", job_id=job_id, error=str(exc))
            return Result.fail(f"compose failed unexpectedly: {exc}")

    # --- legacy -------------------------------------------------------------

    async def _probe_media(self, arguments: dict[str, Any]) -> Result[dict]:
        """Probe a media file via ffprobe."""
        path = arguments.get("file_path", "")
        if not path or not str(path).strip():
            return self._fail("file_path is required")
        try:
            info = await self.ffmpeg.probe(path)
            return self._ok({
                "file_path": info.file_path,
                "format": info.format,
                "duration_sec": info.duration_sec,
                "width": info.width,
                "height": info.height,
                "sample_rate": info.sample_rate,
                "channels": info.channels,
                "codec": info.codec,
            })
        except AppError as exc:
            return self._fail(f"ffprobe failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"probe failed: {exc}")
