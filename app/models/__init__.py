"""SQLAlchemy ORM models.

Importing this package registers every model on the shared metadata so that
``Base.metadata.create_all`` picks them up. Keep this import list in sync with
new models.
"""

from app.database.base import Base, IDMixin, TimestampMixin
from app.models.agent_run import AgentRun
from app.models.asset import Asset, SceneAsset
from app.models.audio_file import AudioFile
from app.models.location import Location
from app.models.project import Project
from app.models.qa_report import QAReport
from app.models.render_job import RenderJob
from app.models.scene import Scene
from app.models.timeline_event import TimelineEvent
from app.models.workflow_state import WorkflowState

__all__ = [
    "AgentRun",
    "Asset",
    "AudioFile",
    "Base",
    "IDMixin",
    "Location",
    "Project",
    "QAReport",
    "RenderJob",
    "Scene",
    "SceneAsset",
    "TimestampMixin",
    "TimelineEvent",
    "WorkflowState",
]
