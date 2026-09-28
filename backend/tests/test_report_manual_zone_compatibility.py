"""Raport HTML: ręczny symbol strefy (BK-204) i ocena MPZP–POG (BK-205).

Testy renderują szablon bez WeasyPrint, więc sprawdzają treść raportu także
poza kontenerem; pełny PDF sprawdza scenariusz końcowy
``test_manual_zone_compatibility_e2e.py``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.schemas.analyze import (
    AnalyzeResponse,
    CompatibilityAssessment,
    CompatibilityZonePair,
    ManualZoneSelection,
    MpzpParameterEvidence,
    MpzpZoneResult,
    PogResult,
)
from app.schemas.source import SourceMetadata
from app.services.pog_scenarios import AGGREGATION_RULE
from app.services.report import _build_report_context, _render_report_html
from app.shared.planning_compatibility_text import COMPATIBILITY_INFORMATIONAL_NOTICE

NOW = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
SHA = "a" * 64


def _source(name: str = "manual_user_input") -> SourceMetadata:
    return SourceMetadata(
        source_name=name, fetched_at=NOW, confidence=0.5, manual_review_required=True
    )


def _manual_zone(*, pinned: bool = True, in_candidates: bool = True) -> MpzpZoneResult:
    return MpzpZoneResult(
        zone_symbol="230_U",
        assignment_method="manual_user_input",
        manual_review_required=True,
        max_building_height_m=12.0,
        parameters=[
            MpzpParameterEvidence(
                name="max_building_height_m",
                normalized_value=12.0,
                confidence=0.9,
                manual_review_required=True,
            )
        ],
        manual_selection=ManualZoneSelection(
            entered_symbol="230_U",
            plan_id="MPZP/1",
            candidate_zone_symbols=["230_U", "231_MN"] if in_candidates else [],
            symbol_in_candidates=in_candidates,
            document_url="https://bip.example.gov.pl/u.pdf" if pinned else None,
            document_sha256=SHA if pinned else None,
            document_fetched_at=NOW if pinned else None,
            document_pinned=pinned,
            selected_at=NOW,
        ),
        source=_source(),
    )


def _response(zones: list[MpzpZoneResult], pog: PogResult | None = None) -> AnalyzeResponse:
    return AnalyzeResponse(
        analysis_id=7,
        status="partial",
        analyzed_at=NOW,
        mpzp_zones=zones,
        pog=pog,
        infrastructure=[],
        risks=[],
        warnings=[],
        sources=[],
    )


def _html(response: AnalyzeResponse) -> str:
    return _render_report_html(_build_report_context(response, None, None))


def test_manual_zone_report_shows_banner_selection_and_unknown_share() -> None:
    html = _html(_response([_manual_zone()]))

    assert "UWAGA: symbol strefy podano ręcznie" in html
    assert "Symbol strefy podano ręcznie</strong>" in html
    assert "nieustalony — brak wektorowej granicy strefy" in html
    assert "kandydaci: 230_U, 231_MN" in html
    assert "kopia przypięta przy wstrzymaniu analizy" in html
    assert SHA in html
    assert "Udział strefy MPZP w powierzchni działki jest nieustalony" in html
    assert "100,0%" not in html


def test_manual_zone_without_pinned_document_and_outside_candidates() -> None:
    html = _html(_response([_manual_zone(pinned=False, in_candidates=False)]))

    assert "dokument nie został przypięty — parametry nieustalone" in html
    assert "spoza kandydatów" in html
    assert "brak kandydatów" in html


def test_vector_zone_report_has_no_manual_banner() -> None:
    zone = MpzpZoneResult(
        zone_symbol="1MN",
        intersection_area_sqm=600.0,
        intersection_pct=60.0,
        is_dominant=True,
        assignment_method="vector_intersection",
        source=_source("MPZP_WEKTOR:test"),
    )

    html = _html(_response([zone]))

    assert "symbol strefy podano ręcznie" not in html.lower()
    assert "60,0%" in html


def test_compatibility_section_is_separate_informational_and_lists_pairs() -> None:
    pair = CompatibilityZonePair(
        mpzp_zone_symbol="1MN",
        mpzp_zone_id="z1",
        mpzp_assignment_method="vector_intersection",
        mpzp_function="single_family_housing",
        pog_zone_id="pog:sj",
        pog_zone_symbol="SJ",
        pog_zone_type="SJ",
        spatially_identified=True,
        overlap_area_sqm=600.0,
        overlap_pct=60.0,
        status="compatible",
        rule_result="compatible",
        rule_id="mpzp-pog-function-table:single_family_housing:SJ",
        rule_version="1.0",
        source="tabela",
        as_of=date(2026, 9, 20),
        rationale="1MN × SJ: zgodny profil.",
        manual_review_required=False,
    )
    manual_pair = pair.model_copy(
        update={
            "mpzp_zone_symbol": "230_U",
            "spatially_identified": False,
            "overlap_area_sqm": None,
            "overlap_pct": None,
            "status": "uncertain",
        }
    )
    assessment = CompatibilityAssessment(
        status="uncertain",
        reason_code="PAIRS_EVALUATED",
        as_of=date(2026, 9, 20),
        rule_id="mpzp-pog-function-table",
        rule_version="1.0",
        aggregation=AGGREGATION_RULE,
        rationale="Oceniono 2 pary.",
        manual_review_required=True,
        zone_pairs=[pair, manual_pair],
        informational_notice=COMPATIBILITY_INFORMATIONAL_NOTICE,
    )
    pog = PogResult(touches_ouz_boundary=False, compatibility_assessment=assessment)

    html = _html(_response([], pog))

    assert html.index("Plan Ogólny Gminy (POG)") < html.index("Relacja MPZP–POG — analiza informacyjna")
    assert "brak wskazanej rozbieżności w tabeli reguł" in html
    assert "nierozstrzygnięte — wymaga analizy ustaleń obu aktów" in html
    assert "600,00 m² (60,0%)" in html
    assert "para niezidentyfikowana przestrzennie" in html
    assert "stan na 20.09.2026" in html
    assert "mpzp-pog-function-table:single_family_housing:SJ v1.0" in html
    assert "nie stwierdza prawnej możliwości zabudowy" in html
    assert "Zgodność potwierdzona" not in html


def test_legacy_assessment_is_shown_as_history_not_full_evaluation() -> None:
    pog = PogResult.model_validate(
        {"touches_ouz_boundary": False, "conflict_with_mpzp": False}
    )

    html = _html(_response([], pog))

    assert "Zapis historyczny (legacy)" in html
    assert "brak konfliktu" in html
    assert "nie jest pełną oceną" in html
    assert "brak — zapis historyczny" in html


def test_report_without_assessment_says_it_was_not_performed() -> None:
    html = _html(_response([], PogResult(touches_ouz_boundary=False)))

    assert "Nie wykonano oceny relacji MPZP–POG" in html
