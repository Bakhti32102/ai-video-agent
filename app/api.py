"""FastAPI application factory.

Exposes a minimal health/status API in Phase 1. The full REST surface for
projects, scenes and renders is a Phase 2 deliverable.
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.config import get_settings
from app.core.logging import get_logger
from app.database import init_db
from app.mcp.client import McpClient
from app.utils.ids import new_id

logger = get_logger("api")


class ProduceRequest(BaseModel):
    script_text: str = Field(min_length=10, max_length=10000)
    total_duration_sec: float = Field(default=30.0, ge=1.0, le=600.0)
    voiceover_path: str | None = None
    project_id: str | None = None


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

    @app.post("/produce")
    async def produce(req: ProduceRequest) -> dict:
        """Run the full video production pipeline and return the result."""
        from app.agents.supervisor import SupervisorAgent

        project_id = req.project_id or new_id("proj_")
        client = McpClient()
        sup = SupervisorAgent(client)
        result = await sup.run_project(
            project_id=project_id,
            script_text=req.script_text,
            voiceover_path=req.voiceover_path,
            total_duration_sec=req.total_duration_sec,
        )
        return {
            "project_id": result["project_id"],
            "final_state": result["final_state"],
            "failed": result["failed"],
            "scenes": len(result["scenes"]),
            "text_overlays": len(result.get("text_overlays", [])),
            "transitions": len(result.get("transitions", [])),
            "render_output": result.get("results", {}).get("render", {}).get("output", {}).get("output_path"),
            "qa_passed": result.get("qa_report", {}).get("passed"),
            "qa_findings": result.get("qa_report", {}).get("findings", []),
        }

    return app


__all__ = ["create_app"]
