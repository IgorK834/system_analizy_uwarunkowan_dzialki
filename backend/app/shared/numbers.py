"""Wspólna, deterministyczna normalizacja liczb z polskich aktów prawnych."""

from __future__ import annotations

import re
from dataclasses import dataclass

_RANGE_PATTERN = re.compile(
    r"(?P<minimum>\d+(?:[.,]\d+)?)\s*(?:°|%)?\s*"
    r"(?:[-–—]|do)\s*"
    r"(?P<maximum>\d+(?:[.,]\d+)?)\s*(?:°|%)?",
    re.IGNORECASE,
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
