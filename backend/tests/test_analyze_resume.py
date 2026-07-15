from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest

from app.db.session import SessionLocal
from app.main import app
from app.models.analysis import Analysis
from app.models.parcel import Parcel
from app.schemas.analyze import SourceMetadata
from app.schemas.mpzp import MpzpParameter, MpzpParseResult
from app.schemas.mpzp import MpzpZoneResult as ParserMpzpZoneResult
from app.services.mpzp import MpzpDiscoveryResult
from app.services.mpzp_fetch import DocumentBlob, MpzpDocumentFetchError
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
        for analysis in db.query(Analysis).all():
            db.delete(analysis)
        db.commit()
        for parcel in db.query(Parcel).filter(
            Parcel.parcel_identifier.like("122101_1.0001.9%")
        ):
            db.delete(parcel)
        db.commit()


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


# --- POST /analyze: brak_wektorow=True path -----------------------------


def test_analyze_returns_manual_zone_required_when_no_vectors() -> None:
    with (
        patch(
            "app.routers.analyze.resolve_parcel", new_callable=AsyncMock
        ) as mock_resolve,
        patch(
            "app.routers.analyze.discover_mpzp", new_callable=AsyncMock
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
    assert body["status"] == "partial"
    assert body["analysis_id"] is not None
    assert body["mpzp_zones"] == []
    assert any(
        warning["code"] == "MPZP_MANUAL_ZONE_REQUIRED" for warning in body["warnings"]
    )


def test_analyze_no_vectors_persists_parcel_and_analysis_in_db() -> None:
    with (
        patch(
            "app.routers.analyze.resolve_parcel", new_callable=AsyncMock
        ) as mock_resolve,
        patch(
            "app.routers.analyze.discover_mpzp", new_callable=AsyncMock
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


def test_analyze_found_vectors_keeps_existing_behaviour_unchanged() -> None:
    with (
        patch(
            "app.routers.analyze.resolve_parcel", new_callable=AsyncMock
        ) as mock_resolve,
        patch(
            "app.routers.analyze.discover_mpzp", new_callable=AsyncMock
        ) as mock_discover,
    ):
        mock_resolve.return_value = _uldk_result("122101_1.0001.9003")
        mock_discover.return_value = _found_discovery()

        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    body = response.json()
    assert body["analysis_id"] is None
    assert body["status"] == "partial"
    assert body["manual_zone_required"] is False
    assert body["mpzp_zones"] == []


def test_analyze_no_vectors_reusing_same_parcel_identifier_gets_or_creates() -> None:
    with (
        patch(
            "app.routers.analyze.resolve_parcel", new_callable=AsyncMock
        ) as mock_resolve,
        patch(
            "app.routers.analyze.discover_mpzp", new_callable=AsyncMock
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
            pending_uchwala_url="https://bip.example.test/uchwala.pdf",
            pending_plan_id="MPZP/2020/1",
            pending_zone_symbol_candidates=["230_U"],
        )
        db.add(analysis)
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
        "app.routers.analyze.fetch_mpzp_document", new_callable=AsyncMock
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
            "app.routers.analyze.fetch_mpzp_document", new_callable=AsyncMock
        ) as mock_fetch,
        patch(
            "app.routers.analyze.parse_mpzp_document", new_callable=AsyncMock
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
    assert body["status"] == "complete"
    assert len(body["mpzp_zones"]) == 1
    zone = body["mpzp_zones"][0]
    assert zone["zone_symbol"] == "230_U"
    assert zone["max_building_height_m"] == 9.0
    assert zone["source"]["source_name"] == "manual_user_input"
    assert zone["source"]["manual_review_required"] is True
    assert zone["source"]["confidence"] == 0.5

    with SessionLocal() as db:
        analysis = db.get(Analysis, analysis_id)
        assert analysis.status == "complete"
        assert analysis.resolved_zone_symbol == "230_U"


def test_resume_keeps_partial_status_when_parser_result_is_not_complete() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9016")
    parser_result = MpzpParseResult(
        plan_id="MPZP/2020/1",
        zones=[ParserMpzpZoneResult(zone_symbol="230_U", parameters=[])],
        status="partial",
    )

    with (
        patch(
            "app.routers.analyze.fetch_mpzp_document", new_callable=AsyncMock
        ) as mock_fetch,
        patch(
            "app.routers.analyze.parse_mpzp_document", new_callable=AsyncMock
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
            "app.routers.analyze.fetch_mpzp_document", new_callable=AsyncMock
        ) as mock_fetch,
        patch(
            "app.routers.analyze.parse_mpzp_document", new_callable=AsyncMock
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
