"""Tests for Phase 2 database enhancements: FK enforcement, indexes, provenance columns."""

from __future__ import annotations

import pytest

from app.database import init_db, reset_engine, session_scope
from app.models import (
    AgentRun,
    Asset,
    AudioFile,
    Location,
    Project,
    QAReport,
    RenderJob,
    Scene,
    SceneAsset,
    TimelineEvent,
    WorkflowState,
)
from app.utils.ids import new_id


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    """Provide a fresh in-memory-style SQLite DB for each test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    reset_engine()
    init_db()
    yield None
    reset_engine()


class TestForeignKeyEnforcement:
    def test_scene_cannot_reference_nonexistent_project(self, fresh_db) -> None:
        with pytest.raises(Exception):
            with session_scope() as session:
                session.add(
                    Scene(
                        id="scene_bad",
                        project_id="nonexistent",
                        index=0,
                        title="bad",
                        start_time=0.0,
                        end_time=1.0,
                    )
                )

    def test_asset_cannot_reference_nonexistent_scene(self, fresh_db) -> None:
        with pytest.raises(Exception):
            with session_scope() as session:
                session.add(
                    SceneAsset(
                        id="sa_bad",
                        scene_id="nonexistent",
                        asset_id="asset_1",
                    )
                )

    def test_valid_fk_chain_succeeds(self, fresh_db) -> None:
        with session_scope() as session:
            pid = new_id("proj_")
            sid = new_id("scene_")
            aid = new_id("asset_")
            session.add(Project(id=pid, name="Test"))
            session.add(
                Scene(id=sid, project_id=pid, index=0, title="Intro", start_time=0.0, end_time=5.0)
            )
            session.add(
                Asset(id=aid, name="photo", asset_type="image", format="png", file_path="photo.png", source="wikimedia")
            )
            session.add(SceneAsset(id=new_id("sa_"), scene_id=sid, asset_id=aid))

    def test_cascade_delete_removes_scenes(self, fresh_db) -> None:
        with session_scope() as session:
            pid = new_id("proj_")
            sid = new_id("scene_")
            session.add(Project(id=pid, name="Test"))
            session.add(
                Scene(id=sid, project_id=pid, index=0, title="Intro", start_time=0.0, end_time=5.0)
            )
            session.flush()
            # Delete the project.
            proj = session.query(Project).filter_by(id=pid).one()
            session.delete(proj)
            session.flush()
            # Scene should be gone.
            assert session.query(Scene).filter_by(id=sid).count() == 0


class TestWorkflowStateColumns:
    def test_workflow_state_has_current_state(self, fresh_db) -> None:
        with session_scope() as session:
            pid = new_id("proj_")
            wsid = new_id("ws_")
            session.add(Project(id=pid, name="Test"))
            session.add(
                WorkflowState(
                    id=wsid,
                    project_id=pid,
                    current_state="analyzing_script",
                    current_phase="script_understanding",
                )
            )
            session.flush()
            ws = session.query(WorkflowState).filter_by(id=wsid).one()
            assert ws.current_state == "analyzing_script"

    def test_workflow_state_previous_state(self, fresh_db) -> None:
        with session_scope() as session:
            pid = new_id("proj_")
            wsid = new_id("ws_")
            session.add(Project(id=pid, name="Test"))
            session.add(
                WorkflowState(
                    id=wsid,
                    project_id=pid,
                    current_state="analyzing_audio",
                    previous_state="analyzing_script",
                )
            )
            session.flush()
            ws = session.query(WorkflowState).filter_by(id=wsid).one()
            assert ws.previous_state == "analyzing_script"


class TestProvenanceColumns:
    def test_location_provenance_column(self, fresh_db) -> None:
        with session_scope() as session:
            lid = new_id("loc_")
            session.add(
                Location(
                    id=lid,
                    name="Paris",
                    latitude=48.85,
                    longitude=2.35,
                    source="nominatim",
                    provenance={"provider": "nominatim", "type": "geocoding"},
                )
            )
            session.flush()
            loc = session.query(Location).filter_by(id=lid).one()
            assert loc.provenance is not None
            assert loc.provenance["provider"] == "nominatim"

    def test_asset_provenance_column(self, fresh_db) -> None:
        with session_scope() as session:
            aid = new_id("asset_")
            session.add(
                Asset(
                    id=aid,
                    name="photo",
                    asset_type="image",
                    format="png",
                    file_path="photo.png",
                    source="wikimedia",
                    provenance={"provider": "wikimedia", "type": "asset"},
                )
            )
            session.flush()
            asset = session.query(Asset).filter_by(id=aid).one()
            assert asset.provenance is not None
            assert asset.provenance["provider"] == "wikimedia"


class TestUniqueConstraint:
    def test_duplicate_scene_index_rejected(self, fresh_db) -> None:
        from app.core.exceptions import DatabaseError

        with pytest.raises(DatabaseError):
            with session_scope() as session:
                pid = new_id("proj_")
                session.add(Project(id=pid, name="Test"))
                session.add(
                    Scene(id=new_id("s1_"), project_id=pid, index=0, title="A", start_time=0.0, end_time=5.0)
                )
                session.add(
                    Scene(id=new_id("s2_"), project_id=pid, index=0, title="B", start_time=5.0, end_time=10.0)
                )

    def test_different_projects_can_have_same_index(self, fresh_db) -> None:
        with session_scope() as session:
            p1 = new_id("proj_")
            p2 = new_id("proj_")
            session.add(Project(id=p1, name="A"))
            session.add(Project(id=p2, name="B"))
            session.add(Scene(id=new_id("s1_"), project_id=p1, index=0, title="A", start_time=0.0, end_time=5.0))
            session.add(Scene(id=new_id("s2_"), project_id=p2, index=0, title="B", start_time=0.0, end_time=5.0))
            session.flush()  # should not raise


class TestIndexCoverage:
    """Smoke-test that indexed columns can be queried efficiently (no full scan errors)."""

    def test_query_by_status(self, fresh_db) -> None:
        with session_scope() as session:
            pid = new_id("proj_")
            session.add(Project(id=pid, name="Test", status="rendering"))
            session.flush()
            results = session.query(Project).filter_by(status="rendering").all()
            assert len(results) == 1

    def test_query_by_agent_type(self, fresh_db) -> None:
        with session_scope() as session:
            pid = new_id("proj_")
            session.add(Project(id=pid, name="Test"))
            session.add(
                Asset(id=new_id("a_"), name="v", asset_type="video", format="mp4", file_path="v.mp4", source="x")
            )
            session.flush()
            results = session.query(Asset).filter_by(asset_type="video").all()
            assert len(results) == 1
