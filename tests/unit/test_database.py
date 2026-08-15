"""Unit tests for database initialization and persistence."""

from __future__ import annotations

import pytest


def test_init_db_creates_all_tables(fresh_db) -> None:
    from sqlalchemy import inspect

    from app.database import get_engine

    inspector = inspect(get_engine())
    tables = set(inspector.get_table_names())
    expected = {
        "projects",
        "scenes",
        "scene_assets",
        "assets",
        "locations",
        "audio_files",
        "timeline_events",
        "render_jobs",
        "qa_reports",
        "agent_runs",
        "workflow_state",
    }
    assert expected.issubset(tables), f"missing tables: {expected - tables}"


def test_project_persistence_roundtrip(fresh_db) -> None:
    from app.services.projects import ProjectService

    svc = ProjectService()
    pid = svc.create_project(name="Test Doc", script_text="A script.")
    fetched = svc.get_project(pid)
    assert fetched is not None
    assert fetched.name == "Test Doc"
    assert fetched.script_text == "A script."
    assert fetched.status == "created"


def test_scene_persistence(fresh_db) -> None:
    from app.services.projects import ProjectService

    svc = ProjectService()
    pid = svc.create_project(name="Doc 2")
    sid = svc.add_scene(pid, index=0, title="Intro", start_time=0.0, end_time=5.0, narration="Hello")
    assert svc.count_scenes(pid) == 1


def test_session_scope_rolls_back_on_error(fresh_db) -> None:
    from app.core.exceptions import DatabaseError
    from app.database import session_scope
    from app.models import Scene as SceneModel

    # FK violation: scene references a non-existent project.
    with pytest.raises(DatabaseError):
        with session_scope() as session:
            session.add(
                SceneModel(id="scene_bad", project_id="nonexistent", index=0, title="bad", start_time=0.0, end_time=1.0)
            )
            session.flush()  # type: ignore[attr-defined]
