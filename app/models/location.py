"""Location ORM model."""

from __future__ import annotations

from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, IDMixin, TimestampMixin
from app.models.types import json_column, text_column


class Location(Base, IDMixin, TimestampMixin):
    __tablename__ = "locations"

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    country: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown", index=True)
    geocode_payload: Mapped[dict | None] = json_column()
    bbox: Mapped[dict | None] = json_column()
    provenance: Mapped[dict | None] = json_column()
    notes: Mapped[str | None] = text_column(nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Location {self.name} ({self.latitude},{self.longitude})>"
