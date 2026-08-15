"""Unit tests for configuration validation."""

from __future__ import annotations

import importlib
import os

import pytest


def test_default_settings_load() -> None:
    from app.config import Settings

    settings = Settings()
    assert settings.app_name == "ai-video-agent"
    assert settings.app_env in {"development", "staging", "production"}
    assert settings.log_level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def test_database_url_must_not_be_empty() -> None:
    from app.config import Settings
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(database_url="")


def test_database_url_rejects_unsupported_scheme() -> None:
    from app.config import Settings
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(database_url="mysql://user:pass@localhost/db")


def test_database_url_accepts_sqlite_and_postgres() -> None:
    from app.config import Settings

    assert Settings(database_url="sqlite:///./x.db").database_url == "sqlite:///./x.db"
    assert Settings(database_url="postgresql://u:p@h/db").database_url == "postgresql://u:p@h/db"


def test_api_keys_present_does_not_expose_values() -> None:
    from app.config import Settings

    settings = Settings(openai_api_key="sk-test", map_api_key="")
    present = settings.api_keys_present()
    assert present["openai"] is True
    assert present["map"] is False
    # The method returns booleans, never the raw key.
    assert all(isinstance(v, bool) for v in present.values())


def test_resolved_path_relative_to_root(tmp_path, monkeypatch) -> None:
    from app.config import Settings, PROJECT_ROOT

    settings = Settings(data_dir="./data")
    assert settings.resolved_path("./data") == (PROJECT_ROOT / "data").resolve()


def test_get_settings_is_cached() -> None:
    from app.config import get_settings

    a = get_settings()
    b = get_settings()
    assert a is b


def test_env_overrides_defaults(monkeypatch) -> None:
    import app.config.settings as mod

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./env_override.db")
    settings = mod.Settings()
    assert settings.app_env == "production"
    assert settings.log_level == "DEBUG"
    assert settings.database_url == "sqlite:///./env_override.db"
