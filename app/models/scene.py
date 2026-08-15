"""Scene ORM model."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, IDMixin, TimestampMixin
from app.models.types import json_column, text_column


class Scene(Base, IDMixin, TimestampMixin):
    __tablename__ = "scenes"
    __table_args__ = (UniqueConstraint("project_id", "index", name="uq_scene_project_index"),)

    project_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="pending", index=True)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    narration: Mapped[str | None] = text_column(nullable=True)
    visual_requirements: Mapped[str | None] = text_column(nullable=True)
    location_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("locations.id", ondelete="SET NULL"), nullable=True
    )
    spec: Mapped[dict | None] = json_column()

    project: Mapped["Project"] = relationship(back_populates="scenes")  # type: ignore[name-defined]  # noqa: F821
    location: Mapped["Location | None"] = relationship()  # type: ignore[name-defined]  # noqa: F821
    assets: Mapped[list["SceneAsset"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        back_populates="scene", cascade="all, delete-orphan"
    )
    timeline_events: Mapped[list["TimelineEvent"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        back_populates="scene", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Scene {self.index} {self.title} [{self.status}]>"
