"""Sekcja ``mpzp_discovery`` w kontrakcie API, macierzy jakości i raporcie PDF (AU-004)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.schemas import analyze as analyze_schemas
from app.schemas.analyze import AnalyzeResponse, MpzpDiscoverySection
from app.schemas.source import SourceMetadata
from app.services import cache
from app.services.mpzp import (
    MpzpDiscoveryResult,
    _aggregate_point_results,
    _discovery_issues,
    discovery_section,
)
from app.services.persistence import mpzp_discovery_from_snapshot
from app.services.report import _build_report_context, _render_report_html
from app.services.section_quality import build_section_quality
from app.modules.planning.infrastructure.kimpzp_feature_info import parse_kimpzp_feature_info
from tests.report_map_reference import load_fixture

FIXTURES = Path(__file__).parent / "fixtures" / "source_contracts" / "kimpzp"
_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def _section(fixture: str, *, points: int = 1) -> MpzpDiscoverySection:
    """Sekcja zbudowana tą samą ścieżką co ``discover_mpzp`` z zamrożonej odpowiedzi."""
    point = parse_kimpzp_feature_info((FIXTURES / fixture).read_text(encoding="utf-8"))
    summary, warnings = _aggregate_point_results([point] * points)
    single = summary.single_act
    result = MpzpDiscoveryResult(
        plan_id=single.resolution_number if single else None,
        candidate_zone_symbols=list(summary.zone_symbols),
        uchwala_url=single.document_url if single else None,
        brak_wektorow=False,
        status=summary.status,
        is_discovery_only=True,
        source_metadata=SourceMetadata(
            source_id="kimpzp",
            source_name="KIMPZP",
            source_url="https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego",
            fetched_at=_NOW,
            confidence=0.6,
            manual_review_required=True,
        ),
        warnings=warnings,
        acts=list(summary.acts),
        reason_codes=list(summary.reason_codes),
        issues=_discovery_issues(summary),
        multiple_acts_at_point=summary.multiple_acts_at_point,
        multiple_acts_on_parcel=summary.multiple_acts_on_parcel,
        sampled_points=summary.queried_points,
        failed_points=summary.failed_points,
    )
    return discovery_section(result)


def _response(discovery: MpzpDiscoverySection | None) -> AnalyzeResponse:
    response, _ = load_fixture("multizone")
    return response.model_copy(
        update={"mpzp_zones": [], "mpzp_discovery": discovery, "section_quality": None}
    )


def _mpzp_quality(response: AnalyzeResponse):
    matrix = build_section_quality(response)
    return next(item for item in matrix.sections if item.section == "mpzp")


def test_contract_versions_are_bumped_for_the_new_section() -> None:
    assert analyze_schemas.MPZP_RESULT_SCHEMA_VERSION == "2.7"
    assert "mpzp-v2.7" in cache.RESULT_CONTRACT_VERSION
    assert len(cache.RESULT_CONTRACT_VERSION) <= 64  # analyses.result_contract_version
    assert analyze_schemas.MPZP_DISCOVERY_SCHEMA_VERSION == "1.0"
    assert "mpzp_discovery" in AnalyzeResponse.model_fields


@pytest.mark.parametrize(
    ("fixture", "status", "codes"),
    [
        ("gora_kalwaria_141801_4.0701.23_8.html", "partial", ["MPZP_ACT_WITHOUT_ZONE", "MPZP_MULTIPLE_ACTS_AT_POINT"]),
        ("inowroclaw_punkt_450000_550000.html", "partial", ["MPZP_ACT_WITHOUT_ZONE"]),
        ("dygowo_321606_2.0029.362.html", "no_coverage", ["KIMPZP_NO_SERVICE_FOR_AREA"]),
        ("warszawa_146510_8.0502.1_3.html", "unavailable", ["MPZP_DISCOVERY_UNAVAILABLE"]),
        ("ruciane_nida_281604_5.0011.107.html", "unknown", ["MPZP_NOT_DETERMINED"]),
    ],
)
def test_section_quality_uses_discovery_status_when_no_zone_was_assigned(
    fixture: str, status: str, codes: list[str]
) -> None:
    quality = _mpzp_quality(_response(_section(fixture)))

    assert quality.status == status
    assert quality.reason_codes[: len(codes)] == codes


def test_section_quality_flags_different_acts_across_points() -> None:
    section = _section("krakow_126105_9.0001.580_4.html").model_copy(
        update={"multiple_acts_on_parcel": True}
    )

    quality = _mpzp_quality(_response(section))

    assert quality.reason_codes[:2] == ["MPZP_ACT_WITHOUT_ZONE", "MPZP_MULTIPLE_ACTS_ON_PARCEL"]


def test_section_quality_of_a_snapshot_without_the_section_is_unchanged() -> None:
    quality = _mpzp_quality(_response(None))

    assert quality.status == "unknown"
    assert quality.reason_codes[0] == "MPZP_NOT_DETERMINED"


def test_snapshot_round_trip_and_legacy_null() -> None:
    section = _section("gora_kalwaria_141801_4.0701.23_8.html")

    assert mpzp_discovery_from_snapshot(section.model_dump(mode="json")) == section
    assert mpzp_discovery_from_snapshot(None) is None


def test_report_lists_every_act_with_links_and_amendments_without_choosing() -> None:
    response = _response(_section("gora_kalwaria_141801_4.0701.23_8.html"))

    html = _render_report_html(_build_report_context(response, None))

    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("tr[data-row=mpzp-discovery-act]")
    assert [row.find("strong").get_text() for row in rows] == ["IV/30/2024", "576/XLVII/2010"]
    first, second = (row.get_text(" ", strip=True) for row in rows)
    assert "http://mpzp.gorakalwaria.pl/portal/mpzp/uch/IV_30_2024.pdf" in first
    assert "obowiązuje od: 26.06.2024" in first or "obowiązuje od: 2024-06-26" in first
    assert "LIV/467/2021" in second and "LIV/467/2021" not in first
    table = soup.select_one("table[data-table=mpzp-discovery]").get_text(" ", strip=True)
    assert "nie wybrano automatycznie" in table
    assert "MPZP_MULTIPLE_ACTS_AT_POINT" in table
    assert "Discovery MPZP (KIMPZP)" in html  # Tabela 9.3 — wersja kontraktu sekcji


def test_report_states_no_coverage_as_missing_data_not_missing_plan() -> None:
    response = _response(_section("dygowo_321606_2.0029.362.html"))

    html = _render_report_html(_build_report_context(response, None))

    status = BeautifulSoup(html, "html.parser").select_one("[data-discovery-status]")
    assert status is not None and status["data-discovery-status"] == "no_coverage"
    assert "brak danych, nie brak planu" in status.get_text()


def test_report_without_the_section_has_no_discovery_table() -> None:
    html = _render_report_html(_build_report_context(_response(None), None))

    assert "data-table=\"mpzp-discovery\"" not in html


def test_report_escapes_untrusted_act_values() -> None:
    section = _section("legnica_026201_1.0009.1319_4.html")
    act = section.acts[0].model_copy(update={"name": "<script>alert(1)</script>"})
    response = _response(section.model_copy(update={"acts": [act]}))

    html = _render_report_html(_build_report_context(response, None))

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
