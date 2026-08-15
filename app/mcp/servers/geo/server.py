"""Geo/Map MCP server (Phase 1 stub).

CRITICAL GUARDRAIL: this server must never invent geographic coordinates. In
Phase 1 it refuses to resolve any location because no real geocoding provider
is wired up. Phase 2 will integrate a configurable provider (Nominatim,
Mapbox, etc.) and emit traceable GeoJSON/vector data.
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName
from app.core.result import Result
from app.mcp.servers.base import BaseMcpServer


class GeoMcpServer(BaseMcpServer):
    """Resolves geographic locations and generates animated map scenes."""

    name = AgentName.GEO

    def list_tools(self) -> list[str]:
        return ["geocode", "build_map_animation"]

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        if tool == "geocode":
            return await self.geocode(arguments)
        if tool == "build_map_animation":
            return await self.build_map_animation(arguments)
        return self._fail(f"unknown tool '{tool}' for Geo MCP server")

    async def geocode(self, arguments: dict[str, Any]) -> Result[dict]:
        """Resolve a place name to verified coordinates.

        Phase 1: always refuses, because no provider is configured. Returning
        fabricated coordinates would violate the core geo guardrail.
        """
        query = arguments.get("query", "")
        if not query or not str(query).strip():
            return self._fail("query must not be empty")
        # TODO(Phase 2): call configured MAP_PROVIDER with MAP_API_KEY.
        return self._fail(
            "geocoding is not implemented in Phase 1; refusing to return "
            "unverifiable coordinates. Configure MAP_PROVIDER in Phase 2."
        )

    async def build_map_animation(self, arguments: dict[str, Any]) -> Result[dict]:
        """Build an animated map scene spec from verified location data.

        Requires a caller-supplied, already-verified Location so coordinates
        remain traceable to their source.
        """
        location = arguments.get("location")
        if not isinstance(location, dict):
            return self._fail("location (dict) is required")
        for key in ("latitude", "longitude", "source"):
            if key not in location:
                return self._fail(f"location missing required field '{key}'")
        lat = location.get("latitude")
        lon = location.get("longitude")
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            return self._fail("latitude/longitude must be numeric")
        if not -90.0 <= lat <= 90.0:
            return self._fail(f"latitude {lat} out of range")
        if not -180.0 <= lon <= 180.0:
            return self._fail(f"longitude {lon} out of range")
        if not str(location.get("source", "")).strip() or str(location["source"]).strip().lower() in {"unknown", "none"}:
            return self._fail("location source must be traceable; refusing unverifiable coordinates")
        # TODO(Phase 2): render actual map tiles / GeoJSON via MapLibre/Leaflet.
        return self._ok(
            {
                "location": location,
                "style": "default",
                "geojson": None,
                "note": "map rendering is Phase 2; only the verified spec is returned",
            },
            warnings=["map rendering not implemented; returns spec only (Phase 2)"],
        )
