"""Pydantic input/output schemas for MCP tool definitions.

These models are used by :class:`~app.mcp.servers.base.ToolDefinition` to
validate tool inputs and outputs. They are kept separate from the data
contracts in ``app.schemas.contracts`` because tool I/O schemas are the
API boundary of the MCP protocol, while contracts are the internal pipeline
representation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import AssetFormat, AssetType, ProvenanceType
from app.schemas.contracts import Contract

# Reuse the strict config from contracts.
ToolModelConfig = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ToolInput(BaseModel):
    """Base for all tool input schemas."""
    model_config = ToolModelConfig


class ToolOutput(BaseModel):
    """Base for all tool output schemas."""
    model_config = ToolModelConfig


# === Script MCP ===


class AnalyzeScriptInput(ToolInput):
    script_text: str = Field(min_length=1, description="Documentary script text")
    total_duration_sec: float = Field(gt=0.0, description="Target total video duration")
    project_id: str = Field(default="proj", pattern=r"^[A-Za-z0-9_\-]{1,64}$")


class AnalyzeScriptOutput(ToolOutput):
    scenes: list[dict[str, Any]] = Field(default_factory=list)
    entities: dict[str, Any] = Field(default_factory=dict)
    locations: list[dict[str, Any]] = Field(default_factory=list)
    requirements: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class SplitIntoScenesInput(ToolInput):
    script_text: str = Field(min_length=1)
    total_duration_sec: float = Field(gt=0.0)
    project_id: str = Field(default="proj", pattern=r"^[A-Za-z0-9_\-]{1,64}$")


class SplitIntoScenesOutput(ToolOutput):
    scenes: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExtractEntitiesInput(ToolInput):
    script_text: str = Field(min_length=1)


class ExtractEntitiesOutput(ToolOutput):
    locations: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExtractLocationsInput(ToolInput):
    script_text: str = Field(min_length=1)


class ExtractLocationsOutput(ToolOutput):
    locations: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# === Audio MCP ===


class InspectAudioInput(ToolInput):
    file_path: str = Field(min_length=1)
    duration_sec: float | None = Field(default=None, gt=0.0)


class InspectAudioOutput(ToolOutput):
    file_path: str
    duration_sec: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    codec: str | None = None
    format: str | None = None
    file_size: int | None = None
    warnings: list[str] = Field(default_factory=list)


class CreateAudioTimelineInput(ToolInput):
    duration_sec: float = Field(gt=0.0)
    scene_count: int = Field(gt=0, le=500)
    silence_segments: list[dict[str, Any]] = Field(default_factory=list)


class CreateAudioTimelineOutput(ToolOutput):
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    total_duration_sec: float


class DetectSilenceInput(ToolInput):
    file_path: str = Field(min_length=1)
    min_silence_sec: float = Field(default=0.5, gt=0.0, le=10.0)


class DetectSilenceOutput(ToolOutput):
    file_path: str
    silence_segments: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# === Geo MCP ===


class GeocodeLocationInput(ToolInput):
    query: str = Field(min_length=1, max_length=500)


class GeocodeLocationOutput(ToolOutput):
    query: str
    status: str
    latitude: float | None = None
    longitude: float | None = None
    display_name: str | None = None
    confidence: float = 0.0
    provider: str = ""
    provenance: dict[str, Any] | None = None
    error: str | None = None


class BatchGeocodeInput(ToolInput):
    queries: list[str] = Field(min_length=1, max_length=100)


class BatchGeocodeOutput(ToolOutput):
    results: list[dict[str, Any]] = Field(default_factory=list)
    resolved: int = 0
    unresolved: int = 0


class ValidateCoordinatesInput(ToolInput):
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)


class ValidateCoordinatesOutput(ToolOutput):
    valid: bool
    latitude: float
    longitude: float
    warnings: list[str] = Field(default_factory=list)


class ReverseGeocodeInput(ToolInput):
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)


class ReverseGeocodeOutput(ToolOutput):
    status: str
    display_name: str | None = None
    provider: str = ""
    error: str | None = None


# === Assets MCP ===


class RegisterAssetInput(ToolInput):
    name: str = Field(min_length=1, max_length=255)
    asset_type: AssetType
    format: AssetFormat
    file_path: str = Field(min_length=1, max_length=512)
    source: str = Field(min_length=1, max_length=128)
    license: str | None = None
    metadata: dict[str, Any] | None = None


class RegisterAssetOutput(ToolOutput):
    asset_id: str
    name: str
    asset_type: str
    format: str
    file_path: str
    source: str
    license: str | None = None
    registered: bool = True


class GetAssetInput(ToolInput):
    asset_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_\-]{1,64}$")


class GetAssetOutput(ToolOutput):
    asset: dict[str, Any] | None = None
    found: bool = True


class ListAssetsInput(ToolInput):
    asset_type: AssetType | None = None


class ListAssetsOutput(ToolOutput):
    assets: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


class ValidateAssetInput(ToolInput):
    file_path: str = Field(min_length=1)
    asset_type: AssetType | None = None


class ValidateAssetOutput(ToolOutput):
    valid: bool
    file_path: str
    format: str | None = None
    errors: list[str] = Field(default_factory=list)


class FindAssetInput(ToolInput):
    query: str = Field(min_length=1, max_length=255)
    asset_type: AssetType | None = None


class FindAssetOutput(ToolOutput):
    assets: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


# === Text MCP ===


class CreateTextOverlayInput(ToolInput):
    scene_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    kind: str = Field(description="title|subtitle|lower_third|location_label|date_label|annotation|historical_label")
    text: str = Field(min_length=1, max_length=500)
    start_time: float = Field(ge=0.0)
    end_time: float = Field(ge=0.0)
    x: float = Field(default=0.1, ge=0.0, le=1.0)
    y: float = Field(default=0.1, ge=0.0, le=1.0)
    font_size: int = Field(default=48, ge=8, le=200)
    color: str = Field(default="#FFFFFF", pattern=r"^#?[0-9A-Fa-f]{6}$")
    animation: str = Field(default="none")


class CreateTextOverlayOutput(ToolOutput):
    overlay: dict[str, Any]
    safe_zone: bool = True
    warnings: list[str] = Field(default_factory=list)


# === Transitions MCP ===


class CreateTransitionInput(ToolInput):
    from_scene_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    to_scene_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    kind: str = Field(description="fade|dissolve|zoom|slide|wipe|map_zoom|map_to_map")
    duration_sec: float = Field(default=0.5, gt=0.0, le=5.0)
    start_time: float = Field(default=0.0, ge=0.0)


class CreateTransitionOutput(ToolOutput):
    transition: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)


# === Sound MCP ===


class CreateSoundEventInput(ToolInput):
    scene_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    asset_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    kind: str = Field(description="whoosh|impact|riser|ambience|historical_atmosphere|transition|music")
    start_time: float = Field(ge=0.0)
    duration_sec: float = Field(gt=0.0, le=600.0)
    volume_db: float = Field(default=0.0, ge=-60.0, le=12.0)
    fade_in_sec: float = Field(default=0.0, ge=0.0, le=10.0)
    fade_out_sec: float = Field(default=0.0, ge=0.0, le=10.0)


class CreateSoundEventOutput(ToolOutput):
    sound_event: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)


class CreateSoundDesignPlanInput(ToolInput):
    scenes: list[dict[str, Any]] = Field(min_length=1)
    total_duration_sec: float = Field(gt=0.0)


class CreateSoundDesignPlanOutput(ToolOutput):
    events: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ValidateSoundEventInput(ToolInput):
    asset_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    start_time: float = Field(ge=0.0)
    duration_sec: float = Field(gt=0.0)


class ValidateSoundEventOutput(ToolOutput):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# === Render MCP ===


class CreateRenderJobInput(ToolInput):
    project_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    output_filename: str = Field(min_length=1, max_length=255)
    width: int = Field(default=1920, ge=1, le=7680)
    height: int = Field(default=1080, ge=1, le=4320)
    fps: float = Field(default=30.0, gt=0.0, le=120.0)
    duration_sec: float | None = Field(default=None, gt=0.0)
    audio_path: str | None = None
    format: str = Field(default="mp4")


class CreateRenderJobOutput(ToolOutput):
    job_id: str
    project_id: str
    status: str = "queued"
    output_path: str
    params: dict[str, Any]


class ValidateRenderJobInput(ToolInput):
    project_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    output_path: str = Field(min_length=1)
    width: int = Field(default=1920, ge=1)
    height: int = Field(default=1080, ge=1)
    fps: float = Field(default=30.0, gt=0.0, le=120.0)


class ValidateRenderJobOutput(ToolOutput):
    valid: bool
    output_path: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RenderVideoInput(ToolInput):
    job_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")


class RenderVideoOutput(ToolOutput):
    job_id: str
    status: str
    output_path: str | None = None
    error: str | None = None


class GetRenderStatusInput(ToolInput):
    job_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")


class GetRenderStatusOutput(ToolOutput):
    job_id: str
    status: str
    output_path: str | None = None
    error: str | None = None


# === QA MCP ===


class ValidateProjectInput(ToolInput):
    project_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    scenes: list[dict[str, Any]] = Field(default_factory=list)
    timeline_events: list[dict[str, Any]] = Field(default_factory=list)
    audio_duration_sec: float | None = None
    video_duration_sec: float | None = None


class ValidateProjectOutput(ToolOutput):
    passed: bool
    findings: list[dict[str, Any]] = Field(default_factory=list)
    summary: str
    checked_at: str


class CreateQaReportInput(ToolInput):
    project_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    scenes: list[dict[str, Any]] = Field(default_factory=list)
    timeline_events: list[dict[str, Any]] = Field(default_factory=list)
    audio_duration_sec: float | None = None
    video_duration_sec: float | None = None
    render_output_path: str | None = None


class CreateQaReportOutput(ToolOutput):
    report: dict[str, Any]
    passed: bool
    findings: list[dict[str, Any]] = Field(default_factory=list)
    summary: str


__all__ = [
    # Script
    "AnalyzeScriptInput", "AnalyzeScriptOutput",
    "SplitIntoScenesInput", "SplitIntoScenesOutput",
    "ExtractEntitiesInput", "ExtractEntitiesOutput",
    "ExtractLocationsInput", "ExtractLocationsOutput",
    # Audio
    "InspectAudioInput", "InspectAudioOutput",
    "CreateAudioTimelineInput", "CreateAudioTimelineOutput",
    "DetectSilenceInput", "DetectSilenceOutput",
    # Geo
    "GeocodeLocationInput", "GeocodeLocationOutput",
    "BatchGeocodeInput", "BatchGeocodeOutput",
    "ValidateCoordinatesInput", "ValidateCoordinatesOutput",
    "ReverseGeocodeInput", "ReverseGeocodeOutput",
    # Assets
    "RegisterAssetInput", "RegisterAssetOutput",
    "GetAssetInput", "GetAssetOutput",
    "ListAssetsInput", "ListAssetsOutput",
    "ValidateAssetInput", "ValidateAssetOutput",
    "FindAssetInput", "FindAssetOutput",
    # Text
    "CreateTextOverlayInput", "CreateTextOverlayOutput",
    # Transitions
    "CreateTransitionInput", "CreateTransitionOutput",
    # Sound
    "CreateSoundEventInput", "CreateSoundEventOutput",
    "CreateSoundDesignPlanInput", "CreateSoundDesignPlanOutput",
    "ValidateSoundEventInput", "ValidateSoundEventOutput",
    # Render
    "CreateRenderJobInput", "CreateRenderJobOutput",
    "ValidateRenderJobInput", "ValidateRenderJobOutput",
    "RenderVideoInput", "RenderVideoOutput",
    "GetRenderStatusInput", "GetRenderStatusOutput",
    # QA
    "ValidateProjectInput", "ValidateProjectOutput",
    "CreateQaReportInput", "CreateQaReportOutput",
    "ToolInput", "ToolOutput",
]
