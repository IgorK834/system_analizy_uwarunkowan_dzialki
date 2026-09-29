"""Scenariusz końcowy BK-301/BK-302 na rzeczywistym połączeniu warstw.

Kontrolny pomiar Warszawy (``GetMinMaxByPolygon``: Hmin 112,3 m, Hmax 115,7 m)
przechodzi przez realny adapter NMT, sekcję kontekstu, orkiestrator, zapis w
PostGIS, odczyt historyczny, trafienie cache i raport PDF (WeasyPrint). Pochodne
rastra liczy realny adapter WCS z dekoderem GDAL na zamrożonym GeoTIFF.
Scenariusz sprawdza też cztery rozłączne zachowania (brak pokrycia, timeout,
stary snapshot, faktyczne 0 m) oraz to, że wznowienie MPZP nie usuwa ``terrain``.

Usługi zewnętrzne są zamrożonymi fixture'ami — bez internetu. Jeżeli ustawiono
``EVIDENCE_OUT``, test zapisuje artefakty dowodowe (JSON, HTML i PDF).
"""

from __future__ import annotations

import io
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pdfplumber
import pytest
import respx
from fastapi.testclient import TestClient
from shapely.geometry import box
from sqlalchemy import delete, select, text

from app.core.settings import settings
from app.db.session import SessionLocal
from app.main import app
from app.models.analysis import Analysis
from app.models.analysis_pending_document import AnalysisPendingDocument
from app.models.infrastructure import Infrastructure
from app.models.mpzp_parameter import MpzpParameter
from app.models.mpzp_zone import MpzpZone
from app.models.parcel import Parcel
from app.models.pog_data import PogData
from app.models.risk import Risk
from app.models.source_record import SourceRecord
from app.modules.analysis import composition
from app.schemas.analyze import UtilitiesPreviewResult
from app.schemas.source import SourceMetadata
from app.services.initiation import ParcelLookupResult
from app.services.mpzp import MpzpDiscoveryResult
from app.services.persistence import build_analyze_response_from_analysis
from app.services.pog_fetch import PogVectorData, PogVectorFeature
from app.services.report import _build_report_context, _render_report_html
from tests.mpzp_fixtures import document_blob, text_pdf
from tests.terrain_fixtures import FakeWcsClient, fixture_bytes, requires_gdal
from tests.test_analysis_orchestrator import _binding_pog_discovery
from tests.test_terrain_relief_usecase import _catalog

pytestmark = pytest.mark.integration

_PREFIX = "BK301_302_E2E_"
NOW = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)
CONTROL = box(637000, 486000, 637100, 486100)
EVIDENCE_OUT = os.environ.get("EVIDENCE_OUT")
NMT_CONTRACTS = Path(__file__).parent / "fixtures" / "source_contracts"
client = TestClient(app)


def _cleanup() -> None:
    with SessionLocal() as db:
        parcel_ids = select(Parcel.id).where(Parcel.parcel_identifier.like(f"{_PREFIX}%"))
        analysis_ids = select(Analysis.id).where(Analysis.parcel_id.in_(parcel_ids))
        zone_ids = select(MpzpZone.id).where(MpzpZone.analysis_id.in_(analysis_ids))
        db.execute(delete(MpzpParameter).where(MpzpParameter.mpzp_zone_id.in_(zone_ids)))
        for model in (SourceRecord, PogData, MpzpZone, Infrastructure, Risk, AnalysisPendingDocument):
            db.execute(delete(model).where(model.analysis_id.in_(analysis_ids)))
        db.execute(delete(Analysis).where(Analysis.id.in_(analysis_ids)))
        db.execute(delete(Parcel).where(Parcel.id.in_(parcel_ids)))
        db.commit()


@pytest.fixture(autouse=True)
def cleanup_rows():
    _cleanup()
    yield
    _cleanup()


@pytest.fixture
def relief_enabled(monkeypatch: pytest.MonkeyPatch) -> FakeWcsClient:
    """Włącza BK-302 z zamrożonym WCS i kontraktem katalogu (bez internetu)."""
    wcs = FakeWcsClient(coverage=fixture_bytes("wcs_getcoverage_warszawa_parcel_window.tif"))
    monkeypatch.setattr(settings, "terrain_relief_enabled", True)
    monkeypatch.setattr(composition, "build_ogc_client", lambda **_: wcs)
    monkeypatch.setattr(
        composition,
        "ensure_source_runnable",
        lambda source_id, catalog=None: _catalog().get(source_id),
    )
    return wcs


def _source(name: str, url: str | None = None, **kwargs) -> SourceMetadata:
    return SourceMetadata(
        source_name=name,
        source_url=url,
        fetched_at=NOW,
        confidence=kwargs.pop("confidence", 0.9),
        manual_review_required=kwargs.pop("manual", False),
        **kwargs,
    )


def _lookup(identifier: str, wkt: str) -> ParcelLookupResult:
    return ParcelLookupResult(
        parcel_identifier=identifier,
        wkt=wkt,
        teryt="146501",
        source_metadata=_source("ULDK", "https://uldk.example.test", confidence=0.95),
    )


def _kiut() -> UtilitiesPreviewResult:
    return UtilitiesPreviewResult(
        coverage_status="covered",
        county_name="Warszawa",
        layer_available=True,
        note="Podgląd nie służy do odległości.",
        source=_source("KIUT (GUGiK)", "https://kiut.example.test/wms"),
    )


def _pog_vectors() -> PogVectorData:
    return PogVectorData(
        planning_zones=[
            PogVectorFeature(
                geometry=CONTROL,
                attributes={"zone_type": "SJ", "feature_id": "pog:SJ"},
                source_crs="EPSG:2180",
                layer_type="planning_zone",
            )
        ],
        ouz_areas=[],
        downtown_areas=[],
        app_metadata={"uchwala_nr": "X/42/2026"},
        status="available",
        wms_fallback_required=False,
        source_metadata=_source("POG_APP", "https://bip.example.test/pog.gml"),
    )


def _no_mpzp() -> MpzpDiscoveryResult:
    return MpzpDiscoveryResult(
        plan_id=None,
        candidate_zone_symbols=[],
        uchwala_url=None,
        brak_wektorow=False,
        status="no_mpzp",
        is_discovery_only=True,
        source_metadata=_source("KIMPZP", confidence=0.3, manual=True),
    )


def _raster_mpzp(document_url: str) -> MpzpDiscoveryResult:
    return MpzpDiscoveryResult(
        plan_id="MPZP/NMT/1",
        candidate_zone_symbols=["1MN"],
        uchwala_url=document_url,
        brak_wektorow=True,
        status="raster_only",
        is_discovery_only=True,
        source_metadata=_source("KIMPZP", confidence=0.3, manual=True),
        warnings=["Gmina nie udostępnia wektorowych danych MPZP."],
    )


def _patches(identifier: str, parcel_wkt: str, discovery: MpzpDiscoveryResult, document_url: str | None = None):
    """Wszystko poza NMT/WCS jest zamrożone; sekcja kontekstu i adapter NMT są realne."""
    return (
        patch("app.services.analysis_orchestrator.resolve_parcel", new=AsyncMock(return_value=_lookup(identifier, parcel_wkt))),
        patch("app.services.context.fetch_kiut_network_section", new=AsyncMock(return_value=[])),
        patch("app.services.context.fetch_flood_risk_section", new=AsyncMock(return_value=[])),
        patch("app.services.context.fetch_nature_protection_section", new=AsyncMock(return_value=[])),
        patch("app.services.analysis_orchestrator.discover_mpzp", new=AsyncMock(return_value=discovery)),
        patch("app.services.analysis_orchestrator.discover_pog", new=AsyncMock(return_value=_binding_pog_discovery())),
        patch("app.services.analysis_orchestrator.fetch_pog_vector_data", new=AsyncMock(return_value=_pog_vectors())),
        patch("app.services.analysis_orchestrator.check_kiut_coverage_for_geometry", new=AsyncMock(return_value=_kiut())),
        patch(
            "app.services.analysis_orchestrator.fetch_mpzp_document",
            new=AsyncMock(return_value=document_blob(text_pdf(), document_url or "https://bip.example.gov.pl/x.pdf")),
        ),
    )


def _post_analyze(identifier: str, parcel_wkt: str, nmt_response, discovery=None, document_url=None):
    patches = _patches(identifier, parcel_wkt, discovery or _no_mpzp(), document_url)
    for item in patches:
        item.start()
    try:
        with respx.mock(assert_all_called=False) as router:
            route = router.get(settings.nmt_base_url)
            if isinstance(nmt_response, Exception):
                route.mock(side_effect=nmt_response)
            else:
                route.mock(return_value=httpx.Response(200, text=nmt_response))
            response = client.post(
                "/analyze", json={"method": "parcel_id", "parcel_identifier": identifier}
            )
            return response, route.call_count
    finally:
        for item in reversed(patches):
            item.stop()


def _nmt(name: str) -> str:
    return (NMT_CONTRACTS / name).read_text(encoding="utf-8")


def _pdf_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        raw = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return re.sub(r"\s+", " ", raw.replace(" ", " "))


def _write_evidence(name: str, payload: bytes | str | dict) -> None:
    if not EVIDENCE_OUT:
        return
    target = Path(EVIDENCE_OUT) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, bytes):
        target.write_bytes(payload)
    elif isinstance(payload, str):
        target.write_text(payload, encoding="utf-8")
    else:
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _reread(analysis_id: int) -> dict:
    with SessionLocal() as db:
        response = build_analyze_response_from_analysis(db.get(Analysis, analysis_id), db)
    return response.model_dump(mode="json")


# --- Kontrolny pomiar: API → DB → odczyt → cache → PDF ----------------------


@requires_gdal
def test_control_measurement_survives_api_db_cache_and_pdf(relief_enabled: FakeWcsClient) -> None:
    identifier = f"{_PREFIX}{uuid4().hex[:6]}"

    response, nmt_calls = _post_analyze(identifier, CONTROL.wkt, _nmt("nmt_getminmaxbypolygon.txt"))

    # API: pomiar, jednostki i metadane źródła.
    assert response.status_code == 200 and nmt_calls == 1
    body = response.json()
    terrain = body["terrain"]
    assert terrain["status"] == "available"
    assert (terrain["min_height_m"], terrain["max_height_m"], terrain["height_difference_m"]) == (112.3, 115.7, 3.4)
    assert (terrain["grid_size_m"], terrain["sampled_points"]) == (4.0, 676)
    assert terrain["source"]["source_id"] == "nmt"
    assert terrain["source"]["response_status"] == 200
    assert len(terrain["source"]["artifact_sha256"]) == 64
    relief = terrain["relief"]
    assert relief["status"] == "available"
    assert relief["resolution_m"] == 1.0
    assert relief["raster"]["gdal_version"].startswith("GDAL ")
    assert relief["source"]["artifact_sha256"].startswith("603a9ddd")
    assert [item["share_pct"] for item in relief["slope_classes"]] == [36.48, 38.58, 11.28, 3.16, 4.35, 6.15]
    assert len(relief["profile"]["samples"]) == 101
    # Źródła WCS 1 m i GetMinMax 4 m różnią się o > 1 m — widoczne ostrzeżenie.
    assert any("różnią się od usługi" in item for item in relief["warnings"])
    # NMT jest sekcją informacyjną: status nie zależy od rzeźby terenu.
    assert body["status"] == "partial"
    analysis_id = body["analysis_id"]
    _write_evidence("bk-301-302/01-api-response.json", body)

    # DB: snapshot JSONB i rejestr źródeł.
    with SessionLocal() as db:
        saved = db.get(Analysis, analysis_id)
        assert saved.terrain == terrain
        records = db.scalars(select(SourceRecord).where(SourceRecord.analysis_id == analysis_id)).all()
    by_name = {record.source_name: record for record in records}
    assert by_name["NMT"].response_status == "200"
    assert by_name["NMT"].artifact_sha256 == terrain["source"]["artifact_sha256"]
    assert by_name["NMT_WCS"].artifact_sha256 == relief["source"]["artifact_sha256"]

    # Odczyt historyczny: identyczny wynik NMT (w tym klasy, profil, rozdzielczość).
    reread = _reread(analysis_id)
    assert reread["terrain"] == terrain

    # Cache: drugie żądanie nie odpytuje NMT i zwraca ten sam pomiar.
    cached, cached_nmt_calls = _post_analyze(identifier, CONTROL.wkt, _nmt("nmt_getminmaxbypolygon.txt"))
    assert cached.status_code == 200 and cached_nmt_calls == 0
    assert cached.json()["analysis_id"] == analysis_id
    assert cached.json()["terrain"] == terrain
    assert len(relief_enabled.coverage_calls) == 1

    # PDF z zapisanego snapshotu.
    report = client.get(f"/report/{analysis_id}")
    assert report.status_code == 200
    pdf_text = _pdf_text(report.content)
    for expected in (
        "Rzeźba terenu (NMT)",
        "Najniższa wysokość (Hmin) 112,3 m",
        "Najwyższa wysokość (Hmax) 115,7 m",
        "Deniwelacja (Hmax − Hmin) 3,4 m",
        "Siatka próbkowania usługi 4 m",
        "Liczba punktów siatki 676",
        "Rozdzielczość danych źródłowych 1 m",
        "Spadek, ekspozycja i profil (raster NMT)",
        "umiarkowany (5–10%)",
        "DTM_PL-KRON86-NH_TIFF",
        "horn1981-3x3-v1",
    ):
        assert expected in pdf_text, expected
    assert terrain["source"]["artifact_sha256"] in pdf_text.replace(" ", "")
    _write_evidence("bk-301-302/02-report.pdf", report.content)


# --- Cztery rozłączne zachowania -------------------------------------------


def _flat_text() -> str:
    return (
        "Polygon:\tPOLYGON((637000 486000,637100 486000,637100 486100,637000 486100,637000 486000))\n"
        "Polygon area:\t10000\nPoints count:\t676\nGrid size [m]:\t4\n"
        "Hmin:\t101.2\nHmax:\t101.2\n"
    )


def test_no_coverage_timeout_legacy_and_real_zero_are_distinct() -> None:
    cases = {
        "no_coverage": _nmt("nmt_getminmaxbypolygon_brak_pokrycia.txt"),
        "timeout": httpx.ReadTimeout("NMT nie odpowiedział"),
        "flat": _flat_text(),
    }
    results: dict[str, dict] = {}
    for name, nmt_response in cases.items():
        identifier = f"{_PREFIX}{name}_{uuid4().hex[:6]}"
        response, _ = _post_analyze(identifier, CONTROL.wkt, nmt_response)
        assert response.status_code == 200
        results[name] = response.json()

    # Stary snapshot: wiersz zapisany przed BK-301 nie ma kolumny terrain (NULL).
    legacy_copy = f"{_PREFIX}legacy_{uuid4().hex[:6]}"
    legacy_response, _ = _post_analyze(legacy_copy, CONTROL.wkt, _flat_text())
    legacy_id = legacy_response.json()["analysis_id"]
    with SessionLocal() as db:
        db.execute(text("UPDATE analyses SET terrain = NULL WHERE id = :id"), {"id": legacy_id})
        db.commit()

    reread = {name: _reread(body["analysis_id"])["terrain"] for name, body in results.items()}
    reread["legacy"] = _reread(legacy_id)["terrain"]

    assert {name: item["status"] for name, item in reread.items()} == {
        "no_coverage": "no_coverage",
        "timeout": "unavailable",
        "flat": "available",
        "legacy": "unknown",
    }
    assert reread["flat"]["height_difference_m"] == 0.0
    for name in ("no_coverage", "timeout", "legacy"):
        assert reread[name]["height_difference_m"] is None, name
        assert reread[name]["min_height_m"] is None, name
    assert reread["no_coverage"]["reason_code"] == "NO_COVERAGE_SENTINEL"
    assert reread["no_coverage"]["grid_size_m"] == 4.0
    assert reread["timeout"]["reason_code"] == "SERVICE_TIMEOUT"
    assert reread["legacy"]["reason_code"] == "LEGACY_SNAPSHOT"
    # Provenance także dla wyników pustych (poza snapshotem bez danych).
    assert reread["no_coverage"]["source"]["response_status"] == 200
    assert reread["timeout"]["source"]["fetched_at"] is not None
    assert reread["timeout"]["source"]["manual_review_required"] is True
    assert reread["legacy"]["source"] is None
    # Świeże odpowiedzi API są identyczne z odczytem z bazy.
    for name in ("no_coverage", "timeout", "flat"):
        assert results[name]["terrain"] == reread[name]

    with SessionLocal() as db:
        timeout_record = db.scalars(
            select(SourceRecord).where(
                SourceRecord.analysis_id == results["timeout"]["analysis_id"],
                SourceRecord.source_name == "NMT",
            )
        ).one()
        coverage_record = db.scalars(
            select(SourceRecord).where(
                SourceRecord.analysis_id == results["no_coverage"]["analysis_id"],
                SourceRecord.source_name == "NMT",
            )
        ).one()
    assert timeout_record.response_status == "unavailable"
    assert coverage_record.response_status == "no_coverage"

    # Raport opisuje każdy stan inaczej; tylko pomiar 0 m mówi o płaskim terenie.
    notes = {}
    for name, analysis_id in {
        **{name: body["analysis_id"] for name, body in results.items()},
        "legacy": legacy_id,
    }.items():
        with SessionLocal() as db:
            response = build_analyze_response_from_analysis(db.get(Analysis, analysis_id), db)
        html = _render_report_html(_build_report_context(response, None, None))
        notes[name] = html
        _write_evidence(f"bk-301-302/03-report-{name}.html", html)
    assert "Deniwelacja (Hmax − Hmin)" in notes["flat"] and "wynosi 0 m" in notes["flat"]
    assert "brak pokrycia danymi NMT" in notes["no_coverage"]
    assert "pomiar niedostępny" in notes["timeout"]
    assert "brak informacji w zapisanym wyniku" in notes["legacy"]
    for name in ("no_coverage", "timeout", "legacy"):
        assert "Deniwelacja (Hmax − Hmin)" not in notes[name]
        assert "nie oznacza płaskiego terenu" in notes[name]
    _write_evidence("bk-301-302/04-four-behaviours.json", reread)


# --- Wznowienie MPZP nie usuwa terrain ---------------------------------------


@requires_gdal
def test_manual_zone_resume_keeps_terrain_snapshot(relief_enabled: FakeWcsClient) -> None:
    identifier = f"{_PREFIX}resume_{uuid4().hex[:6]}"
    document_url = f"https://bip.example.gov.pl/{uuid4().hex[:8]}.pdf"

    waiting, _ = _post_analyze(
        identifier,
        CONTROL.wkt,
        _nmt("nmt_getminmaxbypolygon.txt"),
        discovery=_raster_mpzp(document_url),
        document_url=document_url,
    )
    assert waiting.status_code == 200
    body = waiting.json()
    assert body["status"] == "waiting_for_user_input"
    terrain = body["terrain"]
    assert terrain["height_difference_m"] == 3.4
    assert terrain["relief"]["status"] == "available"

    resumed = client.post("/analyze/resume", json={"analysis_id": body["analysis_id"], "zone_symbol": "1MN"})

    assert resumed.status_code == 200
    result = resumed.json()
    assert result["mpzp_zones"][0]["assignment_method"] == "manual_user_input"
    assert result["terrain"] == terrain
    assert _reread(body["analysis_id"])["terrain"] == terrain
    _write_evidence("bk-301-302/05-resume.json", result)
