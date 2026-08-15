"""SQLAlchemy database engine/session management.

Uses SQLite by default (configured via ``DATABASE_URL``) and is designed so the
schema can later migrate to PostgreSQL without code changes. ``check_same_thread``
is disabled for SQLite to allow FastAPI background tasks.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.core.exceptions import DatabaseError
from app.core.logging import get_logger

logger = get_logger("database")

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _build_engine(settings: Settings) -> Engine:
    url = settings.database_url
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    return create_engine(url, connect_args=connect_args, future=True, echo=False)


def get_engine(settings: Settings | None = None) -> Engine:
    """Return a lazily-initialized, cached SQLAlchemy engine."""
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        _engine = _build_engine(settings)
        logger.debug("Database engine created for %s", _safe_url(settings.database_url))
    return _engine


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    """Return a cached session factory bound to the engine."""
    global _SessionLocal
    if _SessionLocal is None:
        engine = get_engine(settings)
        _SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
    return _SessionLocal


def _safe_url(url: str) -> str:
    """Mask any password embedded in a database URL for logging."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        if ":" in rest.split("@", 1)[0]:
            creds, host_part = rest.split("@", 1)
            user = creds.split(":", 1)[0]
            return f"{scheme}://{user}:***@{host_part}"
    return url


def init_db(settings: Settings | None = None) -> None:
    """Create all tables defined on the metadata.

    Must be called after :mod:`app.models` has been imported so that all ORM
    models are registered on the shared metadata.
    """
    settings = settings or get_settings()
    # Import here to ensure all models are registered before create_all.
    from app.models import Base  # noqa: F401  (side-effect import)

    engine = get_engine(settings)
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized (tables ensured).")


def reset_engine() -> None:
    """Reset cached engine/session (used by tests to point at a fresh DB)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


@contextmanager
def session_scope(settings: Settings | None = None) -> Generator[Session, None, None]:
    """Context manager yielding a session that commits/rolls back automatically."""
    factory = get_session_factory(settings)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Database session failed; rolled back.")
        raise DatabaseError("Database session failed") from None
    finally:
        session.close()
