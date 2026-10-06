"""Deterministyczna normalizacja ilości z ustaleń planów miejscowych (PV3-07).

Jedyna implementacja reguł zamiany zapisu w tekście na wartość znormalizowaną. Korzystają z niej:

- silnik ekstrakcji (``quantity_engine``) — dla wartości znalezionych w tekście uchwały,
- aplikacja (parsery, serwis reguł) — pośrednio przez silnik,
- ewaluator parsera (``scripts/evaluate_mpzp_parser.py``) — dla zapisów adnotacji, bez własnej kopii.

Normalizacja jest czysta i deterministyczna: ta sama para ``(rodzina, zapis)`` zawsze daje ten
sam wynik, a każda przeróbka zapisu (ułamek → procent, ``0`` zamiast ``°``, liczba słowna)
zostawia FLAGĘ, żeby pewność wyniku mogła być obniżona i żeby było widać, że zapis nie był
dosłowny. ``None`` oznacza „nie umiem znormalizować”, nigdy ``0``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Final

from app.modules.planning.domain.quantity_lexicon import (
    BASE_CONFIDENCE,
    FAMILY_BIO,
    FAMILY_COVERAGE,
    FAMILY_HEIGHT,
    FAMILY_INTENSITY,
    FAMILY_PARKING,
    FAMILY_PLOT,
    FAMILY_RETAIL,
    FAMILY_ROOF,
    FAMILY_SETBACK,
    FAMILY_STOREYS,
    FLAG_PENALTIES,
    UNIT_DEGREE,
    UNIT_HECTARE,
    UNIT_NONE,
    UNIT_PERCENT,
)
from app.shared.numbers import parse_number_prefix, parse_number_word, parse_polish_number

# Reguły normalizacji zapisu w adnotacji korpusu (BK-603): nazwy są częścią zamrożonego schematu.
NORMALIZATION_RULES: Final[tuple[str, ...]] = (
    "identity",
    "ratio_to_percent",
    "range_lower",
    "range_upper",
    "word_number",
    "manual",
)

_ANNOTATION_NUMBER: Final[re.Pattern[str]] = re.compile(r"\d+(?:[.,]\d+)?")
_WORD_TOKEN: Final[re.Pattern[str]] = re.compile(r"[^\W\d_]+")

# Granice sensu dla wartości (poza nimi zapis jest odrzucany, nie „naprawiany”).
_MAX_HEIGHT_M: Final[float] = 400.0
_MAX_STOREYS: Final[int] = 40
_MAX_INTENSITY: Final[float] = 20.0
_MAX_ANGLE_DEG: Final[float] = 90.0
_MAX_SETBACK_M: Final[float] = 500.0
_MAX_PARKING: Final[float] = 100.0
_MAX_AREA_M2: Final[float] = 10_000_000.0
# Zgodność zapisu podwójnego ``0,50 (50%)``: różnica większa niż pół punktu procentowego
# oznacza, że ułamek i procent nie opisują tej samej wartości.
_DOUBLE_NOTATION_TOLERANCE: Final[float] = 0.51


class QuantityNormalizationError(ValueError):
    """Zapis nie daje się znormalizować wskazaną regułą."""


@dataclass(frozen=True)
class NormalizedQuantity:
    """Wartość po normalizacji z flagami przeróbek zapisu."""

    value: float
    flags: tuple[str, ...] = ()

    @property
    def penalty(self) -> float:
        return flags_penalty(self.flags)


def flags_penalty(flags: tuple[str, ...] | list[str]) -> float:
    """Mnożnik pewności wynikający z flag (iloczyn kar; flaga nieznana nie zmienia pewności)."""
    penalty = 1.0
    for flag in dict.fromkeys(flags):
        penalty *= FLAG_PENALTIES.get(flag, 1.0)
    return penalty


def engine_confidence(flags: tuple[str, ...] | list[str]) -> float:
    """Pewność silnika (przed kalibracją): pewność bazowa × kary flag, zaokrąglona do 4 miejsc."""
    return round(BASE_CONFIDENCE * flags_penalty(flags), 4)


# --- zapisy adnotacji (ewaluator korzysta z tego samego kodu) --------------------------


def extract_numbers(raw: str) -> list[float]:
    """Wszystkie liczby z fragmentu (``0,01 – 0,9`` → ``[0.01, 0.9]``)."""
    return [parse_polish_number(item) for item in _ANNOTATION_NUMBER.findall(raw)]


def number_word_value(raw: str) -> float | None:
    """Pierwsza liczba słowna we fragmencie (``dwie kondygnacje`` → 2) albo ``None``."""
    for token in _WORD_TOKEN.findall(raw.lower()):
        value = parse_number_word(token)
        if value is None:
            value = parse_number_prefix(token)
        if value is not None:
            return float(value)
    return None


def ratio_to_percent(value: float) -> float:
    """Ułamek → procent bez szumu zmiennoprzecinkowego (``0,35`` → ``35.0``)."""
    return round(value * 100.0, 9)


def normalize_annotation_value(rule: str, raw: str) -> float | None:
    """Jawna normalizacja zapisu z adnotacji; ``None`` = reguła ``manual`` (decyzja człowieka).

    Reguły: ``identity`` (pierwsza liczba), ``ratio_to_percent`` (ułamek → procent),
    ``range_lower``/``range_upper`` (dolna/górna granica zakresu), ``word_number`` (liczba słowna).
    """
    if rule == "manual":
        return None
    if rule == "word_number":
        value = number_word_value(raw)
        if value is None:
            raise QuantityNormalizationError(f"No number word in {raw!r}.")
        return value
    numbers = extract_numbers(raw)
    if rule in {"identity", "ratio_to_percent", "range_lower"}:
        if not numbers:
            raise QuantityNormalizationError(f"No number in {raw!r}.")
        value = numbers[0]
        return ratio_to_percent(value) if rule == "ratio_to_percent" else value
    if rule == "range_upper":
        if len(numbers) < 2:
            raise QuantityNormalizationError(f"No range in {raw!r}.")
        return numbers[1]
    raise QuantityNormalizationError(f"Unknown normalization rule {rule!r}.")


# --- wartości z tekstu uchwały (silnik) -------------------------------------------------


def has_decimal_separator(raw_number: str) -> bool:
    return "," in raw_number or "." in raw_number


def normalize_quantity(
    family: str,
    number: float,
    *,
    unit_kind: str,
    raw_number: str = "",
    parenthesized_percent: float | None = None,
    degree_symbol: str | None = None,
) -> NormalizedQuantity | None:
    """Normalizuje jedną liczbę z tekstu dla rodziny parametru; ``None`` = poza zakresem sensu.

    ``unit_kind`` to rodzaj jednostki znaleziony przy liczbie (``UNIT_*`` z leksykonu),
    ``parenthesized_percent`` — procent z zapisu podwójnego ``0,50 (50%)``, ``degree_symbol`` —
    dosłowny znak stopnia (``°``, ``º``, ``o``), bo tylko ``°`` jest zapisem poprawnym.
    """
    if not math.isfinite(number) or number < 0:
        return None
    if family == FAMILY_HEIGHT:
        return _plain(number, 0 < number <= _MAX_HEIGHT_M)
    if family == FAMILY_STOREYS:
        integral = float(number).is_integer()
        return _plain(number, integral and 1 <= number <= _MAX_STOREYS)
    if family in (FAMILY_COVERAGE, FAMILY_BIO):
        return _percent(number, unit_kind, raw_number, parenthesized_percent)
    if family == FAMILY_INTENSITY:
        return _intensity(number, unit_kind)
    if family == FAMILY_ROOF:
        return _degrees(number, unit_kind, degree_symbol)
    if family == FAMILY_SETBACK:
        return _plain(number, 0 < number <= _MAX_SETBACK_M)
    if family == FAMILY_PARKING:
        return _plain(number, 0 < number <= _MAX_PARKING)
    if family in (FAMILY_PLOT, FAMILY_RETAIL):
        if unit_kind == UNIT_HECTARE:
            area = number * 10_000.0
            return NormalizedQuantity(area, ("hectare_to_m2",)) if 0 < area <= _MAX_AREA_M2 else None
        return _plain(number, 0 < number <= _MAX_AREA_M2)
    return None


def _plain(number: float, valid: bool) -> NormalizedQuantity | None:
    return NormalizedQuantity(number) if valid else None


def _percent(
    number: float, unit_kind: str, raw_number: str, parenthesized_percent: float | None
) -> NormalizedQuantity | None:
    if unit_kind == UNIT_PERCENT:
        return NormalizedQuantity(number) if 0 <= number <= 100 else None
    if unit_kind != UNIT_NONE:
        return None
    if has_decimal_separator(raw_number) and 0 <= number <= 1:
        value = ratio_to_percent(number)
        if parenthesized_percent is None:
            return NormalizedQuantity(value, ("ratio_to_percent",))
        # Zapis podwójny ``0,50 (50%)``: wartością jest procent z nawiasu, o ile zgadza się z ułamkiem.
        if abs(value - parenthesized_percent) <= _DOUBLE_NOTATION_TOLERANCE:
            return NormalizedQuantity(parenthesized_percent, ("double_notation",))
        return NormalizedQuantity(value, ("ratio_to_percent", "double_notation_mismatch"))
    if float(number).is_integer() and 1 <= number <= 100:
        return NormalizedQuantity(number, ("implicit_percent",))
    return None


def _intensity(number: float, unit_kind: str) -> NormalizedQuantity | None:
    if unit_kind == UNIT_NONE:
        return NormalizedQuantity(number) if 0 <= number <= _MAX_INTENSITY else None
    if unit_kind == UNIT_PERCENT and 0 < number <= 100 * _MAX_INTENSITY:
        return NormalizedQuantity(round(number / 100.0, 9), ("percent_to_ratio",))
    return None


def _degrees(number: float, unit_kind: str, degree_symbol: str | None) -> NormalizedQuantity | None:
    if unit_kind == UNIT_DEGREE:
        if not 0 <= number <= _MAX_ANGLE_DEG:
            return None
        # ``°`` i ``˚`` to zapis poprawny; ``º`` (znak porządkowy) i litera ``o`` to artefakty zapisu.
        flags = ("degree_letter",) if degree_symbol in {"º", "o"} else ()
        return NormalizedQuantity(number, flags)
    if unit_kind != UNIT_NONE:
        return None
    if 0 <= number <= _MAX_ANGLE_DEG:
        return NormalizedQuantity(number, ("unit_implied",))
    # Symbol stopnia odczytany jako ``0`` (``od 300 do 450`` zamiast ``od 30° do 45°``).
    if float(number).is_integer() and number % 10 == 0 and 0 < number / 10 <= _MAX_ANGLE_DEG:
        return NormalizedQuantity(number / 10.0, ("degree_artifact",))
    return None
