"""Fasada etapowego pipeline'u parsera dokumentów MPZP.

``parse_mpzp_document`` NIGDY nie propaguje wyjątku na poziomie całej funkcji:
nieoczekiwany błąd jest logowany i zamieniany na ``MpzpParseResult`` ze statusem
``failed``. Etapy klasyfikacji i ekstrakcji tekstu delegują do realnych
implementacji w ``mpzp_parser_extract.py``. Segmentacja, ekstrakcja parametrów
i walidacja tworzą kolejne, jawnie rozdzielone etapy pipeline'u. Segmentacja
korzysta z realnej implementacji zweryfikowanej na publicznej uchwale MPZP.
Ekstrakcja parametrów (etap 4) i walidacja wyniku (etap 5) korzystają z
realnych implementacji w ``mpzp_parser_numeric.py``, ``mpzp_parser_descriptive.py``
i ``mpzp_parser_validate.py``.
"""

from __future__ import annotations

import logging
from typing import Literal

from app.schemas.mpzp import (
    ExtractedEvidence,
    MpzpParseResult,
    MpzpParserWarning,
    MpzpZoneResult,
)
from app.services.mpzp_fetch import DocumentBlob
from app.services.mpzp_parser_descriptive import extract_descriptive_parameters
from app.services.mpzp_parser_extract import (
    classify_document,
    extract_document_text,
)
from app.services.mpzp_parser_numeric import extract_numeric_parameters
from app.services.mpzp_parser_segment import (
    DocumentSegment,
    ZoneSectionResult,
    find_zone_sections,
    segment_document,
)
from app.services.mpzp_parser_validate import validate_mpzp_result

logger = logging.getLogger(__name__)


__all__ = [
    "DocumentSegment",
    "ZoneSectionResult",
    "extract_parameters",
    "find_zone_sections",
    "parse_mpzp_document",
    "segment_document",
    "validate_result",
]


async def parse_mpzp_document(
    document: DocumentBlob,
    zone_symbols: list[str] | None = None,
) -> MpzpParseResult:
    """Uruchamia pipeline i NIGDY nie podnosi niekontrolowanego wyjątku.

    ``zone_symbols`` to pomocniczy zestaw kandydatów z MPZP discovery (Task 3.9),
    a nie ostateczne przypisanie działki do stref planistycznych.
    """
    zone_symbols = zone_symbols or []
    try:
        return await _run_parse_pipeline(document, zone_symbols)
    except Exception:
        # Granica fasady celowo łapie wszystkie przyszłe tryby awarii etapów,
        # ponieważ publiczny kontrakt gwarantuje wynik failed zamiast wyjątku.
        logger.exception("Nieoczekiwany błąd w pipeline parsera MPZP.")
        return MpzpParseResult(
            plan_id=None,
            zones=[],
            status="failed",
            warnings=[
                MpzpParserWarning(
                    stage="validate_result",
                    code="PARSER_UNEXPECTED_ERROR",
                    message=(
                        "Parser MPZP napotkał nieoczekiwany błąd i nie mógł "
                        "zwrócić wyniku."
                    ),
                    zone_symbol=None,
                    parameter_name=None,
                    page_number=None,
                    severity="error",
                )
            ],
        )


async def _run_parse_pipeline(
    document: DocumentBlob,
    zone_symbols: list[str],
) -> MpzpParseResult:
    _document_kind = classify_document(document)
    extraction = await extract_document_text(document)
    warnings = [
        _extraction_warning_to_parser_warning(message)
        for message in extraction.warnings
    ]
    if not zone_symbols:
        zones = [
            MpzpZoneResult(
                zone_symbol="UNKNOWN",
                zone_evidence=None,
                parameters=[],
            )
        ]
    else:
        segments = segment_document(extraction)
        zone_section_results = find_zone_sections(segments, zone_symbols)
        warnings.extend(_zone_section_warnings_to_parser_warnings(zone_section_results))
        zones = extract_parameters(zone_section_results, segments)
    status = validate_result(zones)
    result = MpzpParseResult(
        plan_id=None,
        zones=zones,
        status=status,
        warnings=warnings,
    )
    # Walidacja systemowa (etap 5 w pełnym znaczeniu) jest ostatnim krokiem
    # pipeline'u: dokłada kary confidence i wykrywa konflikty parametrów już
    # na kompletnym wyniku, nie zamienia strukturalnego validate_result powyżej.
    return validate_mpzp_result(result)


def _extraction_warning_to_parser_warning(message: str) -> MpzpParserWarning:
    return MpzpParserWarning(
        stage="extract_text",
        code="TEXT_EXTRACTION_WARNING",
        message=message,
        zone_symbol=None,
        parameter_name=None,
        page_number=None,
        severity="warning",
    )


def _zone_section_warnings_to_parser_warnings(
    results: list[ZoneSectionResult],
) -> list[MpzpParserWarning]:
    warnings: list[MpzpParserWarning] = []
    for result in results:
        for warning in result.warnings:
            code, separator, message = warning.partition(":")
            warnings.append(
                MpzpParserWarning(
                    stage="segment_document",
                    code=code,
                    message=message.strip() if separator else warning,
                    zone_symbol=result.zone_symbol,
                    parameter_name=None,
                    page_number=None,
                    severity="warning",
                )
            )
    return warnings


def extract_parameters(
    zone_section_results: list[ZoneSectionResult],
    segments: list[DocumentSegment] | None = None,
) -> list[MpzpZoneResult]:
    """Ekstrahuje parametry liczbowe i opisowe dla każdej strefy z ``segments``.

    ``segments`` jest potrzebny, bo ekstraktory operują na PEŁNYM tekście
    segmentu, nie na krótkim ``source_text`` kandydata sekcji strefy (patrz
    docstringi ``mpzp_parser_numeric.py``/``mpzp_parser_descriptive.py``).
    Brak ``segments`` (np. wywołanie bez segmentacji) daje puste parametry
    bez błędu — to zachowanie zgodne z resztą pipeline'u opartego na
    graceful degradation.
    """
    segments = segments or []
    zones: list[MpzpZoneResult] = []
    for result in zone_section_results:
        top_candidate = result.candidates[0] if result.candidates else None
        zone_evidence = (
            ExtractedEvidence(
                raw_value=None,
                source_text=top_candidate.source_text,
                page_number=top_candidate.page_number,
            )
            if top_candidate is not None
            else None
        )
        parameters = extract_numeric_parameters(
            result.zone_symbol, result, segments
        ) + extract_descriptive_parameters(result.zone_symbol, result, segments)
        zones.append(
            MpzpZoneResult(
                zone_symbol=result.zone_symbol,
                zone_evidence=zone_evidence,
                parameters=parameters,
            )
        )
    return zones


def validate_result(
    zones: list[MpzpZoneResult],
) -> Literal["complete", "partial", "failed"]:
    """STUB: klasyfikuje strukturalną kompletność wyniku parsera.

    Walidacja sprzeczności parametrów i reguł biznesowych pozostaje zakresem
    przyszłego zadania.
    """
    if not zones:
        return "failed"
    if all(len(zone.parameters) == 0 for zone in zones):
        return "partial"
    return "complete"
