"""Pydantic data contracts for the AI Video Agent.

These models are the authoritative, strictly-validated in-memory representation
of every entity flowing through the MCP pipeline. They are independent from the
SQLAlchemy ORM models (which handle persistence) so the data contract can be
validated without a database.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import (
    AgentName,
    AgentRunStatus,
    AssetFormat,
    AssetType,
    ProjectStatus,
    QASeverity,
    QACategory,
    RenderJobStatus,
    SceneStatus,
    WorkflowPhase,
)
from app.schemas.validators import (
    non_empty_str,
    validate_date_string,
    validate_duration,
    validate_latitude,
    validate_longitude,
    utc_now_iso,
)

# --- common config ----------------------------------------------------------

StrictModelConfig = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=False)

IDField = Annotated[str, Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$", description="Stable identifier")]
LatitudeField = Annotated[float, Field(ge=-90.0, le=90.0)]
LongitudeField = Annotated[float, Field(ge=-180.0, le=180.0)]


class Contract(BaseModel):
    """Base for all data contracts: forbids unknown fields."""

    model_config = StrictModelConfig


# --- Location ---------------------------------------------------------------


class Location(Contract):
    id: IDField
    name: str = Field(min_length=1, max_length=255)
    country: str | None = Field(default=None, max_length=255)
    latitude: LatitudeField
    longitude: LongitudeField
    source: str = Field(min_length=1, max_length=64, description="Geocode provider/source name; never empty")
    date: str | None = Field(default=None, description="Optional YYYY-MM-DD date associated with the location")
    geocode_payload: dict | None = None
    bbox: dict | None = None

    @field_validator("name", "country", "source")
    @classmethod
    def _ne(cls, v: str | None) -> str | None:
        return non_empty_str(v)

    @field_validator("latitude")
    @classmethod
    def _lat(cls, v: float) -> float:
        return validate_latitude(v)

    @field_validator("longitude")
    @classmethod
    def _lon(cls, v: float) -> float:
        return validate_longitude(v)

    @field_validator("date")
    @classmethod
    def _date(cls, v: str | None) -> str | None:
        return validate_date_string(v)


# --- Asset ------------------------------------------------------------------


class Asset(Contract):
    id: IDField
    name: str = Field(min_length=1, max_length=255)
    asset_type: AssetType
    format: AssetFormat
    file_path: str = Field(min_length=1, max_length=512)
    source: str = Field(min_length=1, max_length=128)
    license: str | None = None
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    duration_sec: float | None = None
    metadata: dict | None = None

    @field_validator("name", "file_path", "source")
    @classmethod
    def _ne(cls, v: str) -> str:
        return non_empty_str(v)  # type: ignore[return-value]

    @field_validator("duration_sec")
    @classmethod
    def _dur(cls, v: float | None) -> float | None:
        return validate_duration(v) if v is not None else v

    @model_validator(mode="after")
    def _check_format_matches_path(self) -> "Asset":
        ext = self.file_path.rsplit(".", 1)[-1].lower() if "." in self.file_path else ""
        if ext and self.format != ext:
            raise ValueError(
                f"asset format '{self.format}' does not match file extension '.{ext}'"
            )
        return self


# --- MapAnimation -----------------------------------------------------------


class MapAnimation(Contract):
    id: IDField
    scene_id: IDField
    location_id: IDField
    style: str = Field(min_length=1, max_length=64)
    start_time: float = Field(ge=0.0)
    end_time: float = Field(ge=0.0)
    zoom_start: float = Field(ge=0.0, le=22.0)
    zoom_end: float = Field(ge=0.0, le=22.0)
    bearing_start: float = Field(ge=0.0, le=360.0)
    bearing_end: float = Field(ge=0.0, le=360.0)
    geojson: dict | None = Field(default=None, description="Vector data backing the animation")
    source: str = Field(min_length=1, description="Provenance of map data; never invented")

    @field_validator("source", "style")
    @classmethod
    def _ne(cls, v: str) -> str:
        return non_empty_str(v)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _check_times(self) -> "MapAnimation":
        if self.end_time < self.start_time:
            raise ValueError("end_time must be >= start_time")
        return self


# --- TextOverlay ------------------------------------------------------------


class TextOverlay(Contract):
    id: IDField
    scene_id: IDField
    kind: Literal["title", "subtitle", "lower_third", "caption", "annotation", "label"]
    text: str = Field(min_length=1, max_length=500)
    start_time: float = Field(ge=0.0)
    end_time: float = Field(ge=0.0)
    x: float = Field(ge=0.0, le=1.0, description="Normalized x position [0..1]")
    y: float = Field(ge=0.0, le=1.0, description="Normalized y position [0..1]")
    font_size: int = Field(ge=8, le=200)
    color: str = Field(pattern=r"^#?[0-9A-Fa-f]{6}$", default="#FFFFFF")
    safe_zone: bool = True

    @field_validator("text")
    @classmethod
    def _ne(cls, v: str) -> str:
        return non_empty_str(v)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _check_times(self) -> "TextOverlay":
        if self.end_time < self.start_time:
            raise ValueError("end_time must be >= start_time")
        return self


# --- Transition -------------------------------------------------------------


class Transition(Contract):
    id: IDField
    from_scene_id: IDField | None = None
    to_scene_id: IDField | None = None
    kind: Literal["cut", "fade", "dissolve", "slide", "wipe", "zoom"]
    duration_sec: float = Field(gt=0.0, le=5.0, description="Transition length (0<d<=5s)")
    start_time: float = Field(ge=0.0)

    @field_validator("kind")
    @classmethod
    def _ne(cls, v: str) -> str:
        return non_empty_str(v)  # type: ignore[return-value]


# --- AudioSegment -----------------------------------------------------------


class AudioSegment(Contract):
    id: IDField
    audio_file_id: IDField
    start_time: float = Field(ge=0.0)
    end_time: float = Field(ge=0.0)
    kind: Literal["voiceover", "sfx", "music", "ambience", "silence"]
    label: str | None = None
    volume_db: float = Field(default=0.0, ge=-60.0, le=12.0)

    @model_validator(mode="after")
    def _check_times(self) -> "AudioSegment":
        if self.end_time < self.start_time:
            raise ValueError("end_time must be >= start_time")
        return self


# --- SoundEvent -------------------------------------------------------------


class SoundEvent(Contract):
    id: IDField
    scene_id: IDField | None = None
    asset_id: IDField
    start_time: float = Field(ge=0.0)
    end_time: float = Field(ge=0.0)
    kind: Literal["sfx", "music", "ambience"]
    volume_db: float = Field(default=0.0, ge=-60.0, le=12.0)
    loop: bool = False
    synced_to_visual: bool = True

    @model_validator(mode="after")
    def _check_times(self) -> "SoundEvent":
        if self.end_time < self.start_time:
            raise ValueError("end_time must be >= start_time")
        return self


# --- TimelineEvent ----------------------------------------------------------


class TimelineEvent(Contract):
    id: IDField
    project_id: IDField
    scene_id: IDField | None = None
    event_type: str = Field(min_length=1, max_length=64)
    start_time: float = Field(ge=0.0)
    end_time: float = Field(ge=0.0)
    layer: int = Field(ge=0, le=64, default=0)
    payload: dict | None = None

    @field_validator("event_type")
    @classmethod
    def _ne(cls, v: str) -> str:
        return non_empty_str(v)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _check_times(self) -> "TimelineEvent":
        if self.end_time < self.start_time:
            raise ValueError("end_time must be >= start_time")
        return self


# --- Scene ------------------------------------------------------------------


class Scene(Contract):
    id: IDField
    project_id: IDField
    index: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=255)
    status: SceneStatus = SceneStatus.PENDING
    start_time: float = Field(ge=0.0)
    end_time: float = Field(ge=0.0)
    narration: str | None = None
    visual_requirements: str | None = None
    location_id: IDField | None = None
    location: Location | None = None
    assets: list[Asset] = Field(default_factory=list)
    map_animation: MapAnimation | None = None
    text_overlays: list[TextOverlay] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    sound_events: list[SoundEvent] = Field(default_factory=list)
    spec: dict | None = None

    @field_validator("title", "narration", "visual_requirements")
    @classmethod
    def _ne(cls, v: str | None) -> str | None:
        return non_empty_str(v)

    @model_validator(mode="after")
    def _check_times(self) -> "Scene":
        if self.end_time <= self.start_time:
            raise ValueError("scene end_time must be greater than start_time")
        return self


# --- RenderJob --------------------------------------------------------------


class RenderJob(Contract):
    id: IDField
    project_id: IDField
    status: RenderJobStatus = RenderJobStatus.QUEUED
    output_path: str | None = None
    format: Literal["mp4", "webm", "mov"] = "mp4"
    width: int = Field(ge=1, default=1920)
    height: int = Field(ge=1, default=1080)
    fps: float = Field(gt=0.0, le=120.0, default=30.0)
    duration_sec: float | None = None
    error: str | None = None
    params: dict | None = None

    @field_validator("duration_sec")
    @classmethod
    def _dur(cls, v: float | None) -> float | None:
        return validate_duration(v) if v is not None else v


# --- QAFinding / QAReport ---------------------------------------------------


class QAFinding(Contract):
    category: QACategory
    severity: QASeverity
    message: str = Field(min_length=1, max_length=1000)
    scene_id: IDField | None = None
    details: dict | None = None

    @field_validator("message")
    @classmethod
    def _ne(cls, v: str) -> str:
        return non_empty_str(v)  # type: ignore[return-value]


class QAReport(Contract):
    id: IDField
    project_id: IDField
    passed: bool
    summary: str | None = None
    findings: list[QAFinding] = Field(default_factory=list)
    checked_at: str = Field(default_factory=utc_now_iso)

    @field_validator("summary")
    @classmethod
    def _ne(cls, v: str | None) -> str | None:
        return non_empty_str(v)


# --- AgentResult ------------------------------------------------------------


class AgentResult(Contract):
    """Structured result returned by every agent/MCP server."""

    agent: AgentName
    status: AgentRunStatus
    success: bool
    output: dict | list | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    attempt: int = Field(ge=1, default=1)
    started_at: str = Field(default_factory=utc_now_iso)
    finished_at: str | None = None

    @model_validator(mode="after")
    def _consistency(self) -> "AgentResult":
        if self.success and self.errors:
            raise ValueError("successful results must not contain errors")
        if not self.success and not self.errors:
            raise ValueError("failed results must include at least one error")
        return self


# --- Project ----------------------------------------------------------------


class Project(Contract):
    id: IDField
    name: str = Field(min_length=1, max_length=255)
    status: ProjectStatus = ProjectStatus.CREATED
    script_text: str | None = None
    voiceover_path: str | None = None
    target_duration_sec: float | None = None
    aspect_ratio: str = Field(pattern=r"^\d+:\d+$", default="16:9")
    resolution_width: int = Field(ge=1, default=1920)
    resolution_height: int = Field(ge=1, default=1080)
    output_path: str | None = None
    config: dict | None = None
    scenes: list[Scene] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)

    @field_validator("name")
    @classmethod
    def _ne(cls, v: str) -> str:
        return non_empty_str(v)  # type: ignore[return-value]

    @field_validator("target_duration_sec")
    @classmethod
    def _dur(cls, v: float | None) -> float | None:
        return validate_duration(v) if v is not None else v


# --- WorkflowState (Supervisor) ---------------------------------------------


class WorkflowState(Contract):
    id: IDField
    project_id: IDField
    current_phase: WorkflowPhase = WorkflowPhase.INIT
    previous_phase: WorkflowPhase | None = None
    agent_statuses: dict = Field(default_factory=dict)
    retries: dict = Field(default_factory=dict)
    notes: str | None = None


__all__ = [
    "AgentResult",
    "Asset",
    "AudioSegment",
    "Contract",
    "Location",
    "MapAnimation",
    "Project",
    "QAFinding",
    "QAReport",
    "RenderJob",
    "Scene",
    "SoundEvent",
    "TextOverlay",
    "TimelineEvent",
    "Transition",
    "WorkflowState",
]
