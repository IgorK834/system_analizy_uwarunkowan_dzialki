"""Testy integracyjne generatora raportu PDF i endpointu ``GET /report``.

Testy wymagają uruchomionego kontenera bazy (PostGIS) oraz zależności WeasyPrint
(Pango/Cairo/fonty), dlatego są oznaczone ``integration`` i uruchamiane w Docker
Compose. Kontrolowane fixtures analiz są zapisywane przez ``save_analysis``, aby
raport był budowany z realnego snapshotu w bazie, nie z danych mockowanych.
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pdfplumber
import pytest
import respx
from fastapi.testclient import TestClient
from shapely.geometry import box
from sqlalchemy import delete, select

from app.core.report_config import REPORT_DISCLAIMER
from app.core.settings import settings
from app.db.session import SessionLocal
from app.main import app
from app.models.analysis import Analysis
from app.models.infrastructure import Infrastructure
from app.models.mpzp_parameter import MpzpParameter
from app.models.mpzp_zone import MpzpZone
from app.models.parcel import Parcel
from app.models.pog_data import PogData
from app.models.risk import Risk
from app.models.source_record import SourceRecord
from app.schemas.analyze import (
    AnalyzeResponse,
    GeometryMetrics,
    InfrastructureResult,
    MpzpZoneResult,
    ParcelGeometryResponse,
    PogResult,
    RiskResult,
    UtilitiesPreviewResult,
    WarningMessage,
)
from app.schemas.source import SourceMetadata
from app.schemas.mpzp import (
    MpzpParameter as ParserMpzpParameter,
    MpzpParseResult,
    MpzpParserWarning,
    MpzpZoneResult as ParserMpzpZoneResult,
)
from app.services import report as report_module
from app.services.context import ContextResult, ContextSectionResult
from app.services.mpzp_fetch import DocumentBlob
from app.services.persistence import save_analysis
from app.services.report import (
    AnalysisReportNotFoundError,
    generate_analysis_report_pdf,
)

pytestmark = pytest.mark.integration

client = TestClient(app)

_PARCEL_PREFIX = "REPORT_TEST_"
_ANALYZED_AT = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
_PDF_SIGNATURE = b"%PDF"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Geometria działki w EPSG:2180 (rejon Krakowa), tak jak zapisuje persistence.
_GEOMETRY = box(500000, 200000, 500100, 200100)

# Geometria działki w WGS84 do warstw GeoJSON w wyniku (miniatura mapy).
_PARCEL_WGS84 = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [
            [
                [19.940, 50.060],
                [19.945, 50.060],
                [19.945, 50.064],
                [19.940, 50.064],
                [19.940, 50.060],
            ]
        ],
    },
    "properties": {"layer": "parcel"},
}


def _cleanup() -> None:
    with SessionLocal() as db:
        parcel_ids = select(Parcel.id).where(
            Parcel.parcel_identifier.like(f"{_PARCEL_PREFIX}%")
        )
        analysis_ids = select(Analysis.id).where(Analysis.parcel_id.in_(parcel_ids))
        zone_ids = select(MpzpZone.id).where(MpzpZone.analysis_id.in_(analysis_ids))

        db.execute(
            delete(MpzpParameter).where(MpzpParameter.mpzp_zone_id.in_(zone_ids))
        )
        db.execute(
            delete(SourceRecord).where(SourceRecord.analysis_id.in_(analysis_ids))
        )
        db.execute(delete(Risk).where(Risk.analysis_id.in_(analysis_ids)))
        db.execute(
            delete(Infrastructure).where(Infrastructure.analysis_id.in_(analysis_ids))
        )
        db.execute(delete(PogData).where(PogData.analysis_id.in_(analysis_ids)))
        db.execute(delete(MpzpZone).where(MpzpZone.analysis_id.in_(analysis_ids)))
        db.execute(delete(Analysis).where(Analysis.id.in_(analysis_ids)))
        db.execute(delete(Parcel).where(Parcel.id.in_(parcel_ids)))
        db.commit()


@pytest.fixture(autouse=True)
def cleanup_report_rows():
    _cleanup()
    yield
    _cleanup()


@pytest.fixture(autouse=True)
def disable_report_basemap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Integracyjne testy PDF nie odpytują WMS — domyślnie ścieżka MVP offline."""
    monkeypatch.setattr(settings, "report_map_basemap_enabled", False)
    monkeypatch.setattr(settings, "report_map_kiut_overlay_enabled", False)


def _source(
    name: str,
    url: str | None,
    *,
    confidence: float = 0.9,
    manual_review_required: bool = False,
    response_status: int | None = 200,
) -> SourceMetadata:
    return SourceMetadata(
        source_name=name,
        source_url=url,
        fetched_at=_ANALYZED_AT,
        response_status=response_status,
        confidence=confidence,
        manual_review_required=manual_review_required,
    )


def _parcel_response(identifier: str) -> ParcelGeometryResponse:
    return ParcelGeometryResponse(
        parcel_identifier=identifier,
        geometry_geojson=_PARCEL_WGS84,
        metrics=GeometryMetrics(
            area_sqm=10000.0,
            area_ha=1.0,
            perimeter_m=400.0,
            is_valid=True,
            geometry_repaired=False,
        ),
        source=_source("ULDK", "https://uldk.example.test"),
    )


def _full_response(identifier: str) -> AnalyzeResponse:
    return AnalyzeResponse(
        status="complete",
        analyzed_at=_ANALYZED_AT,
        parcel=_parcel_response(identifier),
        mpzp_zones=[
            MpzpZoneResult(
                zone_symbol="MN.1",
                primary_use="zabudowa mieszkaniowa jednorodzinna",
                supplementary_use="usługi nieuciążliwe",
                max_building_height_m=9.0,
                max_floors=2,
                min_biologically_active_pct=40.0,
                max_floor_area_ratio=0.8,
                min_floor_area_ratio=0.01,
                max_building_coverage_pct=30.0,
                intersection_area_sqm=10000.0,
                intersection_pct=100.0,
                is_dominant=True,
                source=_source("MPZP_BIP", "https://bip.example.test/plan.pdf"),
            )
        ],
        pog=PogResult(
            status="adopted",
            planning_zone="SJ",
            zone_type="SJ",
            in_ouz=True,
            area_ratio=0.75,
            in_downtown_area=True,
            uchwala_nr="X/42/2026",
            uchwala_date=date(2026, 2, 10),
            manual_review_required=False,
            ouz_intersection_area_sqm=2500.0,
            ouz_intersection_pct=25.0,
            touches_ouz_boundary=False,
            source=_source("POG", "https://pog.example.test"),
        ),
        infrastructure=[
            InfrastructureResult(
                network_type="water",
                buffer_m=4.0,
                zone_area_sqm=320.0,
                rule_source="konfiguracja techniczna",
                rule_confidence=0.65,
                rule_note="Bufor techniczny wymaga uzgodnienia z gestorem.",
                affects_buildable_area=True,
                network_geometry_geojson={
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[19.941, 50.061], [19.944, 50.063]],
                    },
                    "properties": {"layer": "network"},
                },
                protection_zone_geojson={
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [19.941, 50.061],
                                [19.943, 50.061],
                                [19.943, 50.063],
                                [19.941, 50.063],
                                [19.941, 50.061],
                            ]
                        ],
                    },
                    "properties": {"layer": "protection_zone"},
                },
                source=_source("KIUT", "https://kiut.example.test", confidence=0.7),
            )
        ],
        utilities_preview=UtilitiesPreviewResult(
            coverage_status="covered",
            county_name="powiat krakowski",
            layer_available=True,
            note=(
                "Powiat publikuje dane GESUT. Brak obiektów na podglądzie nie "
                "oznacza braku sieci; podgląd nie służy do obliczania odległości."
            ),
            source=_source(
                "KIUT (GUGiK)",
                "https://integracja.example.test/kiut",
            ),
        ),
        risks=[
            RiskResult(
                risk_type="flood_zone",
                description="Część działki leży w strefie zagrożenia powodziowego.",
                geometry_geojson={
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [19.940, 50.060],
                                [19.942, 50.060],
                                [19.942, 50.062],
                                [19.940, 50.062],
                                [19.940, 50.060],
                            ]
                        ],
                    },
                    "properties": {"layer": "risk"},
                },
                source=_source("ISOK", "https://isok.example.test"),
            )
        ],
        buildable_area_sqm=6000.0,
        warnings=[
            WarningMessage(
                code="ISOK_WARNING",
                message="Wynik ISOK wymaga sprawdzenia dla części działki.",
                severity="warning",
                source_name="ISOK",
            )
        ],
        sources=[
            _source("ULDK", "https://uldk.example.test"),
            _source("KIMPZP", "https://kimpzp.example.test", response_status=None),
        ],
    )


def _minimal_response(identifier: str) -> AnalyzeResponse:
    return AnalyzeResponse(
        status="partial",
        analyzed_at=_ANALYZED_AT,
        parcel=_parcel_response(identifier),
        mpzp_zones=[],
        pog=None,
        infrastructure=[],
        risks=[],
        buildable_area_sqm=None,
        warnings=[],
        sources=[_source("ULDK", "https://uldk.example.test")],
    )


def _context_result() -> ContextResult:
    return ContextResult(
        kiut=ContextSectionResult(
            section="kiut",
            status="available",
            source_metadata=_source("KIUT", "https://kiut.example.test", confidence=0.7),
            warnings=[],
        ),
        isok=ContextSectionResult(
            section="isok",
            status="unavailable",
            warnings=["Usługa ISOK jest niedostępna."],
        ),
        gdos=ContextSectionResult(
            section="gdos",
            status="available",
            source_metadata=_source("GDOŚ", "https://gdos.example.test"),
            warnings=[],
        ),
        nmt=ContextSectionResult(section="nmt", status="available"),
    )


def _save(
    response: AnalyzeResponse,
    identifier: str,
    *,
    context_result: ContextResult | None = None,
    database_status: str | None = None,
    pending_uchwala_url: str | None = None,
    pending_plan_id: str | None = None,
    pending_zone_symbol_candidates: list[str] | None = None,
    pending_document: DocumentBlob | None = None,
) -> int:
    with SessionLocal() as db:
        analysis = save_analysis(
            response,
            identifier,
            _GEOMETRY,
            db,
            context_result=context_result,
            database_status=database_status,
            pending_uchwala_url=pending_uchwala_url,
            pending_plan_id=pending_plan_id,
            pending_zone_symbol_candidates=pending_zone_symbol_candidates,
            pending_document=pending_document,
        )
        return analysis.id


def _extract_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        raw = "\n".join(page.extract_text() or "" for page in pdf.pages)
    # Normalizujemy białe znaki, aby asercje nie zależały od łamania wierszy PDF.
    return re.sub(r"\s+", " ", raw.replace("\u00a0", " "))


def _count_images(pdf_bytes: bytes) -> int:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return sum(len(page.images) for page in pdf.pages)


# --- Testy generatora PDF (bezpośrednio) ---


def test_full_analysis_pdf_starts_with_pdf_signature() -> None:
    analysis_id = _save(_full_response(f"{_PARCEL_PREFIX}FULL"), f"{_PARCEL_PREFIX}FULL")
    with SessionLocal() as db:
        pdf_bytes = generate_analysis_report_pdf(analysis_id, db)

    assert pdf_bytes.startswith(_PDF_SIGNATURE)
    assert len(pdf_bytes) > 1000


def test_full_analysis_pdf_contains_all_sections_and_polish_characters() -> None:
    identifier = f"{_PARCEL_PREFIX}SECTIONS"
    analysis_id = _save(_full_response(identifier), identifier)
    with SessionLocal() as db:
        pdf_bytes = generate_analysis_report_pdf(analysis_id, db)

    text = _extract_text(pdf_bytes)

    # Kluczowe sekcje raportu.
    assert "Parametry geometryczne" in text
    assert "MPZP" in text
    assert "Plan Ogólny Gminy" in text
    assert "Podgląd uzbrojenia terenu" in text
    assert "Infrastruktura" in text
    assert "Ryzyka" in text
    assert "Źródła danych" in text
    assert "Ograniczenia analizy" in text

    # Polskie znaki muszą przetrwać ekstrakcję tekstu (font z polskim zestawem).
    assert "działki" in text
    assert "Źródła" in text
    assert "zagrożenia" in text

    # Identyfikator i status analizy.
    assert identifier in text
    assert str(analysis_id) in text
    assert "complete" in text
    assert "powiat publikuje dane GESUT w KIUT" in text
    assert "powiat krakowski" in text
    assert "Raport nie zawiera odległości ani liczby sieci" in text
    assert "Nie da się na podstawie podglądu WMS stwierdzić" in text


def test_full_analysis_pdf_contains_disclaimer_clause() -> None:
    identifier = f"{_PARCEL_PREFIX}DISCLAIMER"
    analysis_id = _save(_full_response(identifier), identifier)
    with SessionLocal() as db:
        pdf_bytes = generate_analysis_report_pdf(analysis_id, db)

    text = _extract_text(pdf_bytes)
    normalized_clause = re.sub(r"\s+", " ", REPORT_DISCLAIMER)
    assert normalized_clause in text


def test_full_analysis_pdf_embeds_map_thumbnail() -> None:
    identifier = f"{_PARCEL_PREFIX}MAP"
    analysis_id = _save(_full_response(identifier), identifier)
    with SessionLocal() as db:
        pdf_bytes = generate_analysis_report_pdf(analysis_id, db)

    # Miniatura mapy musi być osadzona jako obraz w PDF.
    assert _count_images(pdf_bytes) >= 1


def test_full_analysis_pdf_contains_source_metadata() -> None:
    identifier = f"{_PARCEL_PREFIX}SRC"
    analysis_id = _save(
        _full_response(identifier), identifier, context_result=_context_result()
    )
    with SessionLocal() as db:
        pdf_bytes = generate_analysis_report_pdf(analysis_id, db)

    text = _extract_text(pdf_bytes)
    # Źródła zawierają datę pobrania, opisową pewność i status/weryfikację.
    assert "16.07.2026" in text
    assert "ULDK" in text
    assert ("wysoka" in text or "średnia" in text or "niska" in text)


def test_report_without_pog_renders_unavailable_section() -> None:
    identifier = f"{_PARCEL_PREFIX}NOPOG"
    analysis_id = _save(_minimal_response(identifier), identifier)
    with SessionLocal() as db:
        pdf_bytes = generate_analysis_report_pdf(analysis_id, db)

    text = _extract_text(pdf_bytes)
    assert pdf_bytes.startswith(_PDF_SIGNATURE)
    # Brak POG jest jawnie pokazany jako niedostępność, nie ukryty.
    assert "brak danych POG" in text
    assert "POG" in text


def test_report_with_manual_review_shows_limitations() -> None:
    identifier = f"{_PARCEL_PREFIX}MANUAL"
    response = _full_response(identifier).model_copy(
        update={
            "mpzp_zones": [
                MpzpZoneResult(
                    zone_symbol="230_U",
                    primary_use="usługi",
                    intersection_area_sqm=10000.0,
                    intersection_pct=100.0,
                    is_dominant=True,
                    source=_source(
                        "MPZP_BIP",
                        "https://bip.example.test/plan.pdf",
                        confidence=0.4,
                        manual_review_required=True,
                    ),
                )
            ],
        }
    )
    analysis_id = _save(
        response, identifier, database_status="waiting_for_zone_symbol"
    )
    with SessionLocal() as db:
        pdf_bytes = generate_analysis_report_pdf(analysis_id, db)

    text = _extract_text(pdf_bytes)
    assert "Ograniczenia analizy" in text
    # Ostrzeżenie o ręcznej weryfikacji i fallbacku WMS musi znaleźć się w raporcie.
    assert "weryfikacji" in text
    assert "rastrow" in text  # "mapy rastrowej"
    # Symbol z discovery bez wektora nie jest opisywany jako ręczny.
    assert "symbol strefy podano ręcznie" not in text.lower()


def test_report_after_real_resume_contains_manual_wms_and_parser_warnings() -> None:
    """Raport czyta snapshot zapisany przez prawdziwy endpoint resume.

    Dokument jest przypinany przy wstrzymaniu (BK-204), więc resume nie pobiera
    go ponownie; mockowany jest wyłącznie parser. Router, transakcja
    wznowienia, persistence oraz późniejszy GET /report wykonują się w całości,
    dzięki czemu test wykrywa utratę danych między UI a raportem.
    """
    identifier = f"{_PARCEL_PREFIX}RESUME"
    waiting_response = _minimal_response(identifier).model_copy(
        update={
            "status": "waiting_for_user_input",
            "manual_zone_required": True,
            "warnings": [
                WarningMessage(
                    code="MPZP_MANUAL_ZONE_REQUIRED",
                    message="Symbol strefy MPZP wymaga ręcznego podania.",
                    severity="warning",
                    source_name="mpzp",
                )
            ],
        }
    )
    document_url = "https://bip.example.test/resume-plan.pdf"
    document = DocumentBlob(
        content=b"%PDF-mock",
        media_type="application/pdf",
        filename="resume-plan.pdf",
        source_metadata=_source(
            "MPZP_BIP",
            document_url,
            confidence=0.45,
            manual_review_required=True,
        ),
    )
    analysis_id = _save(
        waiting_response,
        identifier,
        database_status="waiting_for_zone_symbol",
        pending_uchwala_url=document_url,
        pending_plan_id="MPZP/REPORT/1",
        pending_zone_symbol_candidates=["230_U"],
        pending_document=document,
    )
    parser_result = MpzpParseResult(
        plan_id="MPZP/REPORT/1",
        zones=[
            ParserMpzpZoneResult(
                zone_symbol="230_U",
                parameters=[
                    ParserMpzpParameter(
                        name="max_building_height_m",
                        normalized_value=12.0,
                        unit="m",
                        raw_value="12 m",
                        source_text="Maksymalna wysokość zabudowy wynosi 12 m.",
                        page_number=7,
                        confidence=0.4,
                        manual_review_required=True,
                    )
                ],
            )
        ],
        status="partial",
        warnings=[
            MpzpParserWarning(
                stage="extract_text",
                code="MPZP_PDF_LOW_CONFIDENCE",
                message="Parser PDF wymaga ręcznej weryfikacji odczytu.",
                page_number=7,
                severity="warning",
            )
        ],
    )

    with patch(
        "app.services.analysis_resume.parse_mpzp_document",
        new_callable=AsyncMock,
        return_value=parser_result,
    ) as parse_mock:
        resume_response = client.post(
            "/analyze/resume",
            json={"analysis_id": analysis_id, "zone_symbol": "230_U"},
        )

    assert resume_response.status_code == 200
    resumed = resume_response.json()
    assert resumed["status"] == "partial"
    assert resumed["manual_zone_required"] is False
    assert resumed["mpzp_zones"][0]["source"]["source_name"] == "manual_user_input"
    assert any(
        warning["code"] == "MPZP_MANUAL_ZONE_FALLBACK"
        for warning in resumed["warnings"]
    )
    parse_mock.assert_awaited_once()
    assert parse_mock.await_args.args[0].content == b"%PDF-mock"

    report_response = client.get(f"/report/{analysis_id}")

    assert report_response.status_code == 200
    assert report_response.headers["content-type"] == "application/pdf"
    assert report_response.content.startswith(_PDF_SIGNATURE)
    text = _extract_text(report_response.content)
    assert "230_U" in text
    assert "symbol strefy podano ręcznie" in text.lower()
    assert "nieustalony" in text
    assert "kopia przypięta przy wstrzymaniu analizy" in text
    assert "Relacja MPZP–POG — analiza informacyjna" in text
    assert "Parser PDF wymaga ręcznej weryfikacji odczytu" in text


def test_report_without_context_data_still_generates() -> None:
    identifier = f"{_PARCEL_PREFIX}NOCTX"
    analysis_id = _save(_minimal_response(identifier), identifier)
    with SessionLocal() as db:
        pdf_bytes = generate_analysis_report_pdf(analysis_id, db)

    text = _extract_text(pdf_bytes)
    assert pdf_bytes.startswith(_PDF_SIGNATURE)
    # Sekcje kontekstowe są jawnie niedostępne, ale raport jest kompletny.
    assert "Infrastruktura" in text
    assert "Ryzyka" in text
    assert "niedostępne" in text


def test_report_without_drawable_geometry_adds_warning_not_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Symulujemy brak geometrii do narysowania: renderer zwraca pusty wynik.
    from app.services.report_map import MapRenderResult

    monkeypatch.setattr(
        report_module,
        "render_analysis_map_png",
        lambda *a, **k: MapRenderResult(png_bytes=None),
    )
    identifier = f"{_PARCEL_PREFIX}NOGEO"
    analysis_id = _save(_full_response(identifier), identifier)
    with SessionLocal() as db:
        pdf_bytes = generate_analysis_report_pdf(analysis_id, db)

    text = _extract_text(pdf_bytes)
    assert pdf_bytes.startswith(_PDF_SIGNATURE)
    assert _count_images(pdf_bytes) == 0
    assert "Miniatura mapy jest niedostępna" in text


@respx.mock
def test_report_survives_basemap_failure_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Awaria podkładu WMS nie blokuje PDF — obrys na fallback + ostrzeżenie."""
    wms_url = "https://wms.example.test/report-osm"
    monkeypatch.setattr(settings, "report_map_basemap_enabled", True)
    monkeypatch.setattr(settings, "report_map_wms_base_url", wms_url)
    monkeypatch.setattr(settings, "report_map_wms_layers", "OSM-WMS")
    monkeypatch.setattr(settings, "report_map_kimpzp_overlay_enabled", False)
    respx.get(wms_url).mock(return_value=httpx.Response(503))

    identifier = f"{_PARCEL_PREFIX}BASEFAIL"
    analysis_id = _save(_full_response(identifier), identifier)
    with SessionLocal() as db:
        pdf_bytes = generate_analysis_report_pdf(analysis_id, db)

    text = _extract_text(pdf_bytes)
    assert pdf_bytes.startswith(_PDF_SIGNATURE)
    assert _count_images(pdf_bytes) >= 1
    assert "Nie udało się pobrać podkładu mapowego" in text


def test_report_survives_map_backdrop_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Awaria opcjonalnego podkładu mapowego nie może zablokować raportu.
    def _boom(*args, **kwargs):
        raise RuntimeError("symulowana awaria renderera mapy")

    monkeypatch.setattr(report_module, "render_analysis_map_png", _boom)
    identifier = f"{_PARCEL_PREFIX}MAPFAIL"
    analysis_id = _save(_full_response(identifier), identifier)
    with SessionLocal() as db:
        pdf_bytes = generate_analysis_report_pdf(analysis_id, db)

    text = _extract_text(pdf_bytes)
    assert pdf_bytes.startswith(_PDF_SIGNATURE)
    assert "Nie udało się wygenerować miniatury mapy" in text


def test_generate_report_for_missing_analysis_raises_not_found() -> None:
    with SessionLocal() as db:
        with pytest.raises(AnalysisReportNotFoundError):
            generate_analysis_report_pdf(999_999_999, db)


# --- Testy endpointu HTTP ---


def test_endpoint_returns_pdf_content_type_and_disposition() -> None:
    identifier = f"{_PARCEL_PREFIX}ENDPOINT"
    analysis_id = _save(_full_response(identifier), identifier)

    response = client.get(f"/report/{analysis_id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert f"raport_analizy_{analysis_id}.pdf" in disposition
    assert response.content.startswith(_PDF_SIGNATURE)


def test_endpoint_returns_404_for_missing_analysis() -> None:
    response = client.get("/report/999999999")

    assert response.status_code == 404
    body = response.json()
    assert "nie istnieje" in body["detail"]
    # Odpowiedź błędu nie może ujawniać szczegółów bazy ani stack trace.
    assert "Traceback" not in body["detail"]


def test_endpoint_rejects_non_positive_id() -> None:
    response = client.get("/report/0")
    assert response.status_code == 422


def test_openapi_contains_report_path() -> None:
    openapi = client.get("/openapi.json").json()
    assert "/report/{analysis_id}" in openapi["paths"]
