"""Script Understanding MCP server (Phase 1 stub).

Phase 2 will implement LLM-driven script parsing into scenes. For now the
server exposes a deterministic, rule-based splitter so the contract and
plumbing are testable without an LLM.
"""

from __future__ import annotations

from typing import Any

from app.core.result import Result
from app.mcp.servers.base import BaseMcpServer
from app.schemas.contracts import Scene
from app.core.enums import AgentName


class ScriptMcpServer(BaseMcpServer):
    """Understands a documentary script and produces scene specifications."""

    name = AgentName.SCRIPT

    def list_tools(self) -> list[str]:
        return ["split_scenes", "detect_entities"]

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        if tool == "split_scenes":
            return await self.split_scenes(arguments)
        if tool == "detect_entities":
            return await self.detect_entities(arguments)
        return self._fail(f"unknown tool '{tool}' for Script MCP server")

    async def split_scenes(self, arguments: dict[str, Any]) -> Result[list[dict]]:
        """Split script text into scene specs.

        Phase 1 heuristic: paragraphs separated by blank lines become scenes,
        distributed evenly across the requested duration. The full LLM-driven
        understanding is a Phase 2 TODO.
        """
        text = arguments.get("script_text", "")
        total_duration = float(arguments.get("total_duration_sec", 0.0) or 0.0)
        project_id = arguments.get("project_id", "proj")

        if not text or not text.strip():
            return self._fail("script_text must not be empty")
        if total_duration <= 0:
            return self._fail("total_duration_sec must be positive")

        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [text.strip()]

        n = len(paragraphs)
        per = total_duration / n
        scenes: list[dict] = []
        for i, para in enumerate(paragraphs):
            start = i * per
            scenes.append(
                {
                    "index": i,
                    "title": f"Scene {i + 1}",
                    "narration": para,
                    "start_time": start,
                    "end_time": start + per,
                    "project_id": project_id,
                }
            )
        return self._ok(scenes, warnings=["script splitting is a heuristic stub; LLM understanding is Phase 2"])

    async def detect_entities(self, arguments: dict[str, Any]) -> Result[dict]:
        """Detect locations, dates, people, events, objects.

        TODO(Phase 2): implement NER/LLM-based entity detection. Returns an
        empty structure for now so callers can rely on the contract shape.
        """
        text = arguments.get("script_text", "")
        if not text or not text.strip():
            return self._fail("script_text must not be empty")
        return self._ok(
            {"locations": [], "dates": [], "people": [], "events": [], "objects": []},
            warnings=["entity detection is not implemented; returns empty lists (Phase 2)"],
        )
