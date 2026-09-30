"""Macierz jakości w raporcie PDF (BK-504): ta sama ocena, legenda i wiek na dzień eksportu.

Raport czyta zapisaną macierz; wiek na dzień eksportu jest osobnym ostrzeżeniem
i nie zmienia ani oceny historycznej, ani jej hasha merytorycznego.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pymupdf
from bs4 import BeautifulSoup

from app.modules.reporting.domain.sections import QUALITY_SECTIONS
from app.schemas.analyze import AnalyzeResponse
from app.services.report import _build_report_context, _html_to_pdf, _render_report_html
from app.services.section_quality import build_section_quality
from tests.report_map_reference import load_fixture
from tests.test_report_v2 import NBSP, _sparse_response

EXPORT_SOON = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
EXPORT_LATE = datetime(2027, 10, 20, 10, 0, tzinfo=timezone.utc)


def _html(response: AnalyzeResponse, export_at: datetime) -> str:
    return _render_report_html(_build_report_context(response, None, export_at=export_at))


def _table_rows(html: str, kind: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    return [
        [re.sub(r"\s+", " ", cell.get_text(" ", strip=True).replace(NBSP, " ")) for cell in row.find_all("td")]
        for row in soup.select(f'tr[data-row="{kind}"]')
    ]


def test_matrix_has_one_row_per_section_with_source_time_release_and_freshness() -> None:
    response, _ = load_fixture("multizone")
    rows = _table_rows(_html(response, EXPORT_SOON), "quality")
    assert len(rows) == len(QUALITY_SECTIONS) == 10
    by_label = {row[0].split(" (§")[0]: row for row in rows}
    flood = by_label["Zagrożenie powodziowe (ISOK)"]
    assert "sprawdzono" in flood[1]
    assert "ISOK WFS" in flood[2] and "isok" in flood[2]
    assert "2026-09-20" in flood[3] or "20.09.2026" in flood[3]
    assert "aktualne wg reguły" in flood[5] and "reguła źródła: 7 dni" in flood[5]
    pog = by_label["POG — strefy planistyczne"]
    assert "#12" in pog[4]
    # Źródło bez reguły wieku: świeżość nieustalona, a wiek pokazany faktograficznie.
    assert "świeżość nieustalona" in pog[5] and "brak reguły wieku" in pog[5]
    mpzp = by_label["MPZP"]
    assert "częściowo" in mpzp[1] and "wymaga weryfikacji" in mpzp[1]
    assert "sprzeczne wartości parametrów" in mpzp[6]


def test_freshness_column_does_not_repeat_the_missing_rule_phrase() -> None:
    response, _ = load_fixture("multizone")
    rows = {row[0].split(" (§")[0]: row for row in _table_rows(_html(response, EXPORT_SOON), "quality")}
    parcel_cell = rows["Działka i geometria"][5]
    assert parcel_cell.count("brak reguły wieku") == 1
    assert "mniej niż 1 dzień" in parcel_cell
    assert "kod źródła:" not in " ".join(" ".join(row) for row in rows.values())  # każdy kod ma etykietę


def test_sections_without_source_state_the_reason_of_the_gap() -> None:
    rows = _table_rows(_html(_sparse_response(), EXPORT_SOON), "quality")
    by_label = {row[0].split(" (§")[0]: row for row in rows}
    transport = by_label["Transport i dostęp do drogi"]
    assert "poza zakresem" in transport[1]
    assert "brak źródła" in transport[2]
    assert "brak potwierdzonego kontraktu" in transport[6] and "NO_SOURCE_CONTRACT" in transport[6]
    mpzp = by_label["MPZP"]
    assert "nieustalone" in mpzp[1] and "nie dowodzi braku planu" in mpzp[6]
    flood = by_label["Zagrożenie powodziowe (ISOK)"]
    assert "źródło niedostępne" in flood[1]
    kiut = by_label["Uzbrojenie terenu (KIUT)"]
    assert "nie sprawdzono pokrycia KIUT" in kiut[6]
    for row in rows:
        assert row[6] != "—" or "sprawdzono" in row[1]  # pusty powód tylko przy pełnym wyniku


def test_no_coverage_and_error_are_shown_as_different_states() -> None:
    from app.schemas.analyze import RiskSectionResult, TerrainResult
    from app.schemas.source import SourceMetadata

    response, _ = load_fixture("multizone")
    source = SourceMetadata(source_id="nmt", source_name="NMT", confidence=0.9,
                            manual_review_required=False, fetched_at=response.analyzed_at)
    varied = response.model_copy(
        update={
            "section_quality": None,
            "terrain": TerrainResult(status="no_coverage", reason_code="NO_COVERAGE_SENTINEL", source=source),
            "risk_sections": [
                RiskSectionResult(section="flood", status="error", reason_code="UNEXPECTED_ERROR", relation="unknown"),
                RiskSectionResult(section="nature", status="unavailable", reason_code="SERVICE_TIMEOUT", relation="unknown"),
            ],
        }
    )
    rows = {row[0].split(" (§")[0]: row for row in _table_rows(_html(varied, EXPORT_SOON), "quality")}
    assert "brak pokrycia źródła" in rows["Teren (NMT)"][1]
    assert "błąd sprawdzenia" in rows["Zagrożenie powodziowe (ISOK)"][1]
    assert "źródło niedostępne" in rows["Formy ochrony przyrody (GDOŚ)"][1]


def test_legend_explains_every_status_and_freshness_state() -> None:
    response, _ = load_fixture("multizone")
    html = _html(response, EXPORT_SOON)
    legend = _table_rows(html, "legend-status")
    assert len(legend) == 8
    text = " ".join(" ".join(row) for row in legend)
    for label in ("sprawdzono", "częściowo", "brak pokrycia źródła", "źródło niedostępne",
                  "błąd sprawdzenia", "nieustalone", "poza zakresem", "oczekuje na dane użytkownika"):
        assert label in text
    assert len(_table_rows(html, "legend-freshness")) == 3
    assert "(w tej analizie)" in text


def test_export_age_warning_is_separate_and_does_not_change_the_stored_assessment() -> None:
    response, _ = load_fixture("multizone")
    matrix = response.section_quality
    assert matrix is not None
    soon = _html(response, EXPORT_SOON)
    late = _html(response, EXPORT_LATE)

    # Tabela 8.1 (ocena historyczna) jest identyczna niezależnie od dnia eksportu.
    assert _table_rows(soon, "quality") == _table_rows(late, "quality")
    assert _table_rows(soon, "legend-status") == _table_rows(late, "legend-status")
    assert matrix.matrix_sha256 in soon and matrix.matrix_sha256 in late

    # Tabela 8.3 (wiek na dzień eksportu) różni się i ostrzega tylko wg reguły źródła.
    soon_age = {row[0]: row for row in _table_rows(soon, "export-age")}
    late_age = {row[0]: row for row in _table_rows(late, "export-age")}
    assert "stare" not in " ".join(" ".join(row) for row in soon_age.values())
    for label in ("Zagrożenie powodziowe (ISOK)", "Formy ochrony przyrody (GDOŚ)", "Teren (NMT)"):
        assert "starsze niż reguła" in late_age[label][2]
        assert "stare" in late_age[label][2]
    assert "starsze niż reguła" not in late_age["MPZP"][2]  # brak reguły ≠ stary pomiar
    assert "świeżość nieustalona" in late_age["MPZP"][2]
    assert "Ostrzeżenie: na dzień eksportu dane sekcji" in late
    assert "Ostrzeżenie: na dzień eksportu dane sekcji" not in soon
    assert "nie zmienia oceny historycznej" in late

    # Zapisana ocena nie została zmieniona przez render.
    assert response.section_quality == matrix
    assert matrix.integrity_ok()
    flood = next(item for item in matrix.sections if item.section == "flood")
    assert flood.freshness.state == "fresh"


def _summary_stale_tags(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    return len(soup.select("#sec-summary span.tag-stale"))


def test_summary_shows_stale_tag_only_from_stored_assessment() -> None:
    response, _ = load_fixture("multizone")
    reference = response.analyzed_at + timedelta(days=30)
    stale_matrix = build_section_quality(response.model_copy(update={"section_quality": None}), reference_at=reference)
    stale_response = response.model_copy(update={"section_quality": stale_matrix})
    assert _summary_stale_tags(_html(stale_response, EXPORT_SOON)) == 3  # ISOK, GDOŚ, NMT
    assert _summary_stale_tags(_html(response, EXPORT_LATE)) == 0  # sam eksport nie zmienia oceny


def test_matrix_hash_is_independent_of_export_time_and_visible_in_pdf() -> None:
    response, _ = load_fixture("multizone")
    assert response.section_quality is not None
    digest = response.section_quality.matrix_sha256
    text = []
    for export_at in (EXPORT_SOON, EXPORT_LATE):
        html = _html(response, export_at)
        with pymupdf.open(stream=_html_to_pdf(html), filetype="pdf") as doc:
            text.append(re.sub(r"[\s\-]+", "", " ".join(page.get_text() for page in doc)))
    assert all(digest in item for item in text)


def test_reconstructed_matrix_is_disclosed_in_the_report() -> None:
    response, _ = load_fixture("multizone")
    legacy = response.model_copy(update={"section_quality": None})
    html = _html(legacy, EXPORT_SOON)
    assert "zapis sprzed BK-504 nie zawierał oceny" in html
    assert "LEGACY_QUALITY_RECONSTRUCTED" in html
    assert "zapis sprzed BK-504" not in _html(response, EXPORT_SOON)


def test_tampered_matrix_is_flagged() -> None:
    response, _ = load_fixture("multizone")
    matrix = response.section_quality
    assert matrix is not None
    changed = matrix.sections[1].model_copy(update={"status": "available", "reason_codes": []})
    tampered = matrix.model_copy(update={"sections": [matrix.sections[0], changed, *matrix.sections[2:]]})
    html = _html(response.model_copy(update={"section_quality": tampered}), EXPORT_SOON)
    assert "NIEZGODNA z zapisem" in html
    assert "NIEZGODNA z zapisem" not in _html(response, EXPORT_SOON)


def test_policy_version_and_contract_are_listed_in_provenance() -> None:
    response, _ = load_fixture("multizone")
    assert response.section_quality is not None
    html = _html(response, EXPORT_SOON)
    assert response.section_quality.policy_version in html
    assert "Macierz jakości sekcji" in html


def _save_quality_evidence(name: str, html: str) -> None:
    """Artefakty odbioru (``REPORT_EVIDENCE_DIR``): PDF oraz obrazy stron z macierzą jakości."""
    target = os.environ.get("REPORT_EVIDENCE_DIR")
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    pdf = _html_to_pdf(html)
    (out / f"{name}.pdf").write_bytes(pdf)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        for number, page in enumerate(doc, start=1):
            if "Tabela 8." in page.get_text():
                page.get_pixmap(dpi=90).save(out / f"{name}-p{number:02d}.png")


def test_quality_evidence_pdfs_for_acceptance() -> None:
    """Ten sam snapshot: eksport w dniu analizy, po roku, oraz rzadka analiza z lukami."""
    response, _ = load_fixture("multizone")
    for name, source, export_at in (
        ("quality-multizone-export-on-analysis-day", response, EXPORT_SOON),
        ("quality-multizone-export-after-400-days", response, EXPORT_LATE),
        ("quality-sparse-gaps", _sparse_response(), EXPORT_SOON),
    ):
        html = _html(source, export_at)
        assert "Tabela 8.1. Macierz kompletności i świeżości sekcji (ocena historyczna)" in html
        _save_quality_evidence(name, html)
