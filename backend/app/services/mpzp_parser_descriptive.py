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

Od PV3-21 moduł NIE zawiera własnych wzorców: zapisy opisowe (przeznaczenie, zakazy, dopuszczenia,
ochrona środowiska, parkowanie, rodzaje dachu) rozpoznaje jeden silnik domenowy
``app.modules.planning.domain.descriptive_engine`` — ten sam, którego używają reguły planistyczne
jednostek prawnych (``planning/domain/rules.py``) — a ilości (powierzchnia sprzedaży obiektów
handlowych) ``quantity_engine`` (PV3-07). Tu zostaje opakowanie ustaleń w ``MpzpParameter``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from app.modules.planning.domain.descriptive_engine import (
    DescriptiveFinding,
    find_environmental_restrictions,
    find_parking_requirements,
    find_permissions,
    find_prohibitions,
    find_roof_geometries,
    find_use_designations,
)
from app.modules.planning.domain.quantity_engine import find_quantities
from app.modules.planning.domain.quantity_lexicon import FAMILY_RETAIL
from app.schemas.mpzp import MpzpParameter
from app.services.mpzp_parser_segment import DocumentSegment, ZoneSectionResult

logger = logging.getLogger(__name__)

_BASE_CONFIDENCE: Final[float] = 0.85
# Kara dla primary_use, gdy nie udało się jednoznacznie rozdzielić
# przeznaczenia podstawowego od uzupełniającego (honest degradation, nie
# zgadywanie podziału).
_UNSTRUCTURED_PRIMARY_USE_CONFIDENCE: Final[float] = 0.5


def _context_excerpt(text: str, start: int, end: int, pad: int = 20) -> str:
    left = max(0, start - pad)
    right = min(len(text), end + pad)
    return " ".join(text[left:right].split())


@dataclass(frozen=True)
class DescribedParameter:
    """Ustalenie opisowe z miejscem w przeszukanym tekście (``start``/``end``) — dowód ma wskazywać stronę."""

    name: str
    value: str
    quote: str
    start: int | None
    end: int | None
    confidence: float = _BASE_CONFIDENCE
    manual_review_required: bool = False


def _from_findings(name: str, findings: list[DescriptiveFinding]) -> list[DescribedParameter]:
    return [DescribedParameter(name, f.value, f.quote, f.start, f.end) for f in findings]


def _large_retail_restrictions(text: str) -> list[DescribedParameter]:
    parameters: list[DescribedParameter] = []
    for quantity in find_quantities(text, families=[FAMILY_RETAIL]):
        # Kontekst liczony od rzeczownika (``obiektów handlowych``) do wartości, jak w zapisie źródłowym.
        left = quantity.noun_start if quantity.noun_start is not None else quantity.start
        excerpt = _context_excerpt(text, left, quantity.end, pad=10)
        area = f"{quantity.value:.0f}" if float(quantity.value).is_integer() else f"{quantity.value}"
        value = f"zakaz obiektów handlowych o powierzchni sprzedaży > {area} m²"
        parameters.append(DescribedParameter("large_retail_restriction", value, excerpt, left, quantity.end))
    return parameters


def describe_text(text: str, zone_symbol: str) -> list[DescribedParameter]:
    """Opisowe (nieliczbowe) ustalenia MPZP w podanym tekście, z pozycją każdego w tym tekście.

    Wspólny rdzeń trybu dotychczasowego (tekst skleja kandydackie segmenty) i blokowego (tekst jednego
    bloku strefy, pozycja przeliczana na stronę dokumentu). Wielość dopasowań dla ``prohibition``/
    ``permission``/``roof_geometry`` NIE jest konfliktem — to współistniejące ustalenia.
    """
    uses = find_use_designations(text, zone_symbol)
    parameters: list[DescribedParameter] = []
    parameters.extend(
        DescribedParameter(f.code, f.value, f.quote, f.start, f.end)
        for f in uses
        if f.code == "primary_use" and f.structured
    )
    parameters.extend(DescribedParameter(f.code, f.value, f.quote, f.start, f.end) for f in uses if f.code == "supplementary_use")
    for fallback in (f for f in uses if not f.structured):
        parameters.append(
            DescribedParameter(
                fallback.code, fallback.value, fallback.quote, fallback.start, fallback.end,
                confidence=_UNSTRUCTURED_PRIMARY_USE_CONFIDENCE, manual_review_required=True,
            )
        )
        logger.debug(
            "Strefa %s: nie udało się jednoznacznie rozdzielić przeznaczenia "
            "podstawowego od uzupełniającego, zwrócono cały opis z niższym "
            "confidence.",
            zone_symbol,
        )
    parameters.extend(_from_findings("prohibition", find_prohibitions(text)))
    parameters.extend(_from_findings("permission", find_permissions(text)))
    parameters.extend(
        _from_findings(
            "environmental_restriction",
            # Ograniczenia środowiskowe są rozpoznawane w kontekście nagłówka "ochrony środowiska"
            # (§5 realnego dokumentu) — bez niego "nakaz"/"dopuszczalny poziom" mogą dotyczyć innych paragrafów.
            find_environmental_restrictions(text, heading_required=True),
        )
    )
    parameters.extend(_large_retail_restrictions(text))
    parameters.extend(_from_findings("parking_requirement_descriptive", find_parking_requirements(text)))
    parameters.extend(
        DescribedParameter("roof_geometry", roof.value, _context_excerpt(text, roof.start, roof.end), roof.start, roof.end)
        for roof in find_roof_geometries(text)
    )
    return parameters


def _parameter(described: DescribedParameter, page_number: int | None) -> MpzpParameter:
    return MpzpParameter(
        name=described.name,
        normalized_value=described.value,
        unit=None,
        raw_value=described.quote,
        source_text=described.quote,
        page_number=page_number,
        confidence=described.confidence,
        manual_review_required=described.manual_review_required,
    )


def extract_descriptive_parameters(
    zone_symbol: str,
    zone_section: ZoneSectionResult,
    segments: list[DocumentSegment],
) -> list[MpzpParameter]:
    """Wyciąga opisowe (nieliczbowe) ustalenia MPZP dla jednej strefy.

    Analogicznie do ``extract_numeric_parameters``: łączy tekst wszystkich
    segmentów wskazanych przez kandydatów ``zone_section`` i uruchamia na nim
    wspólny silnik opisowy (``describe_text``). Strony parametrów ustala potem
    ``_attach_evidence_metadata`` względem segmentu zawierającego cytat.
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
    return [_parameter(described, page_number) for described in describe_text(combined_text, zone_symbol)]
