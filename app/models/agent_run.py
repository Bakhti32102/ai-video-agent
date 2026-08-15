"""AgentRun ORM model."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, IDMixin, TimestampMixin
from app.models.types import json_column, text_column


class AgentRun(Base, IDMixin, TimestampMixin):
    __tablename__ = "agent_runs"

    project_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="pending", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    input_payload: Mapped[dict | None] = json_column()
    output_payload: Mapped[dict | None] = json_column()
    error: Mapped[str | None] = text_column(nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AgentRun {self.agent} [{self.status}] #{self.attempt}>"
