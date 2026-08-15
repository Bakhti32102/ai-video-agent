"""Tests for provenance model and enhanced AgentResult contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.enums import AgentName, AgentRunStatus, ProvenanceType, WorkflowState
from app.schemas.contracts import (
    AgentResult,
    Asset,
    GeoProvenance,
    Location,
    Provenance,
    WorkflowState as WorkflowStateContract,
)


class TestProvenance:
    def test_valid_provenance(self) -> None:
        p = Provenance(
            provenance_type=ProvenanceType.ASSET,
            provider="wikimedia",
            source="https://commons.wikimedia.org/123",
        )
        assert p.provider == "wikimedia"
        assert p.retrieved_at  # auto-set

    def test_empty_provider_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Provenance(provenance_type=ProvenanceType.ASSET, provider="", source="x")

    def test_empty_source_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Provenance(provenance_type=ProvenanceType.ASSET, provider="x", source="")

    def test_query_optional(self) -> None:
        p = Provenance(provenance_type=ProvenanceType.ASSET, provider="x", source="y")
        assert p.query is None

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            Provenance(
                provenance_type=ProvenanceType.ASSET,
                provider="x",
                source="y",
                extra_field="bad",  # type: ignore[call-arg]
            )


class TestGeoProvenance:
    def test_valid_geo_provenance(self) -> None:
        gp = GeoProvenance(
            provider="nominatim",
            source="nominatim search",
            latitude=48.85,
            longitude=2.35,
            query="Paris",
        )
        assert gp.provenance_type == ProvenanceType.GEOCODING
        assert gp.latitude == 48.85

    def test_latitude_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            GeoProvenance(provider="x", source="y", latitude=91.0, longitude=2.35)

    def test_longitude_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            GeoProvenance(provider="x", source="y", latitude=48.85, longitude=181.0)

    def test_inherits_provenance_fields(self) -> None:
        gp = GeoProvenance(provider="x", source="y", latitude=0.0, longitude=0.0)
        assert hasattr(gp, "provider")
        assert hasattr(gp, "source")
        assert hasattr(gp, "retrieved_at")


class TestLocationWithProvenance:
    def test_location_with_provenance(self) -> None:
        gp = GeoProvenance(provider="nominatim", source="search", latitude=48.85, longitude=2.35)
        loc = Location(
            id="loc_1",
            name="Paris",
            latitude=48.85,
            longitude=2.35,
            source="nominatim",
            provenance=gp,
        )
        assert loc.provenance is not None
        assert loc.provenance.provider == "nominatim"

    def test_location_without_provenance(self) -> None:
        loc = Location(id="loc_1", name="Paris", latitude=48.85, longitude=2.35, source="nominatim")
        assert loc.provenance is None


class TestAssetWithProvenance:
    def test_asset_with_provenance(self) -> None:
        p = Provenance(provenance_type=ProvenanceType.ASSET, provider="wikimedia", source="commons")
        asset = Asset(
            id="asset_1",
            name="photo",
            asset_type="image",
            format="png",
            file_path="photo.png",
            source="wikimedia",
            provenance=p,
        )
        assert asset.provenance is not None

    def test_asset_without_provenance(self) -> None:
        asset = Asset(
            id="asset_1",
            name="photo",
            asset_type="image",
            format="png",
            file_path="photo.png",
            source="wikimedia",
        )
        assert asset.provenance is None


class TestEnhancedAgentResult:
    def test_auto_generates_run_id(self) -> None:
        r = AgentResult(agent=AgentName.SCRIPT, status=AgentRunStatus.SUCCESS, success=True)
        assert r.run_id.startswith("run_")

    def test_run_id_is_unique(self) -> None:
        r1 = AgentResult(agent=AgentName.SCRIPT, status=AgentRunStatus.SUCCESS, success=True)
        r2 = AgentResult(agent=AgentName.SCRIPT, status=AgentRunStatus.SUCCESS, success=True)
        assert r1.run_id != r2.run_id

    def test_project_id_optional(self) -> None:
        r = AgentResult(agent=AgentName.SCRIPT, status=AgentRunStatus.SUCCESS, success=True)
        assert r.project_id is None

    def test_confidence_default_is_one(self) -> None:
        r = AgentResult(agent=AgentName.SCRIPT, status=AgentRunStatus.SUCCESS, success=True)
        assert r.confidence == 1.0

    def test_confidence_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentResult(agent=AgentName.SCRIPT, status=AgentRunStatus.SUCCESS, success=True, confidence=1.5)

    def test_confidence_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentResult(agent=AgentName.SCRIPT, status=AgentRunStatus.SUCCESS, success=True, confidence=-0.1)

    def test_provenance_optional(self) -> None:
        r = AgentResult(agent=AgentName.SCRIPT, status=AgentRunStatus.SUCCESS, success=True)
        assert r.provenance is None

    def test_finished_before_started_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentResult(
                agent=AgentName.SCRIPT,
                status=AgentRunStatus.SUCCESS,
                success=True,
                started_at="2026-01-01T10:00:00Z",
                finished_at="2026-01-01T09:00:00Z",
            )

    def test_finished_after_started_ok(self) -> None:
        r = AgentResult(
            agent=AgentName.SCRIPT,
            status=AgentRunStatus.SUCCESS,
            success=True,
            started_at="2026-01-01T09:00:00Z",
            finished_at="2026-01-01T10:00:00Z",
        )
        assert r.finished_at == "2026-01-01T10:00:00Z"

    def test_success_with_errors_still_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentResult(
                agent=AgentName.SCRIPT,
                status=AgentRunStatus.SUCCESS,
                success=True,
                errors=["something went wrong"],
            )

    def test_failure_without_errors_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentResult(
                agent=AgentName.SCRIPT,
                status=AgentRunStatus.FAILED,
                success=False,
            )


class TestWorkflowStateContract:
    def test_default_state_is_created(self) -> None:
        ws = WorkflowStateContract(id="ws_1", project_id="proj_1")
        assert ws.current_state == WorkflowState.CREATED

    def test_has_previous_state(self) -> None:
        ws = WorkflowStateContract(
            id="ws_1",
            project_id="proj_1",
            previous_state=WorkflowState.CREATED,
        )
        assert ws.previous_state == WorkflowState.CREATED

    def test_has_current_phase_for_backward_compat(self) -> None:
        ws = WorkflowStateContract(id="ws_1", project_id="proj_1")
        assert ws.current_phase is not None
