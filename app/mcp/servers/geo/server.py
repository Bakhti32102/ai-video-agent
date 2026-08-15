"""Geo/Map MCP server.

Resolves geographic locations to verified coordinates through a pluggable
:class:`~app.services.geo.GeoProvider`. Provider selection is driven by
configuration (``GEO_PROVIDER`` / ``GOOGLE_MAPS_API_KEY``).

CRITICAL GUARDRAIL: this server must never fabricate coordinates. Every
resolved location includes full provenance (provider, query, lat/lon,
display_name, confidence, timestamp). If a location is ambiguous or cannot be
confidently resolved, the result carries ``status = "unresolved"``.

Tools:
- ``geocode_location`` — resolve a place name to verified coordinates
- ``batch_geocode`` — resolve multiple place names
- ``validate_coordinates`` — validate lat/lon ranges
- ``reverse_geocode`` — resolve coordinates to a place name

Legacy tools (backward compat):
- ``geocode`` — alias for ``geocode_location``
- ``build_map_animation`` — builds a map animation spec from a verified location
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName
from app.core.result import Result
from app.mcp.schemas import (
    BatchGeocodeInput,
    BatchGeocodeOutput,
    BuildMapPlanInput,
    BuildMapPlanOutput,
    GeocodeLocationInput,
    GeocodeLocationOutput,
    ReverseGeocodeInput,
    ReverseGeocodeOutput,
    ValidateCoordinatesInput,
    ValidateCoordinatesOutput,
)
from app.mcp.servers.base import BaseMcpServer, ToolDefinition
from app.schemas.contracts import Location
from app.services.geo import GeoProvider, GeocodeResult, get_geo_provider
from app.services.map_engine import MapAnimationEngine


class GeoMcpServer(BaseMcpServer):
    """Resolves geographic locations and generates animated map scenes."""

    name = AgentName.GEO
    version = "3.0.0"
    description = "Geocodes locations via configurable providers; never fabricates coordinates."

    def __init__(self, provider: GeoProvider | None = None) -> None:
        super().__init__()
        self.provider = provider or get_geo_provider()
        self.map_engine = MapAnimationEngine()
        self._register_tool(ToolDefinition(
            name="geocode_location",
            description="Resolve a place name to verified coordinates with full provenance.",
            input_schema=GeocodeLocationInput,
            output_schema=GeocodeLocationOutput,
            handler=self._geocode_location,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="batch_geocode",
            description="Resolve multiple place names in one call.",
            input_schema=BatchGeocodeInput,
            output_schema=BatchGeocodeOutput,
            handler=self._batch_geocode,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="validate_coordinates",
            description="Validate that latitude/longitude are within valid ranges.",
            input_schema=ValidateCoordinatesInput,
            output_schema=ValidateCoordinatesOutput,
            handler=self._validate_coordinates,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="reverse_geocode",
            description="Resolve coordinates to a place name.",
            input_schema=ReverseGeocodeInput,
            output_schema=ReverseGeocodeOutput,
            handler=self._reverse_geocode,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="build_map_plan",
            description="Build a provider-independent MapAnimationPlan from a verified location.",
            input_schema=BuildMapPlanInput,
            output_schema=BuildMapPlanOutput,
            handler=self._build_map_plan,
            tags={"read"},
        ))

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        if tool == "geocode":
            return await self.execute_tool("geocode_location", arguments)
        if tool == "build_map_animation":
            return await self._build_map_animation(arguments)
        return await self.execute_tool(tool, arguments)

    # --- tool handlers ------------------------------------------------------

    async def _geocode_location(self, inp: GeocodeLocationInput) -> Result[GeocodeLocationOutput]:
        result: GeocodeResult = await self.provider.geocode(inp.query)
        return Result.ok(self._result_to_output(result))

    async def _batch_geocode(self, inp: BatchGeocodeInput) -> Result[BatchGeocodeOutput]:
        results: list[dict[str, Any]] = []
        resolved = 0
        unresolved = 0
        for query in inp.queries:
            gr = await self.provider.geocode(query)
            out = self._result_to_output(gr)
            results.append(out)
            if out["status"] == "resolved":
                resolved += 1
            else:
                unresolved += 1
        return Result.ok(BatchGeocodeOutput(
            results=results,
            resolved=resolved,
            unresolved=unresolved,
        ))

    async def _validate_coordinates(self, inp: ValidateCoordinatesInput) -> Result[ValidateCoordinatesOutput]:
        warnings: list[str] = []
        if inp.latitude == 0.0 and inp.longitude == 0.0:
            warnings.append("coordinates (0,0) are almost certainly unset/invalid")
        return Result.ok(ValidateCoordinatesOutput(
            valid=True,
            latitude=inp.latitude,
            longitude=inp.longitude,
            warnings=warnings,
        ))

    async def _reverse_geocode(self, inp: ReverseGeocodeInput) -> Result[ReverseGeocodeOutput]:
        result = await self.provider.reverse_geocode(inp.latitude, inp.longitude)
        return Result.ok(ReverseGeocodeOutput(
            status=result.status,
            display_name=result.display_name,
            provider=result.provider,
            error=result.error,
        ))

    async def _build_map_plan(self, inp: BuildMapPlanInput) -> Result[BuildMapPlanOutput]:
        """Build a MapAnimationPlan from a verified location dict."""
        try:
            loc = self._dict_to_location(inp.location)
        except Exception as exc:
            return Result.fail(f"invalid location: {exc}")
        warnings: list[str] = []
        atype = inp.animation_type
        try:
            if atype == "static":
                plan = self.map_engine.build_static_plan(
                    loc, scene_id=inp.scene_id, duration_sec=inp.duration_sec,
                    zoom=inp.zoom_start, style=inp.style,
                )
            elif atype == "zoom":
                plan = self.map_engine.build_zoom_plan(
                    loc, scene_id=inp.scene_id, duration_sec=inp.duration_sec,
                    zoom_start=inp.zoom_start, zoom_end=inp.zoom_end, style=inp.style,
                )
            elif atype == "pan":
                if not inp.end_location:
                    return Result.fail("pan animation requires end_location")
                end_loc = self._dict_to_location(inp.end_location)
                plan = self.map_engine.build_pan_plan(
                    loc, end_loc, scene_id=inp.scene_id, duration_sec=inp.duration_sec,
                    zoom=inp.zoom_start, style=inp.style,
                )
            elif atype == "route":
                if not inp.route_locations or len(inp.route_locations) < 2:
                    return Result.fail("route animation requires route_locations (>=2)")
                locs = [self._dict_to_location(d) for d in inp.route_locations]
                plan = self.map_engine.build_route_plan(
                    locs, scene_id=inp.scene_id, duration_sec=inp.duration_sec,
                    zoom=inp.zoom_start, style=inp.style,
                )
            else:
                return Result.fail(f"unsupported animation_type: {atype}")
        except ValueError as exc:
            return Result.fail(str(exc))
        warnings.append("map plan is structured; tile rendering via optional adapter")
        return Result.ok(BuildMapPlanOutput(plan=plan.to_dict(), warnings=warnings))

    @staticmethod
    def _dict_to_location(d: dict[str, Any]) -> Location:
        """Convert a dict to a validated Location (requires provenance)."""
        return Location(
            id=d.get("id", "loc"),
            name=d.get("name", "unknown"),
            country=d.get("country"),
            latitude=float(d["latitude"]),
            longitude=float(d["longitude"]),
            source=d.get("source", "unknown"),
            date=d.get("date"),
            provenance=d.get("provenance"),
        )

    # --- legacy: build_map_animation ----------------------------------------

    async def _build_map_animation(self, arguments: dict[str, Any]) -> Result[dict]:
        """Build an animated map scene spec from a verified location.

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
        return self._ok(
            {
                "location": location,
                "style": "default",
                "geojson": None,
                "note": "map rendering returns spec only; tile rendering is a future enhancement",
            },
            warnings=["map tile rendering not yet implemented; returns verified spec only"],
        )

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _result_to_output(result: GeocodeResult) -> dict[str, Any]:
        """Convert a GeocodeResult to the GeocodeLocationOutput dict shape."""
        return GeocodeLocationOutput(
            query=result.query,
            status=result.status,
            latitude=result.latitude,
            longitude=result.longitude,
            display_name=result.display_name,
            confidence=result.confidence,
            provider=result.provider,
            provenance=result.provenance.model_dump(mode="json") if result.provenance else None,
            error=result.error,
        ).model_dump()
