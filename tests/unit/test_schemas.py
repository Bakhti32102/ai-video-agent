"""Unit tests for Pydantic data contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.contracts import (
    AgentResult,
    Asset,
    Location,
    MapAnimation,
    Project,
    QAFinding,
    QAReport,
    RenderJob,
    Scene,
    SoundEvent,
    TextOverlay,
    TimelineEvent,
    Transition,
)


def _valid_location() -> Location:
    return Location(
        id="loc_1",
        name="Paris",
        country="France",
        latitude=48.8566,
        longitude=2.3522,
        source="nominatim",
    )


def _valid_scene(**overrides) -> Scene:
    base = dict(
        id="scene_1",
        project_id="proj_1",
        index=0,
        title="Opening",
        start_time=0.0,
        end_time=10.0,
    )
    base.update(overrides)
    return Scene(**base)


# --- Location ---------------------------------------------------------------


def test_location_valid() -> None:
    loc = _valid_location()
    assert loc.latitude == 48.8566


def test_location_rejects_invalid_latitude() -> None:
    with pytest.raises(ValidationError):
        Location(id="loc", name="X", latitude=999.0, longitude=0.0, source="nominatim")


def test_location_rejects_invalid_longitude() -> None:
    with pytest.raises(ValidationError):
        Location(id="loc", name="X", latitude=0.0, longitude=999.0, source="nominatim")


def test_location_rejects_empty_source() -> None:
    with pytest.raises(ValidationError):
        Location(id="loc", name="X", latitude=1.0, longitude=1.0, source="   ")


def test_location_date_formats() -> None:
    loc = Location(id="l", name="X", latitude=1.0, longitude=1.0, source="mapbox", date="1945-05-08")
    assert loc.date == "1945-05-08"
    with pytest.raises(ValidationError):
        Location(id="l", name="X", latitude=1.0, longitude=1.0, source="mapbox", date="not-a-date")


# --- Asset ------------------------------------------------------------------


def test_asset_valid() -> None:
    a = Asset(
        id="a1",
        name="sword",
        asset_type="icon",
        format="svg",
        file_path="/assets/sword.svg",
        source="local",
    )
    assert a.format == "svg"


def test_asset_rejects_format_extension_mismatch() -> None:
    with pytest.raises(ValidationError):
        Asset(
            id="a1",
            name="sword",
            asset_type="icon",
            format="png",
            file_path="/assets/sword.svg",
            source="local",
        )


def test_asset_rejects_negative_duration() -> None:
    with pytest.raises(ValidationError):
        Asset(
            id="a1",
            name="clip",
            asset_type="audio",
            format="mp3",
            file_path="/a.mp3",
            source="local",
            duration_sec=-1.0,
        )


# --- MapAnimation -----------------------------------------------------------


def test_map_animation_valid() -> None:
    m = MapAnimation(
        id="m1",
        scene_id="scene_1",
        location_id="loc_1",
        style="default",
        start_time=0.0,
        end_time=5.0,
        zoom_start=4.0,
        zoom_end=8.0,
        bearing_start=0.0,
        bearing_end=90.0,
        source="nominatim",
    )
    assert m.zoom_end == 8.0


def test_map_animation_rejects_inverted_times() -> None:
    with pytest.raises(ValidationError):
        MapAnimation(
            id="m1",
            scene_id="scene_1",
            location_id="loc_1",
            style="default",
            start_time=5.0,
            end_time=1.0,
            zoom_start=4.0,
            zoom_end=8.0,
            bearing_start=0.0,
            bearing_end=90.0,
            source="nominatim",
        )


# --- TextOverlay ------------------------------------------------------------


def test_text_overlay_valid() -> None:
    t = TextOverlay(
        id="t1",
        scene_id="scene_1",
        kind="title",
        text="Hello",
        start_time=0.0,
        end_time=2.0,
        x=0.1,
        y=0.1,
        font_size=48,
    )
    assert t.kind == "title"


def test_text_overlay_rejects_out_of_frame_position() -> None:
    with pytest.raises(ValidationError):
        TextOverlay(
            id="t1",
            scene_id="scene_1",
            kind="caption",
            text="Hi",
            start_time=0.0,
            end_time=1.0,
            x=1.5,
            y=0.1,
            font_size=24,
        )


# --- Transition -------------------------------------------------------------


def test_transition_duration_bounds() -> None:
    t = Transition(id="t", to_scene_id="s2", kind="fade", duration_sec=0.5, start_time=0.0)
    assert t.duration_sec == 0.5
    with pytest.raises(ValidationError):
        Transition(id="t", to_scene_id="s2", kind="fade", duration_sec=6.0, start_time=0.0)
    with pytest.raises(ValidationError):
        Transition(id="t", to_scene_id="s2", kind="fade", duration_sec=0.0, start_time=0.0)


# --- TimelineEvent / overlap ------------------------------------------------


def test_timeline_event_valid() -> None:
    e = TimelineEvent(
        id="e1",
        project_id="p1",
        event_type="visual",
        start_time=0.0,
        end_time=1.0,
    )
    assert e.event_type == "visual"


# --- Scene ------------------------------------------------------------------


def test_scene_requires_positive_duration() -> None:
    with pytest.raises(ValidationError):
        _valid_scene(start_time=5.0, end_time=5.0)


# --- RenderJob --------------------------------------------------------------


def test_render_job_defaults() -> None:
    j = RenderJob(id="j1", project_id="p1")
    assert j.status == "queued"
    assert j.width == 1920 and j.height == 1080


# --- QAReport ---------------------------------------------------------------


def test_qa_report_valid() -> None:
    rep = QAReport(id="q1", project_id="p1", passed=True, findings=[])
    assert rep.passed is True


def test_qa_finding_valid() -> None:
    f = QAFinding(category="timing", severity="warning", message="gap")
    assert f.severity == "warning"


# --- AgentResult ------------------------------------------------------------


def test_agent_result_success_consistency() -> None:
    from app.core.enums import AgentName

    r = AgentResult(agent=AgentName.SCRIPT, status="success", success=True)
    assert r.success


def test_agent_result_fails_without_errors() -> None:
    from app.core.enums import AgentName

    with pytest.raises(ValidationError):
        AgentResult(agent=AgentName.SCRIPT, status="failed", success=False)


def test_agent_result_fails_with_errors_when_success() -> None:
    from app.core.enums import AgentName

    with pytest.raises(ValidationError):
        AgentResult(agent=AgentName.SCRIPT, status="success", success=True, errors=["x"])


# --- Project ----------------------------------------------------------------


def test_project_valid() -> None:
    p = Project(id="p1", name="Doc")
    assert p.aspect_ratio == "16:9"
    assert p.scenes == []


def test_project_rejects_bad_aspect_ratio() -> None:
    with pytest.raises(ValidationError):
        Project(id="p1", name="Doc", aspect_ratio="widescreen")


def test_project_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Project(id="p1", name="Doc", bogus=True)
