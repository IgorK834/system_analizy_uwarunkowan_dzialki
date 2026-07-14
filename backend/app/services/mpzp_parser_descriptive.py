"""Ekstraktory ustaleń opisowych (nieliczbowych) z segmentów dokumentu MPZP.

Moduł NIE streszcza dowolnie treści uchwały. Każdy zwrócony ``MpzpParameter``
niesie ``source_text`` jako krótki, dosłowny fragment źródłowy — nie
sparafrazowany opis wygenerowany przez ten kod. ``normalized_value`` dla
parametrów opisowych jest typu ``str`` (schemat ``MpzpParameter.normalized_value:
float | str | None`` już to wspiera); rozróżnienie tekst/enum wynika z
konwencji nazwy parametru i wartości, nie z osobnego pola schematu.

Podobnie jak ``mpzp_parser_numeric.py``, ekstraktory działają na PEŁNYM
tekście segmentu (``DocumentSegment.text``) wskazanym przez kandydatów
``ZoneSectionResult``, nie na krótkim ``source_text`` kandydata.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final

from app.schemas.mpzp import MpzpParameter
from app.services.mpzp_parser_segment import DocumentSegment, ZoneSectionResult

logger = logging.getLogger(__name__)

_BASE_CONFIDENCE: Final[float] = 0.85
# Kara dla primary_use, gdy nie udało się jednoznacznie rozdzielić
# przeznaczenia podstawowego od uzupełniającego (honest degradation, nie
# zgadywanie podziału).
_UNSTRUCTURED_PRIMARY_USE_CONFIDENCE: Final[float] = 0.5

_PURPOSE_SECTION_ANCHOR_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"przeznaczeni\w*\s+i\s+zasad\w*\s+zagospodarowania\s+terenu\b",
    re.IGNORECASE,
)
# Granica sekcji przeznaczenia to następny punkt numerowany "N)" na początku
# wiersza — sekcje uchwały są numerowane sekwencyjnie na tym samym poziomie.
_NEXT_NUMBERED_ITEM_PATTERN: Final[re.Pattern[str]] = re.compile(r"\n\d+\)\s")
_LETTERED_ITEM_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:^|\n)([a-z])\)\s*(.*?)(?=\n[a-z]\)|\Z)", re.DOTALL
)

_PROHIBITION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"zakaz\s+[^,;.\n]+(?:[,;.]|$)", re.IGNORECASE
)
_PERMISSION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"dopuszczeni\w*\s+[^;.\n]+(?:[;.]|$)", re.IGNORECASE
)

_ENVIRONMENT_HEADING_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"ochron\w*\s+środowiska", re.IGNORECASE
)
_ENVIRONMENT_RESTRICTION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # Fragmenty realnego dokumentu bywają zawinięte przez pdfplumber w środku
    # zdania (nowa linia zamiast spacji), więc granica dopuszcza \n, a
    # kończy dopasowanie dopiero na kropce/średniku.
    re.compile(r"nakaz\s+ochrony[^.;]*[.;]", re.IGNORECASE),
    re.compile(r"dopuszczalny\s+poziom\s+hałasu[^.;]*[.;]", re.IGNORECASE),
)

_LARGE_RETAIL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"obiekt\w*\s+handlow\w*.{0,80}?(\d[\d\s]*)\s*m\s*(?:2|²|kw\.?)",
    re.IGNORECASE,
)

_PARKING_DESCRIPTIVE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"nakaz\s+lokalizacji\s+miejsc\s+przeznaczonych\s+na\s+parkowanie[^.;:]*"
    r"[.;:]",
    re.IGNORECASE,
)

_ROOF_GEOMETRY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"dachy\s+(dwuspadow\w*(?:\s+lub\s+wielospadow\w*)?|wielospadow\w*|płask\w*)",
    re.IGNORECASE,
)
_ROOF_GEOMETRY_NORMALIZATION: Final[dict[str, str]] = {
    "dwuspadowe": "dwuspadowy",
    "dwuspadowa": "dwuspadowy",
    "wielospadowe": "wielospadowy",
    "płaskie": "płaski",
    "płaska": "płaski",
}


@dataclass(frozen=True)
class _RawTextMatch:
    """Pojedynczy dosłowny fragment przed zapakowaniem w MpzpParameter."""

    value: str
    source_text: str


def _context_excerpt(text: str, start: int, end: int, pad: int = 20) -> str:
    left = max(0, start - pad)
    right = min(len(text), end + pad)
    return " ".join(text[left:right].split())


def _make_parameters(
    name: str,
    matches: list[_RawTextMatch],
    page_number: int | None,
    confidence: float = _BASE_CONFIDENCE,
) -> list[MpzpParameter]:
    parameters: list[MpzpParameter] = []
    for match in matches:
        parameters.append(
            MpzpParameter(
                name=name,
                normalized_value=match.value,
                unit=None,
                raw_value=match.source_text,
                source_text=match.source_text,
                page_number=page_number,
                confidence=confidence,
                manual_review_required=False,
            )
        )
    return parameters


def _find_purpose_section_window(text: str) -> str | None:
    """Wycina fragment tekstu należący do 'przeznaczenie i zasady...'."""
    anchor = _PURPOSE_SECTION_ANCHOR_PATTERN.search(text)
    if anchor is None:
        return None
    window_start = anchor.end()
    boundary = _NEXT_NUMBERED_ITEM_PATTERN.search(text[window_start:])
    window_end = (
        window_start + boundary.start() if boundary is not None else len(text)
    )
    return text[window_start:window_end]


def _extract_primary_and_supplementary_use(
    text: str,
) -> tuple[list[_RawTextMatch], list[_RawTextMatch], _RawTextMatch | None]:
    """Rozdziela przeznaczenie podstawowe/uzupełniające, gdy struktura a)/b)/c) na to pozwala.

    Zwraca (primary_matches, supplementary_matches, fallback_match). Fallback
    jest wypełniony wyłącznie, gdy sekcja przeznaczenia istnieje, ale nie da
    się jej rozdzielić na literowane podpunkty — to jest honest degradation,
    nie zgadywanie podziału.
    """
    window = _find_purpose_section_window(text)
    if window is None:
        return [], [], None

    items = list(_LETTERED_ITEM_PATTERN.finditer(window))
    if not items:
        # Brak podziału a)/b)/c): całe zdanie trafia jako fallback z niższym
        # confidence, bez próby zgadywania granicy podstawowe/uzupełniające.
        fallback_text = " ".join(window.strip(" :\n").split())
        if not fallback_text:
            return [], [], None
        return [], [], _RawTextMatch(value=fallback_text, source_text=fallback_text)

    primary: list[_RawTextMatch] = []
    supplementary: list[_RawTextMatch] = []
    for item in items:
        item_text = " ".join(item.group(2).strip(" ,;").split())
        if not item_text:
            continue
        if re.match(r"dopuszczeni\w*\s+funkcj\w*", item_text, re.IGNORECASE):
            supplementary.append(_RawTextMatch(value=item_text, source_text=item_text))
        elif re.match(r"zabudowa\b", item_text, re.IGNORECASE):
            primary.append(_RawTextMatch(value=item_text, source_text=item_text))
    return primary, supplementary, None


def _extract_prohibitions(text: str) -> list[_RawTextMatch]:
    matches: list[_RawTextMatch] = []
    for match in _PROHIBITION_PATTERN.finditer(text):
        value = " ".join(match.group(0).strip(" ,;.").split())
        matches.append(_RawTextMatch(value=value, source_text=value))
    return matches


def _extract_permissions(text: str) -> list[_RawTextMatch]:
    matches: list[_RawTextMatch] = []
    for match in _PERMISSION_PATTERN.finditer(text):
        value = " ".join(match.group(0).strip(" ,;.").split())
        matches.append(_RawTextMatch(value=value, source_text=value))
    return matches


def _extract_environmental_restrictions(text: str) -> list[_RawTextMatch]:
    # Ograniczenia środowiskowe są rozpoznawane w kontekście nagłówka
    # "ochrony środowiska" (§5 realnego dokumentu) — bez tego kontekstu
    # słowa "nakaz"/"dopuszczalny poziom" mogłyby dotyczyć innych paragrafów.
    if _ENVIRONMENT_HEADING_PATTERN.search(text) is None:
        return []
    matches: list[_RawTextMatch] = []
    for pattern in _ENVIRONMENT_RESTRICTION_PATTERNS:
        for match in pattern.finditer(text):
            value = " ".join(match.group(0).strip(" ,;.").split())
            matches.append(_RawTextMatch(value=value, source_text=value))
    return matches


def _extract_large_retail_restriction(text: str) -> list[_RawTextMatch]:
    matches: list[_RawTextMatch] = []
    for match in _LARGE_RETAIL_PATTERN.finditer(text):
        excerpt = _context_excerpt(text, match.start(), match.end(), pad=10)
        area = match.group(1).replace(" ", "")
        value = f"zakaz obiektów handlowych o powierzchni sprzedaży > {area} m²"
        matches.append(_RawTextMatch(value=value, source_text=excerpt))
    return matches


def _extract_parking_requirement_descriptive(text: str) -> list[_RawTextMatch]:
    matches: list[_RawTextMatch] = []
    for match in _PARKING_DESCRIPTIVE_PATTERN.finditer(text):
        value = " ".join(match.group(0).strip(" ,;.").split())
        matches.append(_RawTextMatch(value=value, source_text=value))
    return matches


def _extract_roof_geometry(text: str) -> list[_RawTextMatch]:
    matches: list[_RawTextMatch] = []
    seen_categories: set[str] = set()
    for match in _ROOF_GEOMETRY_PATTERN.finditer(text):
        raw_category = match.group(1).lower()
        if "dwuspadow" in raw_category and "wielospadow" in raw_category:
            normalized = "dwuspadowy_lub_wielospadowy"
        else:
            normalized = _ROOF_GEOMETRY_NORMALIZATION.get(raw_category, raw_category)
        # Ta sama kategoria wspomniana wielokrotnie w tym samym segmencie nie
        # jest nową informacją — deduplikujemy po znormalizowanej nazwie.
        if normalized in seen_categories:
            continue
        seen_categories.add(normalized)
        excerpt = _context_excerpt(text, match.start(), match.end())
        matches.append(_RawTextMatch(value=normalized, source_text=excerpt))
    return matches


def extract_descriptive_parameters(
    zone_symbol: str,
    zone_section: ZoneSectionResult,
    segments: list[DocumentSegment],
) -> list[MpzpParameter]:
    """Wyciąga opisowe (nieliczbowe) ustalenia MPZP dla jednej strefy.

    Analogicznie do ``extract_numeric_parameters``: łączy tekst wszystkich
    segmentów wskazanych przez kandydatów ``zone_section`` i uruchamia na nim
    bespoke ekstraktory. Wielość dopasowań dla ``prohibition``/``permission``/
    ``roof_geometry`` NIE jest traktowana jako konflikt — to naturalnie różne,
    współistniejące ustalenia (np. dwie dozwolone geometrie dachu), nie
    sprzeczne wartości jednego parametru liczbowego.
    """
    segment_by_id = {segment.segment_id: segment for segment in segments}
    relevant_segments = [
        segment_by_id[candidate.segment_id]
        for candidate in zone_section.candidates
        if candidate.segment_id in segment_by_id
    ]
    if not relevant_segments:
        return []

    combined_text = "\n".join(segment.text for segment in relevant_segments)
    page_number = relevant_segments[0].page_number

    parameters: list[MpzpParameter] = []

    primary_matches, supplementary_matches, fallback_match = (
        _extract_primary_and_supplementary_use(combined_text)
    )
    parameters.extend(_make_parameters("primary_use", primary_matches, page_number))
    parameters.extend(
        _make_parameters("supplementary_use", supplementary_matches, page_number)
    )
    if fallback_match is not None:
        parameters.append(
            MpzpParameter(
                name="primary_use",
                normalized_value=fallback_match.value,
                unit=None,
                raw_value=fallback_match.source_text,
                source_text=fallback_match.source_text,
                page_number=page_number,
                confidence=_UNSTRUCTURED_PRIMARY_USE_CONFIDENCE,
                manual_review_required=True,
            )
        )
        logger.debug(
            "Strefa %s: nie udało się jednoznacznie rozdzielić przeznaczenia "
            "podstawowego od uzupełniającego, zwrócono cały opis z niższym "
            "confidence.",
            zone_symbol,
        )

    parameters.extend(
        _make_parameters("prohibition", _extract_prohibitions(combined_text), page_number)
    )
    parameters.extend(
        _make_parameters("permission", _extract_permissions(combined_text), page_number)
    )
    parameters.extend(
        _make_parameters(
            "environmental_restriction",
            _extract_environmental_restrictions(combined_text),
            page_number,
        )
    )
    parameters.extend(
        _make_parameters(
            "large_retail_restriction",
            _extract_large_retail_restriction(combined_text),
            page_number,
        )
    )
    parameters.extend(
        _make_parameters(
            "parking_requirement_descriptive",
            _extract_parking_requirement_descriptive(combined_text),
            page_number,
        )
    )
    parameters.extend(
        _make_parameters(
            "roof_geometry", _extract_roof_geometry(combined_text), page_number
        )
    )

    return parameters
