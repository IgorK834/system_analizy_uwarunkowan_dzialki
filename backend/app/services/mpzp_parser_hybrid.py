"""Tryby parsera MPZP i krok modelu językowego: wybór par, scalanie, tryb cienia (PV3-14).

``MPZP_PARSER_MODE``:

- ``legacy`` — zachowanie sprzed PV3-14 (sklejanie segmentów), bez modelu;
- ``v3`` — rdzeń deterministyczny na blokach stref (PV3-05–09), bez modelu;
- ``hybrid_shadow`` — odpowiedź i zapis są dokładnie wynikiem ``v3``; model jest liczony W TLE dla
  wszystkich par (ewaluacja), a różnice trafiają wyłącznie do logu i liczników;
- ``hybrid`` — ``v3`` + zweryfikowani kandydaci modelu (``ai_candidate``).

Kontrola kosztu w ``hybrid``: model jest wywoływany tylko dla bloków stref, które mają parę
(strefa, parametr) bez wartości deterministycznej, z konfliktem albo z niską ``scope_confidence``;
przyjmowane są wyłącznie wartości dla tych par.

Polityka scalania: wartość deterministyczna ma pierwszeństwo; wartość modelu jest dodawana jako
``ai_candidate``, gdy brak deterministycznej; rozbieżność zachowuje obie z ręczną weryfikacją;
zgodność nie dodaje kopii. Niedostępność modelu, wyczerpanie budżetu albo błąd bramek dają wynik
deterministyczny z ostrzeżeniem ``MPZP_LLM_UNAVAILABLE`` i statusem ``partial`` — nigdy wyjątek ani
ciche pominięcie.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from app.modules.documents.domain.document_tree import DocumentTree
from app.modules.planning.application import llm_metrics
from app.modules.planning.application.llm_pipeline import LlmPipelineOutcome, MpzpLlmPipeline
from app.modules.planning.domain import extraction_contract as contract
from app.modules.planning.domain.candidate_verifier import AcceptedCandidate, DocumentContext
from app.modules.planning.domain.zone_blocks import DocumentStructureView, ZoneBlock
from app.modules.planning.domain.zone_scope import ScopeResolution
from app.schemas.mpzp import MpzpParameter, MpzpParserWarning, MpzpParseResult, MpzpZoneResult, ValueCondition

logger = logging.getLogger(__name__)

ParserMode = Literal["legacy", "v3", "hybrid_shadow", "hybrid"]
PARSER_MODES: Final[tuple[str, ...]] = ("legacy", "v3", "hybrid_shadow", "hybrid")
LLM_MODES: Final[frozenset[str]] = frozenset({"hybrid_shadow", "hybrid"})
WARNING_LLM_UNAVAILABLE: Final[str] = "MPZP_LLM_UNAVAILABLE"
WARNING_LLM_CANDIDATES_REJECTED: Final[str] = "MPZP_LLM_CANDIDATES_REJECTED"
REASON_LLM_DISABLED: Final[str] = "llm_disabled"
REASON_KILL_SWITCH: Final[str] = "kill_switch"
DEFAULT_SCOPE_THRESHOLD: Final[float] = 0.6


@dataclass(frozen=True)
class BlocksContext:
    """Stan trybu blokowego potrzebny krokowi modelu (bez ponownego parsowania dokumentu)."""

    tree: DocumentTree
    view: DocumentStructureView
    resolution: ScopeResolution
    symbols: tuple[str, ...]
    extraction_method: str
    quality_score: float | None
    document_sha256: str
    parser_version: str


# --- wybór par (kontrola kosztu) ------------------------------------------------------------------------


def select_targets(
    zones: Sequence[MpzpZoneResult], threshold: float = DEFAULT_SCOPE_THRESHOLD
) -> dict[str, frozenset[str]]:
    """Pary (strefa, parametr katalogu) do modelu: brak wartości deterministycznej, konflikt albo niski zakres."""
    targets: dict[str, frozenset[str]] = {}
    for zone in zones:
        wanted: set[str] = set()
        for parameter in contract.PARAMETERS:
            found = [p for p in zone.parameters if p.name == parameter and p.normalized_value is not None]
            if not found:
                wanted.add(parameter)
            elif any(p.value_kind == "conflict" or p.conflict_group_id is not None for p in found):
                wanted.add(parameter)
            elif all(p.scope_kind == "fallback" or (p.scope_confidence or 0.0) < threshold for p in found):
                wanted.add(parameter)
        if wanted:
            targets[zone.zone_symbol] = frozenset(wanted)
    return targets


def blocks_for_targets(resolution: ScopeResolution, targets: Mapping[str, frozenset[str]]) -> list[ZoneBlock]:
    blocks: dict[str, ZoneBlock] = {}
    for symbol in targets:
        for block in resolution.blocks_for(symbol):
            blocks.setdefault(block.block_id, block)
    return list(blocks.values())


def all_blocks(resolution: ScopeResolution, symbols: Sequence[str]) -> list[ZoneBlock]:
    return blocks_for_targets(resolution, {symbol: frozenset(contract.PARAMETERS) for symbol in symbols})


# --- kandydat → parametr parsera -------------------------------------------------------------------------


def to_parser_parameter(item: AcceptedCandidate, context: BlocksContext) -> MpzpParameter:
    """Parametr parsera z przyjętego kandydata: strona i zakres znaków z dopasowania w bloku."""
    page, char_start, char_end = item.page_number, None, None
    if item.value_doc_spans:
        raw_spans = context.tree.document.raw_spans(*item.value_doc_spans[0])
        if raw_spans:
            page, char_start, char_end = raw_spans[0]
    provenance = item.provenance
    return MpzpParameter(
        name=item.parameter,
        normalized_value=item.value,
        unit=item.unit,
        raw_value=item.raw_value,
        source_text=item.evidence_text,
        page_number=page,
        confidence=item.confidence,
        manual_review_required=True,
        segment_id=item.block_id,
        extraction_method=item.extraction_method,
        parser_version=context.parser_version,
        document_sha256=context.document_sha256,
        char_start=char_start,
        char_end=char_end,
        block_id=item.block_id,
        scope_kind=item.applicability,  # type: ignore[arg-type]
        scope_confidence=item.scope_confidence,
        scope_strategy=item.scope_strategy,
        conditions=[ValueCondition(kind=c.kind, label=c.label, quote=c.quote) for c in item.conditions],  # type: ignore[arg-type]
        value_kind="conditional" if item.conditions else "unconditional",
        confidence_band=item.confidence_band,  # type: ignore[arg-type]
        confidence_calibration=item.confidence_calibration,
        confidence_features=dict(item.confidence_features),
        extraction_strategy="llm",
        normalization_flags=list(item.flags),
        review_status="ai_candidate",
        model_id=provenance.model_id if provenance else None,
        prompt_version=provenance.prompt_version if provenance else None,
        response_sha256=provenance.response_sha256 if provenance else None,
    )


def _condition_key(conditions: Sequence[ValueCondition]) -> frozenset[tuple[str, str]]:
    return frozenset((c.kind, " ".join(c.quote.casefold().split())) for c in conditions)


def _same_value(left: object, right: object) -> bool:
    try:
        return abs(float(left) - float(right)) <= 1e-6  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return left == right


def merge_llm_candidates(
    zones: Sequence[MpzpZoneResult], accepted: Sequence[AcceptedCandidate], context: BlocksContext
) -> tuple[list[MpzpZoneResult], int, int]:
    """Scala kandydatów modelu z wynikiem deterministycznym; zwraca (strefy, dodane, rozbieżne)."""
    by_symbol: dict[str, list[AcceptedCandidate]] = {}
    for item in accepted:
        by_symbol.setdefault(item.zone_symbol, []).append(item)
    merged: list[MpzpZoneResult] = []
    added = disagreements = 0
    for zone in zones:
        parameters = list(zone.parameters)
        contested: set[str] = set()
        for item in by_symbol.get(zone.zone_symbol, []):
            candidate = to_parser_parameter(item, context)
            deterministic = [p for p in zone.parameters if p.name == item.parameter and p.normalized_value is not None]
            same_premise = [p for p in deterministic if _condition_key(p.conditions) == _condition_key(candidate.conditions)]
            if any(_same_value(p.normalized_value, candidate.normalized_value) for p in same_premise):
                continue  # zgodność: wartość deterministyczna wystarcza
            if same_premise:
                contested.add(item.parameter)
                disagreements += 1
            parameters.append(candidate)
            added += 1
        if contested:
            parameters = [
                p.model_copy(update={"manual_review_required": True})
                if p.name in contested and p.review_status is None
                else p
                for p in parameters
            ]
        merged.append(zone.model_copy(update={"parameters": parameters}))
    return merged, added, disagreements


# --- ostrzeżenie i krok trybu ``hybrid`` -------------------------------------------------------------------


def unavailable_warning(reasons: Sequence[str]) -> MpzpParserWarning:
    codes = ", ".join(dict.fromkeys(reasons)) or "unknown"
    return MpzpParserWarning(
        stage="extract_parameters",
        code=WARNING_LLM_UNAVAILABLE,
        message=(
            "Odczyt automatyczny modelem językowym był niedostępny albo niepełny "
            f"({codes}); wynik pochodzi z rdzenia deterministycznego i wymaga ręcznej weryfikacji."
        ),
        zone_symbol=None,
        parameter_name=None,
        page_number=None,
        severity="warning",
    )


def _document_context(context: BlocksContext) -> DocumentContext:
    return DocumentContext(
        view=context.view,
        extraction_method=context.extraction_method,
        ocr_quality=context.quality_score if context.extraction_method == "ocr" else None,
        zone_symbols=context.symbols,
    )


def rejected_warning(counts: Mapping[str, int]) -> MpzpParserWarning:
    summary = ", ".join(f"{gate}: {count}" for gate, count in counts.items() if count)
    return MpzpParserWarning(
        stage="extract_parameters",
        code=WARNING_LLM_CANDIDATES_REJECTED,
        message=(
            "Część odczytów automatycznych odrzuciły bramki deterministyczne względem tekstu uchwały "
            f"({summary}); nie trafiły do wyniku, a brakujące wartości wymagają ręcznej weryfikacji."
        ),
        zone_symbol=None,
        parameter_name=None,
        page_number=None,
        severity="warning",
    )


def _degraded(result: MpzpParseResult, reasons: Sequence[str], *, counted: bool = False) -> MpzpParseResult:
    """Wynik deterministyczny + ostrzeżenie; ``counted`` — powody policzył już potok (bez podwójnego liczenia)."""
    llm_metrics.metrics.increment("llm.degraded")
    for reason in dict.fromkeys(reasons) if not counted else ():
        llm_metrics.metrics.increment(f"llm.unavailable.{reason}")
    status = "partial" if result.status == "complete" else result.status
    return result.model_copy(update={"status": status, "warnings": [*result.warnings, unavailable_warning(reasons)]})


async def apply_hybrid(
    result: MpzpParseResult,
    context: BlocksContext | None,
    pipeline: MpzpLlmPipeline | None,
    *,
    unavailable_reason: str | None = None,
    threshold: float = DEFAULT_SCOPE_THRESHOLD,
) -> MpzpParseResult:
    """Tryb ``hybrid``: rdzeń + zweryfikowani kandydaci modelu dla par wskazanych w polityce."""
    targets = select_targets(result.zones, threshold) if context is not None else {}
    if context is None or not targets:
        # Brak bloków albo wszystkie pary mają pewną wartość deterministyczną: model nie jest wołany.
        await close_pipeline(pipeline)
        return result
    if pipeline is None:
        return _degraded(result, [unavailable_reason or REASON_LLM_DISABLED])
    try:
        outcome = await pipeline.run(
            blocks_for_targets(context.resolution, targets),
            document_sha256=context.document_sha256,
            document=_document_context(context),
            targets=targets,
        )
        zones, added, disagreements = merge_llm_candidates(result.zones, outcome.report.accepted, context)
    except Exception as exc:  # noqa: BLE001 - błąd ścieżki modelu nie może zepsuć wyniku deterministycznego
        logger.warning("mpzp_llm hybrid step failed: %s", type(exc).__name__)
        return _degraded(result, ["pipeline_error"])
    finally:
        await close_pipeline(pipeline)
    llm_metrics.log_event("hybrid_merge", added=added, disagreements=disagreements, calls=outcome.calls)
    merged = result.model_copy(update={"zones": zones})
    # Odrzucenia bramek (bez duplikatów) są jawne: ostrzeżenie z licznikami per bramka i status ``partial``.
    rejected = [item for item in outcome.report.rejected if item.gate != "G8"]
    if rejected:
        counts = {gate: sum(1 for item in rejected if item.gate == gate) for gate in ("G1", "G2", "G3", "G4", "G5", "G6", "G7")}
        llm_metrics.metrics.increment("llm.rejection_warnings")
        merged = merged.model_copy(update={
            "status": "partial" if merged.status == "complete" else merged.status,
            "warnings": [*merged.warnings, rejected_warning(counts)],
        })
    if not outcome.available:
        return _degraded(merged, outcome.unavailable_reasons, counted=True)
    return merged


async def close_pipeline(pipeline: MpzpLlmPipeline | None) -> None:
    if pipeline is None:
        return
    try:
        await pipeline.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.warning("mpzp_llm provider close failed: %s", type(exc).__name__)


# --- tryb cienia --------------------------------------------------------------------------------------------

_SHADOW_TASKS: set[asyncio.Task[None]] = set()


@dataclass(frozen=True)
class ShadowComparison:
    agree: int = 0
    disagree: int = 0
    llm_only: int = 0
    det_only: int = 0
    calls: int = 0
    unavailable: tuple[str, ...] = ()


def compare_with_deterministic(zones: Sequence[MpzpZoneResult], outcome: LlmPipelineOutcome) -> ShadowComparison:
    """Porównanie par (strefa, parametr katalogu): zgodne, rozbieżne, tylko model, tylko rdzeń."""
    agree = disagree = llm_only = det_only = 0
    for zone in zones:
        model = [item for item in outcome.report.accepted if item.zone_symbol == zone.zone_symbol]
        for parameter in contract.PARAMETERS:
            det_values = [p.normalized_value for p in zone.parameters if p.name == parameter and p.normalized_value is not None]
            llm_values = [item.value for item in model if item.parameter == parameter]
            if not det_values and not llm_values:
                continue
            if det_values and not llm_values:
                det_only += 1
            elif llm_values and not det_values:
                llm_only += 1
            elif all(any(_same_value(value, det) for det in det_values) for value in llm_values):
                agree += 1
            else:
                disagree += 1
    return ShadowComparison(agree, disagree, llm_only, det_only, outcome.calls, outcome.unavailable_reasons)


async def run_shadow(result: MpzpParseResult, context: BlocksContext, pipeline: MpzpLlmPipeline) -> ShadowComparison | None:
    """Liczy model dla WSZYSTKICH par i zapisuje różnice do logu i liczników; nie zwraca nic do odpowiedzi."""
    try:
        outcome = await pipeline.run(
            all_blocks(context.resolution, context.symbols),
            document_sha256=context.document_sha256,
            document=_document_context(context),
            targets=None,
        )
        comparison = compare_with_deterministic(result.zones, outcome)
    except Exception as exc:  # noqa: BLE001 - tryb cienia nigdy nie wpływa na analizę
        logger.warning("mpzp_llm shadow failed: %s", type(exc).__name__)
        llm_metrics.metrics.increment("llm.shadow.error")
        return None
    finally:
        await close_pipeline(pipeline)
    registry = llm_metrics.metrics
    registry.add_all({
        "llm.shadow.agree": comparison.agree,
        "llm.shadow.disagree": comparison.disagree,
        "llm.shadow.llm_only": comparison.llm_only,
        "llm.shadow.det_only": comparison.det_only,
    })
    registry.increment("llm.shadow.runs")
    llm_metrics.log_event(
        "shadow_compare",
        document=context.document_sha256[:16],
        agree=comparison.agree,
        disagree=comparison.disagree,
        llm_only=comparison.llm_only,
        det_only=comparison.det_only,
        calls=comparison.calls,
        unavailable=",".join(comparison.unavailable) or "none",
    )
    return comparison


async def schedule_shadow(
    result: MpzpParseResult, context: BlocksContext | None, pipeline: MpzpLlmPipeline | None
) -> bool:
    """Uruchamia porównanie w tle (kopia wyniku); odpowiedź nie czeka i nie zmienia się."""
    if context is None or pipeline is None:
        llm_metrics.metrics.increment("llm.shadow.skipped")
        await close_pipeline(pipeline)
        return False
    snapshot = result.model_copy(deep=True)
    task = asyncio.get_running_loop().create_task(run_shadow(snapshot, context, pipeline))
    _SHADOW_TASKS.add(task)
    task.add_done_callback(_SHADOW_TASKS.discard)
    return True


async def drain_shadow_tasks() -> None:
    """Czeka na porównania w tle (testy i zamykanie procesu)."""
    while _SHADOW_TASKS:
        await asyncio.gather(*list(_SHADOW_TASKS), return_exceptions=True)
