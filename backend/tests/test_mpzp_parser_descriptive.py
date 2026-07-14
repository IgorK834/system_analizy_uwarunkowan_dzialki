"""Testy ekstraktorów ustaleń opisowych MPZP.

Testy z prefiksem "real_" używają WYŁĄCZNIE realnego tekstu uchwały
Bielska-Białej z ``tests/fixtures/mpzp_documents/``. Testy z prefiksem
"synthetic_" konstruują tekst ręcznie i są jawnie oznaczone jako testy
jednostkowe logiki, gdy realny dokument nie daje czystego przykładu.
"""

import json
from pathlib import Path

from app.services.mpzp_parser_descriptive import extract_descriptive_parameters
from app.services.mpzp_parser_extract import TextExtractionResult
from app.services.mpzp_parser_segment import (
    DocumentSegment,
    ZoneSectionCandidate,
    ZoneSectionResult,
    find_zone_sections,
    segment_document,
)

REAL_FIXTURE_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "mpzp_documents"
    / "bielsko_biala_uchwala_viii_187_2024"
)


def _real_bielsko_biala_segments() -> list[DocumentSegment]:
    data = json.loads((REAL_FIXTURE_DIR / "pages.json").read_text(encoding="utf-8"))
    return segment_document(TextExtractionResult(pages=data["pages"]))


def _zone_result(zone_symbol: str, segments: list[DocumentSegment]) -> ZoneSectionResult:
    return find_zone_sections(segments, [zone_symbol])[0]


def _params_by_name(params, name: str):
    return [p for p in params if p.name == name]


def _synthetic_zone_section(
    zone_symbol: str, segment_id: str, page_number: int | None = 1
) -> ZoneSectionResult:
    return ZoneSectionResult(
        zone_symbol=zone_symbol,
        candidates=[
            ZoneSectionCandidate(
                zone_symbol=zone_symbol,
                segment_id=segment_id,
                source_text="synthetic",
                page_number=page_number,
                confidence=0.9,
                match_pattern="synthetic",
            )
        ],
    )


# --- primary_use / supplementary_use (realny fixture, 230_U) ------------


def test_real_230_u_primary_use_has_two_entries() -> None:
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result("230_U", segments)

    params = extract_descriptive_parameters("230_U", zone_section, segments)
    primary_params = _params_by_name(params, "primary_use")

    values = {p.normalized_value for p in primary_params}
    assert values == {
        "zabudowa usługowa służąca funkcji placu targowego",
        "zabudowa usług centrotwórczych",
    }
    assert all(p.manual_review_required is False for p in primary_params)
    assert all(p.source_text for p in primary_params)


def test_real_230_u_supplementary_use_present() -> None:
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result("230_U", segments)

    params = extract_descriptive_parameters("230_U", zone_section, segments)
    supplementary_params = _params_by_name(params, "supplementary_use")

    assert len(supplementary_params) == 1
    assert "dopuszczenie funkcji mieszkaniowej wielorodzinnej" in (
        supplementary_params[0].normalized_value
    )


def test_real_230_umw_primary_use_honest_degradation_when_unstructured() -> None:
    # 230_UMW wyraża przeznaczenie jako jedno zdanie złożone bez podziału
    # a)/b)/c). Parametr MUSI istnieć (nie [] i nie odrzucony), ale z niższym
    # confidence i manual_review_required=True — honest degradation, nie
    # zgadywanie podziału na podstawowe/uzupełniające.
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result("230_UMW", segments)

    params = extract_descriptive_parameters("230_UMW", zone_section, segments)
    primary_params = _params_by_name(params, "primary_use")

    assert len(primary_params) == 1
    assert primary_params[0].manual_review_required is True
    assert primary_params[0].confidence < 0.85
    assert "zabudowa usługowo-mieszkaniowa śródmiejska" in (
        primary_params[0].normalized_value
    )
    # Dla 230_UMW nie ma jednoznacznego przeznaczenia uzupełniającego w tej
    # samej formie co 230_U (brak podziału a)/b)/c)).
    supplementary_params = _params_by_name(params, "supplementary_use")
    assert supplementary_params == []


# --- prohibition (realny fixture, 230_ZP i §5 ogólny) --------------------


def test_real_230_zp_prohibition_zakaz_zabudowy() -> None:
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result("230_ZP", segments)

    params = extract_descriptive_parameters("230_ZP", zone_section, segments)
    prohibition_params = _params_by_name(params, "prohibition")

    assert any(p.normalized_value == "zakaz zabudowy" for p in prohibition_params)


def test_real_general_paragraf_5_prohibition_is_a_list_not_a_blob() -> None:
    # §5 realnego dokumentu ma wiele zakazów — moduł zwraca je jako OSOBNE
    # MpzpParameter, nie jeden połączony blob tekstu.
    segments = _real_bielsko_biala_segments()
    segment = next(s for s in segments if s.segment_id == "seg-0007")
    zone_section = _synthetic_zone_section(
        "OGÓLNY_SEGMENT_TESTOWY", segment.segment_id, segment.page_number
    )

    params = extract_descriptive_parameters(
        "OGÓLNY_SEGMENT_TESTOWY", zone_section, segments
    )
    prohibition_params = _params_by_name(params, "prohibition")

    assert len(prohibition_params) >= 2
    assert all(isinstance(p.normalized_value, str) for p in prohibition_params)


# --- large_retail_restriction (realny fixture, §5 pkt 1b) ----------------


def test_real_large_retail_restriction_2000_m2() -> None:
    segments = _real_bielsko_biala_segments()
    segment = next(s for s in segments if s.segment_id == "seg-0007")
    zone_section = _synthetic_zone_section(
        "OGÓLNY_SEGMENT_TESTOWY", segment.segment_id, segment.page_number
    )

    params = extract_descriptive_parameters(
        "OGÓLNY_SEGMENT_TESTOWY", zone_section, segments
    )
    large_retail_params = _params_by_name(params, "large_retail_restriction")

    assert len(large_retail_params) == 1
    assert "2000" in large_retail_params[0].normalized_value
    assert "2000" in (large_retail_params[0].raw_value or "")


# --- permission (realny fixture, §10 i §4) --------------------------------


def test_real_230_u_permission_includes_supplementary_housing() -> None:
    # Ten sam fragment może trafić i do supplementary_use, i do permission —
    # to dwa różne, uzupełniające się spojrzenia na ten sam fakt, nie duplikat.
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result("230_U", segments)

    params = extract_descriptive_parameters("230_U", zone_section, segments)
    permission_params = _params_by_name(params, "permission")

    assert any(
        "dopuszczenie funkcji mieszkaniowej wielorodzinnej" in p.normalized_value
        for p in permission_params
    )


def test_real_general_paragraf_4_permission_infrastructure_and_greenery() -> None:
    segments = _real_bielsko_biala_segments()
    segment = next(s for s in segments if s.segment_id == "seg-0006")
    zone_section = _synthetic_zone_section(
        "OGÓLNY_SEGMENT_TESTOWY", segment.segment_id, segment.page_number
    )

    params = extract_descriptive_parameters(
        "OGÓLNY_SEGMENT_TESTOWY", zone_section, segments
    )
    permission_params = _params_by_name(params, "permission")
    values = {p.normalized_value for p in permission_params}

    assert any("dopuszczenie infrastruktury technicznej" in v for v in values)
    assert any("dopuszczenie zieleni" in v for v in values)


# --- environmental_restriction (realny fixture, §5) -----------------------


def test_real_environmental_restriction_in_paragraf_5_context() -> None:
    segments = _real_bielsko_biala_segments()
    seg7 = next(s for s in segments if s.segment_id == "seg-0007")
    seg8 = next(s for s in segments if s.segment_id == "seg-0008")
    zone_section = ZoneSectionResult(
        zone_symbol="OGÓLNY_SEGMENT_TESTOWY",
        candidates=[
            ZoneSectionCandidate(
                zone_symbol="OGÓLNY_SEGMENT_TESTOWY",
                segment_id=seg7.segment_id,
                source_text="x",
                page_number=seg7.page_number,
                confidence=0.9,
                match_pattern="synthetic",
            ),
            ZoneSectionCandidate(
                zone_symbol="OGÓLNY_SEGMENT_TESTOWY",
                segment_id=seg8.segment_id,
                source_text="x",
                page_number=seg8.page_number,
                confidence=0.9,
                match_pattern="synthetic",
            ),
        ],
    )

    params = extract_descriptive_parameters(
        "OGÓLNY_SEGMENT_TESTOWY", zone_section, segments
    )
    env_params = _params_by_name(params, "environmental_restriction")
    values = {p.normalized_value for p in env_params}

    assert any("nakaz ochrony wartościowego drzewostanu" in v for v in values)
    assert any("dopuszczalny poziom hałasu" in v for v in values)


def test_environmental_restriction_requires_heading_context() -> None:
    # Bez kontekstu 'ochrony środowiska' fraza 'nakaz ochrony...' nie jest
    # klasyfikowana jako ograniczenie środowiskowe — unika false positive
    # z innych paragrafów uchwały.
    segments = [
        DocumentSegment(
            segment_id="seg-syn-noenv",
            text="nakaz ochrony jakiegoś elementu spoza kontekstu środowiskowego.",
            page_number=1,
            heading=None,
            source="paragraph",
        )
    ]
    zone_section = _synthetic_zone_section("SYN", "seg-syn-noenv")

    params = extract_descriptive_parameters("SYN", zone_section, segments)
    env_params = _params_by_name(params, "environmental_restriction")

    assert env_params == []


# --- parking_requirement_descriptive (realny fixture, §8 ogólny) ---------


def test_real_general_paragraf_8_parking_requirement_descriptive() -> None:
    segments = _real_bielsko_biala_segments()
    segment = next(s for s in segments if "kartę parkingową" in s.text)
    zone_section = _synthetic_zone_section(
        "OGÓLNY_SEGMENT_TESTOWY", segment.segment_id, segment.page_number
    )

    params = extract_descriptive_parameters(
        "OGÓLNY_SEGMENT_TESTOWY", zone_section, segments
    )
    parking_params = _params_by_name(params, "parking_requirement_descriptive")

    assert len(parking_params) == 1
    assert "karta parkingowa" not in parking_params[0].normalized_value  # dosłowny fragment
    assert "kartę parkingową" in parking_params[0].normalized_value


# --- roof_geometry (realny fixture, 230_U, brak konfliktu) ----------------


def test_real_230_u_roof_geometry_has_two_non_conflicting_categories() -> None:
    # Dwie kategorie geometrii dachu w jednym segmencie NIE są konfliktem
    # (różne dopuszczalne opcje), więc manual_review_required musi być False.
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result("230_U", segments)

    params = extract_descriptive_parameters("230_U", zone_section, segments)
    roof_params = _params_by_name(params, "roof_geometry")

    values = {p.normalized_value for p in roof_params}
    assert values == {"dwuspadowy_lub_wielospadowy", "płaski"}
    assert all(p.manual_review_required is False for p in roof_params)


def test_real_230_umw_roof_geometry_single_category() -> None:
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result("230_UMW", segments)

    params = extract_descriptive_parameters("230_UMW", zone_section, segments)
    roof_params = _params_by_name(params, "roof_geometry")

    assert [p.normalized_value for p in roof_params] == ["dwuspadowy_lub_wielospadowy"]


# --- table handling: symbol strefy przekazany explicite, nigdy zgadywany --


def test_table_segment_binds_prohibition_to_explicit_zone_symbol_argument() -> None:
    table_segment = DocumentSegment(
        segment_id="seg-table-0001",
        text="230_U zabudowa usługowa zakaz zabudowy dla ZP",
        page_number=4,
        heading=None,
        source="table",
    )
    zone_section = _synthetic_zone_section("230_U", "seg-table-0001", page_number=4)

    params = extract_descriptive_parameters("230_U", zone_section, [table_segment])
    prohibition_params = _params_by_name(params, "prohibition")

    # Funkcja nigdy nie zgaduje symbolu z treści wiersza — wywołanie z
    # zone_symbol='230_U' zwraca parametr niezależnie od tego, że wiersz
    # tabeli wspomina też 'ZP'.
    assert len(prohibition_params) == 1
    assert prohibition_params[0].normalized_value == "zakaz zabudowy dla ZP"


# --- brak kandydatów -> brak wyniku, nie błąd ----------------------------


def test_no_candidates_returns_empty_list_without_error() -> None:
    zone_section = ZoneSectionResult(zone_symbol="MN", candidates=[])

    params = extract_descriptive_parameters("MN", zone_section, [])

    assert params == []
