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



class TestProjectServicePhase4:
    """Tests for Phase 4 DB persistence methods in ProjectService."""

    def test_save_render_job(self, fresh_db) -> None:
        from app.services.projects import ProjectService
        svc = ProjectService()
        pid = svc.create_project(name="Test")
        job_id = svc.save_render_job(
            pid,
            output_path="output/test.mp4",
            status="completed",
            duration_sec=10.0,
            width=1920,
            height=1080,
        )
        assert job_id.startswith("render_")
        jobs = svc.get_render_jobs(pid)
        assert len(jobs) == 1
        assert jobs[0].output_path == "output/test.mp4"
        assert jobs[0].status == "completed"

    def test_save_qa_report(self, fresh_db) -> None:
        from app.services.projects import ProjectService
        svc = ProjectService()
        pid = svc.create_project(name="Test")
        report_id = svc.save_qa_report(
            pid,
            passed=True,
            findings=[{"category": "x", "severity": "warning", "message": "ok"}],
            summary="1 finding; passed=True",
        )
        assert report_id.startswith("qa_")
        reports = svc.get_qa_reports(pid)
        assert len(reports) == 1
        assert reports[0].passed is True

    def test_save_workflow_state_upsert(self, fresh_db) -> None:
        from app.services.projects import ProjectService
        svc = ProjectService()
        pid = svc.create_project(name="Test")
        state_id1 = svc.save_workflow_state(pid, current_state="created", current_phase="init")
        state_id2 = svc.save_workflow_state(pid, current_state="completed", current_phase="done")
        # Upsert: same ID, updated state.
        assert state_id1 == state_id2
        from app.models import WorkflowState as WorkflowStateModel
        from app.database.session import session_scope
        with session_scope() as session:
            ws = session.get(WorkflowStateModel, state_id1)
            assert ws.current_state == "completed"
            assert ws.current_phase == "done"

    def test_get_render_jobs_empty(self, fresh_db) -> None:
        from app.services.projects import ProjectService
        svc = ProjectService()
        pid = svc.create_project(name="Test")
        assert svc.get_render_jobs(pid) == []

    def test_create_project_with_explicit_id(self, fresh_db) -> None:
        from app.services.projects import ProjectService
        svc = ProjectService()
        svc.create_project(name="Test", project_id="custom_id_123")
        assert svc.get_project("custom_id_123") is not None

