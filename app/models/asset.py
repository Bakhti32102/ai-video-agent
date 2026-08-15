"""Asset and SceneAsset ORM models."""

from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, IDMixin, TimestampMixin
from app.models.types import text_column


class Asset(Base, IDMixin, TimestampMixin):
    """A reusable visual/audio asset."""

    __tablename__ = "assets"

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    asset_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False, default="unknown", index=True)
    license: Mapped[str | None] = mapped_column(String(255), nullable=True)
    width: Mapped[int | None] = mapped_column(nullable=True)
    height: Mapped[int | None] = mapped_column(nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(nullable=True)
    extra_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    scene_links: Mapped[list["SceneAsset"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        back_populates="asset", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Asset {self.name} [{self.asset_type}/{self.format}]>"


class SceneAsset(Base, IDMixin, TimestampMixin):
    """Associates an asset with a scene, including on-scene placement."""

    __tablename__ = "scene_assets"

    scene_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="primary")
    placement: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    scene: Mapped["Scene"] = relationship(back_populates="assets")  # type: ignore[name-defined]  # noqa: F821
    asset: Mapped["Asset"] = relationship(back_populates="scene_links")  # type: ignore[name-defined]  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SceneAsset scene={self.scene_id} asset={self.asset_id}>"
