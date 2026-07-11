"""Fasada etapowego pipeline'u parsera dokumentów MPZP.

``parse_mpzp_document`` NIGDY nie propaguje wyjątku na poziomie całej funkcji:
nieoczekiwany błąd jest logowany i zamieniany na ``MpzpParseResult`` ze statusem
``failed``. Etapy klasyfikacji i ekstrakcji tekstu delegują do realnych
implementacji w ``mpzp_parser_extract.py``. Segmentacja, ekstrakcja parametrów
i walidacja są na tym etapie jawnie udokumentowanymi stubami; ich rzeczywista
logika należy do przyszłych zadań parsera.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.schemas.mpzp import MpzpParseResult, MpzpParserWarning, MpzpZoneResult
from app.services.mpzp_fetch import DocumentBlob
from app.services.mpzp_parser_extract import (
    TextExtractionResult,
    classify_document,
    extract_document_text,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _DocumentSegment:
    zone_symbol: str
    text: str
    page_number: int | None


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
    segments = segment_document(extraction, zone_symbols)
    zones = extract_parameters(segments)
    status = validate_result(zones)
    return MpzpParseResult(
        plan_id=None,
        zones=zones,
        status=status,
        warnings=warnings,
    )


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


def segment_document(
    extraction: TextExtractionResult,
    zone_symbols: list[str],
) -> list[_DocumentSegment]:
    """STUB: zwraca jeden segment całego tekstu dla pierwszego kandydata.

    Rzeczywista segmentacja wielu stref na podstawie struktury uchwały będzie
    wdrożona w przyszłym zadaniu. Brak kandydata jest jawnie oznaczany UNKNOWN.
    """
    zone_symbol = zone_symbols[0] if zone_symbols else "UNKNOWN"
    combined_text = "\n".join(extraction.pages)
    page_number = 1 if extraction.pages else None
    return [
        _DocumentSegment(
            zone_symbol=zone_symbol,
            text=combined_text,
            page_number=page_number,
        )
    ]


def extract_parameters(segments: list[_DocumentSegment]) -> list[MpzpZoneResult]:
    """STUB: tworzy strefy bez parametrów do czasu wdrożenia ekstraktorów."""
    return [
        MpzpZoneResult(
            zone_symbol=segment.zone_symbol,
            zone_evidence=None,
            parameters=[],
        )
        for segment in segments
    ]


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
