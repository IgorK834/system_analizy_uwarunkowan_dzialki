"""Silnik ekstrakcji ilości oparty na leksykonie (PV3-07).

Teksty w testach są sformułowaniami spotykanymi w uchwałach (część to dosłowne fragmenty
zamrożonego korpusu BK-603, część to warianty spoza korpusu — oznaczone ``synthetic``).
"""

from __future__ import annotations

import pytest

from app.modules.planning.domain.quantity_engine import (
    ENGINE_VERSION,
    extract_quantities,
    find_atoms,
    find_nouns,
    find_quantities,
    mask_text,
    split_clauses,
)
from app.modules.planning.domain.quantity_lexicon import (
    FAMILY_HEIGHT,
    FAMILY_IGNORE,
    FAMILY_RETAIL,
    LEXICON_VERSION,
    PARAMETER_ORDER,
)


def values(text: str, parameter: str, **kwargs) -> list[float]:
    return [m.value for m in find_quantities(text, **kwargs) if m.parameter == parameter]


def by_parameter(text: str, **kwargs) -> dict[str, list[float]]:
    result: dict[str, list[float]] = {}
    for match in find_quantities(text, **kwargs):
        result.setdefault(match.parameter, []).append(match.value)
    return result


# --- maskowanie szumu -------------------------------------------------------------------


def test_masking_keeps_length_newlines_and_removes_identifiers_and_footers() -> None:
    text = (
        "§ 36. Teren 2.1MN – wysokość do 10 m, ust. 3 pkt 2 lit. b\n"
        "Dziennik Urzędowy Województwa Dolnośląskiego – 5 – Poz. 1124\n"
        "Id: 2A4C40DF-09D9-4683-9A9A-9539005D82C8. Podpisany Strona 12\n"
        "–––––––––––––––––––––––––––\n"
        "intensywność 0,5"
    )
    masked = mask_text(text)
    assert len(masked) == len(text) and masked.count("\n") == text.count("\n")
    assert "2.1MN" not in masked and "Dziennik" not in masked and "2A4C40DF" not in masked
    assert "pkt 2" not in masked and "–––" not in masked
    assert "wysokość do 10 m" in masked and "intensywność 0,5" in masked


def test_page_footer_does_not_swallow_single_line_ocr_text() -> None:
    # Skan po OCR to jedna linia: nagłówek strony nie może zjeść całego akapitu (regresja PV3-07).
    text = (
        "Dziennik Urzędowy Województwa Wielkopolskiego Nr 91 — 9891 — Poz. 2276. 2) Na terenie "
        "symbolem 146 MN ustala się teren zabudowy. Obowiązuje nieprzekraczalna linia zabudowy w odległości "
        "6 m od granicy pasa drogowego drogi dojazdowej. Wysokość budynków nie może przekraczać 2 kondygnacji."
    )
    assert by_parameter(text) == {"setback_m": [6.0], "max_storeys": [2.0]}


# --- klauzule i dziedziczenie rzeczownika -----------------------------------------------


def test_clauses_follow_list_markers_and_nest_by_rank() -> None:
    text = (
        "§ 5. 1. Ustala się:\n"
        "1) zasady:\n"
        "a) wysokość zabudowy:\n"
        "- do 9 m,\n"
        "-- dla garaży – 5 m,\n"
        "b) intensywność: 0,5;\n"
        "2) inne zasady."
    )
    clauses = split_clauses(text)
    contents = [text[c.start : c.end].strip() for c in clauses]
    # § (ust. ``1.`` w tej samej linii nie jest znacznikiem), pkt, lit., tiret, podwójne tiret, lit., pkt
    assert [c.rank for c in clauses] == [0, 2, 3, 4, 5, 3, 2]

    def clause_starting(prefix: str):
        return next(c for c, t in zip(clauses, contents) if t.startswith(prefix))

    letter, bullet = clause_starting("wysokość zabudowy"), clause_starting("do 9 m")
    nested, sibling = clause_starting("dla garaży"), clause_starting("intensywność")
    assert bullet.parent == letter.index and nested.parent == bullet.index
    assert sibling.parent == clauses[1].index  # lit. b) jest rodzeństwem lit. a): ten sam rodzic (pkt 1)


def test_text_before_first_marker_is_never_a_parent() -> None:
    clauses = split_clauses("zakaz przekroczenia parametrów\n1) wysokość: 10 m\n2) intensywność: 1,0")
    assert clauses[0].rank == -1
    assert all(c.parent is None for c in clauses)


def test_bullet_with_leading_digit_is_continuation_inside_a_wrapped_sentence() -> None:
    wrapped = "wskaźnik powierzchni zabudowy w stosunku do powierzchni działki\n– 0,30 (30%)"
    assert len(split_clauses(wrapped)) == 1
    listed = "maksymalna wysokość zabudowy:\n- 15 m dla zabudowy pierzejowej,\n- 13 m dla pozostałej części terenu,"
    assert len(split_clauses(listed)) == 3  # po dwukropku tiret z liczbą jest pozycją listy


def test_child_clause_inherits_the_noun_of_its_header() -> None:
    text = (
        "d) wysokość budynków:\n"
        "- z dachami symetrycznymi - maksimum 10 m,\n"
        "- z dachami płaskimi - maksimum 8 m,\n"
        "- gospodarczych i garaży - maksimum 5 m,\n"
    )
    matches = [m for m in find_quantities(text) if m.parameter == "max_building_height_m"]
    assert [m.value for m in matches] == [10.0, 8.0, 5.0]
    assert all("inherited_noun" in m.flags and m.strategy == "max_min_noun" for m in matches)
    assert all(m.noun == "wysokość budynków" for m in matches)


def test_sibling_clauses_do_not_share_nouns() -> None:
    text = "a) wysokość zabudowy: 12 m,\nb) dopuszcza się garaże o powierzchni 40 m2,\nc) 3 kondygnacje podziemne"
    assert by_parameter(text) == {"max_building_height_m": [12.0]}


# --- operatory ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "parameter", "value", "strategy"),
    [
        ("Maksymalna wysokość zabudowy – 12 m.", "max_building_height_m", 12.0, "adjective"),
        ("powierzchnia zabudowy - maksimum 30 %", "max_building_coverage_percent", 30.0, "max_min_noun"),
        ("teren biologicznie czynny - minimum 40 %", "min_biologically_active_percent", 40.0, "max_min_noun"),
        ("wskaźnik powierzchni zabudowy: nie większy niż 50%", "max_building_coverage_percent", 50.0, "comparative"),
        ("wskaźnik powierzchni biologicznie czynnej: nie mniejszy niż 35%", "min_biologically_active_percent", 35.0, "comparative"),
        ("Powierzchnia biologicznie czynna nie może być mniejsza niż 30,5%.", "min_biologically_active_percent", 30.5, "comparative"),
        ("wskaźnik intensywności zabudowy nie może przekraczać 0,4", "max_intensity", 0.4, "comparative"),
        ("wysokość budynków do kalenicy nie może przekraczać 7.0 m", "max_building_height_m", 7.0, "comparative"),
        ("maksymalna wysokość zabudowy - do 15,0 m", "max_building_height_m", 15.0, "up_to"),
        ("wysokość zabudowy – nie wyżej niż 6,0 m", "max_building_height_m", 6.0, "comparative"),
        ("Maksymalna wysokość zabudowy wynosi 12,5 m.", "max_building_height_m", 12.5, "exact_word"),
        ("wysokość budynków do 10,5 m", "max_building_height_m", 10.5, "up_to"),  # synthetic
        ("współczynnik intensywności zabudowy: max. 1,2", "max_intensity", 1.2, "max_min_noun"),  # synthetic
        ("zabudowa do 2 kondygnacji nadziemnych", "max_storeys", 2.0, "up_to"),  # synthetic
    ],
)
def test_operator_phrases(text: str, parameter: str, value: float, strategy: str) -> None:
    matches = [m for m in find_quantities(text) if m.parameter == parameter]
    assert [m.value for m in matches] == [value]
    assert matches[0].strategy == strategy


def test_two_comparatives_give_the_lower_and_the_upper_bound() -> None:
    text = "intensywność zabudowy: nie mniejszą niż 0,1 i nie większą niż 0,9,"
    assert by_parameter(text) == {"min_intensity": [0.1], "max_intensity": [0.9]}


def test_min_and_max_nouns_and_labels_in_separate_clauses() -> None:
    assert by_parameter("intensywność zabudowy – minimum 1,0, maksimum 4,0,") == {
        "min_intensity": [1.0],
        "max_intensity": [4.0],
    }
    text = "d) intensywność zabudowy:\n- minimalna – 0,1,\n- maksymalna – 3,0,"
    assert by_parameter(text) == {"min_intensity": [0.1], "max_intensity": [3.0]}
    assert by_parameter("wskaźnik intensywności zabudowy: minimalny - 0,15; maksymalny - 1,20,") == {
        "min_intensity": [0.15],
        "max_intensity": [1.2],
    }
    assert by_parameter("wskaźnik minimalnej intensywności zabudowy – 0,01, maksymalnej: 2,0") == {
        "min_intensity": [0.01],
        "max_intensity": [2.0],
    }


def test_adjective_on_the_noun_phrase_sets_the_direction() -> None:
    assert by_parameter("minimalny udział powierzchni biologicznie czynnej – 30%") == {
        "min_biologically_active_percent": [30.0]
    }
    both = "5) ustala się minimalny i maksymalny wskaźnik intensywności zabudowy:\na) 0,01- 1,2,"
    assert by_parameter(both) == {"min_intensity": [0.01], "max_intensity": [1.2]}  # oba słowa → zakres rozstrzyga wartość


def test_greater_than_and_deltas_are_not_maxima() -> None:
    thresholds = "wysokość zabudowy większej niż 11,0 m - do wysokości tego budynku"
    extraction = extract_quantities(thresholds)
    assert extraction.matches == [] and {d.reason for d in extraction.dropped} == {"threshold"}
    delta = extract_quantities("a) wskaźniki:\n- powierzchni zabudowy - o 30%,\n- intensywności zabudowy - o 0,6;")
    assert delta.matches == []
    header = (
        "d) dopuszcza się zwiększenie maksymalnych wskaźników:\n"
        "- powierzchni zabudowy - do 100%,\n- intensywności zabudowy - o 0,6;"
    )
    extraction = extract_quantities(header)
    assert extraction.matches == [] and {d.reason for d in extraction.dropped} == {"delta_context"}


# --- zakresy ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "low", "high", "strategy"),
    [
        ("wskaźnik intensywności zabudowy: 0,01 – 0,9", 0.01, 0.9, "range_dash"),
        ("intensywność zabudowy - od 0 do 2,0", 0.0, 2.0, "range_from_to"),
        ("nadziemna intensywność zabudowy 0,15 – 2,50", 0.15, 2.5, "range_dash"),
        ("MN – 0,0- 0,5", None, None, None),  # bez nagłówka symboli to nie klauzula intensywności
    ],
)
def test_intensity_ranges(text: str, low: float | None, high: float | None, strategy: str | None) -> None:
    found = by_parameter(text)
    if low is None:
        assert found == {}
        return
    assert found == {"min_intensity": [low], "max_intensity": [high]}
    assert {m.strategy for m in find_quantities(text)} == {strategy}


def test_range_span_covers_the_whole_phrase_including_the_lead_in() -> None:
    text = "o jednakowym kącie nachylenia połaci dachowych od 30° do 50°,"
    matches = find_quantities(text)
    assert {m.raw_value for m in matches} == {"od 30° do 50°"}
    assert {text[m.start : m.end] for m in matches} == {"od 30° do 50°"}
    assert by_parameter(text) == {"roof_angle_min_deg": [30.0], "roof_angle_max_deg": [50.0]}


def test_range_may_wrap_across_a_line_break() -> None:
    wrapped = "kącie nachylenia połaci dachowych od 30° do\n50°,"
    assert by_parameter(wrapped) == {"roof_angle_min_deg": [30.0], "roof_angle_max_deg": [50.0]}


def test_dash_range_with_symbols_on_both_sides_and_ocr_garble() -> None:
    assert by_parameter("nachyleniu 25°– 40° lub dachy płaskie") == {
        "roof_angle_min_deg": [25.0],
        "roof_angle_max_deg": [40.0],
    }
    garbled = by_parameter("o kącie nachylenia połaci od 30” do 45*; 4) pokrycia dachów")
    assert garbled == {"roof_angle_min_deg": [30.0], "roof_angle_max_deg": [45.0]}  # ``4)`` to numer pozycji
    matches = find_quantities("nachyleniu 25*- 40* lub dachy płaskie")
    assert {m.value for m in matches} == {25.0, 40.0}
    assert all(m.flags == ("range_dash",) for m in matches)


def test_degree_zero_artifact_range_is_flagged_not_trusted() -> None:
    matches = find_quantities("o pochyleniu połaci w przedziale od 300 do 450")
    assert {(m.parameter, m.value) for m in matches} == {("roof_angle_min_deg", 30.0), ("roof_angle_max_deg", 45.0)}
    assert all("degree_artifact" in m.flags for m in matches)
    assert all(m.confidence < 0.85 for m in matches)  # artefakt obniża pewność


def test_degree_letters_are_accepted_with_a_flag() -> None:
    for text in ("o spadkach połaci do 30º", "o kącie nachylenia połaci od 30o do 45o"):
        matches = find_quantities(text)
        assert matches and all("degree_letter" in m.flags for m in matches)
    assert by_parameter("o kącie nachylenia połaci od 30o do 45o") == {
        "roof_angle_min_deg": [30.0],
        "roof_angle_max_deg": [45.0],
    }


def test_single_roof_angle_without_direction_is_an_equal_bound_with_a_flag() -> None:
    matches = find_quantities("kąt nachylenia połaci dachowych 35°")
    assert {(m.parameter, m.value) for m in matches} == {("roof_angle_min_deg", 35.0), ("roof_angle_max_deg", 35.0)}
    assert all("operator_implied" in m.flags for m in matches)
    assert by_parameter("kąt nachylenia połaci do 20°") == {"roof_angle_max_deg": [20.0]}


# --- jednostki i zapisy specjalne ---------------------------------------------------------


def test_ratio_percent_and_double_notation() -> None:
    ratio = find_quantities("ustala się maksymalną wielkość powierzchni zabudowy w stosunku do powierzchni działki: 0,35")
    assert [(m.value, m.flags) for m in ratio] == [(35.0, ("ratio_to_percent",))]
    double = find_quantities(
        "maksymalny wskaźnik powierzchni zabudowy w stosunku do powierzchni działki budowlanej – 0,50 (50%),"
    )
    assert [(m.value, m.flags, m.raw_value) for m in double] == [
        (50.0, ("double_notation",), "0,50 (50%)")
    ]
    wrapped = find_quantities("maksymalny wskaźnik powierzchni zabudowy w stosunku do powierzchni działki – 0,3\n(30%),")
    assert [m.value for m in wrapped] == [30.0]
    table = find_quantities("maksymalny udział powierzchni zabudowy 0,60\nminimalny udział powierzchni biologicznie czynnej 0,15")
    assert {(m.parameter, m.value) for m in table} == {
        ("max_building_coverage_percent", 60.0),
        ("min_biologically_active_percent", 15.0),
    }


def test_number_words_and_compound_adjectives() -> None:
    assert by_parameter("liczba kondygnacji nie większa niż dwie kondygnacje nadziemne") == {"max_storeys": [2.0]}
    assert by_parameter("4) liczbę kondygnacji – do dwóch kondygnacji nadziemnych;") == {"max_storeys": [2.0]}
    assert by_parameter("- do trzech kondygnacji naziemnych,") == {"max_storeys": [3.0]}
    jednokondygnacyjne = find_quantities("składy i magazyny:\n- jednokondygnacyjne,")
    assert [(m.value, m.flags) for m in jednokondygnacyjne] == [(1.0, ("number_word", "noun_implied"))]
    assert by_parameter("liczba kondygnacji: do trzech") == {"max_storeys": [3.0]}  # synthetic
    assert by_parameter("Wysokość budynków nie może przekraczać 2 kondygnacji.") == {"max_storeys": [2.0]}
    mixed = by_parameter("wysokość zabudowy - do 4 kondygnacji nadziemnych (w tym poddasze użytkowe) – nie wyżej jednak niż 15,0 m;")
    assert mixed == {"max_building_height_m": [15.0], "max_storeys": [4.0]}


def test_underground_storeys_and_stray_number_words_are_not_values() -> None:
    assert by_parameter("dopuszcza się wykonanie jednej kondygnacji podziemnej") == {}
    assert by_parameter("powierzchnia zabudowy na jednej działce – 30%") == {"max_building_coverage_percent": [30.0]}
    assert by_parameter("maksymalna powierzchnia zabudowy wynosi dwie") == {}  # liczebnik bez kondygnacji to nie procent


def test_thousand_separator_and_unit_variants() -> None:
    assert values("zakaz obiektów handlowych o powierzchni sprzedaży 2 000 m²", "max_retail_sales_area_m2") == [2000.0]
    assert values("wysokość zabudowy nie wyższa niż 16 metrów;", "max_building_height_m") == [16.0]
    assert values("nie może być większa niż 1,20m;\nwysokość budynków: 9 m", "max_building_height_m") == [9.0]
    assert values("minimalną powierzchnię nowo wydzielonej działki budowlanej – 1000 m2.", "min_plot_area_m2") == [1000.0]
    assert values("minimalna powierzchnia działek - 0,1 ha", "min_plot_area_m2") == [1000.0]


def test_statutory_minimum_building_coverage_share() -> None:
    # Czwarty parametr „ustawowy” (ADR-013): minimalny udział powierzchni zabudowy, operator „min”.
    assert values("minimalny udział powierzchni zabudowy – 20%", "min_building_coverage_percent") == [20.0]
    assert values("minimalny wskaźnik zabudowy 10%", "min_building_coverage_percent") == [10.0]
    assert by_parameter("maksymalna powierzchnia zabudowy 30%") == {"max_building_coverage_percent": [30.0]}


# --- wykluczenia --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "ustala się wysokość obiektów małej architektury – nie wyżej niż 3 m;",
        "ustala się wysokość pozostałych obiektów budowlanych - nie wyżej niż 12 m;",
        "maksymalna wysokość obiektów budowlanych infrastruktury technicznej 9 m",
        "maksymalna wysokość urządzeń sportu i rekreacji – 5 m",
        "wysokość górnej krawędzi elewacji budynku, jej gzymsu, attyki lub okapu budynków usługowych: nieprzekraczającą 11,0 m,",
        "wysokość poziomu parteru budynku mierzona od poziomu terenu nie może być większa niż 1,20m;",
        "maksymalną wysokość budowli nie wyższe niż 16,0 m,",
        "ustala się poziom posadowienia parteru: nie wyższy niż 0,5 m od poziomu terenu",
        "zmianie wysokości gruntu max. do 10 m (w formie wykopu lub nasypu)",
        "maksymalna szerokość elewacji frontowych budynków - 100,0 m,",
        "wysokość zabudowy mierzona w pasie o szerokości 15 m",
    ],
)
def test_heights_of_things_other_than_buildings_are_never_returned(text: str) -> None:
    assert values(text, "max_building_height_m") == []


def test_excluded_noun_swallows_the_number_instead_of_handing_it_to_the_previous_noun() -> None:
    table = (
        "maksymalna wysokość budynku funkcji usługowej 16 m\n"
        "maksymalna wysokość obiektów budowlanych infrastruktury technicznej 9 m\n"
        "maksymalna wysokość wolnostojących urządzeń i instalacji oze 3 m"
    )
    extraction = extract_quantities(table)
    assert [m.value for m in extraction.matches] == [16.0]
    assert [d.reason for d in extraction.dropped] == ["excluded_noun", "excluded_noun"]
    nouns = find_nouns(mask_text(table), 0, len(table))
    assert [n.family for n in nouns] == [FAMILY_HEIGHT, FAMILY_IGNORE, FAMILY_IGNORE]


def test_location_distance_is_not_a_setback_and_does_not_leak_into_height() -> None:
    text = "maksymalna wysokość zabudowy w odległości do 4,0 m od tej granicy wynosi 11,0 m,"
    extraction = extract_quantities(text)
    assert [(m.parameter, m.value) for m in extraction.matches] == [("max_building_height_m", 11.0)]
    assert [d.reason for d in extraction.dropped] == ["location_distance"]


def test_numbers_of_other_quantities_are_not_bound() -> None:
    assert by_parameter("na każdy lokal mieszkalny należy zapewnić nie mniej niż 650 m2 powierzchni działki") == {}
    assert by_parameter("lokalizację urządzeń o mocy przekraczającej 500 kW, pozyskiwanej z energii słonecznej,") == {}
    assert by_parameter("dopuszcza się przekrycie do 20% powierzchni dachów w inny sposób") == {}
    assert by_parameter("5. Dla terenów ustala się stawkę procentową opłaty z tytułu wzrostu wartości – 30 %") == {}
    assert by_parameter("Nr 91 — 9891 — Poz. 2276. 2) Na terenie ustala się") == {}


# --- ramki: odsunięcie i parkowanie -------------------------------------------------------


def test_setback_frames() -> None:
    cases = {
        "nieprzekraczalna linia zabudowy w odległości 6 m od granicy pasa drogowego": 6.0,
        "sytuowanie budynku pomocniczego w odległości 1,5 m od granicy działki": 1.5,
        "nieprzekraczalna linia zabudowy - 10,0 m od granicy jednostki z drogą powiatową": 10.0,
        "nieprzekraczalna linia zabudowy w odległości 6,0 m od linii rozgraniczających drogi": 6.0,
        "linię ustala się w odległości nie mniejszej niż 6 m od granicy działki.": 6.0,
    }
    for text, expected in cases.items():
        assert values(text, "setback_m") == [expected], text
    assert by_parameter("w odległości 4 m od ulicy") == {}  # nie granica ani linia


def test_parking_frame() -> None:
    matches = find_quantities("Należy zapewnić 2 miejsca parkingowe na lokal.")
    assert [(m.parameter, m.value, m.unit, m.raw_value) for m in matches] == [
        ("parking_minimum", 2.0, "miejsca/lokal", "2 miejsca parkingowe na lokal")
    ]
    assert matches[0].per_text == "lokal"
    words = find_quantities("co najmniej dwa miejsca postojowe na 1 lokal mieszkalny oraz 1 miejsce na każde 25 m2")
    assert [m.value for m in words if m.parameter == "parking_minimum"][0] == 2.0
    assert "number_word" in words[0].flags


def test_value_first_biologically_active_share() -> None:
    assert values("Należy zapewnić min. 30% powierzchni działki biologicznie czynnej.", "min_biologically_active_percent") == [30.0]
    assert values("co najmniej 25% powierzchni biologicznie czynnej", "min_biologically_active_percent") == [25.0]
    assert values("do 30% powierzchni biologicznie czynnej", "min_biologically_active_percent") == []


# --- zakres strefy ------------------------------------------------------------------------

_LODZ = (
    "a)\nwskaźnik powierzchni zabudowy:\n-\nw terenie 6.6.MW/U - maksimum 55%,\n"
    "-\nw terenie 4.2.MW/U - maksimum 45%,\n-\nw terenach 6.5.MW/U i 6.8.MW/U - maksimum 40%,\n"
    "b)\nwskaźnik powierzchni biologicznie czynnej:\n"
    "-\nw terenach: 1.10.MW/U, 5.3.MW/U, 6.4.MW/U i 6.8MW/U - minimum 10%,\n"
    "-\nw pozostałych terenach - minimum 20%,\n"
)


@pytest.mark.parametrize(
    ("symbol", "coverage", "biologically_active"),
    [
        ("6.6.MW/U", [55.0], [20.0]),
        ("4.2.MW/U", [45.0], [20.0]),
        ("6.8.MW/U", [40.0], [10.0]),  # także literówka ``6.8MW/U`` w tekście (brak kropki)
    ],
)
def test_values_are_restricted_to_the_zone_they_name(symbol: str, coverage: list[float], biologically_active: list[float]) -> None:
    found = by_parameter(_LODZ, target_symbol=symbol)
    assert found["max_building_coverage_percent"] == coverage
    assert found["min_biologically_active_percent"] == biologically_active


def test_without_a_target_every_clause_is_kept_with_its_scope_symbols() -> None:
    matches = [m for m in find_quantities(_LODZ) if m.parameter == "max_building_coverage_percent"]
    assert [m.value for m in matches] == [55.0, 45.0, 40.0]
    assert [m.scope_symbols for m in matches] == [("6.6.MW/U",), ("4.2.MW/U",), ("6.5.MW/U", "6.8.MW/U")]
    assert {m.scope_kind for m in matches} == {"restrict"}


def test_dropped_values_are_reported_with_the_reason() -> None:
    extraction = extract_quantities(_LODZ, target_symbol="6.6.MW/U")
    assert {d.reason for d in extraction.dropped} == {"other_zone"}
    assert len(extraction.dropped) >= 3


def test_list_items_named_by_symbol_under_a_symbols_header() -> None:
    text = (
        "4) ustala się maksymalny wskaźnik powierzchni zabudowy, dla terenów oznaczonych na rysunku planu symbolami:\n"
        "a) MN – 60 % powierzchni działki budowlanej,\n"
        "b) US – 50 % powierzchni działki budowlanej,\n"
        "c) US/WS – 20 % powierzchni działki budowlanej;"
    )
    assert values(text, "max_building_coverage_percent", target_symbol="MN") == [60.0]
    assert values(text, "max_building_coverage_percent", target_symbol="US") == [50.0]
    assert values(text, "max_building_coverage_percent", target_symbol="US/WS") == [20.0]
    # Bez nagłówka z symbolami ``MN –`` to definicja terenu, nie zakres wartości.
    assert extract_quantities("a) MN – tereny zabudowy 30 %").matches == []


def test_scope_comparison_tolerates_ocr_confusions_and_ranges_of_symbols() -> None:
    text = "1) dla terenu A1MN:\na) maksymalna wysokość zabudowy: 10 m,"
    assert values(text, "max_building_height_m", target_symbol="AIMN") == [10.0]  # I zamiast 1
    assert values(text, "max_building_height_m", target_symbol="A2MN") == []
    ranged = "Dla terenów MN.1 – MN.11 maksymalna wysokość zabudowy: 11 m"
    assert values(ranged, "max_building_height_m", target_symbol="MN.5") == [11.0]  # przedział symboli: nie odrzucamy


def test_subzones_are_conditions_not_zone_scopes() -> None:
    text = "a) wysokość zabudowy:\n- w strefie SWZ 22 oznaczonej na rysunku planu - maksimum 22,0 m,"
    assert values(text, "max_building_height_m", target_symbol="6.6.MW/U") == [22.0]


# --- zapis, ślad i determinizm --------------------------------------------------------------


def test_every_match_points_at_its_own_text_and_carries_the_trace() -> None:
    text = (
        "1) ustala się:\na) maksymalną wysokość zabudowy: 11 m, a dla budynków przekrytych dachem\npłaskim: 9,5 m;\n"
        "b) wskaźnik intensywności zabudowy: 0,01 – 0,5,\nc) powierzchnię biologicznie czynną – minimum 60%."
    )
    matches = find_quantities(text)
    assert matches
    for match in matches:
        assert " ".join(text[match.start : match.end].split()) == match.raw_value
        assert match.parameter in PARAMETER_ORDER and match.strategy
        assert match.clause_start <= match.start and match.end <= match.clause_end
        assert 0.0 < match.confidence <= 0.85
    heights = [m for m in matches if m.parameter == "max_building_height_m"]
    assert [m.value for m in heights] == [11.0, 9.5]
    assert heights[1].qualifiers and any(q.role == "pre" for q in heights[1].qualifiers)
    pre = next(q for q in heights[1].qualifiers if q.role == "pre")
    assert "dla budynków przekrytych dachem" in text[pre.start : pre.end]


def test_qualifiers_are_split_between_neighbouring_values() -> None:
    text = (
        "f) wysokość zabudowy: nie większą niż 8,0 m w przypadku budynków z dachem płaskim lub 10,0 m "
        "w przypadku budynków z dachem stromym,"
    )
    flat, steep = find_quantities(text)
    flat_post = [text[q.start : q.end] for q in flat.qualifiers if q.role == "post"]
    steep_post = [text[q.start : q.end] for q in steep.qualifiers if q.role == "post"]
    assert flat_post == ["w przypadku budynków z dachem płaskim"]
    assert steep_post == ["w przypadku budynków z dachem stromym"]
    assert [q for q in steep.qualifiers if q.role == "pre"] == []


def test_header_spans_of_inherited_clauses_are_reported() -> None:
    text = "6) ustala się następujące gabaryty dla budynków gospodarczych i garażowych:\na) wysokość zabudowy – nie wyżej niż 6,0 m,"
    (match,) = find_quantities(text)
    headers = [text[q.start : q.end] for q in match.qualifiers if q.role == "header"]
    assert headers and "dla budynków gospodarczych i garażowych" in headers[0]


def test_extraction_is_deterministic_and_pure() -> None:
    text = _LODZ + "\nwysokość zabudowy: 12 m, a dla dachu płaskiego: 9 m"
    first = extract_quantities(text, target_symbol="6.6.MW/U")
    second = extract_quantities(text, target_symbol="6.6.MW/U")
    assert first == second
    assert extract_quantities("").matches == [] and extract_quantities("   \n  ").matches == []
    assert ENGINE_VERSION.endswith(LEXICON_VERSION)


def test_families_filter_and_retail_threshold() -> None:
    text = "Zakaz lokalizacji obiektów handlowych o powierzchni sprzedaży powyżej 2000 m². Wysokość zabudowy do 12 m."
    only_retail = find_quantities(text, families=[FAMILY_RETAIL])
    assert [(m.parameter, m.value) for m in only_retail] == [("max_retail_sales_area_m2", 2000.0)]
    assert {m.parameter for m in find_quantities(text)} == {"max_retail_sales_area_m2", "max_building_height_m"}
    assert values("obiektów handlowych o powierzchni sprzedaży nie większej niż 200 m2", "max_retail_sales_area_m2") == [200.0]


def test_sentence_barrier_stops_binding_but_abbreviations_do_not() -> None:
    assert by_parameter("Wysokość zabudowy ustala się w planie. Dla działek 12 m ma znaczenie inne.") == {}
    assert values("maksymalna wysokość zabudowy ok. 12 m", "max_building_height_m") == [12.0]
    approximate = find_quantities("maksymalna wysokość zabudowy ok. 12 m")
    assert "approximate" in approximate[0].flags


def test_atoms_report_units_and_number_words() -> None:
    masked = mask_text("do 4 kondygnacji, 30°, 2 000 m2, 0,5 (50%), dwie kondygnacje")
    atoms = find_atoms(masked, 0, len(masked))
    kinds = [(a.unit_kind, a.numbers) for a in atoms]
    assert ("storey", (4.0,)) in kinds and ("deg", (30.0,)) in kinds and ("m2", (2000.0,)) in kinds
    assert any(a.parenthesized_percent == 50.0 for a in atoms)
    assert any(a.number_word and a.numbers == (2.0,) for a in atoms)


# --- sformułowania spoza korpusu (synthetic): próba uogólnienia, nie pomiar -------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Maksymalna wysokość zabudowy – 12 m.", {"max_building_height_m": [12.0]}),
        ("Wysokość zabudowy nie może przekraczać 12 metrów.", {"max_building_height_m": [12.0]}),
        ("maksymalna wysokość zabudowy mieszkaniowej: 9 m, zabudowy usługowej: 12 m", {"max_building_height_m": [9.0, 12.0]}),
        ("intensywność zabudowy od 0,3 do 1,2", {"min_intensity": [0.3], "max_intensity": [1.2]}),
        (
            "nieprzekraczalna linia zabudowy w odległości 5 m od linii rozgraniczającej ulicy",
            {"setback_m": [5.0]},
        ),
        ("minimalny udział powierzchni biologicznie czynnej – 30%", {"min_biologically_active_percent": [30.0]}),
        ("powierzchnia biologicznie czynna min. 25% powierzchni działki", {"min_biologically_active_percent": [25.0]}),
        ("kąt nachylenia połaci dachowych 25° - 45°", {"roof_angle_min_deg": [25.0], "roof_angle_max_deg": [45.0]}),
        ("maksymalna liczba kondygnacji nadziemnych: 3", {"max_storeys": [3.0]}),
        ("maksymalny wskaźnik zabudowy działki: 40%", {"max_building_coverage_percent": [40.0]}),
        ("wysokość zabudowy mierzona od poziomu terenu do kalenicy: max. 11 m", {"max_building_height_m": [11.0]}),
        ("powierzchnia zabudowy nie może przekraczać 30% powierzchni działki", {"max_building_coverage_percent": [30.0]}),
        ("min. 2 miejsca postojowe na 1 lokal mieszkalny", {"parking_minimum": [2.0]}),
        (
            "wysokość zabudowy do 3 kondygnacji nadziemnych i nie więcej niż 12 m",
            {"max_building_height_m": [12.0], "max_storeys": [3.0]},
        ),
        ("współczynnik intensywności zabudowy: max. 1,2", {"max_intensity": [1.2]}),
        ("dachy dwuspadowe o kącie nachylenia 35-45 stopni", {"roof_angle_min_deg": [35.0], "roof_angle_max_deg": [45.0]}),
        # nic nie jest zgadywane: bez rzeczownika albo bez zgodnej jednostki wartość nie powstaje
        ("Dla działek o powierzchni 500 m2 ustala się 12 m.", {}),
        ("obowiązuje zakaz lokalizacji reklam o wysokości 6 m", {}),
        ("wysokość zabudowy ustala się indywidualnie", {}),
    ],
)
def test_synthetic_wordings_outside_the_corpus(text: str, expected: dict[str, list[float]]) -> None:
    assert by_parameter(text) == expected
