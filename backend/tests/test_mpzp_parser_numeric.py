"""Testy ekstraktorów parametrów liczbowych MPZP.

Testy z prefiksem "real_" używają WYŁĄCZNIE realnego tekstu uchwały
Bielska-Białej z ``tests/fixtures/mpzp_documents/``. Testy z prefiksem
"synthetic_" konstruują tekst ręcznie i są jawnie oznaczone jako testy
jednostkowe logiki regex, bo realny fixture nie dostarcza czystego przykładu
dla tego parametru (patrz honest_gap w specyfikacji zadania).
"""

import json
from pathlib import Path

import pytest

from app.services.mpzp_parser_extract import TextExtractionResult
from app.services.mpzp_parser_numeric import (
    _parse_polish_number,
    extract_numeric_parameters,
)
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


# --- _parse_polish_number -----------------------------------------------


def test_parse_polish_number_converts_comma_decimal() -> None:
    assert _parse_polish_number("0,1") == 0.1
    assert _parse_polish_number("2,0") == 2.0


def test_parse_polish_number_handles_plain_dot() -> None:
    assert _parse_polish_number("3.5") == 3.5


def test_parse_polish_number_strips_whitespace_thousands_separator() -> None:
    assert _parse_polish_number("1 234,5") == 1234.5


# --- max_building_height_m (realny fixture) -----------------------------


def test_real_230_u_has_single_max_building_height() -> None:
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result("230_U", segments)

    params = extract_numeric_parameters("230_U", zone_section, segments)
    height_params = _params_by_name(params, "max_building_height_m")

    assert len(height_params) == 1
    assert height_params[0].normalized_value == 15.0
    assert height_params[0].unit == "m"
    assert height_params[0].manual_review_required is False
    assert height_params[0].source_text


def test_real_230_umw_has_two_conditional_max_building_heights_not_a_conflict() -> None:
    # §11: 15 m dla zabudowy pierzejowej, 13 m dla pozostałej części terenu, w tym samym segmencie.
    # Od PV3-08 to dwie wartości WARUNKOWE (różne warunki), a nie sprzeczność.
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result("230_UMW", segments)

    params = extract_numeric_parameters("230_UMW", zone_section, segments)
    height_params = _params_by_name(params, "max_building_height_m")

    assert {p.normalized_value for p in height_params} == {15.0, 13.0}
    assert {p.value_kind for p in height_params} == {"conditional"}
    assert all(p.manual_review_required is False and p.confidence >= 0.8 for p in height_params)
    by_value = {p.normalized_value: [(c.kind, c.label) for c in p.conditions] for p in height_params}
    assert by_value == {
        15.0: [("building_type", "zabudowy pierzejowej")],
        13.0: [("other", "pozostałej części terenu")],
    }
    assert all(c.quote in p.source_text for p in height_params for c in p.conditions)  # cytat warunku leży w dowodzie


def test_same_condition_with_different_values_is_still_a_conflict() -> None:
    segment = _segment(
        "a) maksymalna wysokość zabudowy: 12 m,\n"
        "b) maksymalna wysokość zabudowy: 15 m,\n"
        "c) maksymalna wysokość zabudowy: 8 m dla budynków z dachem płaskim, 9 m dla budynków z dachem płaskim"
    )
    params = extract_numeric_parameters("U", _section("U", segment), [segment])
    heights = _params_by_name(params, "max_building_height_m")
    unconditional = [p for p in heights if not p.conditions]
    flat_roof = [p for p in heights if p.conditions]
    assert {p.normalized_value for p in unconditional} == {12.0, 15.0}
    assert all(p.value_kind == "conflict" and p.manual_review_required and p.confidence < 0.6 for p in unconditional)
    assert {p.normalized_value for p in flat_roof} == {8.0, 9.0}  # to samo ``dach płaski`` z dwiema wartościami
    assert all(p.value_kind == "conflict" for p in flat_roof)


def test_real_230_zp_does_not_match_urzadzenia_sportu_height_as_building_height() -> (
    None
):
    # 230_ZP ma "wysokość URZĄDZEŃ SPORTU I REKREACJI – 5 m", nie wysokość
    # zabudowy — negatywny test precyzji anchoru.
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result("230_ZP", segments)

    params = extract_numeric_parameters("230_ZP", zone_section, segments)
    height_params = _params_by_name(params, "max_building_height_m")

    assert height_params == []


def test_synthetic_height_do_11_m_is_extracted() -> None:
    segments = [
        DocumentSegment(
            segment_id="seg-syn-0001",
            text="ustala się wysokość zabudowy do 11 m dla nowej zabudowy.",
            page_number=1,
            heading=None,
            source="paragraph",
        )
    ]
    zone_section = ZoneSectionResult(
        zone_symbol="SYN",
        candidates=[
            ZoneSectionCandidate(
                zone_symbol="SYN",
                segment_id="seg-syn-0001",
                source_text="wysokość zabudowy do 11 m",
                page_number=1,
                confidence=0.9,
                match_pattern="synthetic",
            )
        ],
    )

    params = extract_numeric_parameters("SYN", zone_section, segments)
    height_params = _params_by_name(params, "max_building_height_m")

    assert len(height_params) == 1
    assert height_params[0].normalized_value == 11.0
    assert height_params[0].unit == "m"


# --- intensywność zabudowy (realny fixture) -----------------------------


def test_real_230_u_intensity_min_and_max() -> None:
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result("230_U", segments)

    params = extract_numeric_parameters("230_U", zone_section, segments)
    min_params = _params_by_name(params, "min_intensity")
    max_params = _params_by_name(params, "max_intensity")

    assert [p.normalized_value for p in min_params] == [0.1]
    assert [p.normalized_value for p in max_params] == [3.0]
    assert min_params[0].manual_review_required is False
    assert max_params[0].manual_review_required is False


def test_real_230_umw_intensity_min_and_max_differs_from_230_u() -> None:
    # Różne wartości MIĘDZY strefami nie są konfliktem — to osobne wywołania.
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result("230_UMW", segments)

    params = extract_numeric_parameters("230_UMW", zone_section, segments)
    min_params = _params_by_name(params, "min_intensity")
    max_params = _params_by_name(params, "max_intensity")

    assert [p.normalized_value for p in min_params] == [0.1]
    assert [p.normalized_value for p in max_params] == [2.5]


# --- maksymalna powierzchnia zabudowy (realny fixture) ------------------


def test_real_230_u_max_building_coverage_percent() -> None:
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result("230_U", segments)

    params = extract_numeric_parameters("230_U", zone_section, segments)
    coverage_params = _params_by_name(params, "max_building_coverage_percent")

    assert [p.normalized_value for p in coverage_params] == [60.0]
    assert coverage_params[0].unit == "percent"


def test_real_230_umw_max_building_coverage_percent() -> None:
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result("230_UMW", segments)

    params = extract_numeric_parameters("230_UMW", zone_section, segments)
    coverage_params = _params_by_name(params, "max_building_coverage_percent")

    assert [p.normalized_value for p in coverage_params] == [50.0]


# --- minimalna powierzchnia biologicznie czynna (realny fixture, 3 strefy) --


@pytest.mark.parametrize(
    ("zone_symbol", "expected_percent"),
    [("230_U", 10.0), ("230_UMW", 20.0), ("230_ZP", 75.0)],
)
def test_real_min_biologically_active_percent_per_zone(
    zone_symbol: str, expected_percent: float
) -> None:
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result(zone_symbol, segments)

    params = extract_numeric_parameters(zone_symbol, zone_section, segments)
    bio_params = _params_by_name(params, "min_biologically_active_percent")

    assert [p.normalized_value for p in bio_params] == [expected_percent]
    assert bio_params[0].unit == "percent"
    assert bio_params[0].manual_review_required is False


# --- kąt nachylenia dachu (realny fixture, konflikt w 230_U) ------------


def test_real_230_u_has_roof_angle_ranges_conditional_on_the_roof_type() -> None:
    # §10: 30°–50° dla dachów dwuspadowych/wielospadowych i 0°–15° dla płaskich w tym samym segmencie.
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result("230_U", segments)

    params = extract_numeric_parameters("230_U", zone_section, segments)
    min_params = _params_by_name(params, "roof_angle_min_deg")
    max_params = _params_by_name(params, "roof_angle_max_deg")

    assert {p.normalized_value for p in min_params} == {30.0, 0.0}
    assert {p.normalized_value for p in max_params} == {50.0, 15.0}
    assert all(p.value_kind == "conditional" and p.manual_review_required is False for p in min_params + max_params)
    labels = {p.normalized_value: p.conditions[0].label for p in min_params}
    assert labels == {30.0: "dach dwuspadowy lub wielospadowy", 0.0: "dach płaski"}
    assert all(p.unit == "deg" for p in min_params + max_params)


def test_real_230_umw_has_single_roof_angle_range() -> None:
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result("230_UMW", segments)

    params = extract_numeric_parameters("230_UMW", zone_section, segments)
    min_params = _params_by_name(params, "roof_angle_min_deg")
    max_params = _params_by_name(params, "roof_angle_max_deg")

    assert [p.normalized_value for p in min_params] == [20.0]
    assert [p.normalized_value for p in max_params] == [45.0]
    assert min_params[0].manual_review_required is False
    assert max_params[0].manual_review_required is False


# --- max_storeys (bez czystego realnego przykładu -> testy syntetyczne) --


def test_synthetic_max_storeys_nie_wiecej_niz() -> None:
    segments = [
        DocumentSegment(
            segment_id="seg-syn-0002",
            text="ustala się nie więcej niż 3 kondygnacje naziemne budynku.",
            page_number=1,
            heading=None,
            source="paragraph",
        )
    ]
    zone_section = ZoneSectionResult(
        zone_symbol="SYN",
        candidates=[
            ZoneSectionCandidate(
                zone_symbol="SYN",
                segment_id="seg-syn-0002",
                source_text="nie więcej niż 3 kondygnacje",
                page_number=1,
                confidence=0.9,
                match_pattern="synthetic",
            )
        ],
    )

    params = extract_numeric_parameters("SYN", zone_section, segments)
    storeys_params = _params_by_name(params, "max_storeys")

    assert len(storeys_params) == 1
    assert storeys_params[0].normalized_value == 3.0


def test_synthetic_max_storeys_maksymalnie() -> None:
    segments = [
        DocumentSegment(
            segment_id="seg-syn-0003",
            text="maksymalnie 2 kondygnacje naziemne.",
            page_number=1,
            heading=None,
            source="paragraph",
        )
    ]
    zone_section = ZoneSectionResult(
        zone_symbol="SYN",
        candidates=[
            ZoneSectionCandidate(
                zone_symbol="SYN",
                segment_id="seg-syn-0003",
                source_text="maksymalnie 2 kondygnacje",
                page_number=1,
                confidence=0.9,
                match_pattern="synthetic",
            )
        ],
    )

    params = extract_numeric_parameters("SYN", zone_section, segments)
    storeys_params = _params_by_name(params, "max_storeys")

    assert len(storeys_params) == 1
    assert storeys_params[0].normalized_value == 2.0


def test_real_document_does_not_fabricate_max_storeys_for_230_u() -> None:
    # Uczciwy negatywny test: realny §10 dla 230_U nie ma czystego,
    # jednoznacznego przypisania max_storeys do tej strefy (tylko wzmianka
    # 'kondygnacją parteru' w innym kontekście) — anchor NIE powinien się
    # na to złapać.
    segments = _real_bielsko_biala_segments()
    zone_section = _zone_result("230_U", segments)

    params = extract_numeric_parameters("230_U", zone_section, segments)
    storeys_params = _params_by_name(params, "max_storeys")

    assert storeys_params == []


# --- setback_m (brak czystego realnego przykładu per-strefa) ------------


def test_synthetic_setback_m_from_boundary() -> None:
    segments = [
        DocumentSegment(
            segment_id="seg-syn-0004",
            text="ściana budynku w odległości 4,5 m od granicy działki.",
            page_number=1,
            heading=None,
            source="paragraph",
        )
    ]
    zone_section = ZoneSectionResult(
        zone_symbol="SYN",
        candidates=[
            ZoneSectionCandidate(
                zone_symbol="SYN",
                segment_id="seg-syn-0004",
                source_text="w odległości 4,5 m od granicy",
                page_number=1,
                confidence=0.9,
                match_pattern="synthetic",
            )
        ],
    )

    params = extract_numeric_parameters("SYN", zone_section, segments)
    setback_params = _params_by_name(params, "setback_m")

    assert len(setback_params) == 1
    assert setback_params[0].normalized_value == 4.5
    assert setback_params[0].unit == "m"


def test_real_general_paragraf_4_setback_is_detected_but_is_not_zone_specific() -> (
    None
):
    # §4 pkt 6 realnego dokumentu ("1,5 m od granicy") jest przepisem
    # OGÓLNYM dla całego obszaru planu, nie przypisanym do 230_U/UMW/ZP.
    # Test dokumentuje, że logika ekstraktora działa na tym segmencie
    # ogólnym, gdy zostanie mu jawnie podany — nie sugeruje przypisania do
    # konkretnej strefy.
    segment = DocumentSegment(
        segment_id="seg-general-0004",
        text=(
            "6) dopuszczenie sytuowania ściany budynku, nie posiadającej "
            "otworów okiennych lub drzwiowych, w odległości 1,5 m od granicy "
            "z sąsiednią działką budowlaną lub bezpośrednio przy granicy;"
        ),
        page_number=3,
        heading=None,
        source="paragraph",
    )
    zone_section = ZoneSectionResult(
        zone_symbol="OGÓLNY_SEGMENT_TESTOWY",
        candidates=[
            ZoneSectionCandidate(
                zone_symbol="OGÓLNY_SEGMENT_TESTOWY",
                segment_id="seg-general-0004",
                source_text="w odległości 1,5 m od granicy",
                page_number=3,
                confidence=0.5,
                match_pattern="synthetic",
            )
        ],
    )

    params = extract_numeric_parameters(
        "OGÓLNY_SEGMENT_TESTOWY", zone_section, [segment]
    )
    setback_params = _params_by_name(params, "setback_m")

    assert len(setback_params) == 1
    assert setback_params[0].normalized_value == 1.5


# --- parking_minimum (brak czystego realnego przykładu per-strefa) ------


def test_synthetic_parking_minimum_places_per_unit() -> None:
    segments = [
        DocumentSegment(
            segment_id="seg-syn-0005",
            text="ustala się 0,7 miejsca na 1 lokal mieszkalny.",
            page_number=1,
            heading=None,
            source="paragraph",
        )
    ]
    zone_section = ZoneSectionResult(
        zone_symbol="SYN",
        candidates=[
            ZoneSectionCandidate(
                zone_symbol="SYN",
                segment_id="seg-syn-0005",
                source_text="0,7 miejsca na 1 lokal mieszkalny",
                page_number=1,
                confidence=0.9,
                match_pattern="synthetic",
            )
        ],
    )

    params = extract_numeric_parameters("SYN", zone_section, segments)
    parking_params = _params_by_name(params, "parking_minimum")

    assert len(parking_params) == 1
    assert parking_params[0].normalized_value == 0.7


def test_real_general_paragraf_8_parking_is_detected_as_general_segment() -> None:
    # §8 pkt 1c realnego dokumentu ("0,7 miejsca na 1 lokal mieszkalny") jest
    # ogólnym wskaźnikiem parkingowym (§8), nie strefowym w tym dokumencie.
    segment = DocumentSegment(
        segment_id="seg-general-0008",
        text="- 0,7 miejsca na 1 lokal mieszkalny,",
        page_number=4,
        heading=None,
        source="paragraph",
    )
    zone_section = ZoneSectionResult(
        zone_symbol="OGÓLNY_SEGMENT_TESTOWY",
        candidates=[
            ZoneSectionCandidate(
                zone_symbol="OGÓLNY_SEGMENT_TESTOWY",
                segment_id="seg-general-0008",
                source_text="0,7 miejsca na 1 lokal mieszkalny",
                page_number=4,
                confidence=0.5,
                match_pattern="synthetic",
            )
        ],
    )

    params = extract_numeric_parameters(
        "OGÓLNY_SEGMENT_TESTOWY", zone_section, [segment]
    )
    parking_params = _params_by_name(params, "parking_minimum")

    assert len(parking_params) == 1
    assert parking_params[0].normalized_value == 0.7


# --- brak kandydatów -> brak wyniku, nie błąd ----------------------------


def test_no_candidates_returns_empty_list_without_error() -> None:
    zone_section = ZoneSectionResult(zone_symbol="MN", candidates=[])

    params = extract_numeric_parameters("MN", zone_section, [])

    assert params == []


# --- tabela: ekstraktor działa identycznie na source='table' -------------


def test_table_segment_extracts_height_identically_to_paragraph() -> None:
    table_segment = DocumentSegment(
        segment_id="seg-table-0001",
        text="230_U zabudowa usługowa maksymalna wysokość zabudowy – 15 m 60%",
        page_number=4,
        heading=None,
        source="table",
    )
    zone_section = ZoneSectionResult(
        zone_symbol="230_U",
        candidates=[
            ZoneSectionCandidate(
                zone_symbol="230_U",
                segment_id="seg-table-0001",
                source_text="230_U zabudowa usługowa",
                page_number=4,
                confidence=0.9,
                match_pattern="synthetic",
            )
        ],
    )

    params = extract_numeric_parameters("230_U", zone_section, [table_segment])
    height_params = _params_by_name(params, "max_building_height_m")

    assert len(height_params) == 1
    assert height_params[0].normalized_value == 15.0


# --- silnik leksykonu (PV3-07): jeden mechanizm za parserem liczbowym ------------------------------


def _segment(text: str, segment_id: str = "seg-pv3-0001") -> DocumentSegment:
    return DocumentSegment(segment_id=segment_id, text=text, page_number=2, heading=None, source="paragraph")


def _section(zone_symbol: str, segment: DocumentSegment) -> ZoneSectionResult:
    return ZoneSectionResult(
        zone_symbol=zone_symbol,
        candidates=[
            ZoneSectionCandidate(
                zone_symbol=zone_symbol,
                segment_id=segment.segment_id,
                source_text=segment.text[:80],
                page_number=segment.page_number,
                confidence=0.9,
                match_pattern="synthetic",
            )
        ],
    )


def test_matches_list_covers_nine_catalog_and_four_statutory_parameters_in_fixed_order() -> None:
    from app.services.mpzp_parser_numeric import extract_numeric_matches

    names = [name for name, _unit, _matches in extract_numeric_matches("")]
    assert names[:9] == [
        "max_building_height_m",
        "min_intensity",
        "max_intensity",
        "max_building_coverage_percent",
        "min_biologically_active_percent",
        "roof_angle_min_deg",
        "roof_angle_max_deg",
        "max_storeys",
        "setback_m",
    ]
    assert set(names[9:]) == {"parking_minimum", "min_building_coverage_percent", "min_plot_area_m2", "max_retail_sales_area_m2"}
    units = {name: unit for name, unit, _ in extract_numeric_matches("")}
    assert units["max_building_height_m"] == "m" and units["roof_angle_min_deg"] == "deg"
    assert units["parking_minimum"] == "miejsca/lokal" and units["min_intensity"] is None


def test_new_wordings_that_used_to_be_gaps_are_recognized() -> None:
    # Dosłowne sformułowania z fixture'ów krakow_morelowa / legnica_szpital, zapisane wcześniej jako luki.
    text = (
        "1) dla terenu MN.1:\n"
        "a) minimalny wskaźnik terenu biologicznie czynnego: 60%,\n"
        "b) maksymalny wskaźnik powierzchni zabudowy: 30%,\n"
        "c) wskaźnik intensywności zabudowy: 0,01 – 0,5,\n"
        "d) maksymalną wysokość zabudowy: 11 m, a dla budynków przekrytych dachem\npłaskim: 9,5 m;\n"
    )
    segment = _segment(text)
    params = extract_numeric_parameters("MN.1", _section("MN.1", segment), [segment])
    by_name = {name: sorted(p.normalized_value for p in params if p.name == name) for name in {p.name for p in params}}
    assert by_name == {
        "min_biologically_active_percent": [60.0],
        "max_building_coverage_percent": [30.0],
        "min_intensity": [0.01],
        "max_intensity": [0.5],
        "max_building_height_m": [9.5, 11.0],
    }


def test_table_ratio_is_converted_to_percent_with_a_flag_and_lower_confidence() -> None:
    segment = _segment("maksymalny udział powierzchni zabudowy 0,60\nminimalny udział powierzchni biologicznie czynnej 0,15")
    params = extract_numeric_parameters("1UZ", _section("1UZ", segment), [segment])
    assert {p.name: p.normalized_value for p in params} == {
        "max_building_coverage_percent": 60.0,
        "min_biologically_active_percent": 15.0,
    }
    assert all(p.normalization_flags == ["ratio_to_percent"] for p in params)
    assert all(p.extraction_strategy == "adjective" and p.confidence < 0.85 for p in params)
    assert all(p.raw_value in {"0,60", "0,15"} for p in params)


def test_values_named_for_another_zone_are_not_returned_for_this_zone() -> None:
    segment = _segment(
        "1) dla terenu MN.1:\na) maksymalna wysokość zabudowy: 11 m,\n"
        "2) dla terenu MN.2:\na) maksymalna wysokość zabudowy: 9 m,\n"
    )
    for symbol, expected in (("MN.1", 11.0), ("MN.2", 9.0)):
        params = extract_numeric_parameters(symbol, _section(symbol, segment), [segment])
        assert [p.normalized_value for p in params if p.name == "max_building_height_m"] == [expected]


def test_degree_artifacts_are_flagged_and_marked_with_lower_confidence() -> None:
    segment = _segment("geometria dachów: o pochyleniu połaci w przedziale od 300 do 450")
    params = extract_numeric_parameters("MN", _section("MN", segment), [segment])
    assert {(p.name, p.normalized_value) for p in params} == {("roof_angle_min_deg", 30.0), ("roof_angle_max_deg", 45.0)}
    assert all("degree_artifact" in p.normalization_flags and p.confidence < 0.7 for p in params)


def test_statutory_parameters_are_extracted_alongside_the_catalog_ones() -> None:
    segment = _segment(
        "2) ustala się:\n"
        "a) minimalny wskaźnik powierzchni zabudowy w stosunku do powierzchni działki budowlanej – 0,01 (1%),\n"
        "b) minimalną powierzchnię nowo wydzielonej działki budowlanej – 1000 m2,\n"
        "c) zapewnić 2 miejsca postojowe na lokal,\n"
        "d) zakaz obiektów handlowych o powierzchni sprzedaży powyżej 2000 m²."
    )
    params = extract_numeric_parameters("U", _section("U", segment), [segment])
    values = {p.name: p.normalized_value for p in params}
    assert values == {
        "min_building_coverage_percent": 1.0,
        "min_plot_area_m2": 1000.0,
        "parking_minimum": 2.0,
        "max_retail_sales_area_m2": 2000.0,
    }
