"""QAReport ORM model."""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, IDMixin, TimestampMixin
from app.models.types import json_column, text_column


class QAReport(Base, IDMixin, TimestampMixin):
    __tablename__ = "qa_reports"

    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="pending")
    passed: Mapped[bool] = mapped_column(default=False)
    summary: Mapped[str | None] = text_column(nullable=True)
    findings: Mapped[dict | None] = json_column()
    checked_at: Mapped[str | None] = text_column(nullable=True)

    project: Mapped["Project"] = relationship(back_populates="qa_reports")  # type: ignore[name-defined]  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"<QAReport {self.id} passed={self.passed}>"
