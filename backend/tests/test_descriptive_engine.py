"""Jeden silnik zapisów opisowych (PV3-21): przypadki brzegowe i pilnowanie, że nie ma drugiego zestawu wzorców."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.modules.planning.domain import descriptive_engine as engine

APP = Path(__file__).resolve().parents[1] / "app"


def _values(findings: list[engine.DescriptiveFinding], code: str | None = None) -> list[str]:
    return [f.value for f in findings if code is None or f.code == code]


def test_labelled_use_designations_inline_and_as_a_list() -> None:
    text = (
        "Przeznaczenie podstawowe: zabudowa mieszkaniowa jednorodzinna.\n"
        "2. W zakresie przeznaczenia terenu ustala się:\n"
        "1)\nprzeznaczenie uzupełniające:\na)\ngaraże wielostanowiskowe,\nb)\ndrogi wewnętrzne;\n"
        "2)\ninne ustalenia."
    )
    findings = engine.find_use_designations(text)
    assert _values(findings, "primary_use") == ["zabudowa mieszkaniowa jednorodzinna"]
    assert _values(findings, "supplementary_use") == ["garaże wielostanowiskowe", "drogi wewnętrzne"]
    for finding in findings:  # cytat jest dosłownym fragmentem tekstu (po normalizacji białych znaków)
        assert finding.quote in " ".join(text.split())
        assert finding.value in " ".join(text[finding.start : finding.end].split())


def test_purpose_section_items_and_the_unstructured_fallback() -> None:
    structured = engine.find_use_designations(
        "przeznaczenie i zasady zagospodarowania terenu:\na) zabudowa usługowa,\nb) dopuszczenie funkcji zieleni;\n2) x"
    )
    assert [(f.code, f.value, f.structured) for f in structured] == [
        ("primary_use", "zabudowa usługowa", True),
        ("supplementary_use", "dopuszczenie funkcji zieleni", True),
    ]
    (fallback,) = engine.find_use_designations("przeznaczenie i zasady zagospodarowania terenu: usługi i zieleń\n3) x")
    assert (fallback.code, fallback.value, fallback.structured) == ("primary_use", "usługi i zieleń", False)


def test_the_same_designation_in_both_forms_is_one_finding() -> None:
    text = (
        "przeznaczenie i zasady zagospodarowania terenu:\na) zabudowa usługowa,\nb) dopuszczenie funkcji zieleni;\n"
        "Przeznaczenie podstawowe: zabudowa usługowa."
    )
    assert _values(engine.find_use_designations(text), "primary_use") == ["zabudowa usługowa"]


@pytest.mark.parametrize(
    "text",
    [
        "przeznaczenie podstawowe - przeznaczenie, które przeważa na danej działce,",
        "przeznaczenie uzupełniające – należy przez to rozumieć przeznaczenie inne niż podstawowe;",
        "przeznaczenie podstawowe:\na)\nprzeznaczenie, które przeważa,",
        "przeznaczenie podstawowe: a)",
    ],
)
def test_glossary_definitions_and_bare_list_marks_are_not_designations(text: str) -> None:
    assert engine.find_use_designations(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("zakaz zabudowy, zakaz grodzenia.", ["zakaz zabudowy", "zakaz grodzenia"]),
        ("zakaz lokalizacji zabudowy w odległości mniejszej niż 18,0 m od linii;",
         ["zakaz lokalizacji zabudowy w odległości mniejszej niż 18,0 m od linii"]),
        ("Zakaz lokalizacji urządzeń o mocy\nprzekraczającej 100 kW;", ["Zakaz lokalizacji urządzeń o mocy przekraczającej 100 kW"]),
        ("zakaz lokalizacji: zakładu poprawczego, domu dziecka", ["zakaz lokalizacji: zakładu poprawczego"]),
        ("zakaz lokalizacji:\n- obiektów handlowych,", []),  # wprowadzenie listy bez treści
        ("zakaz zabudowy\n§ 11. Ustala się", []),  # następny paragraf nie jest ciągiem zakazu
        ("zakaz zabudowy\na) w pasie", []),
        ("zakaz zabudowy", ["zakaz zabudowy"]),
    ],
)
def test_prohibition_boundaries(text: str, expected: list[str]) -> None:
    assert _values(engine.find_prohibitions(text)) == expected


def test_environmental_restrictions_respect_the_heading_policy() -> None:
    text = "Nakaz ochrony istniejących drzew. Dopuszczalny poziom hałasu jak dla zabudowy mieszkaniowej;"
    assert engine.find_environmental_restrictions(text, heading_required=True) == []
    assert _values(engine.find_environmental_restrictions(text, heading_required=False)) == [
        "Nakaz ochrony istniejących drzew",
        "Dopuszczalny poziom hałasu jak dla zabudowy mieszkaniowej",
    ]
    with_heading = "W zakresie ochrony środowiska: " + text
    assert len(engine.find_environmental_restrictions(with_heading, heading_required=True)) == 2
    assert _values(engine.find_environmental_restrictions("ograniczenie środowiskowe dla terenu ZP.", heading_required=False)) == [
        "ograniczenie środowiskowe dla terenu ZP"
    ]


def test_permissions_parking_and_roofs() -> None:
    assert _values(engine.find_permissions("dopuszczenie lokalizacji urządzeń infrastruktury technicznej.")) == [
        "dopuszczenie lokalizacji urządzeń infrastruktury technicznej"
    ]
    assert _values(engine.find_parking_requirements(
        "nakaz lokalizacji miejsc przeznaczonych na parkowanie pojazdów w granicach działki;"
    )) == ["nakaz lokalizacji miejsc przeznaczonych na parkowanie pojazdów w granicach działki"]
    roofs = engine.find_roof_geometries("Dachy dwuspadowe lub wielospadowe. Dachy płaskie. Dach płaski.")
    assert [(r.value, r.quote) for r in roofs] == [
        ("dwuspadowy_lub_wielospadowy", "Dachy dwuspadowe lub wielospadowe"),
        ("płaski", "Dachy płaskie"),
    ]


def test_no_finding_is_an_empty_list_not_a_default_value() -> None:
    text = "§ 1. Uchwala się plan."
    assert engine.find_use_designations(text) == engine.find_prohibitions(text) == []
    assert engine.find_roof_geometries(text) == engine.find_parking_requirements(text) == []


# --- jeden silnik: wzorce ustaleń tylko w domenie ------------------------------------------

# Słownictwo ustaleń planu. Wzorzec z takim słowem poza silnikami domenowymi oznaczałby drugi silnik.
_VOCABULARY = re.compile(
    r"zakaz|nakaz|przeznacz|dopuszcz|wysoko|kondygnac|intensywn|dach|połac|parkow|parking|biologicz|"
    r"powierzchni|odległ|hałas|ochron",
    re.IGNORECASE,
)
# Dawne miejsca drugiego silnika: parser MPZP (warstwa usług) i reguły planistyczne.
_FORMER_ENGINE_FILES = sorted((APP / "services").glob("mpzp_parser*.py")) + [
    APP / "modules" / "planning" / "domain" / "rules.py"
]


def _regex_literals(path: Path) -> list[tuple[int, str]]:
    """Literały przekazane do ``re.compile``/``re.search``/… w pliku (pierwszy argument)."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re"
            and node.args
        ):
            pieces = [n.value for n in ast.walk(node.args[0]) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            found.append((node.lineno, "".join(pieces)))
    return found


def test_value_extraction_patterns_live_only_in_the_domain_engines() -> None:
    assert len(_FORMER_ENGINE_FILES) >= 10  # wszystkie moduły parsera + reguły
    offenders = [
        f"{path.relative_to(APP)}:{line}: {pattern[:60]}"
        for path in _FORMER_ENGINE_FILES
        for line, pattern in _regex_literals(path)
        if _VOCABULARY.search(pattern)
    ]
    assert offenders == [], "Wzorce ustaleń planu należą do quantity_engine/descriptive_engine (PV3-21)"


def test_the_vocabulary_guard_detects_a_reintroduced_pattern(tmp_path: Path) -> None:
    sample = tmp_path / "mpzp_parser_sample.py"
    sample.write_text('import re\nP = re.compile(r"zakaz\\s+[^,;.\\n]+")\nQ = re.compile(r"Rozdział\\s+\\d+")\n', encoding="utf-8")
    flagged = [pattern for _, pattern in _regex_literals(sample) if _VOCABULARY.search(pattern)]
    assert flagged == [r"zakaz\s+[^,;.\n]+"]
