"""WorkflowState ORM model (Supervisor coordination state)."""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, IDMixin, TimestampMixin
from app.models.types import json_column, text_column


class WorkflowState(Base, IDMixin, TimestampMixin):
    __tablename__ = "workflow_state"

    project_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    current_state: Mapped[str] = mapped_column(String(64), nullable=False, default="created", index=True)
    current_phase: Mapped[str] = mapped_column(String(64), nullable=False, default="init")
    previous_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_phase: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retries: Mapped[dict | None] = json_column()
    agent_statuses: Mapped[dict | None] = json_column()
    notes: Mapped[str | None] = text_column(nullable=True)

    project: Mapped["Project"] = relationship(back_populates="workflow_state")  # type: ignore[name-defined]  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WorkflowState project={self.project_id} state={self.current_state}>"
