import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from shapely.geometry import LineString, box
from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.models.analysis import Analysis
from app.models.infrastructure import Infrastructure
from app.models.mpzp_parameter import MpzpParameter
from app.models.mpzp_zone import MpzpZone
from app.models.parcel import Parcel
from app.models.pog_data import PogData
from app.models.risk import Risk
from app.models.source_record import SourceRecord
from app.schemas.analyze import ParcelIdAnalyzeRequest
from app.schemas.mpzp import MpzpParameter as ParserParameter
from app.schemas.mpzp import MpzpParseResult
from app.schemas.mpzp import MpzpZoneResult as ParserZone
from app.schemas.source import SourceMetadata
from app.services.analysis_orchestrator import run_analysis
from app.services.context import ContextResult, ContextSectionResult
from app.services.gdos import NatureProtectionFeature
from app.services.kiut import NetworkFeature
from app.services.mpzp import MpzpDiscoveryResult
from app.services.mpzp_fetch import DocumentBlob, MpzpDocumentFetchError
from app.services.uldk import ParcelLookupResult

pytestmark = pytest.mark.integration

_PREFIX = "ORCHESTRATOR_TEST_"
_NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
_WKT = (
    "MULTIPOLYGON(((500000 200000,500100 200000,500100 200100,"
    "500000 200100,500000 200000)))"
)


def _cleanup() -> None:
    with SessionLocal() as db:
        parcel_ids = select(Parcel.id).where(
            Parcel.parcel_identifier.like(f"{_PREFIX}%")
        )
        analysis_ids = select(Analysis.id).where(Analysis.parcel_id.in_(parcel_ids))
        zone_ids = select(MpzpZone.id).where(MpzpZone.analysis_id.in_(analysis_ids))
        db.execute(
            delete(MpzpParameter).where(MpzpParameter.mpzp_zone_id.in_(zone_ids))
        )
        for model in (SourceRecord, Risk, Infrastructure, PogData, MpzpZone):
            db.execute(delete(model).where(model.analysis_id.in_(analysis_ids)))
        db.execute(delete(Analysis).where(Analysis.id.in_(analysis_ids)))
        db.execute(delete(Parcel).where(Parcel.id.in_(parcel_ids)))
        db.commit()


@pytest.fixture(autouse=True)
def cleanup_orchestrator_rows():
    _cleanup()
    yield
    _cleanup()


def _source(
    name: str,
    url: str | None,
    *,
    confidence: float = 0.9,
    manual: bool = False,
) -> SourceMetadata:
    return SourceMetadata(
        source_name=name,
        source_url=url,
        fetched_at=_NOW,
        response_status=200 if url else None,
        confidence=confidence,
        manual_review_required=manual,
    )


def _lookup(identifier: str) -> ParcelLookupResult:
    return ParcelLookupResult(
        parcel_identifier=identifier,
        wkt=_WKT,
        teryt="146101",
        source_metadata=_source("ULDK", "https://uldk.example.test"),
    )


def _empty_context() -> ContextResult:
    return ContextResult(
        kiut=ContextSectionResult(section="kiut", status="available"),
        isok=ContextSectionResult(section="isok", status="available"),
        gdos=ContextSectionResult(section="gdos", status="available"),
    )


def _found_discovery() -> MpzpDiscoveryResult:
    return MpzpDiscoveryResult(
        plan_id="MPZP/1",
        candidate_zone_symbols=["MN"],
        uchwala_url="https://bip.example.test/uchwala.pdf",
        brak_wektorow=False,
        status="found",
        is_discovery_only=True,
        source_metadata=_source(
            "KIMPZP",
            "https://kimpzp.example.test",
            confidence=0.6,
            manual=True,
        ),
        warnings=["Discovery punktowe wymaga weryfikacji."],
    )


def _no_mpzp_discovery() -> MpzpDiscoveryResult:
    return MpzpDiscoveryResult(
        plan_id=None,
        candidate_zone_symbols=[],
        uchwala_url=None,
        brak_wektorow=False,
        status="no_mpzp",
        is_discovery_only=True,
        source_metadata=_source(
            "KIMPZP",
            "https://kimpzp.example.test",
            confidence=0.3,
            manual=True,
        ),
        warnings=["Nie znaleziono MPZP."],
    )


def _raster_discovery() -> MpzpDiscoveryResult:
    return MpzpDiscoveryResult(
        plan_id="MPZP/RASTER",
        candidate_zone_symbols=["230_U"],
        uchwala_url="https://bip.example.test/raster.pdf",
        brak_wektorow=True,
        status="raster_only",
        is_discovery_only=True,
        source_metadata=_source(
            "KIMPZP",
            "https://kimpzp.example.test",
            confidence=0.3,
            manual=True,
        ),
        warnings=["Brak wektorów MPZP."],
    )


def _document() -> DocumentBlob:
    return DocumentBlob(
        content=b"%PDF-test",
        media_type="application/pdf",
        filename="uchwala.pdf",
        source_metadata=_source("MPZP_BIP", "https://bip.example.test/uchwala.pdf"),
    )


def _parse_result() -> MpzpParseResult:
    return MpzpParseResult(
        plan_id="MPZP/1",
        zones=[
            ParserZone(
                zone_symbol="MN",
                parameters=[
                    ParserParameter(
                        name="max_building_height_m",
                        normalized_value=9.0,
                        confidence=0.8,
                        manual_review_required=False,
                    )
                ],
            )
        ],
        status="complete",
    )


@pytest.mark.asyncio
async def test_full_parcel_id_flow_builds_response_and_persists_analysis() -> None:
    identifier = f"{_PREFIX}FULL"
    request = ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier=identifier)
    with (
        patch(
            "app.services.analysis_orchestrator.resolve_parcel",
            new=AsyncMock(return_value=_lookup(identifier)),
        ),
        patch(
            "app.services.analysis_orchestrator.analyze_context",
            new=AsyncMock(return_value=_empty_context()),
        ),
        patch(
            "app.services.analysis_orchestrator.discover_mpzp",
            new=AsyncMock(return_value=_found_discovery()),
        ),
        patch(
            "app.services.analysis_orchestrator.fetch_mpzp_document",
            new=AsyncMock(return_value=_document()),
        ),
        patch(
            "app.services.analysis_orchestrator.parse_mpzp_document",
            new=AsyncMock(return_value=_parse_result()),
        ),
        SessionLocal() as db,
    ):
        response = await run_analysis(request, db)

    assert response.status == "partial"
    assert response.analysis_id is not None
    assert response.parcel is not None
    assert response.parcel.metrics.area_sqm == 10000.0
    assert response.mpzp_zones[0].zone_symbol == "MN"
    assert response.mpzp_zones[0].max_building_height_m == 9.0
    assert any(source.source_name == "ULDK" for source in response.sources)
    assert any(warning.code == "POG_NOT_IMPLEMENTED" for warning in response.warnings)

    with SessionLocal() as db:
        saved = db.get(Analysis, response.analysis_id)
        assert saved is not None
        assert saved.status == "partial"
        assert saved.parcel.parcel_identifier == identifier


@pytest.mark.asyncio
async def test_second_call_uses_cache_without_context_or_discovery(caplog) -> None:
    identifier = f"{_PREFIX}CACHE"
    request = ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier=identifier)
    mock_context = AsyncMock(return_value=_empty_context())
    mock_discovery = AsyncMock(return_value=_found_discovery())
    analysis_logger = logging.getLogger("app.analysis")
    caplog.set_level(logging.INFO, logger=analysis_logger.name)
    with (
        patch.object(analysis_logger, "disabled", False),
        patch(
            "app.services.analysis_orchestrator.resolve_parcel",
            new=AsyncMock(return_value=_lookup(identifier)),
        ),
        patch(
            "app.services.analysis_orchestrator.analyze_context",
            new=mock_context,
        ),
        patch(
            "app.services.analysis_orchestrator.discover_mpzp",
            new=mock_discovery,
        ),
        patch(
            "app.services.analysis_orchestrator.fetch_mpzp_document",
            new=AsyncMock(return_value=_document()),
        ),
        patch(
            "app.services.analysis_orchestrator.parse_mpzp_document",
            new=AsyncMock(return_value=_parse_result()),
        ),
        SessionLocal() as db,
    ):
        first = await run_analysis(request, db)
        second = await run_analysis(request, db)

    assert second.analysis_id == first.analysis_id
    assert second.parcel is not None
    assert second.parcel.parcel_identifier == identifier
    assert mock_context.await_count == 1
    assert mock_discovery.await_count == 1
    assert "analysis_event=cache_miss" in caplog.text
    assert "analysis_event=cache_hit" in caplog.text
    assert "elapsed_ms=" in caplog.text


@pytest.mark.asyncio
async def test_force_refresh_creates_new_analysis() -> None:
    identifier = f"{_PREFIX}FORCE"
    request = ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier=identifier)
    mock_context = AsyncMock(return_value=_empty_context())
    mock_discovery = AsyncMock(return_value=_no_mpzp_discovery())
    with (
        patch(
            "app.services.analysis_orchestrator.resolve_parcel",
            new=AsyncMock(return_value=_lookup(identifier)),
        ),
        patch(
            "app.services.analysis_orchestrator.analyze_context",
            new=mock_context,
        ),
        patch(
            "app.services.analysis_orchestrator.discover_mpzp",
            new=mock_discovery,
        ),
        SessionLocal() as db,
    ):
        first = await run_analysis(request, db)
        second = await run_analysis(request, db, force_refresh=True)

    assert first.analysis_id != second.analysis_id
    assert mock_context.await_count == 2
    assert mock_discovery.await_count == 2


@pytest.mark.asyncio
async def test_unavailable_isok_keeps_kiut_and_gdos_results() -> None:
    identifier = f"{_PREFIX}PARTIAL"
    kiut_source = _source("KIUT", "https://kiut.example.test")
    gdos_source = _source("GDOŚ", "https://gdos.example.test")
    context = ContextResult(
        kiut=ContextSectionResult(
            section="kiut",
            status="available",
            data=[
                NetworkFeature(
                    network_type="water",
                    geometry=LineString([(500000, 200050), (500100, 200050)]),
                    source_metadata=kiut_source,
                    warning=None,
                )
            ],
            source_metadata=kiut_source,
        ),
        isok=ContextSectionResult(
            section="isok",
            status="unavailable",
            warnings=["Usługa ISOK jest tymczasowo niedostępna."],
        ),
        gdos=ContextSectionResult(
            section="gdos",
            status="available",
            data=[
                NatureProtectionFeature(
                    protection_type="natura2000",
                    name="Dolina testowa",
                    geometry=box(500000, 200000, 500010, 200010),
                    intersection_area_sqm=100.0,
                    area_ratio=1.0,
                    severity="low",
                    source_metadata=gdos_source,
                )
            ],
            source_metadata=gdos_source,
        ),
    )
    with (
        patch(
            "app.services.analysis_orchestrator.resolve_parcel",
            new=AsyncMock(return_value=_lookup(identifier)),
        ),
        patch(
            "app.services.analysis_orchestrator.analyze_context",
            new=AsyncMock(return_value=context),
        ),
        patch(
            "app.services.analysis_orchestrator.discover_mpzp",
            new=AsyncMock(return_value=_no_mpzp_discovery()),
        ),
        SessionLocal() as db,
    ):
        response = await run_analysis(
            ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier=identifier),
            db,
        )

    assert response.status == "partial"
    assert response.infrastructure[0].network_type == "water"
    assert response.infrastructure[0].buffer_m == 1.5
    assert response.risks[0].risk_type == "natura_2000"
    assert any(warning.source_name == "isok" for warning in response.warnings)


@pytest.mark.asyncio
async def test_raster_mpzp_maps_api_and_database_waiting_statuses() -> None:
    identifier = f"{_PREFIX}RASTER"
    with (
        patch(
            "app.services.analysis_orchestrator.resolve_parcel",
            new=AsyncMock(return_value=_lookup(identifier)),
        ),
        patch(
            "app.services.analysis_orchestrator.analyze_context",
            new=AsyncMock(return_value=_empty_context()),
        ),
        patch(
            "app.services.analysis_orchestrator.discover_mpzp",
            new=AsyncMock(return_value=_raster_discovery()),
        ),
        SessionLocal() as db,
    ):
        response = await run_analysis(
            ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier=identifier),
            db,
        )

    assert response.status == "waiting_for_user_input"
    assert response.manual_zone_required is True
    assert response.analysis_id is not None
    assert response.parcel is not None
    with SessionLocal() as db:
        analysis = db.get(Analysis, response.analysis_id)
        assert analysis.status == "waiting_for_zone_symbol"
        assert analysis.pending_plan_id == "MPZP/RASTER"
        assert analysis.pending_zone_symbol_candidates == ["230_U"]


@pytest.mark.asyncio
async def test_mpzp_document_failure_returns_partial_instead_of_raising() -> None:
    identifier = f"{_PREFIX}FETCH_FAIL"
    with (
        patch(
            "app.services.analysis_orchestrator.resolve_parcel",
            new=AsyncMock(return_value=_lookup(identifier)),
        ),
        patch(
            "app.services.analysis_orchestrator.analyze_context",
            new=AsyncMock(return_value=_empty_context()),
        ),
        patch(
            "app.services.analysis_orchestrator.discover_mpzp",
            new=AsyncMock(return_value=_found_discovery()),
        ),
        patch(
            "app.services.analysis_orchestrator.fetch_mpzp_document",
            new=AsyncMock(side_effect=MpzpDocumentFetchError("sekret=nie-loguj")),
        ),
        SessionLocal() as db,
    ):
        response = await run_analysis(
            ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier=identifier),
            db,
        )

    assert response.status == "partial"
    assert response.analysis_id is not None
    assert response.mpzp_zones == []
    assert any(
        warning.code == "MPZP_DOCUMENT_UNAVAILABLE"
        for warning in response.warnings
    )


@pytest.mark.asyncio
async def test_unexpected_context_and_discovery_errors_are_degraded() -> None:
    identifier = f"{_PREFIX}SECTION_FAIL"
    with (
        patch(
            "app.services.analysis_orchestrator.resolve_parcel",
            new=AsyncMock(return_value=_lookup(identifier)),
        ),
        patch(
            "app.services.analysis_orchestrator.analyze_context",
            new=AsyncMock(side_effect=RuntimeError("pełny WKT nie może trafić do logu")),
        ),
        patch(
            "app.services.analysis_orchestrator.discover_mpzp",
            new=AsyncMock(side_effect=RuntimeError("DATABASE_URL=secret")),
        ),
        SessionLocal() as db,
    ):
        response = await run_analysis(
            ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier=identifier),
            db,
        )

    assert response.status == "partial"
    assert any(warning.code == "MPZP_DISCOVERY_ERROR" for warning in response.warnings)
    assert sum(warning.source_name == "isok" for warning in response.warnings) == 1
