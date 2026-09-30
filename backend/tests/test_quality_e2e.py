"""Scenariusz końcowy BK-504: macierz jakości od API po PDF na realnych warstwach.

Analiza przechodzi przez router ``/analyze``, orkiestrator (realne adaptery NMT
i sekcje kontekstu), zapis w PostGIS, odczyt historyczny, trafienie cache i raport
PDF. Usługi zewnętrzne są zamrożonymi fixtures (bez internetu). Sprawdzamy, że:

- ta sama macierz (status, źródło, czas, wydanie, manual, świeżość, powody)
  jest w odpowiedzi API, kolumnie ``analyses.section_quality``, odczycie z bazy,
  cache i PDF;
- brak pokrycia, błąd, niedostępność i stary pomiar są odrębnymi stanami;
- upływ czasu i eksport po roku nie zmieniają zapisanej oceny ani jej hasha,
  a wiek na dzień eksportu jest osobnym ostrzeżeniem;
- zapis sprzed BK-504 jest odtwarzany przy odczycie i nie jest uzupełniany w bazie;
- wznowienie MPZP wystawia macierz od nowa.

Jeżeli ustawiono ``EVIDENCE_OUT``, test zapisuje artefakty dowodowe.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
import respx
from sqlalchemy import select, text

from app.core.access_control import make_analysis_token
from app.core.settings import settings
from app.db.session import SessionLocal
from app.models.analysis import Analysis
from app.schemas.source import SectionQualityMatrix, SourceMetadata
from app.services.gdos import NatureProtectionSection
from app.services.isok import FloodRiskSection, IsokServiceUnavailableError
from app.services.persistence import build_analyze_response_from_analysis
from app.services.report import generate_analysis_report_pdf
from tests import test_terrain_e2e as base
from tests.test_terrain_e2e import (
    CONTROL,
    _nmt,
    _no_mpzp,
    _pdf_text,
    _raster_mpzp,
    _reread,
    _write_evidence,
    client,
)

pytestmark = pytest.mark.integration

_PREFIX = base._PREFIX  # wspólne sprzątanie wierszy z modułem bazowym


@pytest.fixture(autouse=True)
def cleanup_rows():
    base._cleanup()
    yield
    base._cleanup()


@pytest.fixture(autouse=True)
def sources_fetched_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Źródła zamrożone w helperach bazowych dostają czas „teraz” (świeże pobranie)."""
    monkeypatch.setattr(base, "NOW", datetime.now(timezone.utc) - timedelta(seconds=30))


def _isok(*, age_days: int = 0) -> FloodRiskSection:
    return FloodRiskSection(
        features=[],
        source_metadata=SourceMetadata(
            source_id="isok",
            source_name="ISOK",
            source_url="https://isok.example.test/wfs",
            fetched_at=datetime.now(timezone.utc) - timedelta(days=age_days, seconds=20),
            response_status=200,
            confidence=0.95,
            manual_review_required=False,
        ),
    )


def _gdos() -> NatureProtectionSection:
    return NatureProtectionSection(
        features=[],
        source_metadata=SourceMetadata(
            source_id="gdos",
            source_name="GDOS",
            source_url="https://gdos.example.test/wfs",
            fetched_at=datetime.now(timezone.utc) - timedelta(seconds=20),
            response_status=200,
            confidence=0.95,
            manual_review_required=False,
        ),
    )


def _post(
    identifier: str,
    nmt_response: str | Exception,
    *,
    isok: object | None = None,
    discovery=None,
    document_url: str | None = None,
    pog_vectors: object | None = None,
):
    """POST /analyze z zamrożonymi usługami; ISOK/GDOŚ niosą realne provenance."""
    patches = list(base._patches(identifier, CONTROL.wkt, discovery or _no_mpzp(), document_url))
    isok_value = isok if isok is not None else _isok()
    isok_patch = (
        patch("app.services.context.fetch_flood_risk_section", new=AsyncMock(side_effect=isok_value))
        if isinstance(isok_value, BaseException)
        else patch("app.services.context.fetch_flood_risk_section", new=AsyncMock(return_value=isok_value))
    )
    patches.append(isok_patch)
    patches.append(
        patch("app.services.context.fetch_nature_protection_section", new=AsyncMock(return_value=_gdos()))
    )
    if pog_vectors is not None:
        patches.append(
            patch("app.services.analysis_orchestrator.fetch_pog_vector_data", new=AsyncMock(return_value=pog_vectors))
        )
    for item in patches:
        item.start()
    try:
        with respx.mock(assert_all_called=False) as router:
            route = router.get(settings.nmt_base_url)
            if isinstance(nmt_response, Exception):
                route.mock(side_effect=nmt_response)
            else:
                route.mock(return_value=httpx.Response(200, text=nmt_response))
            response = client.post("/analyze", json={"method": "parcel_id", "parcel_identifier": identifier})
            return response, route.call_count
    finally:
        for item in reversed(patches):
            item.stop()


def _sections(body: dict) -> dict[str, dict]:
    return {item["section"]: item for item in body["section_quality"]["sections"]}


def _stored_column(analysis_id: int) -> dict | None:
    with SessionLocal() as db:
        return db.execute(
            text("SELECT section_quality FROM analyses WHERE id = :id"), {"id": analysis_id}
        ).scalar_one()


def _without_legend(matrix: dict) -> dict:
    return {key: value for key, value in matrix.items() if key != "legend"}


def _squash(value: str) -> str:
    return re.sub(r"[\s\-­‐]+", "", value)


# --- API → DB → odczyt → cache → PDF ----------------------------------------------------------


def test_matrix_is_the_same_in_api_db_reread_cache_and_pdf() -> None:
    identifier = f"{_PREFIX}Q1_{uuid4().hex[:6]}"

    response, nmt_calls = _post(identifier, _nmt("nmt_getminmaxbypolygon.txt"))

    assert response.status_code == 200 and nmt_calls == 1
    body = response.json()
    matrix = body["section_quality"]
    sections = _sections(body)
    assert [item["section"] for item in matrix["sections"]] == [
        "parcel", "mpzp", "pog", "pog_overlays", "flood", "nature", "terrain",
        "utilities", "transport", "mpzp_pog_relation",
    ]
    assert matrix["origin"] == "stored" and matrix["schema_version"] == "1.0"
    assert matrix["policy_version"].startswith("quality-policy/1+")
    assert len(matrix["matrix_sha256"]) == 64
    assert matrix["reference_at"] == body["analyzed_at"]  # punkt odniesienia = chwila analizy

    # Status według kontraktów źródeł.
    assert sections["parcel"]["status"] == "available" and sections["parcel"]["source_id"] == "uldk"
    assert sections["flood"]["status"] == "available" and sections["flood"]["source_id"] == "isok"
    assert sections["nature"]["status"] == "available" and sections["nature"]["source_id"] == "gdos"
    assert sections["terrain"]["status"] == "available" and sections["terrain"]["source_id"] == "nmt"
    assert sections["utilities"]["status"] == "partial"
    assert sections["utilities"]["reason_codes"] == ["KIUT_PREVIEW_ONLY"]
    assert sections["transport"]["status"] == "out_of_scope"
    assert sections["transport"]["source_id"] is None
    assert sections["transport"]["reason_codes"] == ["NO_SOURCE_CONTRACT"]
    # Świeżość: reguła tylko tam, gdzie katalog ją ma; reszta jawnie „nieustalona”.
    for key in ("flood", "nature", "terrain"):
        assert sections[key]["freshness"]["state"] == "fresh", key
        assert sections[key]["freshness"]["max_age_days"] == 7, key
        assert sections[key]["freshness"]["basis"] == "project_decision", key
    for key in ("parcel", "pog", "utilities"):
        assert sections[key]["freshness"]["state"] == "unknown", key
        assert sections[key]["freshness"]["reason_code"] == "FRESHNESS_NO_POLICY", key
        assert sections[key]["freshness"]["max_age_days"] is None, key
    assert sections["flood"]["fetched_at"] is not None
    assert {item["id"] for item in matrix["legend"]["statuses"]} >= {"available", "no_coverage", "error"}
    _write_evidence("bk-504-505/00-api-response.json", body)
    _write_evidence("bk-504-505/01-api-section-quality.json", matrix)

    analysis_id = body["analysis_id"]
    # DB: kolumna JSONB = macierz z API (bez legendy, która jest wyliczana).
    stored = _stored_column(analysis_id)
    assert stored == _without_legend(matrix)
    assert SectionQualityMatrix.model_validate(stored).integrity_ok()
    # Odczyt historyczny i cache dają tę samą macierz.
    assert _reread(analysis_id)["section_quality"] == matrix
    cached, cached_nmt_calls = _post(identifier, _nmt("nmt_getminmaxbypolygon.txt"))
    assert cached.status_code == 200 and cached_nmt_calls == 0
    assert cached.json()["analysis_id"] == analysis_id
    assert cached.json()["section_quality"] == matrix

    # PDF z zapisanego snapshotu: ta sama macierz z legendą i sumą kontrolną.
    report = client.get(f"/report/{analysis_id}?access_token={make_analysis_token(analysis_id)}")
    assert report.status_code == 200
    pdf_text = _pdf_text(report.content)
    for expected in (
        "Tabela 8.1. Macierz kompletności i świeżości sekcji (ocena historyczna)",
        "Tabela 8.2. Legenda statusów i świeżości",
        "brak pokrycia źródła",
        "aktualne wg reguły",
        "świeżość nieustalona",
        "brak potwierdzonego kontraktu",
        matrix["policy_version"],
    ):
        assert expected in pdf_text, expected
    assert matrix["matrix_sha256"] in _squash(pdf_text)
    _write_evidence("bk-504-505/02-report.pdf", report.content)


def test_time_passing_and_late_export_never_rewrite_the_stored_assessment() -> None:
    identifier = f"{_PREFIX}Q2_{uuid4().hex[:6]}"
    body = _post(identifier, _nmt("nmt_getminmaxbypolygon.txt"))[0].json()
    analysis_id = body["analysis_id"]
    matrix = body["section_quality"]
    stored_before = _stored_column(analysis_id)
    with SessionLocal() as db:
        map_hash_before = db.get(Analysis, analysis_id).report_map_snapshot["semantic_sha256"]

    export_at = datetime.now(timezone.utc) + timedelta(days=400)
    with SessionLocal() as db:
        late_pdf = generate_analysis_report_pdf(analysis_id, db, export_at=export_at)
    late_text = _pdf_text(late_pdf)

    # Osobne ostrzeżenie o wieku na dzień eksportu — tylko dla źródeł z regułą.
    assert "Tabela 8.3. Wiek danych na dzień eksportu (ostrzeżenie dodatkowe)" in late_text
    assert "Ostrzeżenie: na dzień eksportu dane sekcji" in late_text
    assert "nie zmienia oceny historycznej" in late_text
    assert "starsze niż reguła wieku źródła w dniu eksportu" in late_text

    # Zapis, hash macierzy i hash semantyczny map — bez zmian.
    assert _stored_column(analysis_id) == stored_before
    with SessionLocal() as db:
        assert db.get(Analysis, analysis_id).report_map_snapshot["semantic_sha256"] == map_hash_before
    reread = _reread(analysis_id)["section_quality"]
    assert reread == matrix
    assert reread["matrix_sha256"] == matrix["matrix_sha256"]
    assert {item["freshness"]["state"] for item in reread["sections"] if item["section"] in ("flood", "terrain")} == {
        "fresh"
    }
    assert matrix["matrix_sha256"] in _squash(late_text)

    # Eksport „dziś” nie ma ostrzeżenia o wieku.
    with SessionLocal() as db:
        fresh_text = _pdf_text(generate_analysis_report_pdf(analysis_id, db))
    assert "Ostrzeżenie: na dzień eksportu dane sekcji" not in fresh_text
    _write_evidence("bk-504-505/03-report-late-export.pdf", late_pdf)


# --- Brak pokrycia, błąd, niedostępność i stary pomiar ---------------------------------------------


def test_no_coverage_error_unavailable_and_stale_are_distinct_states() -> None:
    cases: dict[str, tuple[str | Exception, object | None]] = {
        "no_coverage": (_nmt("nmt_getminmaxbypolygon_brak_pokrycia.txt"), None),
        "timeout": (httpx.ReadTimeout("NMT nie odpowiedział"), None),
        "isok_unavailable": (
            base._flat_text(),
            IsokServiceUnavailableError("ISOK nie odpowiada", reason_code="SERVICE_TIMEOUT"),
        ),
        "isok_error": (base._flat_text(), RuntimeError("nieoczekiwany błąd")),
        "stale_measurement": (base._flat_text(), _isok(age_days=20)),
    }
    results: dict[str, dict] = {}
    for name, (nmt_response, isok) in cases.items():
        identifier = f"{_PREFIX}Q3_{name}_{uuid4().hex[:6]}"
        response, _ = _post(identifier, nmt_response, isok=isok)
        assert response.status_code == 200, name
        results[name] = response.json()

    terrain = {name: _sections(body)["terrain"] for name, body in results.items()}
    flood = {name: _sections(body)["flood"] for name, body in results.items()}
    assert terrain["no_coverage"]["status"] == "no_coverage"
    assert terrain["no_coverage"]["reason_codes"] == ["NO_COVERAGE_SENTINEL"]
    assert terrain["timeout"]["status"] == "unavailable"
    assert terrain["timeout"]["reason_codes"] == ["SERVICE_TIMEOUT"]
    assert terrain["no_coverage"]["status"] != terrain["timeout"]["status"]
    assert flood["isok_unavailable"]["status"] == "unavailable"
    assert flood["isok_unavailable"]["reason_codes"][0] == "SERVICE_TIMEOUT"
    assert flood["isok_error"]["status"] == "error"
    assert flood["isok_error"]["reason_codes"][0] == "UNEXPECTED_ERROR"
    assert {flood[name]["status"] for name in ("isok_unavailable", "isok_error")} == {"unavailable", "error"}

    # Stary pomiar: status kontraktowy „sprawdzono”, a świeżość „starsze niż reguła”.
    stale = flood["stale_measurement"]
    assert stale["status"] == "available"
    assert stale["freshness"]["state"] == "stale"
    assert stale["freshness"]["reason_code"] == "FRESHNESS_OLDER_THAN_POLICY"
    assert stale["freshness"]["age_seconds"] > 20 * 86_400
    assert "FRESHNESS_OLDER_THAN_POLICY" in stale["reason_codes"]
    # Zbiorczy status analizy nie jest gwarancją całego raportu: sekcje mają własne stany.
    assert results["isok_unavailable"]["status"] == "partial"

    # Odczyt z bazy zachowuje każdy stan; raport pokazuje je odrębnie.
    for name, body in results.items():
        assert _reread(body["analysis_id"])["section_quality"] == body["section_quality"], name
    with SessionLocal() as db:
        text_by_case = {
            name: _pdf_text(generate_analysis_report_pdf(body["analysis_id"], db))
            for name, body in results.items()
        }
    assert "brak pokrycia źródła" in text_by_case["no_coverage"]
    assert "błąd sprawdzenia" in text_by_case["isok_error"]
    assert "starsze niż reguła" in text_by_case["stale_measurement"]
    # Znacznik „stary pomiar” w podsumowaniu pojawia się tylko przy starym pomiarze
    # (jedno wystąpienie jest w objaśnieniu sekcji 8 w każdym raporcie).
    assert text_by_case["stale_measurement"].count("stary pomiar") > text_by_case["timeout"].count("stary pomiar")
    _write_evidence("bk-504-505/04-distinct-states.json", {n: _sections(b) for n, b in results.items()})


# --- Zapis sprzed BK-504 ------------------------------------------------------------------------------


def test_legacy_row_is_reconstructed_on_read_and_never_written_back() -> None:
    identifier = f"{_PREFIX}Q4_{uuid4().hex[:6]}"
    body = _post(identifier, _nmt("nmt_getminmaxbypolygon.txt"))[0].json()
    analysis_id = body["analysis_id"]
    with SessionLocal() as db:
        db.execute(text("UPDATE analyses SET section_quality = NULL WHERE id = :id"), {"id": analysis_id})
        db.commit()

    reread = _reread(analysis_id)["section_quality"]
    assert reread["origin"] == "reconstructed"
    assert all("LEGACY_QUALITY_RECONSTRUCTED" in item["reason_codes"] for item in reread["sections"])
    assert len(reread["sections"]) == 10

    with SessionLocal() as db:
        report_text = _pdf_text(generate_analysis_report_pdf(analysis_id, db))
    assert "zapis sprzed BK-504 nie zawierał oceny" in report_text
    assert _stored_column(analysis_id) is None  # ani odczyt, ani raport nie uzupełniają zapisu


# --- Wznowienie MPZP -----------------------------------------------------------------------------------


def test_manual_zone_resume_issues_the_matrix_again() -> None:
    identifier = f"{_PREFIX}Q5_{uuid4().hex[:6]}"
    document_url = f"https://bip.example.gov.pl/{uuid4().hex[:8]}.pdf"
    waiting = _post(
        identifier,
        _nmt("nmt_getminmaxbypolygon.txt"),
        discovery=_raster_mpzp(document_url),
        document_url=document_url,
    )[0].json()
    assert waiting["status"] == "waiting_for_user_input"
    before = _sections(waiting)["mpzp"]
    assert before["status"] == "awaiting_input"
    assert before["manual_review_required"] is True
    assert before["reason_codes"] == ["MPZP_MANUAL_ZONE_REQUIRED"]
    assert _stored_column(waiting["analysis_id"]) == _without_legend(waiting["section_quality"])

    resumed = client.post("/analyze/resume", json={"analysis_id": waiting["analysis_id"], "zone_symbol": "1MN"})

    assert resumed.status_code == 200
    body = resumed.json()
    after = _sections(body)["mpzp"]
    assert after["status"] == "partial"
    assert "MPZP_MANUAL_ZONE" in after["reason_codes"]
    assert after["manual_review_required"] is True
    matrix = body["section_quality"]
    assert matrix["matrix_sha256"] != waiting["section_quality"]["matrix_sha256"]
    assert matrix["reference_at"] == body["analyzed_at"] != waiting["section_quality"]["reference_at"]
    assert _stored_column(waiting["analysis_id"]) == _without_legend(matrix)
    # Sekcje nietknięte przez wznowienie zachowują ocenę.
    assert _sections(body)["terrain"]["status"] == _sections(waiting)["terrain"]["status"]
    with SessionLocal() as db:
        assert (
            build_analyze_response_from_analysis(db.get(Analysis, waiting["analysis_id"]), db)
            .section_quality.matrix_sha256  # type: ignore[union-attr]
            == matrix["matrix_sha256"]
        )
        rows = db.scalars(select(Analysis).where(Analysis.id == waiting["analysis_id"])).all()
    assert len(rows) == 1
    _write_evidence("bk-504-505/05-resume.json", body)
