"""Leksykon ilości: spójność danych z katalogiem BK-603 i słownikiem dachów (PV3-07)."""

from __future__ import annotations

import re

import pytest

from app.modules.planning.domain import quantity_lexicon as lexicon
from scripts import evaluate_mpzp_parser as ev

CATALOG_NAMES = set(ev.CATALOG)  # 9 parametrów katalogu BK-603
STATUTORY_NAMES = {
    "parking_minimum",
    "min_building_coverage_percent",
    "min_plot_area_m2",
    "max_retail_sales_area_m2",
}


def _all_names() -> set[str]:
    return {name for spec in lexicon.PARAMETER_FAMILIES.values() for name in spec.names.values() if name is not None}


def test_lexicon_covers_the_nine_catalog_parameters_and_four_statutory_ones() -> None:
    names = _all_names()
    assert CATALOG_NAMES <= names and len(CATALOG_NAMES) == 9
    assert STATUTORY_NAMES <= names
    assert names == CATALOG_NAMES | STATUTORY_NAMES
    statutory_families = {f for f, spec in lexicon.PARAMETER_FAMILIES.items() if spec.statutory}
    statutory_from_families = {n for f in statutory_families for n in lexicon.PARAMETER_FAMILIES[f].names.values() if n}
    assert statutory_from_families == {"parking_minimum", "min_plot_area_m2", "max_retail_sales_area_m2"}
    assert tuple(sorted(lexicon.PARAMETER_ORDER)) == tuple(sorted(names)) and len(set(lexicon.PARAMETER_ORDER)) == len(names)


def test_units_are_consistent_with_the_evaluation_catalog() -> None:
    unit_by_name = {n: spec.unit for spec in lexicon.PARAMETER_FAMILIES.values() for n in spec.names.values() if n}
    expected = {"m": "m", "percent": "percent", "deg": "deg", "ratio": None, "count": None}
    for name, info in ev.CATALOG.items():
        assert unit_by_name[name] == expected[info["unit"]], name


def test_every_family_resolves_every_operator_to_a_name_or_an_explicit_none() -> None:
    for family, spec in lexicon.PARAMETER_FAMILIES.items():
        assert spec.family == family and spec.default_operator in spec.names
        assert set(spec.names) == {lexicon.OP_MAX, lexicon.OP_MIN, lexicon.OP_EXACT} or family == lexicon.FAMILY_SETBACK
        assert spec.accepted_units
        for noun in spec.nouns:
            regex = re.compile(noun, re.IGNORECASE)
            assert regex.search("") is None  # rzeczownik nigdy nie dopasowuje pustego tekstu


@pytest.mark.parametrize(
    ("text", "family"),
    [
        ("wysokość zabudowy", lexicon.FAMILY_HEIGHT),
        ("wysokością budynków", lexicon.FAMILY_HEIGHT),
        ("liczbę kondygnacji", lexicon.FAMILY_STOREYS),
        ("wskaźnik powierzchni zabudowy", lexicon.FAMILY_COVERAGE),
        ("udział procentowy zabudowy", lexicon.FAMILY_COVERAGE),
        ("teren biologicznie czynny", lexicon.FAMILY_BIO),
        ("powierzchni biologicznie czynnej", lexicon.FAMILY_BIO),
        ("intensywność zabudowy", lexicon.FAMILY_INTENSITY),
        ("kącie nachylenia połaci", lexicon.FAMILY_ROOF),
        ("pochyleniu połaci", lexicon.FAMILY_ROOF),
        ("minimalną powierzchnię nowo wydzielonej działki", lexicon.FAMILY_PLOT),
        ("obiektów handlowych", lexicon.FAMILY_RETAIL),
    ],
)
def test_noun_phrases_belong_to_their_family(text: str, family: str) -> None:
    patterns = [re.compile(noun, re.IGNORECASE) for noun in lexicon.PARAMETER_FAMILIES[family].nouns]
    assert any(p.search(text) for p in patterns)


def test_height_noun_whitelist_excludes_other_objects() -> None:
    whitelist = [re.compile(n, re.IGNORECASE) for n in lexicon.PARAMETER_FAMILIES[lexicon.FAMILY_HEIGHT].nouns]
    for excluded in ("wysokość obiektów małej architektury", "wysokość budowli", "wysokość poziomu parteru budynku", "wysokość elewacji"):
        assert not any(p.search(excluded) for p in whitelist), excluded
    assert re.search(lexicon.HEIGHT_OTHER_NOUN, "wysokość obiektów małej architektury", re.IGNORECASE)


def test_operator_regexes_pick_the_right_direction() -> None:
    def operator(window: str) -> str | None:
        found = [op for op, regex in lexicon.OPERATOR_BEFORE if regex.search(window)]
        return found[0] if found else None

    assert operator("wysokość zabudowy nie wyżej jednak niż ") == lexicon.OP_MAX
    assert operator("powierzchnia nie mniejsza niż ") == lexicon.OP_MIN
    assert operator("powierzchnia biologicznie czynna nie może być mniejsza niż ") == lexicon.OP_MIN
    assert operator("zabudowa – minimum ") == lexicon.OP_MIN and operator("zabudowa, maksimum ") == lexicon.OP_MAX
    assert operator("co najmniej ") == lexicon.OP_MIN and operator("wynosi ") == lexicon.OP_EXACT
    assert operator("o wysokości większej niż ") == lexicon.OP_GT and operator("zwiększenie o ") == lexicon.OP_DELTA
    assert operator("po prostu ") is None


def test_unit_regexes_recognize_the_forms_used_in_acts() -> None:
    def kind(text: str) -> str | None:
        for name, regex in lexicon.UNIT_REGEXES:
            if regex.match(text):
                return name
        return None

    assert kind("%") == lexicon.UNIT_PERCENT and kind("procent") == lexicon.UNIT_PERCENT
    assert kind("m²") == lexicon.UNIT_AREA and kind("m2") == lexicon.UNIT_AREA and kind("m kw.") == lexicon.UNIT_AREA
    assert kind("m") == lexicon.UNIT_METER and kind("metrów") == lexicon.UNIT_METER
    assert kind("ha") == lexicon.UNIT_HECTARE
    assert kind("°") == lexicon.UNIT_DEGREE and kind("º") == lexicon.UNIT_DEGREE and kind("stopni") == lexicon.UNIT_DEGREE
    assert kind("kondygnacji") == lexicon.UNIT_STOREY and kind("miejsc") == lexicon.UNIT_PLACE
    assert kind("mb") is None and kind("mieszkaniowej") is None  # ``m`` nie jest przedrostkiem słowa


def test_flag_penalties_are_proper_multipliers() -> None:
    assert lexicon.FLAG_PENALTIES and all(0.0 < value <= 1.0 for value in lexicon.FLAG_PENALTIES.values())
    assert 0.0 < lexicon.BASE_CONFIDENCE < 1.0
    assert lexicon.FLAG_PENALTIES["degree_artifact"] < lexicon.FLAG_PENALTIES["degree_letter"] < 1.0


def test_roof_vocabulary_is_one_dictionary_for_every_consumer() -> None:
    assert lexicon.roof_type_for("płaskie") == "płaski" and lexicon.roof_type_for("DWUSPADOWA") == "dwuspadowy"
    assert lexicon.roof_type_for("stromym") == "stromy" and lexicon.roof_type_for("nieznane") == "nieznane"
    found = lexicon.find_roof_geometry("Dachy dwuspadowe lub wielospadowe oraz dach płaski; dachem stromym; dachy jednospadowe")
    assert [m.canonical for m in found] == ["dwuspadowy_lub_wielospadowy", "płaski", "stromy", "jednospadowy"]
    combined = lexicon.find_roof_geometry("dachy płaskie lub strome")
    assert combined[0].canonical == "płaski_lub_stromy"
    assert set(lexicon.ROOF_TYPE_LABELS) >= {canonical for _, canonical in lexicon.ROOF_TYPES}


def test_markers_and_noise_patterns() -> None:
    for text, group in [("§ 12.", "section"), ("3. Ustala", "paragraph"), ("1) a", "point"), ("a) a", "letter"), ("-- a", "bullet")]:
        match = lexicon.MARKER_PATTERN.match(text)
        assert match is not None and match.lastgroup == group, text
    assert lexicon.MARKER_PATTERN.match("wysokość") is None
    assert any(p.search("Dziennik Urzędowy Województwa Śląskiego – 5 – Poz. 1124") for p in lexicon.NOISE_PATTERNS)
    assert any(p.search("1.10.MW/U") for p in lexicon.NOISE_PATTERNS) and not any(p.search("30°") for p in lexicon.NOISE_PATTERNS)
