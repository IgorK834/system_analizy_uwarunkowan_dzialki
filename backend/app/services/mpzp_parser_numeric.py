"""Ekstraktory parametrów liczbowych z segmentów dokumentu MPZP.

Od PV3-07 moduł NIE zawiera własnych wzorców: deleguje do jednego silnika opartego na
leksykonie (``app.modules.planning.domain.quantity_engine``), który obsługuje też parser
opisowy i serwis reguł, więc poprawka sformułowania jest robiona raz. Moduł zachowuje swój
kontrakt: ``extract_numeric_matches`` (lista ``(nazwa, jednostka, dopasowania)`` dla trybu
dotychczasowego i blokowego) oraz ``extract_numeric_parameters`` (parametry strefy).

Ekstraktory operują na PEŁNYM tekście segmentu (``DocumentSegment.text``), a nie na krótkim
``ZoneSectionCandidate.source_text`` (~200 znaków kontekstu symbolu strefy), bo wartości leżą
często w dalszej części paragrafu.

Każda znaleziona odmienna wartość liczbowa dla tego samego parametru w tej samej strefie jest
zwracana jako OSOBNY ``MpzpParameter`` — moduł nigdy nie wybiera cicho jednej wartości z wielu.
Od PV3-08 wartość niesie swoje WARUNKI (``dla dachu płaskiego``, ``dla budynków gospodarczych``):
różne warunki to wartości warunkowe, a sprzeczność (i kara pewności) powstaje dopiero, gdy ta
sama przesłanka — ten sam zestaw warunków albo ich brak — ma kilka wartości.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Final

from app.modules.planning.domain.quantity_engine import (
    ENGINE_VERSION,
    Qualifier,
    QuantityMatch,
    extract_quantities,
)
from app.modules.planning.domain.quantity_lexicon import PARAMETER_FAMILIES, PARAMETER_ORDER
from app.modules.planning.domain.quantity_normalization import engine_confidence
from app.modules.planning.domain.value_conditions import (
    VALUE_KIND_CONDITIONAL,
    VALUE_KIND_CONFLICT,
    VALUE_KIND_UNCONDITIONAL,
    ValueCondition,
    classify_conditions,
    condition_key,
)
from app.schemas.mpzp import MpzpParameter
from app.schemas.mpzp import ValueCondition as ValueConditionModel
from app.services.mpzp_parser_segment import DocumentSegment, ZoneSectionResult
from app.shared.numbers import parse_polish_number

# Tymczasowy alias kompatybilności dla istniejących testów/wywołań prywatnej
# funkcji; jedyna implementacja pozostaje w app.shared.numbers.
_parse_polish_number = parse_polish_number

logger = logging.getLogger(__name__)

__all__ = [
    "ENGINE_VERSION",
    "extract_numeric_matches",
    "extract_numeric_parameters",
]

# Kara analogiczna do _MULTI_CANDIDATE_CONFIDENCE_PENALTY w mpzp_parser_segment.py:
# sprzeczne wartości tego samego parametru w tej samej strefie nie mogą zachować
# wysokiego confidence, bo żadna z nich nie została jednoznacznie wybrana.
_CONFLICTING_VALUE_CONFIDENCE_PENALTY: Final[float] = 0.6
_EXCERPT_PAD: Final[int] = 40
_CONDITION_EXCERPT_PAD: Final[int] = 20
_EVIDENCE_MAX: Final[int] = 480

# Jednostka parametru z leksykonu: nazwa parametru → jednostka zapisywana w evidence.
_UNIT_BY_PARAMETER: Final[dict[str, str | None]] = {
    name: spec.unit for spec in PARAMETER_FAMILIES.values() for name in spec.names.values() if name is not None
}


@dataclass(frozen=True)
class _RawMatch:
    """Pojedyncze dopasowanie liczbowe przed scaleniem konfliktów."""

    normalized_value: float
    raw_value: str
    source_text: str
    # Zakres dopasowania w tekście przekazanym ekstraktorowi (znaki, ``end`` wyłącznie);
    # tryb blokowy (PV3-06) odwzorowuje go na stronę i pozycję w dokumencie.
    start: int | None = None
    end: int | None = None
    # Ślad silnika (PV3-07): strategia dopasowania, flagi przeróbek zapisu, pewność silnika
    # oraz rozpiętości kwalifikatorów (warunki wartości, PV3-08).
    strategy: str | None = None
    flags: tuple[str, ...] = ()
    engine_confidence: float = 0.85
    qualifiers: tuple[Qualifier, ...] = ()
    operator: str | None = None
    per_text: str | None = None
    scope_symbols: tuple[str, ...] = ()
    conditions: tuple[ValueCondition, ...] = ()
    source: QuantityMatch | None = field(default=None, repr=False, compare=False)


def _context_excerpt(
    text: str, start: int, end: int, pad: int = _EXCERPT_PAD, bounds: tuple[int, int] | None = None
) -> str:
    """Fragment wokół ``[start, end)`` z marginesem ``pad``, nie wychodzący poza ``bounds`` (segment)."""
    low, high = bounds if bounds is not None else (0, len(text))
    left = max(low, start - pad)
    right = min(high, end + pad)
    return " ".join(text[left:right].split())


def _evidence_excerpt(
    text: str, match: QuantityMatch, conditions: tuple[ValueCondition, ...], bounds: tuple[int, int] | None = None
) -> str:
    """Fragment dowodowy: wartość z kontekstem, a dla wartości warunkowej także cytat jej warunku.

    Cytat z nagłówka nadrzędnej pozycji może leżeć daleko, więc zakres obejmuje tylko warunki
    z tej samej klauzuli i tylko wtedy, gdy całość nie przekracza ``_EVIDENCE_MAX`` znaków. Fragment nie
    przekracza granicy segmentu (tekst z różnych stron nie tworzy jednego cytatu).
    """
    inline = [c for c in conditions if c.role != "header"]
    if not inline:
        return _context_excerpt(text, match.start, match.end, bounds=bounds)
    left = min([match.start, *(c.start for c in inline)])
    right = max([match.end, *(c.end for c in inline)])
    if right - left > _EVIDENCE_MAX or (bounds is not None and (left < bounds[0] or right > bounds[1])):
        return _context_excerpt(text, match.start, match.end, bounds=bounds)
    return _context_excerpt(text, left, right, pad=_CONDITION_EXCERPT_PAD, bounds=bounds)


def _raw_match(text: str, match: QuantityMatch, segment_spans: tuple[tuple[int, int], ...] = ()) -> _RawMatch:
    conditions = classify_conditions(text, match.qualifiers)
    bounds = next((span for span in segment_spans if span[0] <= match.start and match.end <= span[1]), None)
    return _RawMatch(
        normalized_value=match.value,
        raw_value=match.raw_value,
        source_text=_evidence_excerpt(text, match, conditions, bounds),
        start=match.start,
        end=match.end,
        strategy=match.strategy,
        flags=match.flags,
        engine_confidence=engine_confidence(match.flags),
        qualifiers=match.qualifiers,
        operator=match.operator,
        per_text=match.per_text,
        scope_symbols=match.scope_symbols,
        conditions=conditions,
        source=match,
    )


def _condition_models(conditions: tuple[ValueCondition, ...]) -> list[ValueConditionModel]:
    return [ValueConditionModel(kind=c.kind, label=c.label, quote=c.quote) for c in conditions]  # type: ignore[arg-type]


def _make_parameters(
    name: str,
    unit: str | None,
    matches: list[_RawMatch],
    page_number: int | None,
) -> list[MpzpParameter]:
    """Buduje listę MpzpParameter z ewentualną karą za sprzeczne wartości.

    Deduplikacja jest po parze (wartość, zestaw warunków): to samo ustalenie powtórzone w innym
    miejscu tekstu nie jest konfliktem, ale ta sama liczba z innym warunkiem to inna informacja.
    Konflikt (kara pewności i ręczna weryfikacja) dotyczy wyłącznie wartości o TYM SAMYM zestawie
    warunków (PV3-08); wartości o różnych warunkach są warunkowe, a nie sprzeczne.
    """
    if not matches:
        return []

    deduped: list[_RawMatch] = []
    seen: set[tuple[float, frozenset]] = set()
    values_by_condition: dict[frozenset, set[float]] = {}
    for match in matches:
        key = condition_key(match.conditions)
        identity = (match.normalized_value, key)
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(match)
        values_by_condition.setdefault(key, set()).add(match.normalized_value)

    parameters: list[MpzpParameter] = []
    for match in deduped:
        key = condition_key(match.conditions)
        conflict = len(values_by_condition[key]) > 1
        confidence = match.engine_confidence
        manual_review_required = False
        if conflict:
            confidence *= _CONFLICTING_VALUE_CONFIDENCE_PENALTY
            manual_review_required = True
        value_kind = (
            VALUE_KIND_CONFLICT if conflict else (VALUE_KIND_CONDITIONAL if match.conditions else VALUE_KIND_UNCONDITIONAL)
        )
        parameters.append(
            MpzpParameter(
                name=name,
                normalized_value=match.normalized_value,
                unit=unit,
                raw_value=match.raw_value,
                source_text=match.source_text,
                page_number=page_number,
                confidence=confidence,
                manual_review_required=manual_review_required,
                extraction_strategy=match.strategy,
                normalization_flags=list(match.flags),
                conditions=_condition_models(match.conditions),
                value_kind=value_kind,  # type: ignore[arg-type]
            )
        )
    return parameters


def extract_numeric_matches(
    text: str, zone_symbol: str | None = None, segment_spans: tuple[tuple[int, int], ...] = ()
) -> list[tuple[str, str | None, list[_RawMatch]]]:
    """Dopasowania wszystkich parametrów liczbowych w tekście: ``(nazwa, jednostka, dopasowania)``.

    Każde dopasowanie niesie zakres w ``text``; kolejność parametrów jest stała
    (``PARAMETER_ORDER``). Funkcję współdzielą tryb dotychczasowy (tekst sklejony z segmentów)
    i tryb blokowy (tekst jednego bloku strefy). ``zone_symbol`` ogranicza wartości do tej strefy
    (``w terenie 6.6.MW/U – maksimum 55%`` nie dotyczy strefy ``6.8.MW/U``). ``segment_spans`` to zakresy
    segmentów w ``text`` (tryb dotychczasowy skleja segmenty): fragment dowodowy nie przekracza segmentu.
    """
    extraction = extract_quantities(text, target_symbol=zone_symbol)
    by_name: dict[str, list[_RawMatch]] = {name: [] for name in PARAMETER_ORDER}
    for match in extraction.matches:
        by_name.setdefault(match.parameter, []).append(_raw_match(text, match, segment_spans))
    return [(name, _UNIT_BY_PARAMETER.get(name), by_name[name]) for name in PARAMETER_ORDER]


def extract_numeric_parameters(
    zone_symbol: str,
    zone_section: ZoneSectionResult,
    segments: list[DocumentSegment],
) -> list[MpzpParameter]:
    """Wyciąga znormalizowane parametry liczbowe dla jednej strefy MPZP.

    Ekstraktory działają na łączonym tekście wszystkich segmentów wskazanych
    przez kandydatów ``zone_section`` (zwykle jeden segment, ale wieloznaczność
    segmentacji może dać więcej). Brak kandydatów oznacza brak tekstu do
    przeszukania — to nie jest błąd tego modułu, tylko konsekwencja
    wcześniejszego etapu ``segment_document``/``find_zone_sections``.

    Uwaga (PV3-06): to jest tryb dotychczasowy — wartości wszystkich kandydatów dostają
    stronę pierwszego z nich. Tryb blokowy (``mpzp_parser_blocks``) przypisuje stronę i
    zakres znaków z miejsca każdego dopasowania.
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
    spans: list[tuple[int, int]] = []
    cursor = 0
    for segment in relevant_segments:
        spans.append((cursor, cursor + len(segment.text)))
        cursor += len(segment.text) + 1  # separator „\n” między segmentami

    parameters: list[MpzpParameter] = []
    for name, unit, matches in extract_numeric_matches(combined_text, zone_symbol, tuple(spans)):
        parameters.extend(_make_parameters(name, unit, matches, page_number))
    return parameters
