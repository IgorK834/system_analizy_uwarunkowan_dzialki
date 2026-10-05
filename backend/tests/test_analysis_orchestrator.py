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
from app.schemas.analyze import ParcelIdAnalyzeRequest, UtilitiesPreviewResult
from app.schemas.mpzp import MpzpParameter as ParserParameter
from app.schemas.mpzp import MpzpParseResult
from app.schemas.mpzp import MpzpZoneResult as ParserZone
from app.schemas.source import SourceMetadata
from app.services.analysis_orchestrator import run_analysis
from app.services.context import ContextResult, ContextSectionResult
from app.services.gdos import NatureProtectionFeature
from app.services.isok import RiskFeature
from app.services.kiut import NetworkFeature
from app.services.mpzp import MpzpDiscoveryResult
from app.services.mpzp_fetch import DocumentBlob, MpzpDocumentFetchError
from app.services.pog import PogDiscoveryResult, PogLayerSection
from app.shared.planning_status import StatusEvidence
from app.services.pog_fetch import PogVectorData, PogVectorFeature
from app.services.persistence import build_analyze_response_from_analysis
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


@pytest.fixture(autouse=True)
def mock_kiut_coverage(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    coverage = AsyncMock(
        return_value=UtilitiesPreviewResult(
            coverage_status="covered",
            county_name="powiat testowy",
            layer_available=True,
            note="Powiat publikuje dane; podgląd nie służy do obliczania odległości.",
            source=_source("KIUT (GUGiK)", "https://kiut.example.test/wms"),
        )
    )
    monkeypatch.setattr(
        "app.services.analysis_orchestrator.check_kiut_coverage_for_geometry",
        coverage,
    )
    return coverage


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
        nmt=ContextSectionResult(section="nmt", status="available"),
    )


def _unknown_pog_discovery() -> PogDiscoveryResult:
    return PogDiscoveryResult(
        legal_status="unknown",
        uchwala_nr=None,
        uchwala_date=None,
        links=[],
        planning_act=PogLayerSection("planning_act", "unavailable"),
        downtown_area=PogLayerSection("downtown_area", "unavailable"),
        ouz=PogLayerSection("ouz", "unavailable"),
        planning_zones=PogLayerSection("planning_zones", "unavailable"),
        is_discovery_only=True,
        source_metadata=_source("POG_FIXTURE", None, confidence=0.0, manual=True),
        warnings=[],
    )


@pytest.fixture(autouse=True)
def mock_pog_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.analysis_orchestrator.discover_pog",
        AsyncMock(return_value=_unknown_pog_discovery()),
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


LEGAL_FORCE = "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/legalForce"


def _binding_pog_discovery() -> PogDiscoveryResult:
    source = _source(
        "POG_GMINA_WMS",
        "https://pog.example.test/wms",
        confidence=0.65,
        manual=True,
    )
    return PogDiscoveryResult(
        legal_status="binding",
        uchwala_nr="X/42/2026",
        uchwala_date="2026-02-10",
        links=["https://bip.example.test/pog.gml"],
        planning_act=PogLayerSection(
            "planning_act", "found", feature_count=1, raw_legal_status=LEGAL_FORCE
        ),
        downtown_area=PogLayerSection("downtown_area", "empty"),
        ouz=PogLayerSection("ouz", "found", feature_count=1),
        planning_zones=PogLayerSection("planning_zones", "found", feature_count=1),
        is_discovery_only=True,
        source_metadata=source,
        source_responded=True,
        raw_legal_status=LEGAL_FORCE,
        legal_evidence=StatusEvidence(
            source_name=source.source_name,
            official=True,
            reference=source.source_url,
            raw_value=LEGAL_FORCE,
            confirmed_at=source.fetched_at,
        ),
    )


def _adopted_pog_vectors() -> PogVectorData:
    parcel = box(500000, 200000, 500100, 200100)
    return PogVectorData(
        planning_zones=[
            PogVectorFeature(
                geometry=parcel,
                attributes={"zone_type": "SJ"},
                source_crs="EPSG:2180",
                layer_type="planning_zone",
            )
        ],
        ouz_areas=[
            PogVectorFeature(
                geometry=box(500000, 200000, 500050, 200100),
                attributes={},
                source_crs="EPSG:2180",
                layer_type="ouz",
            )
        ],
        downtown_areas=[],
        app_metadata={"uchwala_nr": "X/42/2026"},
        status="available",
        wms_fallback_required=False,
        source_metadata=_source(
            "POG_APP_VECTOR",
            "https://bip.example.test/pog.gml",
            confidence=0.75,
            manual=True,
        ),
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
    assert response.parcel.buildable_area_geojson is not None
    assert (
        response.parcel.buildable_area_geojson["properties"][
            "is_technical_approximation"
        ]
        is True
    )
    assert response.mpzp_zones[0].zone_symbol == "MN"
    assert response.mpzp_zones[0].max_building_height_m == 9.0
    assert any(source.source_name == "ULDK" for source in response.sources)
    assert response.pog is not None
    assert response.pog.status == "unknown"
    assert response.utilities_preview is not None
    assert response.utilities_preview.coverage_status == "covered"
    assert any(warning.code == "POG_SCENARIO_UNKNOWN" for warning in response.warnings)
    assert not any(warning.code == "POG_NOT_IMPLEMENTED" for warning in response.warnings)

    with SessionLocal() as db:
        saved = db.get(Analysis, response.analysis_id)
        assert saved is not None
        assert saved.status == "partial"
        assert saved.utilities_preview is not None
        assert saved.utilities_preview["coverage_status"] == "covered"
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
        nmt=ContextSectionResult(section="nmt", status="available"),
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
    assert response.infrastructure[0].zone_area_sqm == pytest.approx(276.0)
    # Reguła produkcyjna nie ma zweryfikowanej podstawy (BK-306): strefa jest
    # wyłącznie symulacją i nie pomniejsza obszaru zabudowy.
    assert response.infrastructure[0].affects_buildable_area is False
    assert response.infrastructure[0].rule_source is not None
    assert response.infrastructure[0].network_geometry_geojson is not None
    assert response.infrastructure[0].network_geometry_geojson["properties"]["layer"] == "network"
    assert response.infrastructure[0].protection_zone_geojson is not None
    assert response.infrastructure[0].protection_zone_geojson["properties"]["layer"] == "protection_zone"
    assert response.buildable_area_sqm == pytest.approx(8464.0)
    assert response.risks[0].risk_type == "natura_2000"
    # BK-303: udział jest polem strukturalnym; opis to prezentacja z tego pola.
    assert response.risks[0].intersection_pct == 100.0
    assert "100,00% działki" in response.risks[0].description
    assert "1.00%" not in response.risks[0].description
    assert response.risks[0].geometry_geojson is not None
    assert response.risks[0].geometry_geojson["properties"]["layer"] == "risk"
    assert any(warning.source_name == "isok" for warning in response.warnings)

    with SessionLocal() as db:
        saved_infrastructure = db.scalar(
            select(Infrastructure).where(
                Infrastructure.analysis_id == response.analysis_id
            )
        )
        assert saved_infrastructure is not None
        assert saved_infrastructure.zone_area_sqm == pytest.approx(276.0)
        assert saved_infrastructure.affects_buildable_area is False
        assert saved_infrastructure.network_geometry_geojson is not None
        assert saved_infrastructure.protection_zone_geojson is not None
        cached = db.get(Analysis, response.analysis_id)
        assert cached is not None
        rebuilt = build_analyze_response_from_analysis(cached, db)
        assert rebuilt.buildable_area_sqm == pytest.approx(8464.0)
        assert rebuilt.infrastructure[0].rule_source is not None
        assert rebuilt.infrastructure[0].network_geometry_geojson is not None
        assert rebuilt.infrastructure[0].protection_zone_geojson is not None
        assert rebuilt.risks[0].geometry_geojson is not None


@pytest.mark.asyncio
async def test_binding_pog_ouz_and_compatibility_run_end_to_end() -> None:
    identifier = f"{_PREFIX}POG_E2E"
    parse_result = _parse_result().model_copy(
        update={
            "zones": [
                ParserZone(
                    zone_symbol="MN",
                    parameters=[
                        ParserParameter(
                            name="primary_use",
                            normalized_value="single_family_housing",
                            confidence=0.9,
                            manual_review_required=False,
                        )
                    ],
                )
            ]
        }
    )
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
            new=AsyncMock(return_value=parse_result),
        ),
        patch(
            "app.services.analysis_orchestrator.discover_pog",
            new=AsyncMock(return_value=_binding_pog_discovery()),
        ),
        patch(
            "app.services.analysis_orchestrator.fetch_pog_vector_data",
            new=AsyncMock(return_value=_adopted_pog_vectors()),
        ),
        SessionLocal() as db,
    ):
        response = await run_analysis(
            ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier=identifier),
            db,
        )

    assert response.pog is not None
    assert response.pog.legal_status == "binding"
    assert response.pog.coverage_status == "available"
    assert response.pog.data_availability == "current"
    assert response.pog.status == "binding"
    assert response.pog.planning_zone == "SJ"
    assert response.pog.area_ratio == pytest.approx(1.0)
    assert response.pog.in_ouz is True
    assert response.pog.ouz_intersection_area_sqm == pytest.approx(5000.0)
    assert response.pog.ouz_intersection_pct == pytest.approx(50.0)
    # BK-205: strefa MPZP z discovery nie ma geometrii, więc para z SJ nie jest
    # zidentyfikowana przestrzennie — tabela zna wynik, ale ocena go nie
    # rozstrzyga. Ocena jest daną pierwszoklasową, nie wpisem w raw_attributes.
    assessment = response.pog.compatibility_assessment
    assert assessment is not None
    assert assessment.status == "uncertain"
    assert assessment.zone_pairs[0].rule_result == "compatible"
    assert assessment.zone_pairs[0].spatially_identified is False
    assert assessment.as_of is not None
    assert response.pog.raw_attributes is not None
    assert "scenario" not in response.pog.raw_attributes
    assert response.mpzp_zones[0].intersection_pct is None

    with SessionLocal() as db:
        saved = db.scalar(
            select(PogData).where(PogData.analysis_id == response.analysis_id)
        )
        assert saved is not None
        assert saved.status == "binding"
        assert saved.legal_status == "binding"
        assert saved.coverage_status == "available"
        assert saved.in_ouz is True
        assert saved.compatibility_assessment["status"] == "uncertain"
        assert saved.result_v2["compatibility_assessment"]["status"] == "uncertain"
        cached_analysis = db.get(Analysis, response.analysis_id)
        assert cached_analysis is not None
        rebuilt = build_analyze_response_from_analysis(cached_analysis, db)
        assert rebuilt.pog is not None
        assert rebuilt.pog.ouz_intersection_pct == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_isok_ratio_is_presented_as_percentage_not_fraction() -> None:
    identifier = f"{_PREFIX}ISOK_PERCENT"
    isok_source = _source("ISOK", "https://isok.example.test")
    context = ContextResult(
        kiut=ContextSectionResult(section="kiut", status="available"),
        isok=ContextSectionResult(
            section="isok",
            status="available",
            data=[
                RiskFeature(
                    risk_type="flood_zone",
                    severity="high",
                    geometry=box(500000, 200000, 500012.5, 200100),
                    intersection_area_sqm=1250.0,
                    area_ratio=0.125,
                    probability_class="1%",
                    source_metadata=isok_source,
                )
            ],
            source_metadata=isok_source,
        ),
        gdos=ContextSectionResult(section="gdos", status="available"),
        nmt=ContextSectionResult(section="nmt", status="available"),
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

    assert response.risks[0].intersection_pct == 12.5
    assert response.risks[0].description.endswith("(12,50% działki).")
    assert "0.12%" not in response.risks[0].description


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


# --- tryby parsera MPZP (PV3-14) -----------------------------------------------------------------------

from app.modules.planning.application.llm_pipeline import MpzpLlmPipeline  # noqa: E402
from app.modules.planning.application.ports import (  # noqa: E402
    StructuredExtractionError,
    StructuredExtractionErrorCode,
)
from app.services import mpzp_parser_hybrid as parser_hybrid  # noqa: E402
from app.services.mpzp_parser_options import MpzpParserOptions  # noqa: E402
from tests.test_mpzp_parser_modes import TEXT as MODES_TEXT  # noqa: E402
from tests.test_mpzp_parser_modes import ScriptedProvider  # noqa: E402
from tests.test_mpzp_parser_modes import extraction as modes_extraction  # noqa: E402


def _discovery_1mn() -> MpzpDiscoveryResult:
    from dataclasses import replace as _replace

    return _replace(_found_discovery(), candidate_zone_symbols=["1MN"])


async def _run_in_mode(identifier: str, options: MpzpParserOptions):
    request = ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier=identifier)
    with (
        patch("app.services.analysis_orchestrator.resolve_parcel", new=AsyncMock(return_value=_lookup(identifier))),
        patch("app.services.analysis_orchestrator.analyze_context", new=AsyncMock(return_value=_empty_context())),
        patch("app.services.analysis_orchestrator.discover_mpzp", new=AsyncMock(return_value=_discovery_1mn())),
        patch("app.services.analysis_orchestrator.fetch_mpzp_document", new=AsyncMock(return_value=_document())),
        patch("app.services.mpzp_parser.extract_document_text", new=AsyncMock(return_value=modes_extraction(MODES_TEXT))),
        patch("app.services.analysis_orchestrator.build_mpzp_parser_options", new=lambda **_kwargs: options),
        SessionLocal() as db,
    ):
        response = await run_analysis(request, db)
    await parser_hybrid.drain_shadow_tasks()
    return response


def _stored(analysis_id: int) -> tuple[list[dict], list[tuple]]:
    with SessionLocal() as db:
        zones = db.scalars(select(MpzpZone).where(MpzpZone.analysis_id == analysis_id).order_by(MpzpZone.id)).all()
        snapshots = [
        {**zone.result_snapshot, "parameters": _without_database_ids(zone.result_snapshot.get("parameters", []))}
        for zone in zones
    ]
        rows = db.execute(
            select(MpzpParameter.parameter_name, MpzpParameter.normalized_value, MpzpParameter.extraction_method,
                   MpzpParameter.review_status, MpzpParameter.model_id, MpzpParameter.prompt_version,
                   MpzpParameter.response_sha256)
            .where(MpzpParameter.mpzp_zone_id.in_([zone.id for zone in zones]))
            .order_by(MpzpParameter.id)
        ).all()
    return snapshots, [tuple(row) for row in rows]


# Identyfikatory nadawane przez bazę przy zapisie audytu dokumentu (inne w każdej analizie, niezależne od trybu).
_DATABASE_IDS = ("legal_unit_id", "document_version_id")


def _without_database_ids(parameters: list[dict]) -> list[dict]:
    return [{key: value for key, value in item.items() if key not in _DATABASE_IDS} for item in parameters]


def _comparable(response) -> dict:  # noqa: ANN001
    data = response.model_dump(mode="json", exclude={"analysis_id", "analyzed_at", "parcel", "section_quality"})
    for zone in data["mpzp_zones"]:
        zone["parameters"] = _without_database_ids(zone["parameters"])
    return {key: data[key] for key in ("status", "mpzp_zones", "warnings", "manual_zone_required")}


@pytest.mark.asyncio
async def test_shadow_mode_changes_neither_the_response_nor_the_saved_snapshot() -> None:
    v3 = await _run_in_mode(f"{_PREFIX}MODE_V3", MpzpParserOptions(mode="v3"))
    provider = ScriptedProvider()
    shadow = await _run_in_mode(
        f"{_PREFIX}MODE_SHADOW", MpzpParserOptions(mode="hybrid_shadow", llm=MpzpLlmPipeline(provider))
    )
    assert provider.calls == 1  # model policzony w tle
    assert _comparable(shadow) == _comparable(v3)
    assert _stored(shadow.analysis_id) == _stored(v3.analysis_id)
    assert not any(p.review_status for zone in shadow.mpzp_zones for p in zone.parameters)


@pytest.mark.asyncio
async def test_hybrid_mode_persists_ai_candidates_with_provenance_and_reads_them_back() -> None:
    provider = ScriptedProvider()
    response = await _run_in_mode(
        f"{_PREFIX}MODE_HYBRID", MpzpParserOptions(mode="hybrid", llm=MpzpLlmPipeline(provider))
    )
    zone = response.mpzp_zones[0]
    ai = [p for p in zone.parameters if p.review_status == "ai_candidate"]
    assert [(p.name, p.normalized_value, p.extraction_method) for p in ai] == [("max_storeys", 3.0, "llm_verified")]
    assert zone.max_floors is None  # wartość modelu nie wypełnia płaskiego pola przed ręczną weryfikacją
    assert zone.manual_review_required and response.status == "partial"
    snapshots, rows = _stored(response.analysis_id)
    llm_rows = [row for row in rows if row[2] == "llm_verified"]
    assert len(llm_rows) == 1 and llm_rows[0][3:6] == ("ai_candidate", "gemini-3.8-flash", "mpzp-extraction/1")
    assert llm_rows[0][6] == ai[0].response_sha256 and len(llm_rows[0][6]) == 64
    with SessionLocal() as db:
        saved = db.get(Analysis, response.analysis_id)
        assert saved is not None
        restored = build_analyze_response_from_analysis(saved, db)
    assert restored.mpzp_zones[0].model_dump(mode="json") == zone.model_dump(mode="json")
    assert snapshots[0]["parameters"][-1]["review_status"] == "ai_candidate"


@pytest.mark.asyncio
async def test_hybrid_mode_without_the_model_is_deterministic_partial_with_a_warning() -> None:
    v3 = await _run_in_mode(f"{_PREFIX}MODE_V3B", MpzpParserOptions(mode="v3"))
    failing = ScriptedProvider(error=StructuredExtractionError(StructuredExtractionErrorCode.TIMEOUT))
    response = await _run_in_mode(
        f"{_PREFIX}MODE_TIMEOUT", MpzpParserOptions(mode="hybrid", llm=MpzpLlmPipeline(failing))
    )
    assert response.status == "partial"
    assert any(w.code == "MPZP_LLM_UNAVAILABLE" and w.source_name == "mpzp" for w in response.warnings)
    assert _without_database_ids(response.model_dump(mode="json")["mpzp_zones"][0]["parameters"]) == (
        _without_database_ids(v3.model_dump(mode="json")["mpzp_zones"][0]["parameters"]))
    disabled = await _run_in_mode(
        f"{_PREFIX}MODE_DISABLED", MpzpParserOptions(mode="hybrid", llm_unavailable_reason="llm_disabled")
    )
    assert disabled.status == "partial" and any(w.code == "MPZP_LLM_UNAVAILABLE" for w in disabled.warnings)


@pytest.mark.asyncio
async def test_the_default_legacy_mode_calls_the_parser_without_a_model_pipeline() -> None:
    identifier = f"{_PREFIX}MODE_LEGACY"
    parse = AsyncMock(return_value=_parse_result())
    with (
        patch("app.services.analysis_orchestrator.resolve_parcel", new=AsyncMock(return_value=_lookup(identifier))),
        patch("app.services.analysis_orchestrator.analyze_context", new=AsyncMock(return_value=_empty_context())),
        patch("app.services.analysis_orchestrator.discover_mpzp", new=AsyncMock(return_value=_found_discovery())),
        patch("app.services.analysis_orchestrator.fetch_mpzp_document", new=AsyncMock(return_value=_document())),
        patch("app.services.analysis_orchestrator.parse_mpzp_document", new=parse),
        patch("app.services.mpzp_parser_options.build_mpzp_llm_pipeline", side_effect=AssertionError("bez modelu")),
        SessionLocal() as db,
    ):
        await run_analysis(ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier=identifier), db)
    assert parse.await_args.kwargs == {"mode": "legacy"}


@pytest.mark.asyncio
async def test_the_orchestrator_passes_one_model_budget_for_the_whole_analysis() -> None:
    from app.modules.planning.application.llm_pipeline import BudgetTracker

    received: list[object] = []

    def options(**kwargs: object) -> MpzpParserOptions:
        received.append(kwargs.get("budget"))
        return MpzpParserOptions(mode="v3")

    identifier = f"{_PREFIX}MODE_BUDGET"
    with (
        patch("app.services.analysis_orchestrator.resolve_parcel", new=AsyncMock(return_value=_lookup(identifier))),
        patch("app.services.analysis_orchestrator.analyze_context", new=AsyncMock(return_value=_empty_context())),
        patch("app.services.analysis_orchestrator.discover_mpzp", new=AsyncMock(return_value=_discovery_1mn())),
        patch("app.services.analysis_orchestrator.fetch_mpzp_document", new=AsyncMock(return_value=_document())),
        patch("app.services.mpzp_parser.extract_document_text", new=AsyncMock(return_value=modes_extraction(MODES_TEXT))),
        patch("app.services.analysis_orchestrator.build_mpzp_parser_options", new=options),
        SessionLocal() as db,
    ):
        await run_analysis(ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier=identifier), db)
    assert len(received) == 1 and isinstance(received[0], BudgetTracker)
