"""RenderJob ORM model."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, IDMixin, TimestampMixin
from app.models.types import json_column, text_column


class RenderJob(Base, IDMixin, TimestampMixin):
    __tablename__ = "render_jobs"

    project_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="queued", index=True)
    output_path: Mapped[str | None] = text_column(nullable=True)
    format: Mapped[str] = mapped_column(String(16), nullable=False, default="mp4")
    width: Mapped[int] = mapped_column(Integer, default=1920)
    height: Mapped[int] = mapped_column(Integer, default=1080)
    fps: Mapped[float] = mapped_column(Float, default=30.0)
    duration_sec: Mapped[float | None] = mapped_column(nullable=True)
    started_at: Mapped[str | None] = text_column(nullable=True)
    finished_at: Mapped[str | None] = text_column(nullable=True)
    error: Mapped[str | None] = text_column(nullable=True)
    params: Mapped[dict | None] = json_column()

    project: Mapped["Project"] = relationship(back_populates="render_jobs")  # type: ignore[name-defined]  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RenderJob {self.id} [{self.status}]>"
