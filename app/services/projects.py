"""Project persistence service.

Thin application layer over the ORM: creates projects/scenes and queries them
back, demonstrating that the database foundation works end-to-end. The full
agent-driven persistence is Phase 2.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import DatabaseError
from app.database.session import session_scope
from app.models import Project as ProjectModel
from app.models import Scene as SceneModel
from app.utils.ids import new_id


class ProjectService:
    """CRUD-style service for projects and scenes."""

    def create_project(self, name: str, script_text: str | None = None, voiceover_path: str | None = None) -> str:
        project_id = new_id("proj_")
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


__all__ = ["ProjectService"]
