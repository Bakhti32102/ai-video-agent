"""FastAPI application factory.

Exposes a minimal health/status API in Phase 1. The full REST surface for
projects, scenes and renders is a Phase 2 deliverable.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.config import get_settings
from app.core.logging import get_logger
from app.database import init_db
from app.mcp.client import McpClient

logger = get_logger("api")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()
    settings.ensure_runtime_dirs()
    title = settings.app_name
    app = FastAPI(title=title, version="0.1.0", docs_url="/docs", redoc_url="/redoc")

    @app.on_event("startup")
    async def _startup() -> None:
        logger.info("Starting %s in %s mode", title, settings.app_env)
        init_db(settings)

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "app": settings.app_name,
            "env": settings.app_env,
            "api_keys": settings.api_keys_present(),
        }

    @app.get("/mcp/tools")
    async def mcp_tools() -> dict:
        client = McpClient()
        return {"servers": client.available_tools()}

    return app


__all__ = ["create_app"]
