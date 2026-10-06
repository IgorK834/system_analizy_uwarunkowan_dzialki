"""Jeden silnik zapisów opisowych (nieliczbowych) ustaleń planu (PV3-21).

Do PV3-21 w repozytorium były dwa zestawy wzorców tych samych ustaleń: parser MPZP
(``services/mpzp_parser_descriptive.py``) i reguły planistyczne jednostek prawnych
(``planning/domain/rules.py``) — z różnymi granicami zakazu, różnym zapisem przeznaczenia i różnym
słownictwem dachów. Teraz oba wejścia wołają TEN moduł (a wartości liczbowe — ``quantity_engine``),
więc ten sam tekst daje te same ustalenia niezależnie od ścieżki, a poprawka sformułowania jest robiona
raz.

Silnik zwraca DOSŁOWNE fragmenty (``quote``) z zakresem znaków w przekazanym tekście; nie streszcza i
nie parafrazuje. ``value`` to zapis znormalizowany wyłącznie białymi znakami (albo forma kanoniczna
słownika dachów). Brak ustalenia to pusta lista, nigdy wartość domyślna.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

from app.modules.planning.domain.quantity_lexicon import find_roof_geometry

DESCRIPTIVE_ENGINE_VERSION: Final[str] = "descriptive/1"

FindingCode = Literal[
    "primary_use",
    "supplementary_use",
    "prohibition",
    "permission",
    "environmental_restriction",
    "parking_requirement_descriptive",
    "roof_geometry",
]

# --- przeznaczenie terenu ---------------------------------------------------------------

# Zapis z etykietą: ``Przeznaczenie podstawowe: zabudowa mieszkaniowa`` albo etykieta z listą
# (``przeznaczenie podstawowe:\na) tereny zabudowy…,\nb) …``).
_PRIMARY_USE_LABEL: Final[re.Pattern[str]] = re.compile(r"przeznaczeni\w*\s+podstawow\w*\s*[:–-]\s*", re.IGNORECASE)
_SUPPLEMENTARY_USE_LABEL: Final[re.Pattern[str]] = re.compile(
    r"przeznaczeni\w*\s+(?:uzupełniając\w*|dopuszczaln\w*)\s*[:–-]\s*", re.IGNORECASE
)
_INLINE_VALUE: Final[re.Pattern[str]] = re.compile(r"[^.;\n]+")
_LIST_START: Final[re.Pattern[str]] = re.compile(r"a\)\s")
_LONE_LIST_MARK: Final[re.Pattern[str]] = re.compile(r"[a-z]\)")
# Koniec listy po etykiecie: następny punkt ``N)``, ustęp ``N.`` albo paragraf.
_LIST_END: Final[re.Pattern[str]] = re.compile(r"\n(?:\d+[).]\s|§)")
# Słowniczek uchwały definiuje pojęcie przez nie samo (``przeznaczenie podstawowe - przeznaczenie, które
# przeważa…``) — to nie jest ustalenie dla terenu.
_DEFINITION_VALUE: Final[re.Pattern[str]] = re.compile(r"przeznaczeni\w*\b|należy\s+(?:przez\s+to\s+)?rozumieć", re.IGNORECASE)
# Zapis sekcji: ``przeznaczenie i zasady zagospodarowania terenu: a) zabudowa…, b) dopuszczenie funkcji…``.
_PURPOSE_SECTION_ANCHOR: Final[re.Pattern[str]] = re.compile(
    r"przeznaczeni\w*\s+i\s+zasad\w*\s+zagospodarowania\s+terenu\b", re.IGNORECASE
)
# Granica sekcji przeznaczenia to następny punkt numerowany "N)" na początku wiersza — sekcje uchwały
# są numerowane sekwencyjnie na tym samym poziomie.
_NEXT_NUMBERED_ITEM: Final[re.Pattern[str]] = re.compile(r"\n\d+\)\s")
_LETTERED_ITEM: Final[re.Pattern[str]] = re.compile(r"(?:^|\n)([a-z])\)\s*(.*?)(?=\n[a-z]\)|\Z)", re.DOTALL)
_SUPPLEMENTARY_ITEM: Final[re.Pattern[str]] = re.compile(r"dopuszczeni\w*\s+funkcj\w*", re.IGNORECASE)
_PRIMARY_ITEM: Final[re.Pattern[str]] = re.compile(r"zabudowa\b", re.IGNORECASE)

# --- zakazy, dopuszczenia, ochrona środowiska, parkowanie --------------------------------

# Zakaz kończy się na pierwszym przecinku, średniku albo kropce: kolejne zakazy w jednym zdaniu
# (``zakaz zabudowy, zakaz grodzenia``) są osobnymi ustaleniami. Wiersz przełamany przez ekstrakcję PDF
# (``zakaz lokalizacji urządzeń … o mocy\nprzekraczającej 100 kW;``) należy do tego samego zakazu, ale
# wprowadzenie listy (``zakaz lokalizacji:``) bez treści w tym samym wierszu nie jest ustaleniem, a wiersz
# zaczynający się punktorem albo oznaczeniem punktu to już następna pozycja, nie ciąg zakazu.
# Przecinek i kropka między cyframi (``18,0 m``, ``1.5``) są częścią liczby, nie końcem zakazu.
_NUMBER_SEPARATOR: Final[str] = r"(?<=\d)[,.](?=\d)"
_PROHIBITION: Final[re.Pattern[str]] = re.compile(
    rf"zakaz\s+(?:[^,;.:\n]|{_NUMBER_SEPARATOR})+"  # treść do dwukropka
    rf"(?::(?:[^,;.\n]|{_NUMBER_SEPARATOR})+)?"  # treść po dwukropku w tym samym wierszu
    rf"(?:\n(?![-–•§]|[a-z]\)|\d+[).])(?:[^,;.:\n]|{_NUMBER_SEPARATOR})+)*"  # wiersze przełamane
    r"(?:[,;.]|$)",
    re.IGNORECASE,
)
_PERMISSION: Final[re.Pattern[str]] = re.compile(r"dopuszczeni\w*\s+[^;.\n]+(?:[;.]|$)", re.IGNORECASE)
_ENVIRONMENT_HEADING: Final[re.Pattern[str]] = re.compile(r"ochron\w*\s+środowiska", re.IGNORECASE)
# Fragmenty realnego dokumentu bywają zawinięte przez ekstrakcję PDF w środku zdania (nowa linia
# zamiast spacji), więc ustalenie kończy dopiero kropka albo średnik.
_ENVIRONMENT_RESTRICTIONS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"nakaz\s+ochrony[^.;]*[.;]", re.IGNORECASE),
    re.compile(r"dopuszczalny\s+poziom\s+hałasu[^.;]*[.;]", re.IGNORECASE),
    re.compile(r"ograniczeni\w*\s+środowisk\w*[^.;]*[.;]", re.IGNORECASE),
)
_PARKING_DESCRIPTIVE: Final[re.Pattern[str]] = re.compile(
    r"nakaz\s+lokalizacji\s+miejsc\s+przeznaczonych\s+na\s+parkowanie[^.;:]*[.;:]", re.IGNORECASE
)
_TRIM: Final[str] = " ,;."


@dataclass(frozen=True)
class DescriptiveFinding:
    """Ustalenie opisowe: kod, wartość, dosłowny cytat i jego zakres w przekazanym tekście.

    ``structured=False`` oznacza przeznaczenie, którego nie dało się rozdzielić na podstawowe i
    uzupełniające (cały opis sekcji) — wejścia obniżają wtedy pewność i wymagają ręcznej weryfikacji.
    """

    code: FindingCode
    value: str
    quote: str
    start: int
    end: int
    structured: bool = True


def _squash(text: str) -> str:
    return " ".join(text.split())


def _literal(code: FindingCode, match: re.Match[str]) -> DescriptiveFinding:
    """Ustalenie, którego wartością jest cały dosłowny zapis (zakaz, dopuszczenie, nakaz…)."""
    value = _squash(match.group(0).strip(_TRIM))
    return DescriptiveFinding(code, value, value, match.start(), match.end())


def _purpose_section(text: str) -> tuple[int, str] | None:
    """Początek i treść sekcji „przeznaczenie i zasady zagospodarowania terenu” (do następnego punktu)."""
    anchor = _PURPOSE_SECTION_ANCHOR.search(text)
    if anchor is None:
        return None
    start = anchor.end()
    boundary = _NEXT_NUMBERED_ITEM.search(text, start)
    return start, text[start : boundary.start() if boundary is not None else len(text)]


def _labelled(code: FindingCode, text: str, label: re.Match[str]) -> list[DescriptiveFinding]:
    """Wartości po etykiecie przeznaczenia: lista podpunktów a)/b)… albo wartość w tym samym wierszu."""
    if _LIST_START.match(text, label.end()):
        boundary = _LIST_END.search(text, label.end())
        end = boundary.start() if boundary is not None else len(text)
        window = text[label.end() : end]
        found: list[DescriptiveFinding] = []
        for item in _LETTERED_ITEM.finditer(window):
            value = _squash(item.group(2).strip(_TRIM))
            if value and not _DEFINITION_VALUE.match(value):
                start = label.end() + item.start(2)
                found.append(DescriptiveFinding(code, value, value, start, label.end() + item.end(2)))
        return found
    inline = _INLINE_VALUE.match(text, label.end())
    if inline is None:
        return []
    value = _squash(inline.group(0))
    if not value or _DEFINITION_VALUE.match(value) or _LONE_LIST_MARK.fullmatch(value):
        return []
    return [DescriptiveFinding(code, value, _squash(text[label.start() : inline.end()]), label.start(), inline.end())]


def find_use_designations(text: str) -> list[DescriptiveFinding]:
    """Przeznaczenie podstawowe i uzupełniające — z etykiet i z podpunktów sekcji przeznaczenia.

    Gdy sekcja przeznaczenia istnieje, ale nie ma ani etykiet, ani podpunktów a)/b)/c), zwracany jest
    cały opis jako ``primary_use`` z ``structured=False`` (uczciwa degradacja, bez zgadywania granicy).
    """
    findings: list[DescriptiveFinding] = []
    for code, label in (("primary_use", _PRIMARY_USE_LABEL), ("supplementary_use", _SUPPLEMENTARY_USE_LABEL)):
        for match in label.finditer(text):
            findings.extend(_labelled(code, text, match))  # type: ignore[arg-type]
    section = _purpose_section(text)
    if section is None:
        return findings
    offset, window = section
    items = list(_LETTERED_ITEM.finditer(window))
    for item in items:
        item_text = _squash(item.group(2).strip(" ,;"))
        if not item_text:
            continue
        code: FindingCode | None = (
            "supplementary_use" if _SUPPLEMENTARY_ITEM.match(item_text)
            else "primary_use" if _PRIMARY_ITEM.match(item_text)
            else None
        )
        if code is not None:
            findings.append(DescriptiveFinding(code, item_text, item_text, offset + item.start(2), offset + item.end(2)))
    if not items and not findings:
        whole = _squash(window.strip(" :\n"))
        if whole:
            findings.append(
                DescriptiveFinding("primary_use", whole, whole, offset, offset + len(window), structured=False)
            )
    # To samo przeznaczenie zapisane w obu formach (podpunkt sekcji i etykieta) to jedno ustalenie;
    # zostaje pierwsze wystąpienie w tekście.
    unique: dict[tuple[str, str], DescriptiveFinding] = {}
    for finding in sorted(findings, key=lambda f: f.start):
        unique.setdefault((finding.code, finding.value.lower()), finding)
    return list(unique.values())


def find_prohibitions(text: str) -> list[DescriptiveFinding]:
    return [_literal("prohibition", match) for match in _PROHIBITION.finditer(text)]


def find_permissions(text: str) -> list[DescriptiveFinding]:
    return [_literal("permission", match) for match in _PERMISSION.finditer(text)]


def find_environmental_restrictions(text: str, *, heading_required: bool) -> list[DescriptiveFinding]:
    """Nakazy ochrony, dopuszczalny poziom hałasu i ograniczenia środowiskowe.

    ``heading_required`` — w tekście całego segmentu dokumentu słowa „nakaz” czy „dopuszczalny poziom”
    mogą dotyczyć innych paragrafów, więc parser MPZP wymaga kontekstu „ochrony środowiska”; jednostka
    prawna jest już wydzielonym zakresem, więc reguły planistyczne go nie wymagają.
    """
    if heading_required and _ENVIRONMENT_HEADING.search(text) is None:
        return []
    return [
        _literal("environmental_restriction", match)
        for pattern in _ENVIRONMENT_RESTRICTIONS
        for match in pattern.finditer(text)
    ]


def find_parking_requirements(text: str) -> list[DescriptiveFinding]:
    return [_literal("parking_requirement_descriptive", match) for match in _PARKING_DESCRIPTIVE.finditer(text)]


def find_roof_geometries(text: str) -> list[DescriptiveFinding]:
    """Rodzaje dachu w formie kanonicznej słownika leksykonu; ta sama kategoria — raz."""
    findings: list[DescriptiveFinding] = []
    seen: set[str] = set()
    for roof in find_roof_geometry(text):
        if roof.canonical in seen:
            continue
        seen.add(roof.canonical)
        findings.append(DescriptiveFinding("roof_geometry", roof.canonical, _squash(roof.raw), roof.start, roof.end))
    return findings
