"""AudioFile ORM model."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, IDMixin, TimestampMixin
from app.models.types import json_column, text_column


class AudioFile(Base, IDMixin, TimestampMixin):
    __tablename__ = "audio_files"

    project_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    audio_type: Mapped[str] = mapped_column(String(64), nullable=False, default="voiceover", index=True)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    duration_sec: Mapped[float] = mapped_column(Float, nullable=False)
    sample_rate: Mapped[int | None] = mapped_column(nullable=True)
    channels: Mapped[int | None] = mapped_column(nullable=True)
    analysis: Mapped[dict | None] = json_column()
    transcript: Mapped[str | None] = text_column(nullable=True)

    project: Mapped["Project"] = relationship(back_populates="audio_files")  # type: ignore[name-defined]  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AudioFile {self.audio_type} {self.file_path}>"
