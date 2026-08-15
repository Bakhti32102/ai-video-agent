"""Provider-independent map animation engine.

Generates a structured :class:`MapAnimationPlan` from verified geographic
data, then renders it to a static image (SVG/PNG) using open-source tooling
(Pillow). This is deliberately decoupled from any specific map renderer so an
optional GEOlayers/After Effects adapter can be added later without changing
the Supervisor.

Design rules:
- Never fabricate geographic boundaries or coordinates. All plans are built
  from *verified* :class:`~app.schemas.contracts.Location` data that already
  carries provenance.
- The plan is a structured, serialisable object — the renderer is a separate
  concern.
- Rendering produces a real image file (SVG and/or PNG) that the Render MCP
  server can consume.

Supported animation types:
- ``static``    — a centered static map frame
- ``zoom``      — zoom-in / zoom-out from a center point
- ``pan``       — pan from one offset to another
- ``route``     — animate along a path of coordinates (where data available)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.schemas.contracts import GeoProvenance, Location
from app.utils.ids import new_id
from app.utils.paths import restrict_to_directory

logger = get_logger("map_engine")

MapAnimationType = Literal["static", "zoom", "pan", "route"]
MapStyle = Literal["default", "terrain", "political", "satellite", "minimal"]


@dataclass
class MapMarker:
    """A marker placed on the map."""

    latitude: float
    longitude: float
    label: str | None = None
    color: str = "#E63946"
    size: int = 12


@dataclass
class MapAnimationPlan:
    """Structured, provider-independent description of a map animation.

    This is the contract between the Geo/Map engine and the Render engine.
    It contains *only* verified data (coordinates carry provenance). It never
    includes fabricated boundaries.
    """

    id: str = field(default_factory=lambda: new_id("map_"))
    scene_id: str = ""
    location_id: str = ""
    animation_type: MapAnimationType = "static"
    style: MapStyle = "default"
    # Center coordinates (verified).
    center_latitude: float = 0.0
    center_longitude: float = 0.0
    zoom_start: float = 4.0
    zoom_end: float = 4.0
    bearing_start: float = 0.0
    bearing_end: float = 0.0
    # For pan/route animations.
    start_latitude: float | None = None
    start_longitude: float | None = None
    end_latitude: float | None = None
    end_longitude: float | None = None
    # Route path (list of verified coordinate points).
    route: list[dict[str, float]] = field(default_factory=list)
    markers: list[MapMarker] = field(default_factory=list)
    labels: list[dict[str, Any]] = field(default_factory=list)
    # Timing.
    duration_sec: float = 5.0
    easing: str = "ease_in_out"
    # Output.
    aspect_ratio: str = "16:9"
    output_width: int = 1920
    output_height: int = 1080
    # Provenance — never invented.
    source: str = "verified"
    provenance: GeoProvenance | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "scene_id": self.scene_id,
            "location_id": self.location_id,
            "animation_type": self.animation_type,
            "style": self.style,
            "center_latitude": self.center_latitude,
            "center_longitude": self.center_longitude,
            "zoom_start": self.zoom_start,
            "zoom_end": self.zoom_end,
            "bearing_start": self.bearing_start,
            "bearing_end": self.bearing_end,
            "duration_sec": self.duration_sec,
            "easing": self.easing,
            "aspect_ratio": self.aspect_ratio,
            "output_width": self.output_width,
            "output_height": self.output_height,
            "source": self.source,
            "markers": [
                {"latitude": m.latitude, "longitude": m.longitude, "label": m.label, "color": m.color, "size": m.size}
                for m in self.markers
            ],
            "labels": self.labels,
        }
        if self.start_latitude is not None:
            d["start_latitude"] = self.start_latitude
            d["start_longitude"] = self.start_longitude
            d["end_latitude"] = self.end_latitude
            d["end_longitude"] = self.end_longitude
        if self.route:
            d["route"] = self.route
        if self.provenance is not None:
            d["provenance"] = self.provenance.model_dump(mode="json")
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MapAnimationPlan":
        """Reconstruct a MapAnimationPlan from its dict form (e.g. from build_map_plan output).

        This is used by the render_map tool to render a plan that was serialized
        through the MCP boundary. Provenance is reconstructed if present.
        Raises ValueError if the dict is not a recognizable map plan.
        """
        if not isinstance(d, dict) or not d:
            raise ValueError("plan dict is empty or not a dict")
        # A valid plan must have at least a center coordinate or markers.
        has_center = "center_latitude" in d and "center_longitude" in d
        has_markers = bool(d.get("markers"))
        if not has_center and not has_markers:
            raise ValueError("plan dict has no center coordinates or markers")
        markers = [
            MapMarker(
                latitude=m.get("latitude", 0.0),
                longitude=m.get("longitude", 0.0),
                label=m.get("label"),
                color=m.get("color", "#E63946"),
                size=m.get("size", 12),
            )
            for m in d.get("markers", [])
        ]
        provenance = None
        prov_data = d.get("provenance")
        if isinstance(prov_data, dict):
            try:
                provenance = GeoProvenance.model_validate(prov_data)
            except Exception:
                provenance = None
        return cls(
            id=d.get("id", new_id("map_")),
            scene_id=d.get("scene_id", ""),
            location_id=d.get("location_id", ""),
            animation_type=d.get("animation_type", "static"),
            style=d.get("style", "default"),
            center_latitude=d.get("center_latitude", 0.0),
            center_longitude=d.get("center_longitude", 0.0),
            zoom_start=d.get("zoom_start", 4.0),
            zoom_end=d.get("zoom_end", 4.0),
            bearing_start=d.get("bearing_start", 0.0),
            bearing_end=d.get("bearing_end", 0.0),
            start_latitude=d.get("start_latitude"),
            start_longitude=d.get("start_longitude"),
            end_latitude=d.get("end_latitude"),
            end_longitude=d.get("end_longitude"),
            route=d.get("route", []),
            markers=markers,
            labels=d.get("labels", []),
            duration_sec=d.get("duration_sec", 5.0),
            easing=d.get("easing", "ease_in_out"),
            aspect_ratio=d.get("aspect_ratio", "16:9"),
            output_width=d.get("output_width", 1920),
            output_height=d.get("output_height", 1080),
            source=d.get("source", "verified"),
            provenance=provenance,
        )


class MapAnimationEngine:
    """Builds :class:`MapAnimationPlan` objects from verified locations.

    This engine does *not* call any external map tile API. It produces a
    structured plan that the renderer can turn into an image. The plan always
    carries the location's provenance so coordinates remain traceable.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def build_static_plan(
        self,
        location: Location,
        *,
        scene_id: str = "",
        duration_sec: float = 5.0,
        zoom: float = 5.0,
        style: MapStyle = "default",
    ) -> MapAnimationPlan:
        """Build a static map plan centered on a verified location."""
        self._require_verified(location)
        return MapAnimationPlan(
            scene_id=scene_id,
            location_id=location.id,
            animation_type="static",
            style=style,
            center_latitude=location.latitude,
            center_longitude=location.longitude,
            zoom_start=zoom,
            zoom_end=zoom,
            duration_sec=duration_sec,
            markers=[MapMarker(location.latitude, location.longitude, location.name)],
            source=location.source,
            provenance=location.provenance,
            output_width=self.settings.video_width,
            output_height=self.settings.video_height,
        )

    def build_zoom_plan(
        self,
        location: Location,
        *,
        scene_id: str = "",
        duration_sec: float = 5.0,
        zoom_start: float = 3.0,
        zoom_end: float = 7.0,
        style: MapStyle = "default",
    ) -> MapAnimationPlan:
        """Build a zoom animation plan (zoom in or out)."""
        self._require_verified(location)
        return MapAnimationPlan(
            scene_id=scene_id,
            location_id=location.id,
            animation_type="zoom",
            style=style,
            center_latitude=location.latitude,
            center_longitude=location.longitude,
            zoom_start=zoom_start,
            zoom_end=zoom_end,
            duration_sec=duration_sec,
            markers=[MapMarker(location.latitude, location.longitude, location.name)],
            source=location.source,
            provenance=location.provenance,
            output_width=self.settings.video_width,
            output_height=self.settings.video_height,
        )

    def build_pan_plan(
        self,
        start_location: Location,
        end_location: Location,
        *,
        scene_id: str = "",
        duration_sec: float = 6.0,
        zoom: float = 5.0,
        style: MapStyle = "default",
    ) -> MapAnimationPlan:
        """Build a pan animation from one verified location to another."""
        self._require_verified(start_location)
        self._require_verified(end_location)
        center_lat = (start_location.latitude + end_location.latitude) / 2
        center_lon = (start_location.longitude + end_location.longitude) / 2
        return MapAnimationPlan(
            scene_id=scene_id,
            location_id=start_location.id,
            animation_type="pan",
            style=style,
            center_latitude=center_lat,
            center_longitude=center_lon,
            zoom_start=zoom,
            zoom_end=zoom,
            start_latitude=start_location.latitude,
            start_longitude=start_location.longitude,
            end_latitude=end_location.latitude,
            end_longitude=end_location.longitude,
            duration_sec=duration_sec,
            markers=[
                MapMarker(start_location.latitude, start_location.longitude, start_location.name, "#E63946"),
                MapMarker(end_location.latitude, end_location.longitude, end_location.name, "#06A77D"),
            ],
            source=f"{start_location.source};{end_location.source}",
            provenance=start_location.provenance,
            output_width=self.settings.video_width,
            output_height=self.settings.video_height,
        )

    def build_route_plan(
        self,
        locations: list[Location],
        *,
        scene_id: str = "",
        duration_sec: float = 8.0,
        zoom: float = 5.0,
        style: MapStyle = "default",
    ) -> MapAnimationPlan:
        """Build a route/path animation along verified locations."""
        if len(locations) < 2:
            raise ValueError("route animation requires at least 2 verified locations")
        for loc in locations:
            self._require_verified(loc)
        route = [
            {"latitude": loc.latitude, "longitude": loc.longitude}
            for loc in locations
        ]
        markers = [
            MapMarker(loc.latitude, loc.longitude, loc.name)
            for loc in locations
        ]
        center_lat = sum(loc.latitude for loc in locations) / len(locations)
        center_lon = sum(loc.longitude for loc in locations) / len(locations)
        return MapAnimationPlan(
            scene_id=scene_id,
            location_id=locations[0].id,
            animation_type="route",
            style=style,
            center_latitude=center_lat,
            center_longitude=center_lon,
            zoom_start=zoom,
            zoom_end=zoom,
            route=route,
            markers=markers,
            duration_sec=duration_sec,
            source=";".join(loc.source for loc in locations),
            provenance=locations[0].provenance,
            output_width=self.settings.video_width,
            output_height=self.settings.video_height,
        )

    @staticmethod
    def _require_verified(location: Location) -> None:
        """Refuse to build a plan from an unverified location."""
        if not location.source or location.source.strip().lower() in {"unknown", "none", ""}:
            raise ValueError(
                f"location '{location.name}' has no traceable source; "
                "refusing to build map plan from unverifiable coordinates"
            )


class MapRenderer:
    """Renders a :class:`MapAnimationPlan` to a static image (SVG + PNG).

    Uses only open-source tooling: SVG generation + Pillow for PNG raster.
    Does *not* fetch map tiles. The result is a stylized map frame suitable
    as a video background — it represents the *plan*, not satellite imagery.

    For real map tiles, a future adapter can fetch OSM raster tiles and
    composite them; this engine provides the structured plan that adapter
    would consume.
    """

    # Stylized color palette (dark documentary theme).
    BG_COLOR = "#1a1a2e"
    LAND_COLOR = "#16213e"
    WATER_COLOR = "#0f3460"
    GRID_COLOR = "#2a2a4a"
    MARKER_COLOR = "#E63946"
    TEXT_COLOR = "#FFFFFF"
    ROUTE_COLOR = "#06A77D"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _validate_output_path(self, path: str) -> Path:
        """Output must be inside the approved output or temp directory."""
        from app.core.exceptions import FileSafetyError

        for root_attr in ("output_path", "temp_path"):
            root = getattr(self.settings, root_attr)
            try:
                return restrict_to_directory(path, root)
            except FileSafetyError:
                continue
        raise FileSafetyError(f"output path not in approved directory: {path}")

    def render_to_svg(self, plan: MapAnimationPlan, output_path: str) -> str:
        """Render a plan to an SVG file. Returns the output path."""
        w, h = plan.output_width, plan.output_height
        svg_parts: list[str] = [
            f'<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
            f'<rect width="{w}" height="{h}" fill="{self.BG_COLOR}"/>',
            # Land mass background.
            f'<rect width="{w}" height="{h}" fill="{self.LAND_COLOR}" opacity="0.6"/>',
        ]
        # Grid lines (stylized, not real geography).
        grid_spacing = 80
        for x in range(0, w, grid_spacing):
            svg_parts.append(
                f'<line x1="{x}" y1="0" x2="{x}" y2="{h}" stroke="{self.GRID_COLOR}" stroke-width="1" opacity="0.3"/>'
            )
        for y in range(0, h, grid_spacing):
            svg_parts.append(
                f'<line x1="0" y1="{y}" x2="{w}" y2="{y}" stroke="{self.GRID_COLOR}" stroke-width="1" opacity="0.3"/>'
            )
        # Project coordinates to pixel space (equirectangular, centered).
        cx, cy = self._project(plan.center_latitude, plan.center_longitude, w, h, plan.zoom_start)
        # Route path.
        if plan.route:
            points = [
                self._project(p["latitude"], p["longitude"], w, h, plan.zoom_start)
                for p in plan.route
            ]
            path_d = " M ".join(f"{px},{py}" for px, py in points)
            svg_parts.append(
                f'<path d="M {path_d}" stroke="{self.ROUTE_COLOR}" stroke-width="4" fill="none" opacity="0.8"/>'
            )
        # Markers.
        for m in plan.markers:
            mx, my = self._project(m.latitude, m.longitude, w, h, plan.zoom_start)
            svg_parts.append(
                f'<circle cx="{mx}" cy="{my}" r="{m.size}" fill="{m.color}" stroke="{self.TEXT_COLOR}" stroke-width="2"/>'
            )
            if m.label:
                svg_parts.append(
                    f'<text x="{mx}" y="{my - m.size - 8}" fill="{self.TEXT_COLOR}" '
                    f'font-size="28" text-anchor="middle" font-family="sans-serif">{self._escape(m.label)}</text>'
                )
        # Title/label.
        title = self._plan_title(plan)
        svg_parts.append(
            f'<text x="40" y="60" fill="{self.TEXT_COLOR}" font-size="36" font-family="sans-serif" font-weight="bold">'
            f'{self._escape(title)}</text>'
        )
        # Provenance footer (never hide the data source).
        if plan.provenance:
            prov_text = f"Source: {plan.provenance.provider} | {plan.provenance.source}"
            svg_parts.append(
                f'<text x="40" y="{h - 30}" fill="{self.TEXT_COLOR}" font-size="18" font-family="sans-serif" opacity="0.7">'
                f'{self._escape(prov_text)}</text>'
            )
        svg_parts.append("</svg>")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("\n".join(svg_parts), encoding="utf-8")
        logger.info("rendered SVG map plan to %s", output_path)
        return output_path

    def render_to_png(self, plan: MapAnimationPlan, output_path: str) -> str:
        """Render a plan to a PNG file using Pillow. Returns the output path.

        The output path is restricted to the configured output or temp
        directory to prevent writing outside approved locations. A relative
        filename is resolved against the output directory.
        """
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as exc:
            raise RuntimeError(
                "Pillow is required for PNG map rendering; install with 'pip install Pillow'"
            ) from exc

        # Restrict output to the approved output/temp directory.
        resolved = self._validate_output_path(output_path)

        w, h = plan.output_width, plan.output_height
        img = Image.new("RGB", (w, h), self._hex(self.BG_COLOR))
        draw = ImageDraw.Draw(img)
        # Land background.
        land_color = self._hex(self.LAND_COLOR)
        # Blend land over background.
        overlay = Image.new("RGB", (w, h), land_color)
        img = Image.blend(img, overlay, alpha=0.6)
        draw = ImageDraw.Draw(img)
        grid_color = self._hex(self.GRID_COLOR)
        grid_spacing = 80
        for x in range(0, w, grid_spacing):
            draw.line([(x, 0), (x, h)], fill=grid_color, width=1)
        for y in range(0, h, grid_spacing):
            draw.line([(0, y), (w, y)], fill=grid_color, width=1)
        # Font (use default if DejaVu not available).
        font_large = self._load_font(36)
        font_med = self._load_font(28)
        font_small = self._load_font(18)
        text_color = self._hex(self.TEXT_COLOR)
        # Route.
        if plan.route:
            route_color = self._hex(self.ROUTE_COLOR)
            points = [
                self._project(p["latitude"], p["longitude"], w, h, plan.zoom_start)
                for p in plan.route
            ]
            for i in range(len(points) - 1):
                draw.line([points[i], points[i + 1]], fill=route_color, width=4)
        # Markers.
        for m in plan.markers:
            mx, my = self._project(m.latitude, m.longitude, w, h, plan.zoom_start)
            mc = self._hex(m.color)
            draw.ellipse(
                [mx - m.size, my - m.size, mx + m.size, my + m.size],
                fill=mc, outline=text_color, width=2,
            )
            if m.label:
                draw.text(
                    (mx - len(m.label) * 7, my - m.size - 30),
                    m.label, fill=text_color, font=font_med,
                )
        # Title.
        title = self._plan_title(plan)
        draw.text((40, 30), title, fill=text_color, font=font_large)
        # Provenance footer.
        if plan.provenance:
            prov_text = f"Source: {plan.provenance.provider} | {plan.provenance.source}"
            draw.text((40, h - 40), prov_text, fill=text_color, font=font_small)
        Path(resolved).parent.mkdir(parents=True, exist_ok=True)
        img.save(resolved, "PNG")
        logger.info("rendered PNG map plan to %s", resolved)
        return str(resolved)

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _project(lat: float, lon: float, width: int, height: int, zoom: float) -> tuple[int, int]:
        """Project lat/lon to pixel coordinates (equirectangular, centered).

        This is a simplified projection for visualization — not a substitute
        for accurate map projections. It keeps the marker centered and scales
        with zoom.
        """
        scale = (2 ** zoom) * (width / 360.0) / 4.0
        cx = width / 2
        cy = height / 2
        px = int(cx + lon * scale)
        py = int(cy - lat * scale * (height / width))
        # Clamp to image bounds.
        px = max(0, min(width - 1, px))
        py = max(0, min(height - 1, py))
        return px, py

    @staticmethod
    def _hex(color: str) -> tuple[int, int, int]:
        """Convert #RRGGBB to (r, g, b)."""
        c = color.lstrip("#")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)

    @staticmethod
    def _escape(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    @staticmethod
    def _load_font(size: int):
        """Load a font, falling back to default if none found."""
        try:
            from PIL import ImageFont
            # Try common Linux font paths.
            for path in (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            ):
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
            return ImageFont.load_default()
        except Exception:
            return None

    @staticmethod
    def _plan_title(plan: MapAnimationPlan) -> str:
        atype = plan.animation_type.replace("_", " ").title()
        return f"{atype} Map"


__all__ = [
    "MapAnimationEngine",
    "MapAnimationPlan",
    "MapMarker",
    "MapRenderer",
]
