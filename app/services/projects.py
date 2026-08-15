"""Project persistence service.

Thin application layer over the ORM: creates projects/scenes and queries them
back, demonstrating that the database foundation works end-to-end. The full
agent-driven persistence is Phase 2.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import DatabaseError
from app.database.session import session_scope
from app.models import Project as ProjectModel
from app.models import QAReport as QAReportModel
from app.models import RenderJob as RenderJobModel
from app.models import Scene as SceneModel
from app.models import WorkflowState as WorkflowStateModel
from app.utils.ids import new_id


class ProjectService:
    """CRUD-style service for projects and scenes."""

    def create_project(
        self,
        name: str,
        script_text: str | None = None,
        voiceover_path: str | None = None,
        *,
        project_id: str | None = None,
    ) -> str:
        project_id = project_id or new_id("proj_")
        with session_scope() as session:  # type: Session
            project = ProjectModel(
                id=project_id,
                name=name,
                status="created",
                script_text=script_text,
                voiceover_path=voiceover_path,
            )
            session.add(project)
        return project_id

    def add_scene(
        self,
        project_id: str,
        index: int,
        title: str,
        start_time: float,
        end_time: float,
        narration: str | None = None,
    ) -> str:
        scene_id = new_id("scene_")
        with session_scope() as session:  # type: Session
            scene = SceneModel(
                id=scene_id,
                project_id=project_id,
                index=index,
                title=title,
                status="pending",
                start_time=start_time,
                end_time=end_time,
                narration=narration,
            )
            session.add(scene)
        return scene_id

    def get_project(self, project_id: str) -> ProjectModel | None:
        with session_scope() as session:  # type: Session
            stmt = select(ProjectModel).where(ProjectModel.id == project_id)
            project = session.execute(stmt).scalar_one_or_none()
            if project is not None:
                session.expunge(project)
            return project

    def count_scenes(self, project_id: str) -> int:
        with session_scope() as session:  # type: Session
            stmt = select(SceneModel).where(SceneModel.project_id == project_id)
            return len(list(session.execute(stmt).scalars()))

    # --- Phase 4: render job, QA report, workflow state persistence ---------

    def save_render_job(
        self,
        project_id: str,
        *,
        output_path: str | None = None,
        status: str = "completed",
        format: str = "mp4",
        width: int = 1920,
        height: int = 1080,
        fps: float = 30.0,
        duration_sec: float | None = None,
        params: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> str:
        """Persist a render job record. Returns the job ID."""
        job_id = new_id("render_")
        now = datetime.now(timezone.utc).isoformat()
        with session_scope() as session:  # type: Session
            job = RenderJobModel(
                id=job_id,
                project_id=project_id,
                status=status,
                output_path=output_path,
                format=format,
                width=width,
                height=height,
                fps=fps,
                duration_sec=duration_sec,
                started_at=now,
                finished_at=now if status in ("completed", "failed") else None,
                error=error,
                params=params,
            )
            session.add(job)
        return job_id

    def save_qa_report(
        self,
        project_id: str,
        *,
        passed: bool,
        findings: list[dict[str, Any]],
        summary: str = "",
    ) -> str:
        """Persist a QA report record. Returns the report ID."""
        report_id = new_id("qa_")
        with session_scope() as session:  # type: Session
            report = QAReportModel(
                id=report_id,
                project_id=project_id,
                passed=passed,
                findings=findings,
                summary=summary,
            )
            session.add(report)
        return report_id

    def save_workflow_state(
        self,
        project_id: str,
        *,
        current_state: str,
        previous_state: str | None = None,
        current_phase: str | None = None,
        agent_statuses: dict[str, str] | None = None,
        retries: dict[str, int] | None = None,
    ) -> str:
        """Upsert a workflow state snapshot (one per project). Returns the state ID."""
        with session_scope() as session:  # type: Session
            stmt = select(WorkflowStateModel).where(WorkflowStateModel.project_id == project_id)
            existing = session.execute(stmt).scalar_one_or_none()
            if existing is not None:
                existing.current_state = current_state
                if previous_state is not None:
                    existing.previous_state = previous_state
                if current_phase is not None:
                    existing.current_phase = current_phase
                if agent_statuses is not None:
                    existing.agent_statuses = agent_statuses
                if retries is not None:
                    existing.retries = retries
                state_id = existing.id
            else:
                state_id = new_id("wf_")
                state = WorkflowStateModel(
                    id=state_id,
                    project_id=project_id,
                    current_state=current_state,
                    previous_state=previous_state,
                    current_phase=current_phase or "init",
                    agent_statuses=agent_statuses or {},
                    retries=retries or {},
                )
                session.add(state)
        return state_id

    def get_render_jobs(self, project_id: str) -> list[RenderJobModel]:
        """Return all render jobs for a project, newest first."""
        with session_scope() as session:  # type: Session
            stmt = (
                select(RenderJobModel)
                .where(RenderJobModel.project_id == project_id)
                .order_by(RenderJobModel.created_at.desc())
            )
            jobs = list(session.execute(stmt).scalars())
            for job in jobs:
                session.expunge(job)
            return jobs

    def get_qa_reports(self, project_id: str) -> list[QAReportModel]:
        """Return all QA reports for a project, newest first."""
        with session_scope() as session:  # type: Session
            stmt = (
                select(QAReportModel)
                .where(QAReportModel.project_id == project_id)
                .order_by(QAReportModel.created_at.desc())
            )
            reports = list(session.execute(stmt).scalars())
            for report in reports:
                session.expunge(report)
            return reports


__all__ = ["ProjectService"]
