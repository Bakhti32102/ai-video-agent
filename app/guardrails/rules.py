"""Core guardrail validation rules.

Every function returns a :class:`Result` so callers can collect failures and
the supervisor can decide on retries. Rules never silently repair critical
data; they report structured errors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.result import Result
from app.guardrails.media import (
    SUPPORTED_AUDIO_FORMATS,
    SUPPORTED_GEO_FORMATS,
    SUPPORTED_IMAGE_FORMATS,
    SUPPORTED_VIDEO_FORMATS,
    file_extension,
    is_supported_media,
)
from app.schemas.contracts import (
    Asset,
    Location,
    Scene,
    TimelineEvent,
)

# --- required fields --------------------------------------------------------


def check_required_fields(payload: dict, required: list[str]) -> Result[dict]:
    """Ensure every required top-level key is present and non-empty."""
    missing = [k for k in required if k not in payload or payload[k] in (None, "")]
    if missing:
        return Result.fail(f"missing required fields: {', '.join(missing)}")
    return Result.ok(payload)


# --- file paths -------------------------------------------------------------


def check_file_path(path: str, must_exist: bool = False) -> Result[str]:
    """Validate that a path is non-empty and (optionally) exists on disk."""
    if not path or not str(path).strip():
        return Result.fail("file path must not be empty")
    p = Path(path)
    if must_exist and not p.exists():
        return Result.fail(f"file does not exist: {path}")
    return Result.ok(path)


def check_supported_media(path: str, allowed: frozenset[str] | None = None) -> Result[str]:
    """Validate that a media file path has a supported extension."""
    ext = file_extension(path)
    if not ext:
        return Result.fail(f"file has no extension: {path}")
    if not is_supported_media(path, allowed):
        return Result.fail(f"unsupported media format '{ext}': {path}")
    return Result.ok(path)


# --- durations & timing -----------------------------------------------------


def check_duration(value: float, *, min_value: float = 0.0, max_value: float | None = None) -> Result[float]:
    """Validate a duration value is within bounds."""
    if value < min_value:
        return Result.fail(f"duration {value} below minimum {min_value}")
    if max_value is not None and value > max_value:
        return Result.fail(f"duration {value} exceeds maximum {max_value}")
    return Result.ok(value)


def check_time_range(start: float, end: float) -> Result[tuple[float, float]]:
    """Validate start <= end and both are non-negative."""
    if start < 0 or end < 0:
        return Result.fail("times must be non-negative")
    if end < start:
        return Result.fail(f"end_time {end} precedes start_time {start}")
    return Result.ok((start, end))


def check_scene_timing(scene: Scene, project_duration: float | None = None) -> Result[Scene]:
    """Validate a scene's start/end times and optional bounds vs project duration."""
    r = check_time_range(scene.start_time, scene.end_time)
    if r.is_failure:
        return Result.fail(*r.errors)
    if scene.end_time == scene.start_time:
        return Result.fail(f"scene {scene.index} has zero duration")
    if project_duration is not None and scene.end_time > project_duration:
        return Result.fail(
            f"scene {scene.index} end_time {scene.end_time} exceeds project duration {project_duration}"
        )
    return Result.ok(scene)


# --- timeline overlaps ------------------------------------------------------


def check_timeline_overlaps(events: list[TimelineEvent]) -> Result[list[TimelineEvent]]:
    """Detect overlapping events on the same layer within a project timeline."""
    by_layer: dict[int, list[TimelineEvent]] = {}
    for ev in events:
        by_layer.setdefault(ev.layer, []).append(ev)

    overlaps: list[str] = []
    for layer, layer_events in by_layer.items():
        layer_events.sort(key=lambda e: e.start_time)
        for prev, curr in zip(layer_events, layer_events[1:]):
            if curr.start_time < prev.end_time:
                overlaps.append(
                    f"overlap on layer {layer}: {prev.event_type}@{prev.start_time}-{prev.end_time} "
                    f"vs {curr.event_type}@{curr.start_time}-{curr.end_time}"
                )
    if overlaps:
        return Result.fail(*overlaps)
    return Result.ok(events)


# --- geographic coordinates -------------------------------------------------


def check_coordinates(latitude: float, longitude: float) -> Result[tuple[float, float]]:
    """Validate lat/lon are within their valid ranges and not (0,0) by accident."""
    if not -90.0 <= latitude <= 90.0:
        return Result.fail(f"latitude {latitude} out of range [-90, 90]")
    if not -180.0 <= longitude <= 180.0:
        return Result.fail(f"longitude {longitude} out of range [-180, 180]")
    if latitude == 0.0 and longitude == 0.0:
        return Result.fail("coordinates (0,0) are almost certainly an unset/invalid value")
    return Result.ok((latitude, longitude))


def check_location(location: Location) -> Result[Location]:
    """Validate a Location's coordinates and that its source is traceable."""
    r = check_coordinates(location.latitude, location.longitude)
    if r.is_failure:
        return Result.fail(*r.errors)
    if not location.source or location.source.strip().lower() in {"unknown", "none", ""}:
        return Result.fail(
            f"location '{location.name}' has unverifiable source '{location.source}'; "
            "geographic data must be traceable to a real provider"
        )
    return Result.ok(location)


# --- assets -----------------------------------------------------------------


def check_asset(asset: Asset) -> Result[Asset]:
    """Validate an asset's format matches its declared type and path extension."""
    type_to_formats = {
        "icon": SUPPORTED_IMAGE_FORMATS | {"svg"},
        "image": SUPPORTED_IMAGE_FORMATS,
        "svg": {"svg"},
        "map_tile": SUPPORTED_IMAGE_FORMATS,
        "vector": SUPPORTED_GEO_FORMATS,
        "audio": SUPPORTED_AUDIO_FORMATS,
        "video_clip": SUPPORTED_VIDEO_FORMATS,
        "font": frozenset({"ttf", "otf"}),
    }
    allowed = type_to_formats.get(asset.asset_type)
    if allowed is None:
        # 'other' accepts any supported format
        allowed = frozenset()
    ext = file_extension(asset.file_path)
    if not ext:
        return Result.fail(f"asset '{asset.name}' has no file extension: {asset.file_path}")
    if allowed and ext not in allowed:
        return Result.fail(
            f"asset '{asset.name}' type '{asset.asset_type}' does not accept '.{ext}'"
        )
    if asset.format != ext:
        return Result.fail(
            f"asset '{asset.name}' format field '{asset.format}' does not match path extension '.{ext}'"
        )
    if not asset.source or asset.source.strip().lower() in {"unknown", "none", ""}:
        return Result.fail(f"asset '{asset.name}' has no traceable source")
    return Result.ok(asset)


def check_missing_assets(assets: list[Asset], required_ids: list[str]) -> Result[list[str]]:
    """Report any required asset IDs that are not present in the provided list."""
    present = {a.id for a in assets}
    missing = [aid for aid in required_ids if aid not in present]
    if missing:
        return Result.fail(f"missing assets: {', '.join(missing)}")
    return Result.ok(missing)


# --- configuration ----------------------------------------------------------


def check_api_configuration(config: dict) -> Result[dict]:
    """Validate that API/provider configuration is safe.

    Refuses empty provider names and refuses obviously-placeholder API keys.
    Does NOT require keys to be present (some providers are open/free).
    """
    errors: list[str] = []
    provider = config.get("provider")
    if provider is not None and (not str(provider).strip() or str(provider).strip() == "none"):
        errors.append("provider is set to empty/none; disable the feature explicitly instead")
    api_key = config.get("api_key")
    if api_key is not None and str(api_key).strip():
        if str(api_key).strip().lower() in {"changeme", "your-key", "xxx", "placeholder"}:
            errors.append("api_key looks like a placeholder; refusing to use it")
    if errors:
        return Result.fail(*errors)
    return Result.ok(config)


# --- agent output schema ----------------------------------------------------


def check_agent_output(model_cls: type[BaseModel], payload: Any) -> Result[BaseModel]:
    """Validate an agent's raw output against a Pydantic model class."""
    try:
        instance = model_cls.model_validate(payload)
    except ValidationError as exc:
        return Result.fail("agent output failed schema validation", *[str(e) for e in exc.errors()])
    return Result.ok(instance)


# --- path traversal ---------------------------------------------------------


def check_path_traversal(path: str) -> Result[str]:
    """Reject paths that contain traversal sequences (``..``) or control chars."""
    from app.utils.paths import contains_traversal, is_path_safe

    if not path or not str(path).strip():
        return Result.fail("path must not be empty")
    if contains_traversal(path):
        return Result.fail(f"path traversal detected: {path}")
    if not is_path_safe(path, allow_absolute=True):
        return Result.fail(f"unsafe path rejected: {path}")
    return Result.ok(path)


def check_file_path_safe(path: str, *, base_dir: str | None = None, must_exist: bool = False) -> Result[str]:
    """Full file-path safety check: traversal + extension + containment.

    Combines :func:`check_file_path`, :func:`check_path_traversal`, and an
    optional containment check against ``base_dir``.
    """
    r = check_file_path(path, must_exist=False)
    if r.is_failure:
        return r
    r = check_path_traversal(path)
    if r.is_failure:
        return r
    if base_dir is not None:
        from app.utils.paths import restrict_to_directory
        from app.core.exceptions import FileSafetyError

        try:
            restrict_to_directory(path, base_dir, must_exist=must_exist)
        except FileSafetyError as exc:
            return Result.fail(str(exc))
    elif must_exist:
        r = check_file_path(path, must_exist=True)
        if r.is_failure:
            return r
    return Result.ok(path)


# --- invalid IDs ------------------------------------------------------------

import re as _re

_ID_PATTERN = _re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def check_id(value: str | None, *, field: str = "id") -> Result[str]:
    """Validate that an ID matches the contract pattern and is non-empty."""
    if value is None or not str(value).strip():
        return Result.fail(f"{field} must not be empty")
    v = str(value).strip()
    if not _ID_PATTERN.match(v):
        return Result.fail(
            f"{field} '{v}' is invalid; must match ^[A-Za-z0-9_\\-]{{1,64}}$"
        )
    return Result.ok(v)


# --- workflow state transitions ---------------------------------------------


def check_workflow_transition(current: str, target: str) -> Result[str]:
    """Validate a workflow state transition using the state machine."""
    from app.core.enums import WorkflowState
    from app.core.workflow import can_transition_to

    try:
        cur = WorkflowState(current)
    except ValueError:
        return Result.fail(f"unknown current workflow state: '{current}'")
    try:
        tgt = WorkflowState(target)
    except ValueError:
        return Result.fail(f"unknown target workflow state: '{target}'")
    result = can_transition_to(cur, tgt)
    if result.is_failure:
        return Result.fail(*result.errors)
    return Result.ok(target)


# --- agent status -----------------------------------------------------------


def check_agent_status(status: str) -> Result[str]:
    """Validate that an agent status is a known AgentRunStatus value."""
    from app.core.enums import AgentRunStatus

    valid = {s.value for s in AgentRunStatus}
    if status not in valid:
        return Result.fail(
            f"invalid agent status '{status}'; must be one of {sorted(valid)}"
        )
    return Result.ok(status)


# --- provenance -------------------------------------------------------------


def check_provenance(provenance: Any) -> Result[Any]:
    """Validate that externally-obtained data carries traceable provenance.

    Provenance is required for any data that did not originate inside the
    pipeline. The source/provider must be non-empty and must not be a
    placeholder like 'unknown' or 'none'.
    """
    if provenance is None:
        return Result.fail("provenance is required for externally-obtained data; refusing untraceable data")
    if isinstance(provenance, dict):
        provider = str(provenance.get("provider", "")).strip().lower()
        source = str(provenance.get("source", "")).strip().lower()
        if not provider or provider in {"unknown", "none"}:
            return Result.fail("provenance provider must be traceable; 'unknown'/'none' rejected")
        if not source or source in {"unknown", "none"}:
            return Result.fail("provenance source must be traceable; 'unknown'/'none' rejected")
        return Result.ok(provenance)
    # If it's a Pydantic model with provider/source attributes.
    provider = str(getattr(provenance, "provider", "")).strip().lower()
    source = str(getattr(provenance, "source", "")).strip().lower()
    if not provider or provider in {"unknown", "none"}:
        return Result.fail("provenance provider must be traceable; 'unknown'/'none' rejected")
    if not source or source in {"unknown", "none"}:
        return Result.fail("provenance source must be traceable; 'unknown'/'none' rejected")
    return Result.ok(provenance)


__all__ = [
    "check_agent_output",
    "check_agent_status",
    "check_api_configuration",
    "check_asset",
    "check_coordinates",
    "check_duration",
    "check_file_path",
    "check_file_path_safe",
    "check_id",
    "check_location",
    "check_missing_assets",
    "check_path_traversal",
    "check_provenance",
    "check_required_fields",
    "check_scene_timing",
    "check_supported_media",
    "check_time_range",
    "check_timeline_overlaps",
    "check_workflow_transition",
]
