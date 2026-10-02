"""Scenariusze końcowe BK-204 i BK-205 na rzeczywistym połączeniu warstw.

BK-204: ``POST /analyze`` (brak wektora) → ``waiting_for_user_input`` z planem,
kandydatami i przypiętym dokumentem → podgląd źródła
(``GET /analyze/{id}/pending-document``) → błędny symbol / nieznana analiza bez
zmian snapshotu → ``POST /analyze/resume`` z realnym parserem tekstowego PDF
(pdfplumber) → ``partial`` z nieustalonym udziałem i flagami weryfikacji →
ponowne resume 409 bez dublowania stref → ``GET /report/{id}`` (WeasyPrint) z
tekstem „symbol strefy podano ręcznie”.

BK-205: import wersjonowanego wektora MPZP (dwie strefy 60/40) + obowiązujący
POG z dwiema strefami → ``run_analysis`` → ``compatibility_assessment`` z parami
zidentyfikowanymi przestrzennie w EPSG:2180 → zapis w bazie → odczyt → UI-owy
kontrakt JSON → HTML/PDF z osobnymi ustaleniami MPZP i POG.

Zewnętrzne usługi (ULDK, KIMPZP, BIP, RU, KIUT) są zamrożonymi fixture'ami —
bez internetu. Jeżeli ustawiono ``EVIDENCE_OUT``, testy zapisują artefakty
dowodowe (JSON i PDF) do tego katalogu.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pdfplumber
import pytest
from fastapi.testclient import TestClient
from shapely import from_wkt
from shapely.geometry import box
from sqlalchemy import delete, select

from app.core.access_control import make_analysis_token
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
from app.schemas.analyze import ParcelIdAnalyzeRequest, UtilitiesPreviewResult
from app.schemas.source import SourceMetadata
from app.services.analysis_orchestrator import run_analysis
from app.services.context import ContextResult, ContextSectionResult
from app.services.initiation import ParcelLookupResult
from app.services.mpzp import MpzpDiscoveryResult
from app.services.persistence import build_analyze_response_from_analysis
from app.services.pog_fetch import PogVectorData, PogVectorFeature
from app.services.report import _build_report_context, _render_report_html
from tests.mpzp_fixtures import (
    act,
    document_blob,
    import_acts,
    origin,
    rect,
    text_pdf,
    zone,
)
from tests.test_analysis_orchestrator import _binding_pog_discovery

pytestmark = pytest.mark.integration

_PREFIX = "BK204_205_E2E_"
NOW = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
client = TestClient(app)
EVIDENCE_OUT = os.environ.get("EVIDENCE_OUT")


def _cleanup() -> None:
    with SessionLocal() as db:
        parcel_ids = select(Parcel.id).where(Parcel.parcel_identifier.like(f"{_PREFIX}%"))
        analysis_ids = select(Analysis.id).where(Analysis.parcel_id.in_(parcel_ids))
        zone_ids = select(MpzpZone.id).where(MpzpZone.analysis_id.in_(analysis_ids))
        db.execute(delete(MpzpParameter).where(MpzpParameter.mpzp_zone_id.in_(zone_ids)))
        for model in (SourceRecord, PogData, MpzpZone, Infrastructure, Risk):
            db.execute(delete(model).where(model.analysis_id.in_(analysis_ids)))
        db.execute(delete(Analysis).where(Analysis.id.in_(analysis_ids)))
        db.execute(delete(Parcel).where(Parcel.id.in_(parcel_ids)))
        db.commit()


@pytest.fixture(autouse=True)
def cleanup_rows():
    _cleanup()
    yield
    _cleanup()


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
        teryt="126101",
        source_metadata=_source("ULDK", "https://uldk.example.test", confidence=0.95),
    )


def _context() -> ContextResult:
    nmt_source = _source("NMT_GUGIK", "https://nmt.example.test", confidence=0.8)
    return ContextResult(
        kiut=ContextSectionResult(section="kiut", status="available"),
        isok=ContextSectionResult(section="isok", status="available"),
        gdos=ContextSectionResult(section="gdos", status="available"),
        nmt=ContextSectionResult(section="nmt", status="available", source_metadata=nmt_source),
    )


def _kiut() -> UtilitiesPreviewResult:
    return UtilitiesPreviewResult(
        coverage_status="covered",
        county_name="powiat testowy",
        layer_available=True,
        note="Podgląd nie służy do odległości.",
        source=_source("KIUT (GUGiK)", "https://kiut.example.test/wms"),
    )


def _pog_vectors(*zones: tuple[str, object]) -> PogVectorData:
    return PogVectorData(
        planning_zones=[
            PogVectorFeature(
                geometry=geometry,
                attributes={"zone_type": zone_type, "feature_id": f"pog:{zone_type}"},
                source_crs="EPSG:2180",
                layer_type="planning_zone",
            )
            for zone_type, geometry in zones
        ],
        ouz_areas=[],
        downtown_areas=[],
        app_metadata={"uchwala_nr": "X/42/2026"},
        status="available",
        wms_fallback_required=False,
        source_metadata=_source("POG_APP", "https://bip.example.test/pog.gml"),
    )


def _raster_discovery(document_url: str) -> MpzpDiscoveryResult:
    return MpzpDiscoveryResult(
        plan_id="MPZP/E2E/1",
        candidate_zone_symbols=["1MN", "2MN"],
        uchwala_url=document_url,
        brak_wektorow=True,
        status="raster_only",
        is_discovery_only=True,
        source_metadata=_source("KIMPZP", "https://kimpzp.example.test", confidence=0.3, manual=True),
        warnings=["Gmina nie udostępnia wektorowych danych MPZP."],
    )


def _pdf_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        raw = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return re.sub(r"\s+", " ", raw.replace(" ", " "))


def _write_evidence(name: str, payload: bytes | dict) -> None:
    if not EVIDENCE_OUT:
        return
    target = Path(EVIDENCE_OUT) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, bytes):
        target.write_bytes(payload)
    else:
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


# --- BK-204 ------------------------------------------------------------------


def test_bk204_waiting_preview_resume_partial_and_pdf() -> None:
    x, y = origin()
    parcel = box(x, y, x + 100, y + 10)
    parcel_identifier = f"{_PREFIX}{uuid4().hex[:6]}"
    document_url = f"https://bip.example.gov.pl/{uuid4().hex[:8]}.pdf"
    original_pdf = text_pdf()
    original_sha = hashlib.sha256(original_pdf).hexdigest()
    fetch = AsyncMock(return_value=document_blob(original_pdf, document_url))
    patches = (
        patch("app.services.analysis_orchestrator.resolve_parcel", new=AsyncMock(return_value=_lookup(parcel_identifier, parcel.wkt))),
        patch("app.services.analysis_orchestrator.analyze_context", new=AsyncMock(return_value=_context())),
        patch("app.services.analysis_orchestrator.discover_mpzp", new=AsyncMock(return_value=_raster_discovery(document_url))),
        patch("app.services.analysis_orchestrator.discover_pog", new=AsyncMock(return_value=_binding_pog_discovery())),
        patch("app.services.analysis_orchestrator.fetch_pog_vector_data", new=AsyncMock(return_value=_pog_vectors(("SJ", parcel)))),
        patch("app.services.analysis_orchestrator.check_kiut_coverage_for_geometry", new=AsyncMock(return_value=_kiut())),
        patch("app.services.analysis_orchestrator.fetch_mpzp_document", new=fetch),
    )
    for item in patches:
        item.start()
    try:
        waiting = client.post("/analyze", json={"method": "parcel_id", "parcel_identifier": parcel_identifier})
    finally:
        for item in reversed(patches):
            item.stop()

    # 1. waiting_for_zone_symbol: plan, kandydaci i przypięty dokument przed formularzem.
    assert waiting.status_code == 200
    body = waiting.json()
    analysis_id = body["analysis_id"]
    assert body["status"] == "waiting_for_user_input"
    assert body["manual_zone_required"] is True
    context = body["manual_zone_context"]
    assert context["plan_id"] == "MPZP/E2E/1"
    assert context["candidate_zone_symbols"] == ["1MN", "2MN"]
    assert context["document_status"] == "pinned"
    assert context["document"]["sha256"] == original_sha
    assert context["raster_preview_source_key"] == "mpzp"
    assert body["pog"]["legal_status"] == "binding"
    with SessionLocal() as db:
        assert db.get(Analysis, analysis_id).status == "waiting_for_zone_symbol"
    _write_evidence("bk-204/01-waiting.json", body)

    # 2. Podgląd źródła: dokładnie przypięta kopia, nie adres zewnętrzny.
    preview = client.get(context["document"]["preview_path"])
    assert preview.status_code == 200
    assert preview.content == original_pdf
    assert preview.headers["x-document-sha256"] == original_sha

    # 3. Pod tym samym URL opublikowano inną uchwałę — resume nie może jej użyć.
    fetch.return_value = document_blob(text_pdf(("§ 5. Dla terenu 1MN: maksymalna wysokość zabudowy: 15 m.",)), document_url)

    # 4. Błędny symbol (niedozwolony znak; spacja wewnętrzna jest od PV3-04 poprawna) i
    # nieznana analiza nie zmieniają snapshotu.
    assert client.post("/analyze/resume", json={"analysis_id": analysis_id, "zone_symbol": "1 MN!"}).status_code == 422
    assert client.post("/analyze/resume", json={"analysis_id": 999_999_999, "zone_symbol": "1MN"}).status_code == 404
    with SessionLocal() as db:
        assert db.get(Analysis, analysis_id).status == "waiting_for_zone_symbol"
        assert db.scalars(select(MpzpZone).where(MpzpZone.analysis_id == analysis_id)).all() == []

    # 5. Resume z realnym parserem przypiętego PDF.
    resumed = client.post("/analyze/resume", json={"analysis_id": analysis_id, "zone_symbol": "1MN"})
    fetch.assert_awaited_once_with(document_url)  # tylko przy wstrzymaniu
    assert resumed.status_code == 200
    result = resumed.json()
    assert result["status"] == "partial"
    assert result["manual_zone_required"] is False
    [manual_zone] = result["mpzp_zones"]
    assert manual_zone["zone_symbol"] == "1MN"
    assert manual_zone["assignment_method"] == "manual_user_input"
    assert manual_zone["intersection_area_sqm"] is None
    assert manual_zone["intersection_pct"] is None
    assert manual_zone["is_dominant"] is False
    assert manual_zone["max_building_height_m"] == 9.0  # z przypiętej wersji, nie 15 m
    assert manual_zone["parameters"]
    assert all(p["manual_review_required"] for p in manual_zone["parameters"])
    assert {p["document_sha256"] for p in manual_zone["parameters"]} == {original_sha}
    assert manual_zone["manual_selection"]["document_sha256"] == original_sha
    assert manual_zone["manual_selection"]["candidate_zone_symbols"] == ["1MN", "2MN"]
    # POG, NMT i ryzyka zachowane; ocena zgodności nie rozstrzyga pary bez geometrii.
    assert result["pog"]["legal_status"] == "binding"
    assert [zone["type"] for zone in result["pog"]["zones"]] == ["SJ"]
    assert any(source["source_name"] == "NMT_GUGIK" for source in result["sources"])
    assessment = result["pog"]["compatibility_assessment"]
    assert assessment["status"] in {"uncertain", "unknown"}
    assert assessment["manual_review_required"] is True
    assert all(not pair["spatially_identified"] for pair in assessment["zone_pairs"])
    _write_evidence("bk-204/02-resumed.json", result)

    # 6. Ponowne resume: 409 i brak dublowania stref.
    assert client.post("/analyze/resume", json={"analysis_id": analysis_id, "zone_symbol": "1MN"}).status_code == 409
    with SessionLocal() as db:
        assert len(db.scalars(select(MpzpZone).where(MpzpZone.analysis_id == analysis_id)).all()) == 1

    # 7. Raport PDF z zapisanego snapshotu.
    report = client.get(f"/report/{analysis_id}?access_token={make_analysis_token(analysis_id)}")
    assert report.status_code == 200
    text = _pdf_text(report.content)
    assert "symbol strefy podano ręcznie" in text.lower()
    assert "nieustalony" in text
    assert original_sha in text.replace(" ", "")
    assert "Relacja MPZP–POG — analiza informacyjna" in text
    _write_evidence("bk-204/03-report.pdf", report.content)


# --- BK-205 ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bk205_spatial_pairs_api_db_ui_pdf(tmp_path: Path) -> None:
    x, y = origin()
    identifier = f"plan-{uuid4().hex[:8]}"
    document_url = f"https://bip.example.gov.pl/{identifier}.pdf"
    zones = (
        zone("1MN", rect(x, y, x + 60, y + 10), "single_family_housing"),
        zone("2MN", rect(x + 60, y, x + 100, y + 10), "production"),
    )
    with SessionLocal() as db:
        import_acts(db, tmp_path, f"mpzp_{uuid4().hex[:8]}", act(identifier, rect(x, y, x + 100, y + 10), zones, document_url=document_url))
        db.commit()
    parcel = from_wkt(rect(x, y, x + 100, y + 10))
    parcel_identifier = f"{_PREFIX}{uuid4().hex[:6]}"
    left, right = box(x, y, x + 60, y + 10), box(x + 60, y, x + 100, y + 10)
    no_document = MpzpDiscoveryResult(
        plan_id=None, candidate_zone_symbols=[], uchwala_url=None, brak_wektorow=False,
        status="no_mpzp", is_discovery_only=True,
        source_metadata=_source("KIMPZP", confidence=0.3, manual=True),
    )
    with (
        patch("app.services.analysis_orchestrator.resolve_parcel", new=AsyncMock(return_value=_lookup(parcel_identifier, parcel.wkt))),
        patch("app.services.analysis_orchestrator.analyze_context", new=AsyncMock(return_value=_context())),
        patch("app.services.analysis_orchestrator.discover_mpzp", new=AsyncMock(return_value=no_document)),
        patch("app.services.analysis_orchestrator.discover_pog", new=AsyncMock(return_value=_binding_pog_discovery())),
        patch("app.services.analysis_orchestrator.fetch_pog_vector_data", new=AsyncMock(return_value=_pog_vectors(("SJ", left), ("SN", right)))),
        patch("app.services.analysis_orchestrator.check_kiut_coverage_for_geometry", new=AsyncMock(return_value=_kiut())),
        patch("app.services.analysis_orchestrator.fetch_mpzp_document", new=AsyncMock(return_value=document_blob(text_pdf(), document_url))),
        SessionLocal() as db,
    ):
        response = await run_analysis(ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier=parcel_identifier), db)

    # API: pary stref z geometrii, bez uśredniania; każda rozstrzygnięta para z regułą.
    assert [(z.zone_symbol, round(z.intersection_pct or 0)) for z in response.mpzp_zones] == [("1MN", 60), ("2MN", 40)]
    assert response.pog is not None and [z.type for z in response.pog.zones] == ["SJ", "SN"]
    assessment = response.pog.compatibility_assessment
    assert assessment is not None
    assert assessment.status == "incompatible"
    pairs = {(p.mpzp_zone_symbol, p.pog_zone_type): p for p in assessment.zone_pairs}
    assert set(pairs) == {("1MN", "SJ"), ("2MN", "SN")}
    assert pairs[("1MN", "SJ")].status == "compatible"
    assert pairs[("2MN", "SN")].status == "incompatible"
    assert pairs[("1MN", "SJ")].overlap_area_sqm == pytest.approx(600.0)
    assert pairs[("2MN", "SN")].overlap_area_sqm == pytest.approx(400.0)
    for pair in assessment.zone_pairs:
        assert pair.spatially_identified
        assert pair.rule_id and pair.rule_version and pair.source and pair.as_of
    assert {s.kind for s in assessment.sources} == {"rule_set", "pog", "mpzp"}
    assert "conflict_with_mpzp" not in response.pog.model_dump()
    api_json = response.model_dump(mode="json")
    assert "intersection_wkt" not in json.dumps(api_json)

    # DB → odczyt: kolumna i snapshot v2 zachowują uzasadnienie i pary.
    with SessionLocal() as db:
        record = db.scalar(select(PogData).where(PogData.analysis_id == response.analysis_id))
        assert record.compatibility_assessment["status"] == "incompatible"
        assert record.result_v2["compatibility_assessment"] == record.compatibility_assessment
        rebuilt = build_analyze_response_from_analysis(db.get(Analysis, response.analysis_id), db)
    assert rebuilt.pog.compatibility_assessment.model_dump() == assessment.model_dump()
    assert rebuilt.status == "partial"  # ocena informacyjna nie podnosi statusu

    # Raport: MPZP i POG osobno, ocena jako analiza informacyjna, bez stwierdzeń prawnych.
    html = _render_report_html(_build_report_context(rebuilt))
    mpzp_at = html.index('id="sec-mpzp"')
    pog_at = html.index('id="sec-pog"')
    relation_at = html.index("Relacja MPZP–POG — analiza informacyjna")
    assert mpzp_at < pog_at < relation_at
    assert "potencjalna rozbieżność funkcji" in html
    assert "brak wskazanej rozbieżności w tabeli reguł" in html
    assert "mpzp-pog-function-table:production:SN v1.0" in html
    assert "nie przesądza o prawnej możliwości zabudowy" in html
    lowered = html.lower()
    for phrase in ("można zabudować", "zabudowa jest dopuszczalna", "zgodność potwierdzona"):
        assert phrase not in lowered
    _write_evidence("bk-205/result.json", rebuilt.model_dump(mode="json"))

    report = client.get(f"/report/{response.analysis_id}?access_token={make_analysis_token(response.analysis_id)}")
    assert report.status_code == 200
    pdf_text = _pdf_text(report.content)
    assert "Relacja MPZP–POG — analiza informacyjna" in pdf_text
    assert "potencjalna rozbieżność funkcji" in pdf_text
    _write_evidence("bk-205/report.pdf", report.content)
