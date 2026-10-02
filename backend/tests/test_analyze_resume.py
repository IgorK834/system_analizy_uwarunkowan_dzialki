from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, select

from app.core.access_control import make_analysis_token
from app.db.session import SessionLocal
from app.main import app
from app.models.analysis import Analysis
from app.models.analysis_pending_document import AnalysisPendingDocument
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
from hashlib import sha256
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
                legal_status="unknown",
                uchwala_nr=None,
                uchwala_date=None,
                links=[],
                planning_act=PogLayerSection("planning_act", "unavailable"),
                downtown_area=PogLayerSection("downtown_area", "unavailable"),
                ouz=PogLayerSection("ouz", "unavailable"),
                planning_zones=PogLayerSection("planning_zones", "unavailable"),
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


@pytest.fixture(autouse=True)
def mock_pending_document_fetch(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Wstrzymanie analizy przypina dokument — w testach bez sieci."""
    fetch = AsyncMock(return_value=_document_blob())
    monkeypatch.setattr("app.services.analysis_orchestrator.fetch_mpzp_document", fetch)
    return fetch


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
    # BK-204: przed formularzem UI dostaje plan, kandydatów i przypięty dokument.
    context = body["manual_zone_context"]
    assert context["plan_id"] == "MPZP/2020/1"
    assert context["candidate_zone_symbols"] == ["230_U"]
    assert context["document_status"] == "pinned"
    assert context["document"]["sha256"] == sha256(b"%PDF-mock").hexdigest()
    assert context["document"]["preview_path"] == (
        f"/analyze/{body['analysis_id']}/pending-document"
        f"?access_token={make_analysis_token(body['analysis_id'])}"
    )
    assert body["access_token"] == make_analysis_token(body["analysis_id"])
    assert context["document"]["requested_url_verified"] is True
    assert context["raster_preview_source_key"] == "mpzp"
    assert context["symbol_max_length"] == 40
    assert context["symbol_rules_version"] == "zone-symbol/2"
    assert "nieustalony" in context["notice"]


def test_analyze_no_vectors_marks_document_unavailable_when_pin_fails(
    mock_pending_document_fetch: AsyncMock,
) -> None:
    mock_pending_document_fetch.side_effect = MpzpDocumentFetchError("brak")
    with (
        patch(
            "app.services.analysis_orchestrator.resolve_parcel",
            new=AsyncMock(return_value=_uldk_result("122101_1.0001.9005")),
        ),
        patch(
            "app.services.analysis_orchestrator.analyze_context",
            new=AsyncMock(return_value=_empty_context()),
        ),
        patch(
            "app.services.analysis_orchestrator.discover_mpzp",
            new=AsyncMock(return_value=_raster_only_discovery()),
        ),
    ):
        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    body = response.json()
    assert body["status"] == "waiting_for_user_input"
    assert body["manual_zone_context"]["document_status"] == "unavailable"
    assert body["manual_zone_context"]["document"] is None
    assert "nie zostaną odczytane" in body["manual_zone_context"]["notice"]
    assert "MPZP_PENDING_DOCUMENT_UNAVAILABLE" in {w["code"] for w in body["warnings"]}
    with SessionLocal() as db:
        assert db.scalar(
            select(AnalysisPendingDocument).where(
                AnalysisPendingDocument.analysis_id == body["analysis_id"]
            )
        ) is None


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
        pinned = db.scalar(
            select(AnalysisPendingDocument).where(
                AnalysisPendingDocument.analysis_id == analysis_id
            )
        )
        assert pinned is not None
        assert pinned.content == b"%PDF-mock"
        assert pinned.content_sha256 == sha256(b"%PDF-mock").hexdigest()
        assert pinned.requested_url == "https://bip.example.test/uchwala.pdf"
        assert pinned.document_version_id is not None

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

PINNED_CONTENT = b"%PDF-pinned-at-pause"


def _create_waiting_analysis(
    parcel_identifier: str = "122101_1.0001.9010",
    *,
    pinned_content: bytes | None = PINNED_CONTENT,
    candidates: list[str] | None = None,
) -> int:
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
            pending_zone_symbol_candidates=candidates or ["230_U"],
            warnings=[
                {
                    "code": "MPZP_MANUAL_ZONE_REQUIRED",
                    "message": "Podaj symbol.",
                    "severity": "warning",
                    "source_name": "mpzp",
                },
                {
                    "code": "NMT_TEST_WARNING",
                    "message": "Ostrzeżenie NMT zachowane przy wznowieniu.",
                    "severity": "info",
                    "source_name": "nmt",
                },
            ],
        )
        db.add(analysis)
        db.flush()
        if pinned_content is not None:
            db.add(
                AnalysisPendingDocument(
                    analysis_id=analysis.id,
                    requested_url="https://bip.example.test/uchwala.pdf",
                    final_url="https://bip.example.test/uchwala.pdf",
                    media_type="application/pdf",
                    filename="uchwala.pdf",
                    content=pinned_content,
                    content_sha256=sha256(pinned_content).hexdigest(),
                    size_bytes=len(pinned_content),
                    fetched_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
                    response_status=200,
                )
            )
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
        db.add(
            SourceRecord(
                analysis_id=analysis.id,
                source_name="NMT",
                source_url="https://nmt.example.test",
                response_status="available",
                confidence=0.7,
                manual_review_required=False,
            )
        )
        db.commit()
        return analysis.id


def _zone_count(analysis_id: int) -> int:
    with SessionLocal() as db:
        return len(
            db.scalars(select(MpzpZone).where(MpzpZone.analysis_id == analysis_id)).all()
        )


def _assert_snapshot_untouched(analysis_id: int) -> None:
    with SessionLocal() as db:
        analysis = db.get(Analysis, analysis_id)
        assert analysis is not None
        assert analysis.status == "waiting_for_zone_symbol"
        assert analysis.resolved_zone_symbol is None
        assert analysis.pending_uchwala_url == "https://bip.example.test/uchwala.pdf"
        assert analysis.pending_zone_symbol_candidates
        assert _zone_count(analysis_id) == 0
        assert db.scalar(
            select(SourceRecord).where(
                SourceRecord.analysis_id == analysis_id,
                SourceRecord.source_name == "manual_user_input",
            )
        ) is None


def _parser_result(*parameters: MpzpParameter, symbol: str = "230_U", status="complete"):
    return MpzpParseResult(
        plan_id="MPZP/2020/1",
        zones=[ParserMpzpZoneResult(zone_symbol=symbol, parameters=list(parameters))],
        status=status,
    )


def _resume(analysis_id: int, symbol: str = "230_U", parser_result=None):
    parse = AsyncMock(return_value=parser_result or _parser_result())
    with patch("app.services.analysis_resume.parse_mpzp_document", new=parse):
        response = client.post(
            "/analyze/resume",
            json={"analysis_id": analysis_id, "zone_symbol": symbol},
        )
    return response, parse


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


@pytest.mark.parametrize(
    "symbol",
    ["230\n_U", "A" * 41, "230;U", "<b>U</b>", "   ", "230\x00U"],
)
def test_resume_rejects_invalid_symbol_without_touching_snapshot(symbol: str) -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9012")

    response, parse = _resume(analysis_id, symbol)

    assert response.status_code == 422
    parse.assert_not_awaited()
    _assert_snapshot_untouched(analysis_id)


def test_resume_returns_503_for_corrupted_pinned_document_without_writes() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9014")
    with SessionLocal() as db:
        pinned = db.scalar(
            select(AnalysisPendingDocument).where(
                AnalysisPendingDocument.analysis_id == analysis_id
            )
        )
        pinned.content = b"%PDF-podmieniony"
        db.commit()

    response, parse = _resume(analysis_id)

    assert response.status_code == 503
    assert "nadal oczekuje" in response.json()["detail"]
    parse.assert_not_awaited()
    _assert_snapshot_untouched(analysis_id)


def test_resume_returns_503_when_pinned_document_cannot_be_parsed() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9021")
    with patch(
        "app.services.analysis_resume.parse_mpzp_document",
        new=AsyncMock(side_effect=RuntimeError("uszkodzony PDF")),
    ):
        response = client.post(
            "/analyze/resume",
            json={"analysis_id": analysis_id, "zone_symbol": "230_U"},
        )

    assert response.status_code == 503
    _assert_snapshot_untouched(analysis_id)


def test_resume_parses_only_pinned_document_and_never_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uchwała podmieniona pod tym samym URL nie trafia do wyniku."""
    fetch = AsyncMock(side_effect=AssertionError("resume nie może pobierać dokumentu"))
    monkeypatch.setattr("app.services.mpzp_fetch.fetch_mpzp_document", fetch)
    analysis_id = _create_waiting_analysis("122101_1.0001.9022")

    response, parse = _resume(analysis_id)

    assert response.status_code == 200
    fetch.assert_not_awaited()
    document = parse.await_args.args[0]
    assert document.content == PINNED_CONTENT
    assert document.source_metadata.artifact_sha256 == sha256(PINNED_CONTENT).hexdigest()
    assert parse.await_args.args[1] == ["230_U"]


def test_resume_success_keeps_unknown_share_and_flags_every_parameter() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9015")
    parser_result = _parser_result(
        MpzpParameter(
            name="max_building_height_m",
            normalized_value=9.0,
            confidence=0.9,
            manual_review_required=False,
        ),
        MpzpParameter(
            name="min_biologically_active_percent",
            normalized_value=40.0,
            confidence=0.9,
            manual_review_required=False,
        ),
    )

    response, _ = _resume(analysis_id, parser_result=parser_result)

    assert response.status_code == 200
    body = response.json()
    assert body["manual_zone_required"] is False
    assert body["manual_zone_context"] is None
    assert body["status"] == "partial"
    assert body["parcel"]["parcel_identifier"] == "122101_1.0001.9015"
    assert body["pog"] is not None
    assert body["infrastructure"][0]["network_type"] == "water"
    assert body["infrastructure"][0]["zone_area_sqm"] == 12.0
    assert body["risks"][0]["risk_type"] == "flood_zone"
    assert body["buildable_area_sqm"] == 64.0
    assert any(source["source_name"] == "NMT" for source in body["sources"])
    codes = {warning["code"] for warning in body["warnings"]}
    assert "NMT_TEST_WARNING" in codes
    assert "MPZP_MANUAL_ZONE_REQUIRED" not in codes
    assert "MPZP_MANUAL_ZONE_FALLBACK" in codes
    assert len(body["mpzp_zones"]) == 1
    zone = body["mpzp_zones"][0]
    assert zone["zone_symbol"] == "230_U"
    assert zone["assignment_method"] == "manual_user_input"
    assert zone["intersection_area_sqm"] is None
    assert zone["intersection_pct"] is None
    assert zone["is_dominant"] is False
    assert zone["manual_review_required"] is True
    assert zone["max_building_height_m"] == 9.0
    assert zone["act_identifier"] == "MPZP/2020/1"
    assert [p["manual_review_required"] for p in zone["parameters"]] == [True, True]
    assert zone["source"]["source_name"] == "manual_user_input"
    assert zone["source"]["manual_review_required"] is True
    assert zone["source"]["confidence"] == 0.5
    selection = zone["manual_selection"]
    assert selection["entered_symbol"] == "230_U"
    assert selection["plan_id"] == "MPZP/2020/1"
    assert selection["candidate_zone_symbols"] == ["230_U"]
    assert selection["symbol_in_candidates"] is True
    assert selection["document_pinned"] is True
    assert selection["document_sha256"] == sha256(PINNED_CONTENT).hexdigest()
    assert "intersection_wkt" not in zone

    with SessionLocal() as db:
        analysis = db.get(Analysis, analysis_id)
        assert analysis.status == "partial"
        assert analysis.resolved_zone_symbol == "230_U"
        assert analysis.pending_uchwala_url is None
        assert analysis.pending_plan_id is None
        assert analysis.pending_zone_symbol_candidates is None
        saved_zone = db.scalar(select(MpzpZone).where(MpzpZone.analysis_id == analysis_id))
        assert saved_zone is not None
        assert saved_zone.assignment_method == "manual_user_input"
        assert saved_zone.intersection_area_sqm is None
        assert saved_zone.intersection_pct is None
        assert all(parameter.manual_review_required for parameter in saved_zone.parameters)
        manual_source = db.scalar(
            select(SourceRecord).where(
                SourceRecord.analysis_id == analysis_id,
                SourceRecord.source_name == "manual_user_input",
            )
        )
        assert manual_source is not None
        assert manual_source.manual_review_required is True
        assert manual_source.artifact_sha256 == sha256(PINNED_CONTENT).hexdigest()
        # Przypięty dokument zostaje jako dowód użytej wersji.
        assert db.scalar(
            select(AnalysisPendingDocument).where(
                AnalysisPendingDocument.analysis_id == analysis_id
            )
        ) is not None


def test_second_resume_is_rejected_and_does_not_duplicate_zones() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9023")

    first, _ = _resume(analysis_id)
    second, parse = _resume(analysis_id, "230_U")

    assert first.status_code == 200
    assert second.status_code == 409
    parse.assert_not_awaited()
    assert _zone_count(analysis_id) == 1


def test_resume_rechecks_status_under_row_lock() -> None:
    """Równoległe wznowienie zakończone w trakcie parsowania → 409, bez zapisu."""
    analysis_id = _create_waiting_analysis("122101_1.0001.9024")

    async def parse_while_other_request_finishes(*_args, **_kwargs):
        with SessionLocal() as other:
            other_analysis = other.get(Analysis, analysis_id)
            other_analysis.status = "partial"
            other.commit()
        return _parser_result()

    with patch(
        "app.services.analysis_resume.parse_mpzp_document",
        new=AsyncMock(side_effect=parse_while_other_request_finishes),
    ):
        response = client.post(
            "/analyze/resume",
            json={"analysis_id": analysis_id, "zone_symbol": "230_U"},
        )

    assert response.status_code == 409
    assert _zone_count(analysis_id) == 0


def test_resume_without_pinned_document_keeps_parameters_unknown() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9025", pinned_content=None)

    response, parse = _resume(analysis_id)

    assert response.status_code == 200
    parse.assert_not_awaited()
    body = response.json()
    zone = body["mpzp_zones"][0]
    assert zone["parameters"] == []
    assert zone["max_building_height_m"] is None
    assert zone["intersection_pct"] is None
    assert zone["manual_selection"]["document_pinned"] is False
    assert body["status"] == "partial"
    assert "MPZP_PINNED_DOCUMENT_MISSING" in {w["code"] for w in body["warnings"]}


def test_resume_warns_when_symbol_is_outside_candidates() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9026", candidates=["1MN", "2U"])

    response, _ = _resume(analysis_id, "230_U")

    body = response.json()
    assert body["mpzp_zones"][0]["manual_selection"]["symbol_in_candidates"] is False
    assert "MPZP_MANUAL_SYMBOL_NOT_IN_CANDIDATES" in {w["code"] for w in body["warnings"]}


def test_resume_accepts_a_symbol_with_spaces_and_keeps_the_original_entry() -> None:
    """PV3-04: ``146 MN`` jest realnym symbolem; zapis ma formę kanoniczną, dowód — oryginał."""
    analysis_id = _create_waiting_analysis("122101_1.0001.9040", candidates=["146MN", "1 PK"])

    response, parse = _resume(
        analysis_id, "  146   MN ", parser_result=_parser_result(symbol="146 MN")
    )

    assert response.status_code == 200
    body = response.json()
    selection = body["mpzp_zones"][0]["manual_selection"]
    assert body["mpzp_zones"][0]["zone_symbol"] == "146 MN"
    assert selection["entered_symbol"] == "146 MN"
    assert selection["entered_symbol_raw"] == "  146   MN "
    assert selection["symbol_in_candidates"] is True  # `146MN` z discovery to ten sam symbol
    assert parse.await_args.args[1] == ["146 MN"]  # parser dostaje formę kanoniczną
    with SessionLocal() as db:
        assert db.get(Analysis, analysis_id).resolved_zone_symbol == "146 MN"


def test_resume_stores_a_symbol_of_the_maximum_canonical_length() -> None:
    """Kolumna ``analyses.resolved_zone_symbol`` (migracja 027) mieści symbol 40-znakowy."""
    symbol = "22 KD G1/2(Z1/4)," + "X" * 23
    assert len(symbol) == 40
    analysis_id = _create_waiting_analysis("122101_1.0001.9041")

    response, _ = _resume(analysis_id, symbol, parser_result=_parser_result(symbol=symbol))

    assert response.status_code == 200
    with SessionLocal() as db:
        assert db.get(Analysis, analysis_id).resolved_zone_symbol == symbol


def test_resume_keeps_manual_symbol_when_parser_does_not_find_it() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9027")

    response, _ = _resume(analysis_id, parser_result=_parser_result(symbol="1MN"))

    body = response.json()
    assert body["mpzp_zones"][0]["zone_symbol"] == "230_U"
    assert body["mpzp_zones"][0]["parameters"] == []
    assert "MPZP_SYMBOL_NOT_FOUND_IN_DOCUMENT" in {w["code"] for w in body["warnings"]}


def test_resume_preserves_complete_pog_v2_snapshot() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9019")
    pog_v2 = {
        "schema_version": "2.0",
        "legal_status": "adopted",
        "coverage_status": "complete",
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

    response, _ = _resume(analysis_id, parser_result=_parser_result(status="partial"))

    assert response.status_code == 200
    returned = response.json()["pog"]
    assert returned["schema_version"] == "2.0"
    # Snapshot 2.0 bez przypiętego wydania nie ma potwierdzenia źródłowego:
    # alias adopted → unknown, complete → available (BK-106).
    assert returned["legal_status"] == "unknown"
    assert returned["coverage_status"] == "available"
    assert [zone["id"] for zone in returned["zones"]] == ["zone:sj", "zone:su"]
    assert returned["zones"][0]["max_building_height_m"] is None
    assert returned["zones"][1]["max_building_height_m"] == 0.0
    assessment = returned["compatibility_assessment"]
    assert (assessment["status"], assessment["reason_code"]) == (
        "unknown",
        "POG_STATUS_UNKNOWN",
    )
    with SessionLocal() as db:
        saved = db.scalar(select(PogData).where(PogData.analysis_id == analysis_id))
        assert saved is not None
        assert saved.result_v2["zones"] == pog_v2["zones"]
        assert saved.result_v2["legal_status"] == "adopted"


def test_resume_recomputes_compatibility_without_resolving_manual_pairs() -> None:
    """Obowiązujący POG (SN) × ręczny symbol MPZP z funkcją produkcyjną.

    Tabela zna wynik (incompatible), ale strefa podana ręcznie nie ma geometrii,
    więc para nie jest zidentyfikowana przestrzennie: status ``uncertain``,
    ``rule_result=incompatible``, ręczna weryfikacja. Ocena trafia do odpowiedzi,
    kolumny i ``result_v2`` (źródło prawdy odczytu), a boolean znika.
    """
    from app.services.persistence import build_analyze_response_from_analysis

    analysis_id = _create_waiting_analysis("122101_1.0001.9020")
    pog_v2 = {
        "schema_version": "2.0",
        "legal_status": "binding",
        "legal_status_evidence": {
            "source_name": "RU",
            "official": True,
            "reference": "https://ru.example.test/act/1",
        },
        "status_confirmed_at": "2026-07-10T00:00:00+00:00",
        "coverage_status": "available",
        "act": {"id": "pog:test-2", "version": "2026-09-24"},
        "zones": [
            {
                "id": "zone:sn",
                "symbol": "SN",
                "type": "SN",
                "area_sqm": 100.0,
                "area_pct": 100.0,
                "max_building_height_m": None,
            }
        ],
        "dominant_zone_id": "zone:sn",
        "ouz": [],
        "downtown_areas": [],
        "social_infrastructure_standard_areas": [],
        "status": "binding",
        "planning_zone": "SN",
        "zone_type": "SN",
        "in_ouz": False,
        "area_ratio": 1.0,
        "in_downtown_area": False,
        "manual_review_required": False,
        "conflict_with_mpzp": None,
        "touches_ouz_boundary": False,
    }
    with SessionLocal() as db:
        record = db.scalar(select(PogData).where(PogData.analysis_id == analysis_id))
        assert record is not None
        record.legal_status = "binding"
        record.planning_zone = "SN"
        record.schema_version = "2.0"
        record.result_v2 = pog_v2
        record.legacy_partial = False
        db.commit()

    response, _ = _resume(
        analysis_id,
        parser_result=_parser_result(
            MpzpParameter(
                name="primary_use",
                normalized_value="production",
                confidence=0.9,
                manual_review_required=False,
            )
        ),
    )

    assert response.status_code == 200
    body_pog = response.json()["pog"]
    assert "conflict_with_mpzp" not in body_pog
    assessment = body_pog["compatibility_assessment"]
    assert assessment["status"] == "uncertain"
    assert assessment["as_of"] == "2026-07-10"
    pair = assessment["zone_pairs"][0]
    assert pair["mpzp_zone_symbol"] == "230_U"
    assert pair["mpzp_assignment_method"] == "manual_user_input"
    assert pair["spatially_identified"] is False
    assert pair["rule_result"] == "incompatible"
    assert pair["rule_id"] == "mpzp-pog-function-table:production:SN"
    assert pair["manual_review_required"] is True
    assert body_pog["manual_review_required"] is True
    assert "MPZP_POG_PAIRS_NOT_SPATIAL" in {w["code"] for w in response.json()["warnings"]}

    with SessionLocal() as db:
        saved = db.scalar(select(PogData).where(PogData.analysis_id == analysis_id))
        assert saved is not None
        assert saved.compatibility_assessment["status"] == "uncertain"
        assert saved.manual_review_required is True
        assert "conflict_with_mpzp" not in saved.result_v2
        assert saved.result_v2["compatibility_assessment"]["zone_pairs"][0]["rule_result"] == (
            "incompatible"
        )
        analysis = db.get(Analysis, analysis_id)
        rebuilt = build_analyze_response_from_analysis(analysis, db)
        assert rebuilt.pog is not None
        assert rebuilt.pog.compatibility_assessment is not None
        assert rebuilt.pog.compatibility_assessment.status == "uncertain"


def test_resume_keeps_partial_status_when_parser_result_is_not_complete() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9016")

    response, _ = _resume(analysis_id, parser_result=_parser_result(status="partial"))

    assert response.status_code == 200
    assert response.json()["status"] == "partial"


def test_resume_warns_about_parameters_not_in_flat_contract() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9017")

    response, _ = _resume(
        analysis_id,
        parser_result=_parser_result(
            MpzpParameter(
                name="prohibition",
                normalized_value="zakaz zabudowy",
                confidence=0.6,
                manual_review_required=True,
            )
        ),
    )

    body = response.json()
    assert any(
        warning["code"] == "MPZP_PARAMETERS_NOT_IN_FLAT_CONTRACT"
        for warning in body["warnings"]
    )


def test_resume_reports_document_audit_persistence_failure() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9028")
    with patch(
        "app.services.analysis_resume.persist_parser_audit",
        side_effect=RuntimeError("audyt niedostępny"),
    ):
        response, _ = _resume(analysis_id)

    assert response.status_code == 200
    body = response.json()
    assert "MPZP_DOCUMENT_PERSISTENCE_FAILED" in {w["code"] for w in body["warnings"]}
    assert body["status"] == "partial"
    assert _zone_count(analysis_id) == 1


def test_resume_uses_manual_zone_symbol_confidence_constant() -> None:
    from app.services.mpzp_zones import MANUAL_ZONE_SYMBOL_CONFIDENCE

    assert MANUAL_ZONE_SYMBOL_CONFIDENCE == 0.5


@pytest.mark.asyncio
async def test_resume_rolls_back_all_snapshot_changes_when_persistence_fails() -> None:
    from app.services.analysis_resume import resume_analysis_with_zone

    analysis_id = _create_waiting_analysis("122101_1.0001.9018")
    with (
        patch(
            "app.services.analysis_resume.parse_mpzp_document",
            new=AsyncMock(return_value=_parser_result(status="partial")),
        ),
        patch(
            "app.services.analysis_resume.add_mpzp_zone_snapshot",
            side_effect=RuntimeError("kontrolowany błąd zapisu"),
        ),
        SessionLocal() as db,
    ):
        with pytest.raises(RuntimeError, match="kontrolowany błąd zapisu"):
            await resume_analysis_with_zone(analysis_id, "230_U", db)

    _assert_snapshot_untouched(analysis_id)


# --- GET /analyze/{id}/pending-document -----------------------------------


def test_pending_document_serves_exact_pinned_copy_with_safe_headers() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9029")

    response = client.get(f"/analyze/{analysis_id}/pending-document?access_token={make_analysis_token(analysis_id)}")

    assert response.status_code == 200
    assert response.content == PINNED_CONTENT
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["x-document-sha256"] == sha256(PINNED_CONTENT).hexdigest()
    assert response.headers["referrer-policy"] == "no-referrer"


def test_pending_html_document_is_attachment_in_sandbox() -> None:
    analysis_id = _create_waiting_analysis("122101_1.0001.9030")
    with SessionLocal() as db:
        pinned = db.scalar(
            select(AnalysisPendingDocument).where(
                AnalysisPendingDocument.analysis_id == analysis_id
            )
        )
        pinned.media_type = "text/html"
        db.commit()

    response = client.get(f"/analyze/{analysis_id}/pending-document?access_token={make_analysis_token(analysis_id)}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert "sandbox" in response.headers["content-security-policy"]


def test_pending_document_returns_404_for_unknown_or_unpinned_analysis() -> None:
    unpinned = _create_waiting_analysis("122101_1.0001.9031", pinned_content=None)

    assert client.get(f"/analyze/999999999/pending-document?access_token={make_analysis_token(999999999)}").status_code == 404
    assert client.get(f"/analyze/{unpinned}/pending-document?access_token={make_analysis_token(unpinned)}").status_code == 404
    assert client.get("/analyze/0/pending-document").status_code == 422
