"""Tests for the geo provider abstraction."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.services.geo import (
    GeocodeResult,
    GeoProvider,
    GoogleGeoProvider,
    NoneGeoProvider,
    OpenStreetMapGeoProvider,
    get_geo_provider,
)
from app.schemas.contracts import GeoProvenance


class _MockGeoProvider(GeoProvider):
    """Deterministic mock provider for testing."""

    name = "mock"

    def __init__(self, results: dict[str, GeocodeResult] | None = None) -> None:
        self._results = results or {}

    async def geocode(self, query: str) -> GeocodeResult:
        return self._results.get(query, GeocodeResult(
            query=query, status="unresolved", provider=self.name, error="not found",
        ))


@pytest.mark.asyncio
async def test_none_provider_returns_unresolved() -> None:
    provider = NoneGeoProvider()
    result = await provider.geocode("Paris")
    assert result.status == "unresolved"
    assert result.latitude is None
    assert result.longitude is None
    assert "no geo provider configured" in result.error


def test_none_provider_not_configured() -> None:
    assert not NoneGeoProvider().is_configured


@pytest.mark.asyncio
async def test_mock_provider_returns_resolved() -> None:
    mock = _MockGeoProvider({
        "Paris": GeocodeResult(
            query="Paris", status="resolved", latitude=48.85, longitude=2.35,
            display_name="Paris, France", confidence=0.9, provider="mock",
            provenance=GeoProvenance(
                provider="mock", source="test", latitude=48.85, longitude=2.35, query="Paris",
            ),
        )
    })
    result = await mock.geocode("Paris")
    assert result.status == "resolved"
    assert result.latitude == 48.85
    assert result.longitude == 2.35
    assert result.confidence == 0.9
    assert result.provenance is not None
    assert result.provenance.provider == "mock"


@pytest.mark.asyncio
async def test_mock_provider_returns_unresolved_for_unknown() -> None:
    mock = _MockGeoProvider()
    result = await mock.geocode("Nowhere")
    assert result.status == "unresolved"
    assert result.latitude is None


def test_osm_provider_is_configured() -> None:
    provider = OpenStreetMapGeoProvider()
    assert provider.is_configured  # Nominatim is free
    assert provider.name == "osm"


def test_google_provider_not_configured_without_key() -> None:
    settings = Settings(google_maps_api_key="")
    provider = GoogleGeoProvider(settings)
    assert not provider.is_configured


def test_google_provider_configured_with_key() -> None:
    settings = Settings(google_maps_api_key="fake-key-123")
    provider = GoogleGeoProvider(settings)
    assert provider.is_configured


@pytest.mark.asyncio
async def test_google_provider_returns_unresolved_without_key() -> None:
    settings = Settings(google_maps_api_key="")
    provider = GoogleGeoProvider(settings)
    result = await provider.geocode("Paris")
    assert result.status == "unresolved"
    assert "GOOGLE_MAPS_API_KEY not configured" in result.error


def test_get_geo_provider_none_default() -> None:
    settings = Settings(geo_provider="none")
    provider = get_geo_provider(settings)
    assert isinstance(provider, NoneGeoProvider)


def test_get_geo_provider_osm() -> None:
    settings = Settings(geo_provider="osm")
    provider = get_geo_provider(settings)
    assert isinstance(provider, OpenStreetMapGeoProvider)


def test_get_geo_provider_google() -> None:
    settings = Settings(geo_provider="google", google_maps_api_key="key")
    provider = get_geo_provider(settings)
    assert isinstance(provider, GoogleGeoProvider)


@pytest.mark.asyncio
async def test_geocode_result_to_dict() -> None:
    r = GeocodeResult(
        query="test", status="resolved", latitude=1.0, longitude=2.0,
        display_name="Test", confidence=0.5, provider="mock",
    )
    d = r.to_dict()
    assert d["status"] == "resolved"
    assert d["latitude"] == 1.0
    assert d["confidence"] == 0.5


@pytest.mark.asyncio
async def test_reverse_geocode_default_unsupported() -> None:
    provider = _MockGeoProvider()
    result = await provider.reverse_geocode(48.85, 2.35)
    assert result.status == "unresolved"
    assert "not supported" in result.error
