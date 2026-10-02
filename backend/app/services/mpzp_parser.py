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

import hashlib
import logging
from typing import Literal

from app.modules.planning.domain.zone_scope import resolve_zone_scope
from app.schemas.mpzp import (
    ExtractedEvidence,
    MpzpParseResult,
    MpzpParserWarning,
    MpzpZoneResult,
    ParserDocumentAudit,
    ParserDocumentPage,
    ParserDocumentSegment,
)
from app.services.mpzp_fetch import DocumentBlob
from app.services.mpzp_parser_descriptive import extract_descriptive_parameters
from app.services.mpzp_parser_extract import (
    OcrProvider,
    classify_document,
    extract_document_text,
)
from app.services.mpzp_parser_blocks import (
    MPZP_PARSER_VERSION_BLOCKS,
    extract_zone_from_blocks,
    scope_warnings,
)
from app.services.mpzp_parser_numeric import extract_numeric_parameters
from app.services.mpzp_parser_segment import (
    DocumentSegment,
    ZoneSectionResult,
    discover_zone_symbols,
    find_zone_sections,
    segment_document,
)
from app.services.mpzp_parser_structure import build_tree_from_extraction, structure_view
from app.services.mpzp_parser_validate import validate_mpzp_result

logger = logging.getLogger(__name__)

# Wersja reguł parsera zapisywana przy każdym parametrze (evidence BK-203).
MPZP_PARSER_VERSION = "mpzp-parser/2.0"
# Tryb zakresu strefy (PV3-06): ``legacy`` skleja tekst kandydackich segmentów (domyślny do
# czasu Task 20.14), ``blocks`` przypisuje parametry na poziomie bloku strefy z prawdziwym
# źródłem (strona i zakres znaków) każdej wartości.
ScopeMode = Literal["legacy", "blocks"]


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
    ocr_provider: OcrProvider | None = None,
    scope_mode: ScopeMode = "legacy",
) -> MpzpParseResult:
    """Uruchamia pipeline i NIGDY nie podnosi niekontrolowanego wyjątku.

    ``zone_symbols`` to pomocniczy zestaw kandydatów z MPZP discovery (Task 3.9),
    a nie ostateczne przypisanie działki do stref planistycznych. ``scope_mode``
    wybiera sposób przypisania parametrów do strefy (patrz ``ScopeMode``).
    """
    zone_symbols = zone_symbols or []
    try:
        return await _run_parse_pipeline(document, zone_symbols, ocr_provider, scope_mode)
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
    ocr_provider: OcrProvider | None,
    scope_mode: ScopeMode = "legacy",
) -> MpzpParseResult:
    _document_kind = classify_document(document)
    extraction = (
        await extract_document_text(document, ocr_provider)
        if ocr_provider is not None
        else await extract_document_text(document)
    )
    warnings = [
        _extraction_warning_to_parser_warning(message)
        for message in extraction.warnings
    ]
    segments = segment_document(extraction)
    inferred_symbols = False
    active_zone_symbols = zone_symbols
    if not active_zone_symbols:
        active_zone_symbols = discover_zone_symbols(segments)
        inferred_symbols = bool(active_zone_symbols)

    if not active_zone_symbols:
        zones = [
            MpzpZoneResult(
                zone_symbol="UNKNOWN",
                zone_evidence=None,
                parameters=[],
            )
        ]
        warnings.append(
            MpzpParserWarning(
                stage="segment_document",
                code="ZONE_SYMBOL_NOT_DISCOVERED",
                message=(
                    "Nie przekazano symbolu strefy i nie udało się go "
                    "jednoznacznie odkryć w dokumencie. Wynik wymaga ręcznej "
                    "weryfikacji."
                ),
                zone_symbol="UNKNOWN",
                parameter_name=None,
                page_number=None,
                severity="warning",
            )
        )
    else:
        if scope_mode == "blocks":
            tree = build_tree_from_extraction(extraction)
            view = structure_view(tree)
            resolution = resolve_zone_scope(view, active_zone_symbols)
            warnings.extend(scope_warnings(resolution, active_zone_symbols))
            zones = [
                extract_zone_from_blocks(symbol, resolution, tree, view) for symbol in active_zone_symbols
            ]
        else:
            zone_section_results = find_zone_sections(segments, active_zone_symbols)
            warnings.extend(_zone_section_warnings_to_parser_warnings(zone_section_results))
            zones = extract_parameters(zone_section_results, segments)
        if inferred_symbols:
            warnings.append(
                MpzpParserWarning(
                    stage="segment_document",
                    code="ZONE_SYMBOLS_INFERRED",
                    message=(
                        "Symbole stref odkryto w tekście bez potwierdzenia z "
                        "geometrii planu; wszystkie wartości wymagają ręcznej "
                        "weryfikacji."
                    ),
                    zone_symbol=None,
                    parameter_name=None,
                    page_number=None,
                    severity="warning",
                )
            )
            zones = [
                zone.model_copy(
                    update={
                        "parameters": [
                            parameter.model_copy(
                                update={
                                    "confidence": parameter.confidence * 0.65,
                                    "manual_review_required": True,
                                }
                            )
                            for parameter in zone.parameters
                        ]
                    }
                )
                for zone in zones
            ]
    status = validate_result(zones)
    if inferred_symbols and status == "complete":
        status = "partial"
    result = MpzpParseResult(
        plan_id=None,
        zones=zones,
        status=status,
        warnings=warnings,
        document_audit=_build_document_audit(
            document.media_type,
            extraction,
            segments,
        ),
    )
    # Walidacja systemowa (etap 5 w pełnym znaczeniu) jest ostatnim krokiem
    # pipeline'u: dokłada kary confidence i wykrywa konflikty parametrów już
    # na kompletnym wyniku, nie zamienia strukturalnego validate_result powyżej.
    validated = validate_mpzp_result(result)
    return _attach_evidence_metadata(
        validated,
        segments,
        document_sha256=hashlib.sha256(document.content).hexdigest(),
        extraction_method=extraction.extraction_method,
        parser_version=MPZP_PARSER_VERSION_BLOCKS if scope_mode == "blocks" else MPZP_PARSER_VERSION,
    )


def _attach_evidence_metadata(
    result: MpzpParseResult,
    segments: list[DocumentSegment],
    *,
    document_sha256: str,
    extraction_method: str,
    parser_version: str = MPZP_PARSER_VERSION,
) -> MpzpParseResult:
    """Dopina do parametru segment/stronę dowodu, hash dokumentu i wersję parsera.

    Segment i strona pochodzą z segmentu, który faktycznie zawiera fragment
    dowodowy — a nie z pierwszego segmentu strefy — więc wskazują miejsce
    wartości w uchwale także wtedy, gdy strefa ma kilka kandydatów sekcji.
    Parametr z trybu blokowego niesie już stronę, zakres znaków i blok z własnego
    dopasowania, więc jego miejsce nie jest ponownie wyszukiwane.
    """
    normalized_segments = [
        (segment, " ".join(segment.text.split())) for segment in segments
    ]

    def locate(evidence: str | None) -> DocumentSegment | None:
        if not evidence:
            return None
        needle = " ".join(evidence.split())
        return next(
            (segment for segment, text in normalized_segments if needle in text),
            None,
        )

    zones = []
    for zone in result.zones:
        parameters = []
        for parameter in zone.parameters:
            if parameter.block_id is not None:
                parameters.append(
                    parameter.model_copy(
                        update={
                            "document_sha256": document_sha256,
                            "extraction_method": extraction_method,
                            "parser_version": parser_version,
                        }
                    )
                )
                continue
            segment = locate(parameter.source_text)
            parameters.append(
                parameter.model_copy(
                    update={
                        "segment_id": segment.segment_id if segment else None,
                        "page_number": (
                            segment.page_number
                            if segment is not None and segment.page_number is not None
                            else parameter.page_number
                        ),
                        "document_sha256": document_sha256,
                        "extraction_method": extraction_method,
                        "parser_version": parser_version,
                    }
                )
            )
        zones.append(zone.model_copy(update={"parameters": parameters}))
    return result.model_copy(update={"zones": zones})


def _extraction_warning_to_parser_warning(message: str) -> MpzpParserWarning:
    lowered = message.lower()
    if "ocr nie powiódł" in lowered or "bez czytelnego tekstu" in lowered:
        code = "OCR_FAILED"
    elif "odczytano przez ocr" in lowered:
        code = "OCR_APPLIED"
    elif "ocr" in lowered:
        code = "NEEDS_OCR"
    else:
        code = "TEXT_EXTRACTION_WARNING"
    return MpzpParserWarning(
        stage="extract_text",
        code=code,
        message=message,
        zone_symbol=None,
        parameter_name=None,
        page_number=None,
        severity="warning",
    )


def _build_document_audit(
    media_type: str,
    extraction,
    segments: list[DocumentSegment],
) -> ParserDocumentAudit:
    return ParserDocumentAudit(
        media_type=media_type,
        extraction_method=extraction.extraction_method,
        ocr_engine_version=extraction.ocr_engine_version,
        quality_score=extraction.quality_score,
        manual_review_required=extraction.manual_review_required,
        pages=[
            ParserDocumentPage(
                page_number=index,
                text=text,
                ocr_used=extraction.ocr_used,
                quality=(
                    extraction.page_qualities[index - 1]
                    if index <= len(extraction.page_qualities)
                    else None
                ),
                blocks=(
                    extraction.blocks[index - 1]
                    if index <= len(extraction.blocks)
                    else []
                ),
            )
            for index, text in enumerate(extraction.pages, start=1)
        ],
        segments=[
            ParserDocumentSegment(
                segment_id=segment.segment_id,
                text=segment.text,
                page_number=segment.page_number,
                heading=segment.heading,
                source=segment.source,
            )
            for segment in segments
        ],
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
