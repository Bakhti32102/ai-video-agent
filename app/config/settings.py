"""Application configuration loaded from environment variables.

All runtime configuration is read via :class:`Settings` so that nothing is
hard-coded. Real secrets must live in a ``.env`` file (never committed) and are
surfaced only through these typed settings objects.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root: two levels up from this file (app/config/settings.py -> root)
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

AppEnv = Literal["development", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
MapProvider = Literal["nominatim", "mapbox", "maptiler", "google", "none"]
LlmProvider = Literal["openai", "openrouter", "google", "none"]


class Settings(BaseSettings):
    """Typed application settings sourced from environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_env: AppEnv = "development"
    app_name: str = "ai-video-agent"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: LogLevel = "INFO"

    # --- Database ---
    database_url: str = "sqlite:///./data/ai_video_agent.db"

    # --- LLM ---
    llm_provider: LlmProvider = "none"
    llm_model: str = ""
    openai_api_key: str = ""
    openrouter_api_key: str = ""
    google_api_key: str = ""

    # --- Map / geocoding ---
    map_provider: MapProvider = "none"
    map_api_key: str = ""

    # --- FFmpeg ---
    ffmpeg_path: str = "ffmpeg"

    # --- Runtime directories ---
    data_dir: str = "./data"
    assets_dir: str = "./assets"
    output_dir: str = "./output"
    logs_dir: str = "./logs"

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("DATABASE_URL must not be empty")
        if not v.startswith(("sqlite://", "postgresql://", "postgresql+psycopg://")):
            raise ValueError(
                "DATABASE_URL must be a sqlite://, postgresql://, "
                "or postgresql+psycopg:// URL"
            )
        return v.strip()

    def resolved_path(self, raw: str) -> Path:
        """Resolve a possibly-relative path against the project root."""
        p = Path(raw)
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()

    @property
    def data_path(self) -> Path:
        return self.resolved_path(self.data_dir)

    @property
    def assets_path(self) -> Path:
        return self.resolved_path(self.assets_dir)

    @property
    def output_path(self) -> Path:
        return self.resolved_path(self.output_dir)

    @property
    def logs_path(self) -> Path:
        return self.resolved_path(self.logs_dir)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def ensure_runtime_dirs(self) -> None:
        """Create the runtime data/asset/output/log directories if missing."""
        for p in (self.data_path, self.assets_path, self.output_path, self.logs_path):
            p.mkdir(parents=True, exist_ok=True)

    def api_keys_present(self) -> dict[str, bool]:
        """Report which provider keys are configured (without exposing values)."""
        return {
            "openai": bool(self.openai_api_key),
            "openrouter": bool(self.openrouter_api_key),
            "google": bool(self.google_api_key),
            "map": bool(self.map_api_key),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` singleton."""
    return Settings()
