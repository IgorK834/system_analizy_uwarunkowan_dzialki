"""Ekstrakcja parametrów strefy z bloków zakresu (PV3-06).

Tryb blokowy zastępuje sklejanie tekstu kandydackich segmentów: każdy blok strefy
(``ZoneBlock``) jest przeszukiwany osobno, a KAŻDA wartość niesie stronę i zakres znaków
z własnego dopasowania — w tekście SUROWYM strony, więc dowód da się sprawdzić względem
zapisanego tekstu stron. Wartości z klauzuli ogólnej, resztowej i z zakresu
nierozstrzygniętego mają własny ``scope_kind``, nie są mieszane z sekcją strefy i
zawsze wymagają ręcznej weryfikacji.

Dopasowania wartości pochodzą z tego samego silnika leksykonu co tryb dotychczasowy
(``quantity_engine`` przez ``mpzp_parser_numeric``, PV3-07); ten moduł odpowiada za przypisanie
do strefy i źródło wartości. Symbol strefy trafia do silnika, więc wartości przypisane w tekście
innym strefom (``w terenie 6.6.MW/U – maksimum 55%``) nie trafiają do wyniku strefy ``6.8.MW/U``.
"""

from __future__ import annotations

from typing import Final

from app.modules.documents.domain.document_tree import DocumentTree
from app.modules.planning.domain.zone_blocks import DocumentStructureView, ZoneBlock
from app.modules.planning.domain.zone_scope import (
    ZONE_SCOPE_MULTIPLE_SECTIONS,
    ZONE_SECTION_NOT_FOUND,
    ZONE_SYMBOL_NEAR_MATCH,
    ZONE_SYMBOL_OCR_MATCH,
    ScopeResolution,
)
from app.modules.planning.domain.zone_blocks import ZONE_SCOPE_AMBIGUOUS
from app.modules.planning.domain.value_conditions import (
    VALUE_KIND_CONDITIONAL,
    VALUE_KIND_CONFLICT,
    VALUE_KIND_UNCONDITIONAL,
    condition_key,
)
from app.schemas.mpzp import ExtractedEvidence, MpzpParameter, MpzpParserWarning, MpzpZoneResult
from app.services.mpzp_parser_descriptive import extract_descriptive_parameters
from app.services.mpzp_parser_numeric import _condition_models, extract_numeric_matches
from app.services.mpzp_parser_segment import DocumentSegment, ZoneSectionCandidate, ZoneSectionResult

MPZP_PARSER_VERSION_BLOCKS: Final[str] = "mpzp-parser/3.0-det+scope.1"
_BASE_CONFIDENCE: Final[float] = 0.85
_DEDICATED_SCOPE_CONFIDENCE: Final[float] = 0.9
_CONFLICT_PENALTY: Final[float] = 0.6
_MANUAL_SCOPE_THRESHOLD: Final[float] = 0.6
_EXCERPT_PAD: Final[int] = 40
_CONDITION_PAD: Final[int] = 20
_EVIDENCE_MAX: Final[int] = 480

_WARNING_MESSAGES: Final[dict[str, str]] = {
    ZONE_SECTION_NOT_FOUND: "nie znaleziono sekcji dokumentu dla strefy {symbol}.",
    ZONE_SCOPE_AMBIGUOUS: (
        "Zakres strefy {symbol} nie został rozstrzygnięty: wartości pochodzą z szerszego fragmentu "
        "dokumentu i wymagają ręcznej weryfikacji."
    ),
    ZONE_SYMBOL_OCR_MATCH: (
        "Symbol strefy {symbol} dopasowano przy założeniu pomyłek OCR (I/1, O/0, l/1); pewność obniżono."
    ),
    ZONE_SCOPE_MULTIPLE_SECTIONS: "Znaleziono kilka osobnych sekcji dla strefy {symbol}; wymagają porównania.",
    ZONE_SYMBOL_NEAR_MATCH: (
        "W dokumencie symbol strefy {symbol} występuje z drobną różnicą zapisu (np. brak kropki); "
        "pozycję listy dołączono, ale wymaga ręcznej weryfikacji."
    ),
}


def scope_warnings(resolution: ScopeResolution, symbols: list[str]) -> list[MpzpParserWarning]:
    """Ostrzeżenia rozstrzygania zakresu: per symbol i per blok (np. pozycje „w strefie …”)."""
    warnings: list[MpzpParserWarning] = []
    for symbol in symbols:
        codes = list(resolution.warnings_for(symbol))
        for block in resolution.blocks_for(symbol):
            codes.extend(code for code in block.warnings if code not in codes)
        for code in dict.fromkeys(codes):
            template = _WARNING_MESSAGES.get(code)
            if template is None:
                continue
            warnings.append(
                MpzpParserWarning(
                    stage="segment_document",
                    code=code,
                    message=template.format(symbol=symbol),
                    zone_symbol=symbol,
                    parameter_name=None,
                    page_number=None,
                    severity="warning",
                )
            )
    return warnings


def _excerpt(text: str, start: int, end: int) -> str:
    return " ".join(text[max(0, start - _EXCERPT_PAD) : end + _EXCERPT_PAD].split())


def _evidence_text(view: DocumentStructureView, block: ZoneBlock, match, doc_start: int, doc_end: int) -> str:
    """Fragment dowodowy wartości; dla wartości warunkowej obejmuje też cytat warunku z tej samej klauzuli."""
    inline = [c for c in match.conditions if c.role != "header"]
    if inline:
        left = min([match.start, *(c.start for c in inline)])
        right = max([match.end, *(c.end for c in inline)])
        located = block.locate(left, right)
        if len(located) == 1 and located[0][1] - located[0][0] <= _EVIDENCE_MAX:
            return " ".join(view.text[max(0, located[0][0] - _CONDITION_PAD) : located[0][1] + _CONDITION_PAD].split())
    return _excerpt(view.text, doc_start, doc_end)


def extract_block_parameters(
    block: ZoneBlock, tree: DocumentTree, view: DocumentStructureView, zone_symbol: str | None = None
) -> list[MpzpParameter]:
    """Parametry liczbowe jednego bloku; strona i zakres z miejsca dopasowania."""
    factor = min(1.0, block.scope_confidence / _DEDICATED_SCOPE_CONFIDENCE)
    manual = block.scope_kind != "zone_section" or block.scope_confidence < _MANUAL_SCOPE_THRESHOLD
    found: list[MpzpParameter] = []
    for name, unit, matches in extract_numeric_matches(block.text, zone_symbol):
        for match in matches:
            if match.start is None or match.end is None:
                continue
            spans = block.locate(match.start, match.end)
            if not spans:
                continue
            doc_start, doc_end = spans[0]
            raw_spans = tree.document.raw_spans(doc_start, doc_end)
            page, raw_start, raw_end = raw_spans[0] if raw_spans else (view.page_at(doc_start), None, None)
            found.append(
                MpzpParameter(
                    name=name,
                    normalized_value=match.normalized_value,
                    unit=unit,
                    raw_value=match.raw_value,
                    source_text=_evidence_text(view, block, match, doc_start, doc_end),
                    page_number=page,
                    confidence=round(_BASE_CONFIDENCE * factor, 4),
                    manual_review_required=manual,
                    segment_id=block.block_id,
                    char_start=raw_start,
                    char_end=raw_end,
                    block_id=block.block_id,
                    scope_kind=block.scope_kind,
                    scope_confidence=block.scope_confidence,
                    scope_strategy=block.strategy,
                    extraction_strategy=match.strategy,
                    normalization_flags=list(match.flags),
                    conditions=_condition_models(match.conditions),
                    value_kind=VALUE_KIND_CONDITIONAL if match.conditions else VALUE_KIND_UNCONDITIONAL,  # type: ignore[arg-type]
                )
            )
    return found


def _descriptive(symbol: str, blocks: list[ZoneBlock]) -> list[MpzpParameter]:
    """Opisy (przeznaczenie, zakazy…) z bloków sekcji strefy; bez zakresu znaków wartości."""
    sections = [block for block in blocks if block.scope_kind == "zone_section"]
    if not sections:
        return []
    segments = [
        DocumentSegment(segment_id=block.block_id, text=block.text, page_number=block.pages[0] if block.pages else None,
                        heading=None, source="paragraph")
        for block in sections
    ]
    candidates = [
        ZoneSectionCandidate(symbol, block.block_id, block.text[:200], block.pages[0] if block.pages else None,
                             block.scope_confidence, f"scope/strategy-{block.strategy}")
        for block in sections
    ]
    parameters = extract_descriptive_parameters(symbol, ZoneSectionResult(zone_symbol=symbol, candidates=candidates), segments)
    return [p.model_copy(update={"scope_kind": "zone_section", "scope_strategy": sections[0].strategy}) for p in parameters]


def extract_zone_from_blocks(
    symbol: str, resolution: ScopeResolution, tree: DocumentTree, view: DocumentStructureView
) -> MpzpZoneResult:
    """Wynik jednej strefy: parametry z jej bloków, bez mieszania zakresów i bez duplikatów."""
    blocks = list(resolution.blocks_for(symbol))
    parameters: list[MpzpParameter] = []
    seen: set[tuple[str, str, object, frozenset]] = set()
    for block in blocks:
        for parameter in extract_block_parameters(block, tree, view, symbol):
            key = (parameter.name, block.scope_kind, parameter.normalized_value, condition_key(parameter.conditions))
            if key not in seen:
                seen.add(key)
                parameters.append(parameter)
    # Konflikt (kara pewności i ręczna weryfikacja) tylko dla wartości o tej samej przesłance:
    # ta sama nazwa, ten sam zakres bloku i ten sam zestaw warunków (PV3-08).
    values: dict[tuple[str, str | None, frozenset], set[object]] = {}
    for parameter in parameters:
        values.setdefault((parameter.name, parameter.scope_kind, condition_key(parameter.conditions)), set()).add(
            parameter.normalized_value
        )
    parameters = [
        parameter.model_copy(
            update={
                "confidence": round(parameter.confidence * _CONFLICT_PENALTY, 4),
                "manual_review_required": True,
                "value_kind": VALUE_KIND_CONFLICT,
            }
        )
        if len(values[(parameter.name, parameter.scope_kind, condition_key(parameter.conditions))]) > 1
        else parameter
        for parameter in parameters
    ]
    parameters.extend(_descriptive(symbol, blocks))
    first = next((b for b in blocks if b.scope_kind == "zone_section"), blocks[0] if blocks else None)
    evidence = (
        ExtractedEvidence(raw_value=None, source_text=" ".join(first.text[:200].split()),
                          page_number=first.pages[0] if first.pages else None)
        if first is not None
        else None
    )
    return MpzpZoneResult(zone_symbol=symbol, zone_evidence=evidence, parameters=parameters)
