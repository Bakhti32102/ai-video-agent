"""Project ORM model."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, IDMixin, TimestampMixin
from app.models.types import json_column, text_column


class Project(Base, IDMixin, TimestampMixin):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="created", index=True)
    script_text: Mapped[str | None] = text_column(nullable=True)
    voiceover_path: Mapped[str | None] = text_column(nullable=True)
    target_duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="16:9", nullable=False)
    resolution_width: Mapped[int] = mapped_column(default=1920)
    resolution_height: Mapped[int] = mapped_column(default=1080)
    output_path: Mapped[str | None] = text_column(nullable=True)
    config: Mapped[dict | None] = json_column()

    scenes: Mapped[list["Scene"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        back_populates="project", cascade="all, delete-orphan"
    )
    audio_files: Mapped[list["AudioFile"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        back_populates="project", cascade="all, delete-orphan"
    )
    render_jobs: Mapped[list["RenderJob"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        back_populates="project", cascade="all, delete-orphan"
    )
    qa_reports: Mapped[list["QAReport"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        back_populates="project", cascade="all, delete-orphan"
    )
    workflow_state: Mapped["WorkflowState | None"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        back_populates="project", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project {self.id} {self.name} [{self.status}]>"
