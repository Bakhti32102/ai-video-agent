"""TimelineEvent ORM model."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, IDMixin, TimestampMixin
from app.models.types import json_column


class TimelineEvent(Base, IDMixin, TimestampMixin):
    """A single event on the project timeline (visual, text, sound, transition)."""

    __tablename__ = "timeline_events"

    project_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scene_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    layer: Mapped[int] = mapped_column(default=0)
    payload: Mapped[dict | None] = json_column()

    scene: Mapped["Scene | None"] = relationship(back_populates="timeline_events")  # type: ignore[name-defined]  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TimelineEvent {self.event_type} {self.start_time}->{self.end_time}>"
