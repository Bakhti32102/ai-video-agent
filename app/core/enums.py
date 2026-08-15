"""Shared enumerations used across models, schemas and guardrails.

Enums are intentionally string-backed so they serialize cleanly to JSON and
SQLite.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """String enum base (backport-friendly for Python < 3.11 StrEnum)."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class ProjectStatus(StrEnum):
    CREATED = "created"
    SCRIPT_PARSED = "script_parsed"
    AUDIO_ANALYZED = "audio_analyzed"
    SCENES_BUILT = "scenes_built"
    RENDERING = "rendering"
    QA_PASSED = "qa_passed"
    QA_FAILED = "qa_failed"
    COMPLETED = "completed"
    FAILED = "failed"


class SceneStatus(StrEnum):
    PENDING = "pending"
    PLANNED = "planned"
    ASSETS_READY = "assets_ready"
    RENDERED = "rendered"
    QA_PASSED = "qa_passed"
    QA_FAILED = "qa_failed"
    FAILED = "failed"


class AgentName(StrEnum):
    SUPERVISOR = "supervisor"
    SCRIPT = "script"
    AUDIO = "audio"
    GEO = "geo"
    ASSET = "asset"
    TEXT = "text"
    TRANSITION = "transition"
    SOUND = "sound"
    QA = "qa"
    RENDER = "render"


class AgentRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    RETRY = "retry"
    SKIPPED = "skipped"


class AssetType(StrEnum):
    ICON = "icon"
    IMAGE = "image"
    MAP_TILE = "map_tile"
    VECTOR = "vector"
    SVG = "svg"
    AUDIO = "audio"
    VIDEO_CLIP = "video_clip"
    FONT = "font"
    OTHER = "other"


class AssetFormat(StrEnum):
    SVG = "svg"
    PNG = "png"
    WEBP = "webp"
    JPG = "jpg"
    MP3 = "mp3"
    WAV = "wav"
    AAC = "aac"
    MP4 = "mp4"
    WEBM = "webm"
    GEOJSON = "geojson"
    TTF = "ttf"
    OTF = "otf"


class RenderJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class QASeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class QACategory(StrEnum):
    MISSING_SCENE = "missing_scene"
    TIMING = "timing"
    AUDIO_VIDEO_MISMATCH = "audio_video_mismatch"
    INVALID_COORDINATES = "invalid_coordinates"
    MISSING_ASSET = "missing_asset"
    TEXT_OVERFLOW = "text_overflow"
    RENDER_ERROR = "render_error"
    OTHER = "other"


class WorkflowPhase(StrEnum):
    INIT = "init"
    SCRIPT_UNDERSTANDING = "script_understanding"
    AUDIO_ANALYSIS = "audio_analysis"
    GEO_RESOLUTION = "geo_resolution"
    ASSET_SELECTION = "asset_selection"
    TEXT_GENERATION = "text_generation"
    TRANSITION_SELECTION = "transition_selection"
    SOUND_DESIGN = "sound_design"
    RENDERING = "rendering"
    QA = "qa"
    DONE = "done"
