"""Application entrypoint.

Run the API server:
    python -m app.main               # starts uvicorn
    python -m app.main init-db       # initialize the database and exit
    python -m app.main check         # verify config + db + imports
"""

from __future__ import annotations

import sys

from app.config import get_settings
from app.core.logging import get_logger
from app.database import init_db, reset_engine
from app.mcp.client import McpClient

logger = get_logger("main")


def cmd_init_db() -> int:
    settings = get_settings()
    settings.ensure_runtime_dirs()
    init_db(settings)
    logger.info("Database initialized at %s", settings.database_url)
    return 0


def cmd_check() -> int:
    """Verify configuration, imports, database and MCP registry."""
    settings = get_settings()
    settings.ensure_runtime_dirs()
    logger.info("Configuration OK: env=%s, log_level=%s", settings.app_env, settings.log_level)
    logger.info("API keys present: %s", settings.api_keys_present())

    init_db(settings)
    logger.info("Database initialized OK")

    client = McpClient()
    tools = client.available_tools()
    logger.info("MCP servers registered: %s", list(tools))
    return 0


def cmd_serve(host: str | None = None, port: int | None = None) -> int:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.api:create_app",
        factory=True,
        host=host or settings.app_host,
        port=port or settings.app_port,
        log_level=settings.log_level.lower(),
    )
    return 0


def cmd_produce(argv: list[str]) -> int:
    """Run the full video production pipeline on a script.

    Usage:
        python -m app.main produce --script "The Gadsden Purchase..." [--duration 30]
                                   [--voiceover path/to/audio.mp3]
                                   [--project-id my_project]
    """
    import argparse
    import asyncio
    import json

    parser = argparse.ArgumentParser(prog="app.main produce", description="Produce a documentary video")
    parser.add_argument("--script", required=True, help="Documentary script text")
    parser.add_argument("--duration", type=float, default=30.0, help="Target video duration in seconds")
    parser.add_argument("--voiceover", default=None, help="Path to voiceover audio file")
    parser.add_argument("--project-id", default=None, help="Project identifier (auto-generated if omitted)")
    args = parser.parse_args(argv)

    settings = get_settings()
    settings.ensure_runtime_dirs()
    init_db(settings)

    from app.agents.supervisor import SupervisorAgent
    from app.utils.ids import new_id

    project_id = args.project_id or new_id("proj_")

    async def _run() -> dict:
        client = McpClient()
        sup = SupervisorAgent(client)
        return await sup.run_project(
            project_id=project_id,
            script_text=args.script,
            voiceover_path=args.voiceover,
            total_duration_sec=args.duration,
        )

    result = asyncio.run(_run())
    # Print summary to stdout.
    print(json.dumps({
        "project_id": result["project_id"],
        "final_state": result["final_state"],
        "failed": result["failed"],
        "scenes": len(result["scenes"]),
        "text_overlays": len(result.get("text_overlays", [])),
        "transitions": len(result.get("transitions", [])),
        "render_output": result.get("results", {}).get("render", {}).get("output", {}).get("output_path"),
        "qa_passed": result.get("qa_report", {}).get("passed"),
    }, indent=2))
    return 0 if not result["failed"] else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    reset_engine()  # start clean
    if not argv or argv[0] in {"serve", "run"}:
        return cmd_serve()
    if argv[0] == "init-db":
        return cmd_init_db()
    if argv[0] == "check":
        return cmd_check()
    if argv[0] == "produce":
        return cmd_produce(argv[1:])
    logger.error("unknown command: %s", argv[0])
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
