"""Warunki wartości parametrów MPZP: rozróżnienie wartości warunkowych od sprzeczności (PV3-08).

Uchwała podaje często kilka wartości tego samego parametru, każdą dla innego przypadku: inna
wysokość dla dachu płaskiego, dla budynków gospodarczych, w podstrefie albo w pasie przy
granicy. To NIE jest sprzeczność. Sprzeczność jest wtedy, gdy ten sam przypadek (albo brak
przypadku) ma dwie różne wartości.

Moduł zamienia rozpiętości tekstu obok wartości (``Qualifier`` z silnika ilości) na warunki
o stałej postaci: ``kind`` (``building_type``, ``roof_type``, ``subzone``, ``location``,
``other``), ``label`` (znormalizowana nazwa do wyświetlenia) i ``quote`` (dosłowny cytat z tekstu
uchwały). Rozpoznanie jest słownikowe i konserwatywne: tekst, którego żaden wzorzec nie rozpoznaje
(``w stosunku do powierzchni działki``), nie staje się warunkiem; lepszy brak warunku niż
wymyślony. Symbole stref (``w terenie 6.6.MW/U``) to zakres, nie warunek — rozstrzyga je silnik.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from app.modules.planning.domain.quantity_engine import Qualifier
from app.modules.planning.domain.quantity_lexicon import ROOF_TYPE_LABELS, find_roof_geometry

KIND_BUILDING_TYPE: Final[str] = "building_type"
KIND_ROOF_TYPE: Final[str] = "roof_type"
KIND_SUBZONE: Final[str] = "subzone"
KIND_LOCATION: Final[str] = "location"
KIND_OTHER: Final[str] = "other"
CONDITION_KINDS: Final[tuple[str, ...]] = (
    KIND_BUILDING_TYPE,
    KIND_ROOF_TYPE,
    KIND_SUBZONE,
    KIND_LOCATION,
    KIND_OTHER,
)

# Wersja słownika warunków; zmiana zmienia wynik parsera (wchodzi do kalibracji pewności, PV3-09).
CONDITIONS_VERSION: Final[str] = "value-conditions/1.0"

VALUE_KIND_UNCONDITIONAL: Final[str] = "unconditional"
VALUE_KIND_CONDITIONAL: Final[str] = "conditional"
VALUE_KIND_CONFLICT: Final[str] = "conflict"

# Kwalifikator całej klauzuli (``clause``) uzupełnia kolejne wartości tylko o te rodzaje warunków;
# dachu nie, bo każda wartość zwykle wskazuje własny dach (``8 m dla dachu płaskiego, 10 m dla stromego``).
_CLAUSE_INHERITED_KINDS: Final[frozenset[str]] = frozenset({KIND_BUILDING_TYPE, KIND_SUBZONE, KIND_LOCATION})

_NUM = r"\d+(?:[.,]\d+)?"
_BUILDING_WORD = (
    r"(?:mieszkal\w*|mieszkani\w*|usługow\w*|gospodarcz\w*|garaż\w*|pomocnicz\w*|produkcyjn\w*|"
    r"handlow\w*|jednorodzinn\w*|wielorodzinn\w*|pierzejow\w*|frontow\w*|magazyn\w*|biurow\w*|"
    r"szeregow\w*|bliźniacz\w*|wolnostoją\w*|letniskow\w*|oświat\w*|sportow\w*|rekreacyjn\w*|"
    r"wiat\w*|altan\w*|oficyn\w*|użyteczności\s+publicznej)"
)
_BUILDING_NOUN = r"(?:budynk\w*|zabudow\w*|obiekt\w*|garaż\w*|wiat\w*)"
_BUILDING = re.compile(
    rf"(?:{_BUILDING_NOUN}\s+)?{_BUILDING_WORD}"
    rf"(?:(?:\s*[-–]\s*|\s*,\s*|\s+)(?:(?:i|oraz|lub)\s+)?{_BUILDING_WORD})*",
    re.IGNORECASE,
)

_SUBZONE_OUTSIDE = re.compile(r"poza\s+stref\w+(?:\s+oznaczon\w+\s+na\s+rysunku\s+planu)?", re.IGNORECASE)
_SUBZONE_WORD = re.compile(r"\bstref\w*", re.IGNORECASE)
_SUBZONE_MARKED = re.compile(r"\s*oznaczon\w+\s+na\s+rysunku\s+planu", re.IGNORECASE)
_SUBZONE_BOUNDARY = re.compile(r"\s(?:dla|w|z|przy|do|od)\s|[,;:\n]|\s[-–—]\s", re.IGNORECASE)
_SUBZONE_MAX = 90

_LOCATION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        rf"w\s+odległości\s+(?:do|od|nie\s+(?:mniejszej|większej)\s+niż|co\s+najmniej)?\s*{_NUM}\s*m\s+od\s+[^,;:\n]{{1,60}}",
        r"od\s+strony\s+[^,;:\n]{1,50}",
        rf"w\s+pasie\s+(?:o\s+szerokości\s+{_NUM}\s*m\s+)?[^,;:\n]{{0,70}}",
        r"(?:bezpośrednio\s+)?przy\s+granicy[^,;:\n]{0,50}",
        # ``w granicach działki budowlanej`` to tylko odniesienie wskaźnika, nie warunek położenia.
        r"w\s+granicach\s+(?:strefy|zieleni|obszaru|pasa)[^,;:\n]{1,60}",
        r"wzdłuż\s+[^,;:\n]{1,60}",
        r"na\s+części\s+terenu[^,;:\n]{0,60}",
        r"położon\w+\s+[^,;:\n]{1,60}",
    )
)
_OTHER_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # Miara wysokości do innego punktu budynku niż w głównym ustaleniu.
        r"od\s+poziomu\s+terenu\s+do\s+[^,;:\n-]{1,40}",
        r"do\s+(?:kalenicy|gzymsu|attyki|okapu)\w*(?:\s+dachu)?",
        r"w\s+przypadku\s+[^,;:\n]{1,100}",
        # ``w pozostałych terenach`` to zakres (rozstrzyga go silnik po symbolach), nie warunek wartości.
        r"pozostał\w+\s+(?:części\s+terenu|zabudow\w+|budynk\w+|obiekt\w+)",
        r"(?:jeżeli|jeśli|gdy|o\s+ile)\s+[^,;:\n]{1,100}",
    )
)
_FILLER_AFTER_BUILDING: Final[re.Pattern[str]] = re.compile(r"\s+(?:oraz|i|lub)$", re.IGNORECASE)


@dataclass(frozen=True)
class ValueCondition:
    """Warunek wartości: rodzaj, etykieta, cytat i miejsce w tekście (do wskazania dowodu)."""

    kind: str
    label: str
    quote: str
    start: int
    end: int
    role: str

    @property
    def key(self) -> tuple[str, str]:
        """Tożsamość warunku (rodzaj + etykieta); cytat i miejsce nie decydują o równości warunków."""
        return (self.kind, self.label)


def _normalize(fragment: str) -> str:
    return " ".join(fragment.split())


def _label(fragment: str) -> str:
    return _normalize(fragment).strip(" ,;:–-—").lower()


def _roof_conditions(text: str, start: int, end: int, role: str) -> list[ValueCondition]:
    found: list[ValueCondition] = []
    for roof in find_roof_geometry(text[start:end]):
        label = ROOF_TYPE_LABELS.get(roof.canonical, f"dach {roof.canonical.replace('_lub_', ' lub ')}")
        found.append(ValueCondition(KIND_ROOF_TYPE, label, _normalize(roof.raw), start + roof.start, start + roof.end, role))
    return found


def _subzone_conditions(text: str, start: int, end: int, role: str) -> list[ValueCondition]:
    region = text[start:end]
    found: list[ValueCondition] = []
    for match in _SUBZONE_OUTSIDE.finditer(region):
        quote = _normalize(match.group(0))
        found.append(ValueCondition(KIND_SUBZONE, quote.lower(), quote, start + match.start(), start + match.end(), role))
    taken = [(c.start, c.end) for c in found]
    for word in _SUBZONE_WORD.finditer(region):
        if any(start + word.start() < t_end and t_start < start + word.end() for t_start, t_end in taken):
            continue
        rest = region[word.end() : word.end() + _SUBZONE_MAX]
        marked = _SUBZONE_MARKED.search(rest)
        boundary = _SUBZONE_BOUNDARY.search(rest)
        if marked is not None and (boundary is None or marked.start() <= boundary.start() or boundary.group(0).strip() == "na"):
            name_part, stop = rest[: marked.start()], marked.end()
        elif boundary is not None:
            name_part, stop = rest[: boundary.start()], boundary.start()
        else:
            name_part, stop = rest, len(rest)
        name = _normalize(name_part)
        if not name and marked is None:
            continue  # samo słowo „strefy” (np. w nazwie wskaźnika) nie wskazuje podstrefy
        quote = _normalize(region[word.start() : word.end() + stop])
        label = f"strefa {name}" if name else "strefa oznaczona na rysunku planu"
        found.append(ValueCondition(KIND_SUBZONE, label, quote, start + word.start(), start + word.end() + stop, role))
    return found


def _pattern_conditions(
    text: str, start: int, end: int, role: str, kind: str, patterns: Iterable[re.Pattern[str]]
) -> list[ValueCondition]:
    found: list[ValueCondition] = []
    region = text[start:end]
    for pattern in patterns:
        for match in pattern.finditer(region):
            quote = _normalize(match.group(0))
            quote = re.sub(r"[\s,;:–-]+$", "", quote)
            if quote:
                found.append(ValueCondition(kind, _label(quote), quote, start + match.start(), start + match.end(), role))
    return found


def _building_conditions(text: str, start: int, end: int, role: str) -> list[ValueCondition]:
    found: list[ValueCondition] = []
    for match in _BUILDING.finditer(text, start, end):
        phrase = _FILLER_AFTER_BUILDING.sub("", _normalize(match.group(0)))
        found.append(ValueCondition(KIND_BUILDING_TYPE, _label(phrase), phrase, match.start(), match.end(), role))
    return found


def _overlaps(candidate: ValueCondition, taken: Sequence[ValueCondition]) -> bool:
    return any(candidate.start < other.end and other.start < candidate.end for other in taken)


def classify_conditions(text: str, qualifiers: Iterable[Qualifier]) -> tuple[ValueCondition, ...]:
    """Warunki wartości z rozpiętości kwalifikatorów; bez nakładania i bez duplikatów.

    Kolejność rozpoznawania: dach, podstrefa, położenie, „inne” (``w przypadku …``, ``pozostałej
    części terenu``, pomiar do gzymsu), rodzaj zabudowy. Wyspecjalizowany warunek (dach) wygrywa
    z ogólnym ``w przypadku budynków z dachem płaskim`` — dostaje samo ``dach płaski``.
    """
    own: list[ValueCondition] = []
    inherited: list[ValueCondition] = []
    for qualifier in qualifiers:
        start, end = qualifier.start, qualifier.end
        if end <= start:
            continue
        found: list[ValueCondition] = []
        for batch in (
            _roof_conditions(text, start, end, qualifier.role),
            _subzone_conditions(text, start, end, qualifier.role),
            _pattern_conditions(text, start, end, qualifier.role, KIND_LOCATION, _LOCATION_PATTERNS),
            _pattern_conditions(text, start, end, qualifier.role, KIND_OTHER, _OTHER_PATTERNS),
            _building_conditions(text, start, end, qualifier.role),
        ):
            for candidate in batch:
                if not _overlaps(candidate, found):
                    found.append(candidate)
        if qualifier.role == "clause":
            inherited.extend(c for c in found if c.kind in _CLAUSE_INHERITED_KINDS)
        else:
            own.extend(found)

    # Kwalifikator klauzuli nie nadpisuje własnego warunku wartości tego samego rodzaju.
    own_kinds = {c.kind for c in own}
    merged = own + [c for c in inherited if c.kind not in own_kinds]
    result: list[ValueCondition] = []
    seen: set[tuple[str, str]] = set()
    for condition in sorted(merged, key=lambda c: (c.start, c.end)):
        if condition.key in seen or _overlaps(condition, result):
            continue
        seen.add(condition.key)
        result.append(condition)
    return tuple(result)


def condition_key(conditions: Iterable[object]) -> frozenset[tuple[str, str]]:
    """Tożsamość zestawu warunków (kolejność i cytaty nie mają znaczenia); pusty = bezwarunkowa."""
    return frozenset((str(getattr(c, "kind")), str(getattr(c, "label"))) for c in conditions)


def source_span(conditions: Iterable[ValueCondition], role_filter: Iterable[str] | None = None) -> tuple[int, int] | None:
    """Najmniejszy zakres tekstu obejmujący cytaty warunków (opcjonalnie tylko z wybranych ról)."""
    roles = set(role_filter) if role_filter is not None else None
    spans = [(c.start, c.end) for c in conditions if roles is None or c.role in roles]
    if not spans:
        return None
    return min(s for s, _ in spans), max(e for _, e in spans)
