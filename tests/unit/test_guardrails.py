"""Unit tests for guardrail rules."""

from __future__ import annotations

import pytest

from app.guardrails import (
    Guardrails,
    check_api_configuration,
    check_asset,
    check_coordinates,
    check_duration,
    check_file_path,
    check_location,
    check_missing_assets,
    check_required_fields,
    check_scene_timing,
    check_supported_media,
    check_time_range,
    check_timeline_overlaps,
)
from app.schemas.contracts import Asset, Location, Scene, TimelineEvent


def _loc(**kw) -> Location:
    base = dict(id="l1", name="Rome", latitude=41.9, longitude=12.49, source="nominatim")
    base.update(kw)
    return Location(**base)


# --- required fields --------------------------------------------------------


def test_required_fields_present() -> None:
    r = check_required_fields({"a": 1, "b": 2}, ["a", "b"])
    assert r.success


def test_required_fields_missing() -> None:
    r = check_required_fields({"a": 1}, ["a", "b"])
    assert r.is_failure
    assert "b" in r.errors[0]


# --- file paths -------------------------------------------------------------


def test_file_path_empty_rejected() -> None:
    assert check_file_path("").is_failure


def test_file_path_must_exist(tmp_path) -> None:
    p = tmp_path / "x.txt"
    p.write_text("hi")
    assert check_file_path(str(p), must_exist=True).success
    assert check_file_path(str(tmp_path / "missing.txt"), must_exist=True).is_failure


# --- supported media --------------------------------------------------------


def test_supported_media_accepts_png() -> None:
    assert check_supported_media("/a/b.png").success


def test_supported_media_rejects_bmp() -> None:
    assert check_supported_media("/a/b.bmp").is_failure


def test_supported_media_rejects_no_extension() -> None:
    assert check_supported_media("/a/file").is_failure


# --- durations --------------------------------------------------------------


def test_duration_negative_rejected() -> None:
    assert check_duration(-1.0).is_failure


def test_duration_bounds() -> None:
    assert check_duration(5.0, max_value=10.0).success
    assert check_duration(15.0, max_value=10.0).is_failure


# --- time range -------------------------------------------------------------


def test_time_range_valid() -> None:
    assert check_time_range(0.0, 5.0).success


def test_time_range_inverted_rejected() -> None:
    assert check_time_range(5.0, 1.0).is_failure


# --- scene timing -----------------------------------------------------------


def _scene(**kw) -> Scene:
    base = dict(id="s1", project_id="p1", index=0, title="T", start_time=0.0, end_time=5.0)
    base.update(kw)
    return Scene(**base)


def test_scene_timing_valid() -> None:
    assert check_scene_timing(_scene()).success


def test_scene_timing_exceeds_project_duration() -> None:
    r = check_scene_timing(_scene(end_time=20.0), project_duration=10.0)
    assert r.is_failure


def test_scene_timing_zero_duration_rejected() -> None:
    # The Scene schema itself rejects end == start, so zero-duration is caught
    # at the contract layer; verify the underlying time-range guardrail too.
    assert check_time_range(3.0, 3.0).success  # equal is allowed by time_range
    # but scene timing requires strictly positive duration.
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Scene(id="s1", project_id="p1", index=0, title="T", start_time=3.0, end_time=3.0)


# --- timeline overlaps ------------------------------------------------------


def _ev(start: float, end: float, layer: int = 0) -> TimelineEvent:
    return TimelineEvent(id=f"e{start}", project_id="p1", event_type="x", start_time=start, end_time=end, layer=layer)


def test_timeline_no_overlap() -> None:
    assert check_timeline_overlaps([_ev(0, 1), _ev(1, 2)]).success


def test_timeline_overlap_detected() -> None:
    r = check_timeline_overlaps([_ev(0, 2), _ev(1, 3)])
    assert r.is_failure
    assert "overlap" in r.errors[0]


def test_timeline_different_layers_no_overlap() -> None:
    assert check_timeline_overlaps([_ev(0, 2, layer=0), _ev(1, 3, layer=1)]).success


# --- coordinates ------------------------------------------------------------


def test_coordinates_valid() -> None:
    assert check_coordinates(10.0, 20.0).success


def test_coordinates_origin_rejected() -> None:
    assert check_coordinates(0.0, 0.0).is_failure


def test_coordinates_out_of_range() -> None:
    assert check_coordinates(91.0, 0.0).is_failure
    assert check_coordinates(0.0, -181.0).is_failure


# --- location ---------------------------------------------------------------


def test_location_valid() -> None:
    assert check_location(_loc()).success


def test_location_unverifiable_source_rejected() -> None:
    assert check_location(_loc(source="unknown")).is_failure


# --- asset ------------------------------------------------------------------


def _asset(**kw) -> Asset:
    base = dict(id="a1", name="icon", asset_type="icon", format="svg", file_path="/a.svg", source="local")
    base.update(kw)
    return Asset(**base)


def test_asset_valid() -> None:
    assert check_asset(_asset()).success


def test_asset_type_format_mismatch_rejected() -> None:
    # audio asset but svg extension
    r = check_asset(_asset(asset_type="audio", format="svg", file_path="/a.svg"))
    assert r.is_failure


def test_asset_missing_source_rejected() -> None:
    assert check_asset(_asset(source="unknown")).is_failure


# --- missing assets ---------------------------------------------------------


def test_missing_assets_detected() -> None:
    r = check_missing_assets([_asset(id="a1")], ["a1", "a2"])
    assert r.is_failure
    assert "a2" in r.errors[0]


def test_missing_assets_none() -> None:
    assert check_missing_assets([_asset(id="a1"), _asset(id="a2")], ["a1", "a2"]).success


# --- api configuration ------------------------------------------------------


def test_api_config_valid_no_key() -> None:
    assert check_api_configuration({"provider": "nominatim", "api_key": ""}).success


def test_api_config_rejects_placeholder_key() -> None:
    assert check_api_configuration({"provider": "nominatim", "api_key": "changeme"}).is_failure


def test_api_config_rejects_none_provider() -> None:
    assert check_api_configuration({"provider": "none"}).is_failure


# --- facade -----------------------------------------------------------------


def test_guardrails_facade_project_ok() -> None:
    g = Guardrails()
    from app.schemas.contracts import Project

    p = Project(id="p1", name="Doc")
    assert g.project(p).success


def test_guardrails_facade_project_with_bad_scene() -> None:
    g = Guardrails()
    from app.schemas.contracts import Project

    bad_scene = _scene(location=_loc(source="unknown"))
    p = Project(id="p1", name="Doc", scenes=[bad_scene])
    r = g.project(p)
    assert r.is_failure


def test_guardrails_qa_report_passed_with_errors_rejected() -> None:
    g = Guardrails()
    from app.core.enums import QASeverity, QACategory
    from app.schemas.contracts import QAFinding, QAReport

    rep = QAReport(
        id="q1",
        project_id="p1",
        passed=True,
        findings=[QAFinding(category=QACategory.TIMING, severity=QASeverity.ERROR, message="bad")],
    )
    assert g.qa_report(rep).is_failure


def test_guardrails_video_output_valid():
    from app.guardrails import Guardrails
    g = Guardrails()
    r = g.video_output(width=1920, height=1080, fps=30.0, codec="h264", duration_sec=10.0)
    assert not r.is_failure


def test_guardrails_video_output_rejects_low_resolution():
    from app.guardrails import Guardrails
    g = Guardrails()
    r = g.video_output(width=640, height=480, fps=30.0, codec="h264")
    assert r.is_failure
    assert "1280x720" in r.errors[0]


def test_guardrails_video_output_rejects_bad_codec():
    from app.guardrails import Guardrails
    g = Guardrails()
    r = g.video_output(width=1920, height=1080, fps=30.0, codec="mpeg2")
    assert r.is_failure


def test_guardrails_video_output_rejects_short_duration():
    from app.guardrails import Guardrails
    g = Guardrails()
    r = g.video_output(width=1920, height=1080, fps=30.0, codec="h264", duration_sec=0.5)
    assert r.is_failure

