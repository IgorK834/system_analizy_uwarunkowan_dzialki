from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.main import app
from app.models.analysis import Analysis
from app.models.infrastructure import Infrastructure
from app.models.mpzp_parameter import MpzpParameter as MpzpParameterRecord
from app.models.mpzp_zone import MpzpZone
from app.models.parcel import Parcel
from app.models.pog_data import PogData
from app.models.risk import Risk
from app.models.source_record import SourceRecord
from app.schemas.analyze import SourceMetadata, UtilitiesPreviewResult
from app.schemas.mpzp import MpzpParameter, MpzpParseResult
from app.schemas.mpzp import MpzpZoneResult as ParserMpzpZoneResult
from app.services.mpzp import MpzpDiscoveryResult
from app.services.mpzp_fetch import DocumentBlob, MpzpDocumentFetchError
from app.services.pog import PogDiscoveryResult, PogLayerSection
from app.services.context import ContextResult, ContextSectionResult
from app.services.uldk import ParcelLookupResult

pytestmark = pytest.mark.integration

client = TestClient(app)

TEST_WKT = (
    "MULTIPOLYGON(((500000 200000,500100 200000,500100 200100,"
    "500000 200100,500000 200000)))"
)


def _uldk_result(parcel_identifier: str = "122101_1.0001.9999") -> ParcelLookupResult:
    return ParcelLookupResult(
        parcel_identifier=parcel_identifier,
        wkt=TEST_WKT,
        teryt="122101",
        source_metadata=SourceMetadata(
            source_name="ULDK",
            source_url="https://uldk.gugik.gov.pl/",
            fetched_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
            confidence=1.0,
            manual_review_required=False,
        ),
    )


def _raster_only_discovery() -> MpzpDiscoveryResult:
    return MpzpDiscoveryResult(
        plan_id="MPZP/2020/1",
        candidate_zone_symbols=["230_U"],
        uchwala_url="https://bip.example.test/uchwala.pdf",
        brak_wektorow=True,
        status="raster_only",
        is_discovery_only=True,
        source_metadata=SourceMetadata(
            source_name="KIMPZP",
            source_url="https://example.local/kimpzp",
            fetched_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
            confidence=0.3,
            manual_review_required=True,
        ),
        warnings=["Gmina nie udostępnia wektorowych danych MPZP."],
    )


def _found_discovery() -> MpzpDiscoveryResult:
    return MpzpDiscoveryResult(
        plan_id="MPZP/2020/1",
        candidate_zone_symbols=["MN"],
        uchwala_url="https://bip.example.test/uchwala.pdf",
        brak_wektorow=False,
        status="found",
        is_discovery_only=True,
        source_metadata=SourceMetadata(
            source_name="KIMPZP",
            source_url="https://example.local/kimpzp",
            fetched_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
            confidence=0.6,
            manual_review_required=True,
        ),
        warnings=[],
    )


@pytest.fixture(autouse=True)
def cleanup_test_analyses():
    """Usuwa dane testowe po każdym teście, żeby testy integracyjne nie się kumulowały."""
    yield
    with SessionLocal() as db:
        parcel_ids = select(Parcel.id).where(
            Parcel.parcel_identifier.like("122101_1.0001.9%")
        )
        analysis_ids = select(Analysis.id).where(Analysis.parcel_id.in_(parcel_ids))
        zone_ids = select(MpzpZone.id).where(MpzpZone.analysis_id.in_(analysis_ids))
        db.execute(
            delete(MpzpParameterRecord).where(
                MpzpParameterRecord.mpzp_zone_id.in_(zone_ids)
            )
        )
        for model in (SourceRecord, Risk, Infrastructure, PogData, MpzpZone):
            db.execute(delete(model).where(model.analysis_id.in_(analysis_ids)))
        db.execute(delete(Analysis).where(Analysis.id.in_(analysis_ids)))
        db.execute(delete(Parcel).where(Parcel.id.in_(parcel_ids)))
        db.commit()


@pytest.fixture(autouse=True)
def mock_kiut_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.analysis_orchestrator.check_kiut_coverage_for_geometry",
        AsyncMock(
            return_value=UtilitiesPreviewResult(
                coverage_status="covered",
                county_name="powiat testowy",
                layer_available=True,
                note="Powiat publikuje dane; podgląd nie służy do odległości.",
                source=SourceMetadata(
                    source_name="KIUT (GUGiK)",
                    source_url="https://kiut.example.test/wms",
                    confidence=0.9,
                    manual_review_required=False,
                ),
            )
        ),
    )


@pytest.fixture(autouse=True)
def mock_pog_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SourceMetadata(
        source_name="POG_FIXTURE",
        source_url=None,
        confidence=0.0,
        manual_review_required=True,
    )
    monkeypatch.setattr(
        "app.services.analysis_orchestrator.discover_pog",
        AsyncMock(
            return_value=PogDiscoveryResult(
                status="unknown",
                uchwala_nr=None,
                uchwala_date=None,
                links=[],
                planning_act=PogLayerSection("planning_act", "unknown"),
                downtown_area=PogLayerSection("downtown_area", "unknown"),
                ouz=PogLayerSection("ouz", "unknown"),
                planning_zones=PogLayerSection("planning_zones", "unknown"),
                is_discovery_only=True,
                source_metadata=source,
                warnings=[],
            )
        ),
    )


def _document_blob() -> DocumentBlob:
    return DocumentBlob(
        content=b"%PDF-mock",
        media_type="application/pdf",
        filename="uchwala.pdf",
        source_metadata=SourceMetadata(
            source_name="MPZP_BIP",
            source_url="https://bip.example.test/uchwala.pdf",
            fetched_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
            confidence=0.9,
            manual_review_required=False,
        ),
    )


def _empty_context() -> ContextResult:
    return ContextResult(
        kiut=ContextSectionResult(section="kiut", status="available"),
        isok=ContextSectionResult(section="isok", status="available"),
        gdos=ContextSectionResult(section="gdos", status="available"),
        nmt=ContextSectionResult(section="nmt", status="available"),
    )


# --- POST /analyze: brak_wektorow=True path -----------------------------


def test_analyze_returns_manual_zone_required_when_no_vectors() -> None:
    with (
        patch(
            "app.services.analysis_orchestrator.resolve_parcel",
            new_callable=AsyncMock,
        ) as mock_resolve,
        patch(
            "app.services.analysis_orchestrator.analyze_context",
            new=AsyncMock(return_value=_empty_context()),
        ),
        patch(
            "app.services.analysis_orchestrator.discover_mpzp",
            new_callable=AsyncMock,
        ) as mock_discover,
    ):
        mock_resolve.return_value = _uldk_result("122101_1.0001.9001")
        mock_discover.return_value = _raster_only_discovery()

        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["manual_zone_required"] is True
    assert body["status"] == "waiting_for_user_input"
    assert body["analysis_id"] is not None
    assert body["parcel"] is not None
    assert body["mpzp_zones"] == []
    assert any(
        warning["code"] == "MPZP_MANUAL_ZONE_REQUIRED" for warning in body["warnings"]
    )


def test_analyze_no_vectors_persists_parcel_and_analysis_in_db() -> None:
    with (
        patch(
            "app.services.analysis_orchestrator.resolve_parcel",
            new_callable=AsyncMock,
        ) as mock_resolve,
        patch(
            "app.services.analysis_orchestrator.analyze_context",
            new=AsyncMock(return_value=_empty_context()),
        ),
        patch(
            "app.services.analysis_orchestrator.discover_mpzp",
            new_callable=AsyncMock,
        ) as mock_discover,
    ):
        mock_resolve.return_value = _uldk_result("122101_1.0001.9002")
        mock_discover.return_value = _raster_only_discovery()

        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    analysis_id = response.json()["analysis_id"]
    with SessionLocal() as db:
        analysis = db.get(Analysis, analysis_id)
        assert analysis is not None
        assert analysis.status == "waiting_for_zone_symbol"
        assert analysis.pending_uchwala_url == "https://bip.example.test/uchwala.pdf"
        assert analysis.pending_plan_id == "MPZP/2020/1"
        assert analysis.pending_zone_symbol_candidates == ["230_U"]

        parcel = db.get(Parcel, analysis.parcel_id)
        assert parcel is not None
        assert parcel.parcel_identifier == "122101_1.0001.9002"


def test_analyze_found_vectors_runs_orchestrator_and_persists_result() -> None:
    parser_result = MpzpParseResult(
        plan_id="MPZP/2020/1",
        zones=[ParserMpzpZoneResult(zone_symbol="MN", parameters=[])],
        status="partial",
    )
    with (
        patch(
            "app.services.analysis_orchestrator.resolve_parcel",
            new_callable=AsyncMock,
        ) as mock_resolve,
        patch(
            "app.services.analysis_orchestrator.analyze_context",
            new=AsyncMock(return_value=_empty_context()),
        ),
        patch(
            "app.services.analysis_orchestrator.discover_mpzp",
            new_callable=AsyncMock,
        ) as mock_discover,
        patch(
            "app.services.analysis_orchestrator.fetch_mpzp_document",
            new=AsyncMock(return_value=_document_blob()),
        ),
        patch(
            "app.services.analysis_orchestrator.parse_mpzp_document",
            new=AsyncMock(return_value=parser_result),
        ),
    ):
        mock_resolve.return_value = _uldk_result("122101_1.0001.9003")
        mock_discover.return_value = _found_discovery()

        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    body = response.json()
    assert body["analysis_id"] is not None
    assert body["status"] == "partial"
    assert body["manual_zone_required"] is False
    assert body["parcel"] is not None
    assert body["mpzp_zones"][0]["zone_symbol"] == "MN"


def test_analyze_no_vectors_reusing_same_parcel_identifier_gets_or_creates() -> None:
    with (
        patch(
            "app.services.analysis_orchestrator.resolve_parcel",
            new_callable=AsyncMock,
        ) as mock_resolve,
        patch(
            "app.services.analysis_orchestrator.analyze_context",
            new=AsyncMock(return_value=_empty_context()),
        ),
        patch(
            "app.services.analysis_orchestrator.discover_mpzp",
            new_callable=AsyncMock,
        ) as mock_discover,
    ):
        mock_resolve.return_value = _uldk_result("122101_1.0001.9004")
        mock_discover.return_value = _raster_only_discovery()

        first = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )
        second = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    first_analysis_id = first.json()["analysis_id"]
    second_analysis_id = second.json()["analysis_id"]
    assert first_analysis_id != second_analysis_id

    with SessionLocal() as db:
        first_analysis = db.get(Analysis, first_analysis_id)
        second_analysis = db.get(Analysis, second_analysis_id)
        assert first_analysis.parcel_id == second_analysis.parcel_id


# --- POST /analyze/resume -------------------------------------------------


def _create_waiting_analysis(parcel_identifier: str = "122101_1.0001.9010") -> int:
    with SessionLocal() as db:
        parcel = Parcel(
            parcel_identifier=parcel_identifier,
            geometry="SRID=2180;MULTIPOLYGON(((0 0,10 0,10 10,0 10,0 0)))",
            area_sqm=100.0,
        )
        db.add(parcel)
        db.flush()
        analysis = Analysis(
            parcel_id=parcel.id,
            status="waiting_for_zone_symbol",
            buildable_area_sqm=64.0,
            pending_uchwala_url="https://bip.example.test/uchwala.pdf",
            pending_plan_id="MPZP/2020/1",
            pending_zone_symbol_candidates=["230_U"],
        )
        db.add(analysis)
        db.flush()
        db.add(
            PogData(
                analysis_id=analysis.id,
                status="unknown",
                planning_zone=None,
                zone_type=None,
                in_ouz=False,
                area_ratio=None,
                in_downtown_area=False,
                manual_review_required=True,
                conflict_with_mpzp=None,
                raw_attributes={"ouz": {"status": "unknown"}},
                touches_ouz_boundary=False,
                source_url="https://pog.example.test",
                confidence=0.2,
            )
        )
        db.add(
            Infrastructure(
                analysis_id=analysis.id,
                network_type="water",
                buffer_m=1.5,
                zone_area_sqm=12.0,
                rule_source="test rule",
                rule_confidence=0.8,
                rule_note="test note",
                affects_buildable_area=True,
                source_url="https://kiut.example.test",
                confidence=0.8,
                manual_review_required=False,
            )
        )
        db.add(
            Risk(
                analysis_id=analysis.id,
                risk_type="flood_zone",
                description="Testowe ryzyko powodziowe.",
                source_url="https://isok.example.test",
                confidence=0.8,
                manual_review_required=False,
            )
        )
        db.commit()
        return analysis.id


def test_resume_returns_404_for_unknown_analysis() -> None:
    response = client.post(
        "/analyze/resume", json={"analysis_id": 999999999, "zone_symbol": "230_U"}
    )

    assert response.status_code == 404


def test_resume_returns_409_when_analysis_not_waiting() -> None:
    with SessionLocal() as db:
        parcel = Parcel(
            parcel_identifier="122101_1.0001.9011",
            geometry="SRID=2180;MULTIPOLYGON(((0 0,10 0,10 10,0 10,0 0)))",
            area_sqm=100.0,
        )
        db.add(parcel)
        db.flush()
        analysis = Analysis(parcel_id=parcel.id, status="partial")
        db.add(analysis)
        db.commit()
        analysis_id = analysis.id

    response = client.post(
        "/analyze/resume",
        json={"analysis_id": analysis_id, "zone_symbol": "230_U"},
    )

    assert response.status_code == 409


def test_resume_returns_422_for_invalid_zone_symbol_with_newline() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9012")

    response = client.post(
        "/analyze/resume",
        json={"analysis_id": analysis_id, "zone_symbol": "230\n_U"},
    )

    assert response.status_code == 422


def test_resume_returns_422_for_too_long_zone_symbol() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9013")

    response = client.post(
        "/analyze/resume",
        json={"analysis_id": analysis_id, "zone_symbol": "A" * 21},
    )

    assert response.status_code == 422


def test_resume_returns_503_when_document_fetch_fails() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9014")

    with patch(
        "app.services.analysis_resume.fetch_mpzp_document", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.side_effect = MpzpDocumentFetchError("dokument zniknął")

        response = client.post(
            "/analyze/resume",
            json={"analysis_id": analysis_id, "zone_symbol": "230_U"},
        )

    assert response.status_code == 503


def test_resume_success_updates_status_and_returns_manual_source() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9015")
    parser_result = MpzpParseResult(
        plan_id="MPZP/2020/1",
        zones=[
            ParserMpzpZoneResult(
                zone_symbol="230_U",
                parameters=[
                    MpzpParameter(
                        name="max_building_height_m",
                        normalized_value=9.0,
                        confidence=0.5,
                        manual_review_required=True,
                    )
                ],
            )
        ],
        status="complete",
    )

    with (
        patch(
            "app.services.analysis_resume.fetch_mpzp_document", new_callable=AsyncMock
        ) as mock_fetch,
        patch(
            "app.services.analysis_resume.parse_mpzp_document", new_callable=AsyncMock
        ) as mock_parse,
    ):
        mock_fetch.return_value = _document_blob()
        mock_parse.return_value = parser_result

        response = client.post(
            "/analyze/resume",
            json={"analysis_id": analysis_id, "zone_symbol": "230_U"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["manual_zone_required"] is False
    assert body["status"] == "partial"
    assert body["parcel"] is not None
    assert body["parcel"]["parcel_identifier"] == "122101_1.0001.9015"
    assert body["pog"] is not None
    assert body["infrastructure"][0]["network_type"] == "water"
    assert body["infrastructure"][0]["zone_area_sqm"] == 12.0
    assert body["risks"][0]["risk_type"] == "flood_zone"
    assert body["buildable_area_sqm"] == 64.0
    assert len(body["mpzp_zones"]) == 1
    zone = body["mpzp_zones"][0]
    assert zone["zone_symbol"] == "230_U"
    assert zone["max_building_height_m"] == 9.0
    assert zone["source"]["source_name"] == "manual_user_input"
    assert zone["source"]["manual_review_required"] is True
    assert zone["source"]["confidence"] == 0.5

    with SessionLocal() as db:
        analysis = db.get(Analysis, analysis_id)
        assert analysis.status == "partial"
        assert analysis.resolved_zone_symbol == "230_U"
        assert analysis.pending_uchwala_url is None
        assert analysis.pending_plan_id is None
        assert analysis.pending_zone_symbol_candidates is None
        saved_zone = db.scalar(
            select(MpzpZone).where(MpzpZone.analysis_id == analysis_id)
        )
        assert saved_zone is not None
        assert saved_zone.zone_symbol == "230_U"
        assert saved_zone.parameters[0].normalized_value == "9.0"
        manual_source = db.scalar(
            select(SourceRecord).where(
                SourceRecord.analysis_id == analysis_id,
                SourceRecord.source_name == "manual_user_input",
            )
        )
        assert manual_source is not None
        assert manual_source.manual_review_required is True


def test_resume_preserves_complete_pog_v2_snapshot() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9019")
    pog_v2 = {
        "schema_version": "2.0",
        "legal_status": "adopted",
        "coverage_status": "full",
        "act": {"id": "pog:test", "version": "2026-09-24"},
        "zones": [
            {
                "id": "zone:sj",
                "symbol": "SJ",
                "type": "SJ",
                "area_sqm": 60.0,
                "area_pct": 60.0,
                "max_building_height_m": None,
            },
            {
                "id": "zone:su",
                "symbol": "SU",
                "type": "SU",
                "area_sqm": 40.0,
                "area_pct": 40.0,
                "max_building_height_m": 0.0,
            },
        ],
        "dominant_zone_id": "zone:sj",
        "ouz": [],
        "downtown_areas": [],
        "social_infrastructure_standard_areas": [],
        "status": "adopted",
        "planning_zone": "SJ",
        "zone_type": "SJ",
        "in_ouz": False,
        "area_ratio": 0.6,
        "in_downtown_area": False,
        "manual_review_required": False,
        "touches_ouz_boundary": False,
    }
    with SessionLocal() as db:
        record = db.scalar(select(PogData).where(PogData.analysis_id == analysis_id))
        assert record is not None
        record.schema_version = "2.0"
        record.result_v2 = pog_v2
        record.legacy_partial = False
        db.commit()

    parser_result = MpzpParseResult(
        plan_id="MPZP/2020/1",
        zones=[ParserMpzpZoneResult(zone_symbol="230_U", parameters=[])],
        status="partial",
    )
    with (
        patch(
            "app.services.analysis_resume.fetch_mpzp_document",
            new=AsyncMock(return_value=_document_blob()),
        ),
        patch(
            "app.services.analysis_resume.parse_mpzp_document",
            new=AsyncMock(return_value=parser_result),
        ),
    ):
        response = client.post(
            "/analyze/resume",
            json={"analysis_id": analysis_id, "zone_symbol": "230_U"},
        )

    assert response.status_code == 200
    returned = response.json()["pog"]
    assert returned["schema_version"] == "2.0"
    assert [zone["id"] for zone in returned["zones"]] == ["zone:sj", "zone:su"]
    assert returned["zones"][0]["max_building_height_m"] is None
    assert returned["zones"][1]["max_building_height_m"] == 0.0
    with SessionLocal() as db:
        saved = db.scalar(select(PogData).where(PogData.analysis_id == analysis_id))
        assert saved is not None
        assert saved.result_v2["zones"] == pog_v2["zones"]


def test_resume_keeps_partial_status_when_parser_result_is_not_complete() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9016")
    parser_result = MpzpParseResult(
        plan_id="MPZP/2020/1",
        zones=[ParserMpzpZoneResult(zone_symbol="230_U", parameters=[])],
        status="partial",
    )

    with (
        patch(
            "app.services.analysis_resume.fetch_mpzp_document", new_callable=AsyncMock
        ) as mock_fetch,
        patch(
            "app.services.analysis_resume.parse_mpzp_document", new_callable=AsyncMock
        ) as mock_parse,
    ):
        mock_fetch.return_value = _document_blob()
        mock_parse.return_value = parser_result

        response = client.post(
            "/analyze/resume",
            json={"analysis_id": analysis_id, "zone_symbol": "230_U"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "partial"


def test_resume_warns_about_parameters_not_in_flat_contract() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9017")
    parser_result = MpzpParseResult(
        plan_id="MPZP/2020/1",
        zones=[
            ParserMpzpZoneResult(
                zone_symbol="230_U",
                parameters=[
                    MpzpParameter(
                        name="prohibition",
                        normalized_value="zakaz zabudowy",
                        confidence=0.6,
                        manual_review_required=True,
                    )
                ],
            )
        ],
        status="complete",
    )

    with (
        patch(
            "app.services.analysis_resume.fetch_mpzp_document", new_callable=AsyncMock
        ) as mock_fetch,
        patch(
            "app.services.analysis_resume.parse_mpzp_document", new_callable=AsyncMock
        ) as mock_parse,
    ):
        mock_fetch.return_value = _document_blob()
        mock_parse.return_value = parser_result

        response = client.post(
            "/analyze/resume",
            json={"analysis_id": analysis_id, "zone_symbol": "230_U"},
        )

    body = response.json()
    assert any(
        warning["code"] == "MPZP_PARAMETERS_NOT_IN_FLAT_CONTRACT"
        for warning in body["warnings"]
    )


def test_resume_uses_manual_zone_symbol_confidence_constant() -> None:
    from app.services.mpzp_zones import MANUAL_ZONE_SYMBOL_CONFIDENCE

    assert MANUAL_ZONE_SYMBOL_CONFIDENCE == 0.5


@pytest.mark.asyncio
async def test_resume_rolls_back_all_snapshot_changes_when_persistence_fails() -> None:
    from app.services.analysis_resume import resume_analysis_with_zone

    analysis_id = _create_waiting_analysis("122101_1.0001.9018")
    parser_result = MpzpParseResult(
        plan_id="MPZP/2020/1",
        zones=[ParserMpzpZoneResult(zone_symbol="230_U", parameters=[])],
        status="partial",
    )
    with (
        patch(
            "app.services.analysis_resume.fetch_mpzp_document",
            new=AsyncMock(return_value=_document_blob()),
        ),
        patch(
            "app.services.analysis_resume.parse_mpzp_document",
            new=AsyncMock(return_value=parser_result),
        ),
        patch(
            "app.services.analysis_resume.add_mpzp_zone_snapshot",
            side_effect=RuntimeError("kontrolowany błąd zapisu"),
        ),
        SessionLocal() as db,
    ):
        with pytest.raises(RuntimeError, match="kontrolowany błąd zapisu"):
            await resume_analysis_with_zone(analysis_id, "230_U", db)

    with SessionLocal() as db:
        analysis = db.get(Analysis, analysis_id)
        assert analysis is not None
        assert analysis.status == "waiting_for_zone_symbol"
        assert analysis.resolved_zone_symbol is None
        assert analysis.pending_uchwala_url == "https://bip.example.test/uchwala.pdf"
        assert db.scalar(
            select(MpzpZone).where(MpzpZone.analysis_id == analysis_id)
        ) is None
