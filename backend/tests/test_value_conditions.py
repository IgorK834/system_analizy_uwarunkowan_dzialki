"""Warunki wartości parametrów MPZP: wartość warunkowa to nie sprzeczność (PV3-08)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.modules.planning.domain.quantity_engine import Qualifier, find_quantities
from app.modules.planning.domain.value_conditions import (
    CONDITION_KINDS,
    CONDITIONS_VERSION,
    ValueCondition,
    classify_conditions,
    condition_key,
    source_span,
)
from scripts import evaluate_mpzp_parser as ev
from tests.parcel_fixtures_config import find_repo_root

FIXTURES = find_repo_root() / "backend/tests/fixtures/mpzp_evaluation"


def conditions_of(text: str, parameter: str = "max_building_height_m") -> list[list[tuple[str, str, str]]]:
    """Dla każdej wartości parametru: lista (rodzaj, etykieta, cytat)."""
    return [
        [(c.kind, c.label, c.quote) for c in classify_conditions(text, m.qualifiers)]
        for m in find_quantities(text)
        if m.parameter == parameter
    ]


# --- przypadki z uchwał: dach, rodzaj zabudowy, podstrefa, położenie, inne --------------------


def test_flat_roof_value_next_to_the_general_one() -> None:
    text = "d) maksymalną wysokość zabudowy: 11 m, a dla budynków przekrytych dachem\npłaskim: 9,5 m;"
    assert conditions_of(text) == [[], [("roof_type", "dach płaski", "dachem płaskim")]]


def test_flat_and_steep_roof_are_two_conditional_values_with_the_building_type_of_the_clause() -> None:
    text = (
        "f) wysokość zabudowy:\n"
        "- dla budynków mieszkalnych jednorodzinnych i usługowych nie większą niż 8,0 m w przypadku\n"
        "budynków z dachem płaskim lub 10,0 m w przypadku budynków z dachem stromym,"
    )
    flat, steep = conditions_of(text)
    building = ("building_type", "budynków mieszkalnych jednorodzinnych i usługowych", "budynków mieszkalnych jednorodzinnych i usługowych")
    assert flat == [building, ("roof_type", "dach płaski", "dachem płaskim")]
    # druga wartość dostaje rodzaj zabudowy z całej klauzuli, a dach — własny
    assert steep == [building, ("roof_type", "dach stromy", "dachem stromym")]


def test_building_types_in_list_items_with_an_inherited_noun() -> None:
    text = (
        "d) wysokość budynków:\n"
        "- z dachami symetrycznymi - maksimum 10 m,\n"
        "- gospodarczych i garaży - maksimum 5 m,\n"
        "- mieszkalnych jednorodzinnych z dachami płaskimi - maksimum 8 m,\n"
    )
    assert conditions_of(text) == [
        [("roof_type", "dach symetryczny", "dachami symetrycznymi")],
        [("building_type", "gospodarczych i garaży", "gospodarczych i garaży")],
        [
            ("building_type", "mieszkalnych jednorodzinnych", "mieszkalnych jednorodzinnych"),
            ("roof_type", "dach płaski", "dachami płaskimi"),
        ],
    ]


def test_building_type_from_the_header_of_the_parent_clause() -> None:
    text = (
        "6) ustala się następujące gabaryty, usytuowanie, kolorystykę i pokrycie dachu dla budynków gospodarczych i garażowych:\n"
        "a) wysokość zabudowy – nie wyżej niż 6,0 m,"
    )
    ((found,),) = conditions_of(text)
    assert found[0] == "building_type" and "gospodarczych i garażowych" in found[2]


def test_measurement_point_and_location_conditions() -> None:
    text = "- wysokość budynków od poziomu terenu do gzymsu lub attyki - do 12,0 m, do kalenicy dachu - do\n15,0 m,"
    assert conditions_of(text) == [
        [("other", "od poziomu terenu do gzymsu lub attyki", "od poziomu terenu do gzymsu lub attyki")],
        [("other", "do kalenicy dachu", "do kalenicy dachu")],
    ]
    near_boundary = "maksymalna wysokość zabudowy w odległości do 4,0 m od tej granicy wynosi 11,0 m,"
    assert conditions_of(near_boundary) == [[("location", "w odległości do 4,0 m od tej granicy", "w odległości do 4,0 m od tej granicy")]]


def test_post_qualifier_of_a_value_first_list_and_the_remaining_part_of_the_zone() -> None:
    text = (
        "b) maksymalna wysokość zabudowy:\n"
        "- 15 m dla zabudowy pierzejowej przy ul. gen. Sikorskiego w pasie o szerokości 15 m wzdłuż północnej granicy,\n"
        "- 13 m dla pozostałej części terenu,"
    )
    first, second = conditions_of(text)
    assert ("building_type", "zabudowy pierzejowej", "zabudowy pierzejowej") in first
    assert second == [("other", "pozostałej części terenu", "pozostałej części terenu")]


def test_subzone_conditions() -> None:
    text = (
        "e) wprowadza się strefy zwiększonego udziału terenu biologicznie czynnego oznaczone na rysunku planu, dla których ustala się:\n"
        "- udział terenu biologicznie czynnego w powierzchni strefy - minimum 60%,"
    )
    ((found,),) = [
        [(c.kind, c.label) for c in classify_conditions(text, m.qualifiers)]
        for m in find_quantities(text)
        if m.parameter == "min_biologically_active_percent"
    ]
    assert found == ("subzone", "strefa zwiększonego udziału terenu biologicznie czynnego")
    outside = "a) wysokość zabudowy:\n- dla zabudowy poza strefami oznaczonymi na rysunku planu:\n-- dla zabudowy frontowej - maksimum 18,0 m,"
    ((kinds,),) = [[[c.kind for c in classify_conditions(outside, m.qualifiers)]] for m in find_quantities(outside)]
    assert kinds == ["subzone", "building_type"]
    named = "a) wysokość zabudowy:\n- w strefie SWZ 22 oznaczonej na rysunku planu - maksimum 22,0 m,"
    assert conditions_of(named) == [[("subzone", "strefa SWZ 22", "strefie SWZ 22 oznaczonej na rysunku planu")]]


# --- czego NIE uznajemy za warunek ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "maksymalny wskaźnik powierzchni zabudowy w stosunku do powierzchni działki budowlanej – 0,50 (50%),",
        "a) parametry w granicach działki budowlanej:\n- wskaźnik powierzchni zabudowy - maksimum 30 %",
        "ustala się minimalny udział procentowy powierzchni biologicznie czynnej w odniesieniu do powierzchni działki: 60%, z zachowaniem wymogu §6 ust. 7,",
        "maksymalna powierzchnia zabudowy – 60% powierzchni działki budowlanej lub jej części położonej w terenie oznaczonym symbolem 230_U,",
        "o spadkach połaci do 30º, dla zabudowy frontowej - o kalenicy głównej położonej równolegle do linii zabudowy;",
        "intensywność zabudowy: minimum 0,5 i 2. dachy dwuspadowe o kącie nachylenia połaci do 45°",
    ],
)
def test_filler_and_other_requirements_are_not_conditions(text: str) -> None:
    for match in find_quantities(text):
        if match.parameter in {"max_building_coverage_percent", "min_biologically_active_percent", "roof_angle_max_deg", "min_intensity"}:
            conditions = classify_conditions(text, match.qualifiers)
            assert not any(c.kind in {"location", "building_type"} for c in conditions), (match.parameter, conditions)


def test_zone_scope_symbols_are_not_conditions() -> None:
    text = (
        "b)\nintensywność zabudowy:\n-\nw terenach 3.5.MW/U i 6.6.MW/U - minimum 0,5, maksimum 2,5,\n"
    )
    for match in find_quantities(text):
        assert classify_conditions(text, match.qualifiers) == ()


def test_unrelated_text_gives_no_condition_and_overlaps_are_resolved_by_priority() -> None:
    assert classify_conditions("abc", [Qualifier(0, 0, "pre"), Qualifier(2, 1, "pre")]) == ()
    # ``w przypadku budynków z dachem płaskim`` daje jeden, konkretniejszy warunek (dach), nie dwa nakładające się
    text = "w przypadku budynków z dachem płaskim"
    found = classify_conditions(text, [Qualifier(0, len(text), "post")])
    assert [(c.kind, c.label) for c in found] == [("roof_type", "dach płaski")]
    only_other = "w przypadku obiektów zabytkowych"
    found = classify_conditions(only_other, [Qualifier(0, len(only_other), "post")])
    assert [(c.kind, c.quote) for c in found] == [("other", "w przypadku obiektów zabytkowych")]


def test_clause_qualifier_does_not_override_the_values_own_condition_of_the_same_kind() -> None:
    text = "dla budynków usługowych: wysokość 12 m, a dla budynków mieszkalnych: 9 m"
    own = Qualifier(text.index("mieszkalnych"), text.index("mieszkalnych") + len("mieszkalnych"), "pre")
    clause = Qualifier(0, len("dla budynków usługowych"), "clause")
    found = classify_conditions(text, [own, clause])
    assert [(c.kind, c.label) for c in found] == [("building_type", "mieszkalnych")]
    only_clause = classify_conditions(text, [clause, Qualifier(0, 3, "clause")])
    assert [(c.kind, c.label) for c in only_clause] == [("building_type", "budynków usługowych")]


# --- klucz warunków, zakres dowodu, wersja ---------------------------------------------------------


def test_condition_key_ignores_order_quote_and_location() -> None:
    a = ValueCondition("roof_type", "dach płaski", "dachem płaskim", 3, 17, "pre")
    b = ValueCondition("building_type", "usługowe", "usługowych", 30, 40, "clause")
    c = ValueCondition("roof_type", "dach płaski", "dachami płaskimi", 90, 100, "post")
    assert condition_key([a, b]) == condition_key([c, b]) and condition_key([]) == frozenset()
    assert condition_key([a]) != condition_key([b])
    assert a.key == ("roof_type", "dach płaski")


def test_source_span_covers_the_quotes_of_the_selected_roles() -> None:
    a = ValueCondition("roof_type", "dach płaski", "dachem płaskim", 10, 20, "pre")
    b = ValueCondition("building_type", "usługowe", "usługowych", 2, 5, "header")
    assert source_span([a, b]) == (2, 20) and source_span([a, b], ["pre"]) == (10, 20) and source_span([a], ["header"]) is None
    assert source_span([]) is None
    assert set(CONDITION_KINDS) == {"building_type", "roof_type", "subzone", "location", "other"}
    assert CONDITIONS_VERSION.startswith("value-conditions/")


# --- korpus: warunki są czytelne i weryfikowalne w tekście źródłowym ------------------------------


@pytest.fixture(scope="module")
def corpus_parameters() -> list[Any]:
    import asyncio

    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    collected = []
    for sample in manifest["samples"]:
        loaded = ev.load_document(FIXTURES, manifest["documents"][sample["document_id"]])
        symbols = [zone["symbol"] for zone in sample["zones"]]
        result = asyncio.run(ev.run_parser(loaded, symbols, "blocks"))
        collected.extend((sample["sample_id"], loaded, p) for z in result.zones for p in z.parameters)
    return collected


def test_every_condition_has_a_kind_a_label_and_a_quote_found_in_the_source_text(corpus_parameters: list[Any]) -> None:
    conditional = [(sid, loaded, p) for sid, loaded, p in corpus_parameters if p.conditions]
    assert len(conditional) > 60
    for _sid, loaded, parameter in conditional:
        document = " ".join(" ".join(loaded["pages"]).split()).lower()
        assert parameter.value_kind in {"conditional", "conflict"}
        for condition in parameter.conditions:
            assert condition.kind in CONDITION_KINDS and condition.label.strip()
            # Cytat jest dosłowny (po normalizacji białych znaków i wielkości liter); część cytatów wielo-
            # wyrazowych leży w tekście z łamaniem wiersza, dlatego porównujemy tekst bez podziałów.
            assert " ".join(condition.quote.split()).lower() in document, condition


def test_annotated_conditional_values_get_conditions_in_the_corpus(corpus_parameters: list[Any]) -> None:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    by_key: dict[tuple[str, str, str], list[Any]] = {}
    for sid, _loaded, parameter in corpus_parameters:
        by_key.setdefault((sid, "*", parameter.name), []).append(parameter)
    hits = misses = 0
    for sample in manifest["samples"]:
        for zone in sample["zones"]:
            for annotation in zone["annotations"]:
                if (annotation.get("ambiguity") or {}).get("kind") != "conditional_value":
                    continue
                candidates = [
                    p for p in by_key.get((sample["sample_id"], "*", annotation["parameter"]), [])
                    if p.normalized_value == pytest.approx(annotation["normalized_value"])
                ]
                if not candidates:
                    continue
                if any(p.conditions for p in candidates):
                    hits += 1
                else:
                    misses += 1
    # 58 z 59 wartości oznaczonych w adnotacjach jako warunkowe dostaje warunek (jedyna: odsunięcie bez przymiotnika).
    assert hits >= 55 and misses <= 2
