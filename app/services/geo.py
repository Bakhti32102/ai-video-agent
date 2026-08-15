"""Geocoding provider abstraction.

The Geo MCP server resolves place names to coordinates through a pluggable
:class:`GeoProvider`. Providers are selected through configuration
(``GEO_PROVIDER`` / ``GOOGLE_MAPS_API_KEY``). The core guardrail is
absolute: **no provider may fabricate coordinates**. If a location cannot be
confidently resolved, the result carries ``status = "unresolved"``.

Provider implementations:
- :class:`OpenStreetMapGeoProvider` — free Nominatim API (no key required).
- :class:`GoogleGeoProvider` — Google Maps Geocoding API (key required).

Caching and rate limiting are provided by :class:`CachingGeoProvider`, which
wraps any provider so repeated queries do not hit external APIs and the
Nominatim usage policy (max 1 request/second) is honoured.

Both make HTTP requests only when explicitly invoked. Tests inject mock
providers so no paid API is ever called during testing.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings, get_settings
from app.core.enums import ProvenanceType
from app.core.logging import get_logger
from app.core.result import Result
from app.schemas.contracts import GeoProvenance
from app.utils.ids import new_id

logger = get_logger("geo_provider")


@dataclass
class GeocodeResult:
    """Structured geocoding outcome.

    A result is either resolved (with verified coordinates + provenance) or
    unresolved (ambiguous/not found). It is never fabricated.
    """

    query: str
    status: str  # "resolved" | "unresolved"
    latitude: float | None = None
    longitude: float | None = None
    display_name: str | None = None
    confidence: float = 0.0
    provider: str = ""
    raw: dict | None = None
    provenance: GeoProvenance | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "query": self.query,
            "status": self.status,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "display_name": self.display_name,
            "confidence": self.confidence,
            "provider": self.provider,
        }
        if self.provenance is not None:
            d["provenance"] = self.provenance.model_dump(mode="json")
        if self.raw is not None:
            d["raw"] = self.raw
        if self.error is not None:
            d["error"] = self.error
        return d


class GeoProvider(ABC):
    """Abstract geocoding provider interface.

    Implementations must never fabricate coordinates. If a query is ambiguous
    or yields no confident match, return a result with ``status="unresolved"``.
    """

    name: str = "unknown"

    @abstractmethod
    async def geocode(self, query: str) -> GeocodeResult:
        """Resolve a place name to coordinates with provenance."""

    async def reverse_geocode(self, latitude: float, longitude: float) -> GeocodeResult:
        """Resolve coordinates to a place name (default: not supported)."""
        return GeocodeResult(
            query=f"{latitude},{longitude}",
            status="unresolved",
            provider=self.name,
            error=f"reverse geocoding not supported by {self.name}",
        )

    @property
    def is_configured(self) -> bool:
        """Return True if this provider has the credentials it needs."""
        return True


class NoneGeoProvider(GeoProvider):
    """No-op provider used when no geo provider is configured.

    Always returns ``unresolved`` so the pipeline never fabricates coordinates.
    """

    name = "none"

    async def geocode(self, query: str) -> GeocodeResult:
        return GeocodeResult(
            query=query,
            status="unresolved",
            provider=self.name,
            error="no geo provider configured; set GEO_PROVIDER to 'osm' or 'google'",
        )

    @property
    def is_configured(self) -> bool:
        return False


class OpenStreetMapGeoProvider(GeoProvider):
    """OpenStreetMap Nominatim geocoder (free, no API key required).

    Uses the public Nominatim API. Honours the usage policy by making a single
    request per call and respecting a minimum interval between requests.
    Results include full provenance.
    """

    name = "osm"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        user_agent: str = "ai-video-agent",
        nominatim_url: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.user_agent = user_agent
        self.nominatim_url = nominatim_url or self.settings.nominatim_url

    @property
    def is_configured(self) -> bool:
        return True  # Nominatim is free; no key needed.

    async def geocode(self, query: str) -> GeocodeResult:
        if not query or not query.strip():
            return GeocodeResult(query=query, status="unresolved", provider=self.name, error="empty query")

        params = urllib.parse.urlencode({"q": query.strip(), "format": "jsonv2", "limit": 1})
        url = f"{self.nominatim_url}?{params}"
        try:
            raw_responses = self._http_get_json(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OSM geocode failed for '%s': %s", query, exc)
            return GeocodeResult(query=query, status="unresolved", provider=self.name, error=str(exc))

        if not raw_responses:
            return GeocodeResult(query=query, status="unresolved", provider=self.name, error="no results")

        hit = raw_responses[0]
        try:
            lat = float(hit["lat"])
            lon = float(hit["lon"])
        except (KeyError, TypeError, ValueError):
            return GeocodeResult(query=query, status="unresolved", provider=self.name, error="malformed response")

        display_name = hit.get("display_name", query)
        # Nominatim doesn't return a confidence score; estimate from the
        # importance field if present, else default to 0.5.
        importance = float(hit.get("importance", 0.5))
        confidence = max(0.0, min(1.0, importance))

        provenance = GeoProvenance(
            provider=self.name,
            source=self.nominatim_url,
            query=query,
            latitude=lat,
            longitude=lon,
            raw_payload=hit,
        )
        return GeocodeResult(
            query=query,
            status="resolved",
            latitude=lat,
            longitude=lon,
            display_name=display_name,
            confidence=confidence,
            provider=self.name,
            raw=hit,
            provenance=provenance,
        )

    def _http_get_json(self, url: str) -> Any:
        """Perform a synchronous HTTP GET (Nominatim is fast enough for our use)."""
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - controlled URL
            return json.loads(resp.read().decode("utf-8"))


class GoogleGeoProvider(GeoProvider):
    """Google Maps Geocoding API provider (requires an API key).

    The key is read from ``GOOGLE_MAPS_API_KEY``. If absent, the provider
    reports ``is_configured = False`` and returns unresolved results.
    """

    name = "google"
    GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.google_maps_api_key)

    async def geocode(self, query: str) -> GeocodeResult:
        if not self.is_configured:
            return GeocodeResult(
                query=query, status="unresolved", provider=self.name,
                error="GOOGLE_MAPS_API_KEY not configured",
            )
        if not query or not query.strip():
            return GeocodeResult(query=query, status="unresolved", provider=self.name, error="empty query")

        params = urllib.parse.urlencode({"address": query.strip(), "key": self.settings.google_maps_api_key})
        url = f"{self.GEOCODE_URL}?{params}"
        try:
            payload = self._http_get_json(url)
        except Exception as exc:  # noqa: BLE001
            return GeocodeResult(query=query, status="unresolved", provider=self.name, error=str(exc))

        status = payload.get("status", "")
        if status != "OK":
            return GeocodeResult(
                query=query, status="unresolved", provider=self.name,
                error=f"Google API status: {status}", raw=payload,
            )

        results = payload.get("results", [])
        if not results:
            return GeocodeResult(query=query, status="unresolved", provider=self.name, error="no results")

        hit = results[0]
        geom = hit.get("geometry", {})
        loc = geom.get("location", {})
        try:
            lat = float(loc["lat"])
            lon = float(loc["lng"])
        except (KeyError, TypeError, ValueError):
            return GeocodeResult(query=query, status="unresolved", provider=self.name, error="malformed response")

        # Google's location_type: ROOFTOP is most precise.
        loc_type = geom.get("location_type", "APPROXIMATE")
        confidence_map = {"ROOFTOP": 0.95, "RANGE_INTERPOLATED": 0.8, "GEOMETRIC_CENTER": 0.7, "APPROXIMATE": 0.5}
        confidence = confidence_map.get(loc_type, 0.5)

        provenance = GeoProvenance(
            provider=self.name,
            source=self.GEOCODE_URL,
            query=query,
            latitude=lat,
            longitude=lon,
            raw_payload=hit,
        )
        return GeocodeResult(
            query=query,
            status="resolved",
            latitude=lat,
            longitude=lon,
            display_name=hit.get("formatted_address", query),
            confidence=confidence,
            provider=self.name,
            raw=hit,
            provenance=provenance,
        )

    def _http_get_json(self, url: str) -> Any:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - controlled URL
            return json.loads(resp.read().decode("utf-8"))


class CachingGeoProvider(GeoProvider):
    """Wrapper that adds an in-memory cache and rate limiting to any provider.

    - Repeated queries with the same normalized text return the cached result
      instantly (no external API call).
    - A minimum interval between requests is enforced (default 1.0s, matching
      the Nominatim usage policy). Rate limiting only applies to actual
      provider calls; cache hits are not rate-limited.
    """

    name = "cache"

    def __init__(
        self,
        inner: GeoProvider,
        *,
        min_interval_sec: float = 1.0,
        cache_ttl_sec: float | None = None,
    ) -> None:
        self._inner = inner
        self._min_interval = max(0.0, min_interval_sec)
        self._cache_ttl = cache_ttl_sec
        self._cache: dict[str, tuple[GeocodeResult, float]] = {}
        self._last_request_time: float = 0.0

    @property
    def is_configured(self) -> bool:
        return self._inner.is_configured

    async def geocode(self, query: str) -> GeocodeResult:
        key = self._cache_key(query)
        now = time.monotonic()
        # Cache hit?
        if key in self._cache:
            cached_result, cached_at = self._cache[key]
            if self._cache_ttl is None or (now - cached_at) < self._cache_ttl:
                logger.debug("geocode cache hit: %s", query)
                return cached_result
        # Rate limit: sleep if we made a request too recently.
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            delay = self._min_interval - elapsed
            logger.debug("rate limiting: sleeping %.2fs before next geocode", delay)
            time.sleep(delay)
        self._last_request_time = time.monotonic()
        result = await self._inner.geocode(query)
        # Cache all results (resolved or unresolved) so we don't re-query.
        self._cache[key] = (result, time.monotonic())
        return result

    async def reverse_geocode(self, latitude: float, longitude: float) -> GeocodeResult:
        return await self._inner.reverse_geocode(latitude, longitude)

    def cache_stats(self) -> dict[str, Any]:
        return {
            "entries": len(self._cache),
            "min_interval_sec": self._min_interval,
            "ttl_sec": self._cache_ttl,
        }

    def clear_cache(self) -> None:
        self._cache.clear()

    @staticmethod
    def _cache_key(query: str) -> str:
        return query.strip().lower()


# --- Query normalization --------------------------------------------------

# Common place names that need a country/region suffix to disambiguate
# in geocoding APIs. Keys are matched case-insensitively against the raw
# extracted location name. Values are the normalized query.
_DISAMBIGUATION_MAP: dict[str, str] = {
    # US states that collide with city/region names elsewhere.
    "arizona": "Arizona, USA",
    "new mexico": "New Mexico, USA",
    "georgia": "Georgia, USA",
    "washington": "Washington, USA",
    "mexico": "Mexico",
    "mexico city": "Mexico City, Mexico",
    "united states": "United States",
    "united states of america": "United States",
    "sonora": "Sonora, Mexico",
    "chihuahua": "Chihuahua, Mexico",
}

# Known country names that should be title-cased but not suffixed.
_COUNTRY_NAMES: frozenset[str] = frozenset({
    "mexico", "canada", "brazil", "argentina", "france", "germany",
    "spain", "italy", "japan", "china", "india", "russia", "england",
})


def normalize_geo_query(raw_name: str) -> str:
    """Normalize a raw extracted location name into a geocoding-friendly query.

    The script server extracts locations as lowercase keywords (e.g. "arizona",
    "new mexico", "mexico city"). Geocoding APIs like Nominatim resolve much
    better when these are properly qualified. This function:
    - Checks the disambiguation map first (highest priority).
    - Title-cases the query so the geocoder receives proper capitalization.
    - Never fabricates a location: it only re-qualifies a name the script
      already mentioned.

    Examples:
        "arizona"      -> "Arizona, USA"
        "new mexico"   -> "New Mexico, USA"
        "mexico city"   -> "Mexico City, Mexico"
        "mesa del osos" -> "Mesa Del Osos"
    """
    key = raw_name.strip().lower()
    if not key:
        return raw_name
    if key in _DISAMBIGUATION_MAP:
        return _DISAMBIGUATION_MAP[key]
    # Title-case multi-word names; leave country names as-is (already mapped).
    return raw_name.strip().title()


def get_geo_provider(settings: Settings | None = None) -> GeoProvider:
    """Factory: return the configured geo provider.

    Selection order:
    1. ``GEO_PROVIDER=google`` with ``GOOGLE_MAPS_API_KEY`` -> GoogleGeoProvider
    2. ``GEO_PROVIDER=osm`` -> OpenStreetMapGeoProvider (free, no key)
    3. ``GEO_PROVIDER=none`` (or unset) -> NoneGeoProvider (always unresolved)

    OSM and Google providers are wrapped in :class:`CachingGeoProvider` to
    honour rate limits and avoid redundant API calls.
    """
    settings = settings or get_settings()
    provider = settings.geo_provider
    if provider == "google":
        return CachingGeoProvider(GoogleGeoProvider(settings), min_interval_sec=0.2)
    if provider == "osm":
        # Nominatim usage policy: max 1 request/second.
        return CachingGeoProvider(OpenStreetMapGeoProvider(settings), min_interval_sec=1.0)
    return NoneGeoProvider()


__all__ = [
    "CachingGeoProvider",
    "GeocodeResult",
    "GeoProvider",
    "GoogleGeoProvider",
    "NoneGeoProvider",
    "OpenStreetMapGeoProvider",
    "get_geo_provider",
    "normalize_geo_query",
]
