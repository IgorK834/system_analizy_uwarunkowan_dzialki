"""Wspólna, deterministyczna normalizacja liczb z polskich aktów prawnych.

Jedyne miejsce, w którym zapis liczby (separator dziesiętny, spacja tysięcy, liczba
słowna) zamienia się na ``float``. Leksykon ilości MPZP (``quantity_lexicon``), silnik
ekstrakcji i ewaluator parsera korzystają z tych samych funkcji, więc ta sama liczba nie
może dostać dwóch różnych wartości w różnych częściach systemu (PV3-07).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

# Liczba z opcjonalną spacją tysięcy (``2 000``) i separatorem dziesiętnym ``,`` albo ``.``.
# Grupa tysięcy wymaga dokładnie trzech cyfr, więc ``od 12 do 45`` nie skleja się w ``12``.
NUMBER_PATTERN: Final[str] = r"(?:\d{1,3}(?:[\u00a0 ]\d{3})+|\d+)(?:[.,]\d+)?"

_RANGE_PATTERN = re.compile(
    r"(?P<minimum>\d+(?:[.,]\d+)?)\s*(?:°|%)?\s*"
    r"(?:[-–—]|do)\s*"
    r"(?P<maximum>\d+(?:[.,]\d+)?)\s*(?:°|%)?",
    re.IGNORECASE,
)

# Liczby słowne używane w uchwałach przy kondygnacjach i miejscach postojowych (1–10).
# Dokładne formy odmiany; dopasowanie po całym słowie, nie po przedrostku (``dwa`` ≠ ``dwanaście``).
_NUMBER_WORD_EXACT: Final[dict[str, int]] = {
    "jeden": 1,
    "jedna": 1,
    "jedno": 1,
    "jednego": 1,
    "jednej": 1,
    "jedną": 1,
    "jednym": 1,
    "dwa": 2,
    "dwie": 2,
    "dwóch": 2,
    "dwu": 2,
    "dwoma": 2,
    "dwiema": 2,
    "trzy": 3,
    "trzech": 3,
    "trzema": 3,
    "cztery": 4,
    "czterech": 4,
    "czterema": 4,
    "pięć": 5,
    "pięciu": 5,
    "pięcioma": 5,
    "sześć": 6,
    "sześciu": 6,
    "siedem": 7,
    "siedmiu": 7,
    "osiem": 8,
    "ośmiu": 8,
    "dziewięć": 9,
    "dziewięciu": 9,
    "dziesięć": 10,
    "dziesięciu": 10,
}
# Przedrostki złożeń typu ``dwukondygnacyjny`` (liczba + przymiotnik).
_NUMBER_WORD_COMPOUND_PREFIXES: Final[tuple[tuple[str, int], ...]] = (
    ("jedno", 1),
    ("dwu", 2),
    ("trzy", 3),
    ("cztero", 4),
    ("pięcio", 5),
    ("sześcio", 6),
    ("siedmio", 7),
    ("ośmio", 8),
)
NUMBER_WORD_PATTERN: Final[str] = (
    r"jedn\w+|dwa|dwie|dwóch|dwu|dwoma|dwiema|trzy|trzech|trzema|cztery|czterech|czterema|"
    r"pięć|pięciu|pięcioma|sześć|sześciu|siedem|siedmiu|osiem|ośmiu|dziewięć|dziewięciu|"
    r"dziesięć|dziesięciu"
)


@dataclass(frozen=True)
class NumericRange:
    """Znormalizowany zakres liczbowy wraz z dosłownym zapisem."""

    minimum: float
    maximum: float
    raw_value: str


def parse_polish_number(raw: str) -> float:
    """Zamienia polski zapis dziesiętny i separatory tysięcy na ``float``."""
    cleaned = raw.strip().replace("\u00a0", "").replace(" ", "")
    if not cleaned:
        raise ValueError("Pusta wartość liczbowa.")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", ".")
    return float(cleaned)


def parse_number_word(word: str) -> int | None:
    """Liczba całkowita z formy słownej (``dwie``, ``dwóch``, ``trzech``) albo ``None``.

    Rozpoznaje tylko jednowyrazowe liczebniki 1–10, które występują w ustaleniach planu;
    złożenia (``dwukondygnacyjny``) obsługuje ``parse_number_prefix``.
    """
    token = word.strip().lower()
    return _NUMBER_WORD_EXACT.get(token)


def parse_number_prefix(word: str) -> int | None:
    """Liczba z przedrostka złożenia: ``dwukondygnacyjne`` → 2, ``jednokondygnacyjny`` → 1."""
    token = word.strip().lower()
    for prefix, value in _NUMBER_WORD_COMPOUND_PREFIXES:
        if token.startswith(prefix):
            return value
    return None


def parse_numeric_range(raw: str) -> NumericRange | None:
    """Rozpoznaje zakres zapisany jako ``0,1–1,5`` albo ``0.1 do 1.5``."""
    match = _RANGE_PATTERN.search(raw)
    if match is None:
        return None
    minimum = parse_polish_number(match.group("minimum"))
    maximum = parse_polish_number(match.group("maximum"))
    if minimum > maximum:
        minimum, maximum = maximum, minimum
    return NumericRange(
        minimum=minimum,
        maximum=maximum,
        raw_value=match.group(0),
    )
