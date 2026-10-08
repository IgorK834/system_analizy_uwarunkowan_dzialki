"""Potok ekstrakcji modelem językowym dla bloków stref: cache, budżet, weryfikacja (PV3-13/14).

Kolejność dla każdego bloku strefy:

1. **Cache** (PV3-13): klucz bloku to SHA-256 z ``document_sha256``, ``block_sha256``,
   ``prompt_version``, ``schema_version``, ``model_id`` i ``params_hash`` (dostawca, skróty
   instrukcji i schematu, temperatura, limity podziału, symbole i ścieżka bloku). Trafienie
   ``ok`` odtwarza odpowiedzi części bloku z zapisu — **bez wywołania sieci** — i przepuszcza je
   przez ten sam kontrakt i te same bramki, co odpowiedź świeżą. Zmiana modelu, promptu albo
   schematu zmienia klucz, więc unieważnia cache.
2. **Budżet** na analizę (liczba żądań i szacowane tokeny wejścia): żądanie ponad budżet nie jest
   wysyłane (``budget_exhausted``).
3. **Model** przez port ``StructuredExtractionProvider`` (usługa ``LlmExtractionService``); wynik
   jest zapisywany w cache — wyłącznie wyjście modelu i skróty wejścia, nigdy treść żądania.
4. **Weryfikacja** (PV3-12): kandydaci przechodzą bramki G1–G8 (``candidate_verifier``); przyjęci
   mają status ``ai_candidate`` i provenance (model, wersja promptu, skrót odpowiedzi).

Potok nigdy nie podnosi wyjątku: niedostępność modelu, przekroczenie budżetu albo błąd bramek
dają ``unavailable_reasons`` (wynik deterministyczny zostaje, a wywołujący dokłada ostrzeżenie
``MPZP_LLM_UNAVAILABLE``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from app.modules.planning.application import llm_metrics
from app.modules.planning.application.llm_extraction import BlockExtraction, ExtractionLimits, LlmExtractionService
from app.modules.planning.application.ports import (
    LlmExtractionCache,
    LlmExtractionRecord,
    StructuredExtractionError,
    StructuredExtractionErrorCode,
    StructuredExtractionProvider,
    StructuredExtractionRequest,
    StructuredExtractionResult,
)
from app.modules.planning.domain import candidate_verifier as verifier
from app.modules.planning.domain import extraction_contract as contract
from app.modules.planning.domain.extraction_contract import RejectedCandidate
from app.modules.planning.domain.zone_blocks import ZoneBlock

logger = logging.getLogger(__name__)

CACHE_KEY_VERSION: Final[str] = "mpzp-llm-cache/1"
REASON_PIPELINE_ERROR: Final[str] = "pipeline_error"
REASON_CACHE_ERROR: Final[str] = "cache_error"
SOURCE_CACHE: Final[str] = "cache"
SOURCE_MODEL: Final[str] = "model"
SOURCE_NONE: Final[str] = "none"
# Szacunek tokenów wejścia przed wywołaniem (znaki / 4, w górę); po wywołaniu liczy się wartość zgłoszona.
CHARS_PER_TOKEN: Final[int] = 4


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- klucz cache -----------------------------------------------------------------------------------


def extraction_params_hash(
    *,
    provider: str,
    block: ZoneBlock,
    limits: ExtractionLimits,
    extra: Mapping[str, Any] | None = None,
) -> str:
    """Skrót parametrów, które zmieniają żądanie albo odpowiedź, a nie są tekstem bloku."""
    return _sha256(
        canonical_json(
            {
                "cache_key_version": CACHE_KEY_VERSION,
                "provider": provider,
                "prompt_sha256": contract.prompt_sha256(),
                "schema_sha256": contract.SCHEMA_SHA256,
                "temperature": limits.temperature,
                "max_output_tokens": limits.max_output_tokens,
                "block_char_limit": limits.block_char_limit,
                "chunk_overlap_chars": limits.chunk_overlap_chars,
                "lead_chars": limits.lead_chars,
                "max_chunks": limits.max_chunks,
                "symbols": list(block.symbols),
                "path": " ".join(block.path.split()),
                "extra": dict(sorted((extra or {}).items())),
            }
        )
    )


def block_cache_key(
    *,
    document_sha256: str,
    block_sha256: str,
    prompt_version: str,
    schema_version: str,
    model_id: str,
    params_hash: str,
) -> str:
    """Klucz zapisu ``mpzp_llm_extractions`` (unikalny): skróty wejścia, wersje, model, parametry."""
    return _sha256(
        canonical_json(
            {
                "document_sha256": document_sha256,
                "block_sha256": block_sha256,
                "prompt_version": prompt_version,
                "schema_version": schema_version,
                "model_id": model_id,
                "params_hash": params_hash,
            }
        )
    )


# --- budżet ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LlmBudget:
    """Limity ścieżki modelu w obrębie JEDNEJ analizy (PV3-15; progi startowe z ADR-012).

    ``max_requests`` / ``max_input_tokens`` — na analizę (6 żądań, 12 000 tokenów wejścia),
    ``max_input_tokens_per_document`` — na dokument uchwały, ``max_input_tokens_per_request`` — na
    żądanie (4000), ``time_budget_seconds`` — ile ścieżka modelu może dodać do czasu analizy (licząc
    od pierwszego żądania); ``None`` = bez limitu czasu. Limity dobowe i miesięczne (twardy stop
    między analizami i procesami) egzekwuje adapter ``infrastructure/llm/budget.py``.
    """

    max_requests: int = 6
    max_input_tokens: int = 12_000
    max_input_tokens_per_document: int | None = None
    max_input_tokens_per_request: int | None = None
    time_budget_seconds: float | None = None

    def __post_init__(self) -> None:
        values = (self.max_requests, self.max_input_tokens, self.max_input_tokens_per_document or 0,
                  self.max_input_tokens_per_request or 0, self.time_budget_seconds or 0)
        if any(value < 0 for value in values):
            raise ValueError("Budżet modelu nie może być ujemny.")


def estimate_input_tokens(request: StructuredExtractionRequest) -> int:
    return math.ceil((len(request.system_instruction) + len(request.user_text)) / CHARS_PER_TOKEN)


class BudgetTracker:
    """Stan budżetu jednej analizy: żądania, tokeny (łącznie i per dokument) i termin czasu.

    Termin jest propagowany: ``deadline`` (zegar monotoniczny) ustawia wywołujący — np. orkiestrator
    analizy — a ``time_budget_seconds`` liczy się od pierwszego żądania modelu; obowiązuje wcześniejszy.
    Żądanie ponad limit albo po terminie nie jest wysyłane, a trwające żądanie jest przerywane po
    upływie pozostałego czasu (``deadline_exceeded``).
    """

    def __init__(
        self,
        budget: LlmBudget,
        *,
        deadline: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.budget = budget
        self.requests = 0
        self.input_tokens = 0
        self.document_tokens: dict[str, int] = {}
        self.exhausted = False
        self.deadline = deadline
        self.clock = clock
        self._llm_started: float | None = None

    def remaining_seconds(self) -> float | None:
        """Pozostały czas ścieżki modelu albo ``None`` (bez limitu)."""
        limits: list[float] = []
        if self.deadline is not None:
            limits.append(self.deadline - self.clock())
        if self.budget.time_budget_seconds is not None and self._llm_started is not None:
            limits.append(self._llm_started + self.budget.time_budget_seconds - self.clock())
        return min(limits) if limits else None

    def _refuse(self, code: StructuredExtractionErrorCode) -> StructuredExtractionError:
        self.exhausted = True
        return StructuredExtractionError(code)

    def reserve(self, request: StructuredExtractionRequest, document: str = "") -> int:
        if self._llm_started is None:
            self._llm_started = self.clock()
        remaining = self.remaining_seconds()
        if remaining is not None and remaining <= 0:
            raise self._refuse(StructuredExtractionErrorCode.DEADLINE_EXCEEDED)
        estimate = estimate_input_tokens(request)
        per_request = self.budget.max_input_tokens_per_request
        if per_request is not None and estimate > per_request:
            raise StructuredExtractionError(StructuredExtractionErrorCode.REQUEST_TOKEN_LIMIT)
        if (
            self.requests + 1 > self.budget.max_requests
            or self.input_tokens + estimate > self.budget.max_input_tokens
        ):
            raise self._refuse(StructuredExtractionErrorCode.BUDGET_EXHAUSTED)
        per_document = self.budget.max_input_tokens_per_document
        if per_document is not None and self.document_tokens.get(document, 0) + estimate > per_document:
            raise self._refuse(StructuredExtractionErrorCode.DOCUMENT_BUDGET_EXHAUSTED)
        self.requests += 1
        self.input_tokens += estimate
        self.document_tokens[document] = self.document_tokens.get(document, 0) + estimate
        return estimate

    def settle(self, estimate: int, reported: int | None, document: str = "") -> None:
        if reported is not None:
            self.input_tokens += reported - estimate
            self.document_tokens[document] = self.document_tokens.get(document, 0) + reported - estimate


# --- dostawcy pomocniczy ---------------------------------------------------------------------------------


class _RecordingProvider:
    """Przepuszcza żądania do dostawcy (po budżecie i w terminie) i zapamiętuje wyniki do zapisu w cache."""

    def __init__(
        self, inner: StructuredExtractionProvider, budget: BudgetTracker | None, document: str = ""
    ) -> None:
        self.inner = inner
        self.provider_name = inner.provider_name
        self.model = inner.model
        self.budget = budget
        self.document = document
        self.calls = 0
        self.results: list[StructuredExtractionResult] = []
        self.errors: list[str] = []

    async def extract_structured(self, request: StructuredExtractionRequest) -> StructuredExtractionResult:
        estimate = self.budget.reserve(request, self.document) if self.budget is not None else 0
        remaining = self.budget.remaining_seconds() if self.budget is not None else None
        self.calls += 1
        try:
            if remaining is None:
                result = await self.inner.extract_structured(request)
            else:
                result = await asyncio.wait_for(self.inner.extract_structured(request), timeout=max(0.0, remaining))
        except TimeoutError:
            self.errors.append(StructuredExtractionErrorCode.DEADLINE_EXCEEDED.value)
            raise StructuredExtractionError(StructuredExtractionErrorCode.DEADLINE_EXCEEDED) from None
        except StructuredExtractionError as error:
            self.errors.append(error.code.value)
            raise
        if self.budget is not None:
            self.budget.settle(estimate, result.input_tokens, self.document)
        self.results.append(result)
        return result


class _StoredResponseProvider:
    """Odtwarza odpowiedzi części bloku z zapisu cache — bez sieci i bez budżetu."""

    def __init__(self, record: LlmExtractionRecord, provider_name: str, model: str) -> None:
        self.provider_name = provider_name
        self.model = model
        chunks = (record.response or {}).get("chunks", [])
        self._by_request = {str(chunk.get("request_sha256")): chunk for chunk in chunks if isinstance(chunk, Mapping)}

    async def extract_structured(self, request: StructuredExtractionRequest) -> StructuredExtractionResult:
        chunk = self._by_request.get(request.request_sha256)
        if chunk is None or not isinstance(chunk.get("content"), Mapping):
            raise StructuredExtractionError(StructuredExtractionErrorCode.REPLAY_MISS)
        content = chunk["content"]
        if _sha256(canonical_json(content)) != chunk.get("response_sha256"):
            raise StructuredExtractionError(StructuredExtractionErrorCode.REPLAY_INTEGRITY)
        returned = chunk.get("model_returned") if isinstance(chunk.get("model_returned"), str) else None
        return StructuredExtractionResult(
            content=content,
            provider=self.provider_name,
            model_requested=self.model,
            model_returned=returned,
            model_mismatch=returned is not None and returned != self.model,
            response_sha256=str(chunk["response_sha256"]),
            request_sha256=request.request_sha256,
            input_sha256=request.input_sha256,
            finish_reason=str(chunk.get("finish_reason") or "STOP"),
        )


# --- wynik -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LlmPricing:
    """Cena za 1 mln tokenów (USD) do szacowania kosztu — ADR-012: cena od 2027-01-01."""

    input_usd_per_mtok: float = 1.50
    output_usd_per_mtok: float = 7.50

    def estimate(self, input_tokens: int | None, output_tokens: int | None) -> float | None:
        if input_tokens is None and output_tokens is None:
            return None
        cost = (input_tokens or 0) * self.input_usd_per_mtok + (output_tokens or 0) * self.output_usd_per_mtok
        return round(cost / 1_000_000, 6)


@dataclass(frozen=True)
class BlockRun:
    """Co się stało z jednym blokiem (bez treści): źródło odpowiedzi, status, skrót, koszt."""

    block_id: str
    cache_key: str
    source: str  # cache | model | none
    status: str  # status ``BlockExtraction``: ok | partial | failed | skipped
    record_status: str | None
    response_sha256: str | None
    calls: int
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class LlmPipelineOutcome:
    report: verifier.VerificationReport
    blocks: tuple[BlockRun, ...]
    calls: int
    cache_hits: int
    not_targeted: int
    unavailable_reasons: tuple[str, ...]
    model_id: str
    prompt_version: str = contract.PROMPT_VERSION
    schema_version: str = contract.SCHEMA_VERSION
    # Zużycie świeżych wywołań (trafienia cache nie kosztują); ``None`` = dostawca nie zgłosił, nigdy 0.
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    # Opóźnienia udanych żądań do dostawcy [ms] (PV3-19); puste, gdy dostawca ich nie zgłosił.
    latencies_ms: tuple[float, ...] = ()

    @property
    def available(self) -> bool:
        return not self.unavailable_reasons


@dataclass
class _Totals:
    calls: int = 0
    cache_hits: int = 0
    not_targeted: int = 0
    reasons: list[str] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latencies_ms: list[float] = field(default_factory=list)

    def add_usage(self, input_tokens: int | None, output_tokens: int | None, cost: float | None) -> None:
        if input_tokens is not None:
            self.input_tokens = (self.input_tokens or 0) + input_tokens
        if output_tokens is not None:
            self.output_tokens = (self.output_tokens or 0) + output_tokens
        if cost is not None:
            self.cost_usd = round((self.cost_usd or 0.0) + cost, 6)


def _record_status(extraction: BlockExtraction) -> tuple[str, str | None]:
    if extraction.status == "ok":
        return "ok", None
    rejected = [chunk for chunk in extraction.provenance.chunks if chunk.status == "rejected"]
    if rejected:
        return "rejected_schema", rejected[0].error_code
    return "error", extraction.failures[0] if extraction.failures else None


def _sum(values: Sequence[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


# Kod odrzucenia kontraktu (PV3-11) → bramka i kod weryfikatora (PV3-12).
CONTRACT_GATE_CODES: Final[Mapping[str, tuple[str, str]]] = {
    contract.CODE_UNKNOWN_SYMBOL: (verifier.G7, verifier.CODE_SYMBOL_NOT_IN_BLOCK),
    contract.CODE_OPERATOR_NOT_ALLOWED: (verifier.G2, verifier.CODE_OPERATOR_NOT_ALLOWED),
    contract.CODE_EMPTY_EVIDENCE: (verifier.G3, verifier.CODE_QUOTE_EMPTY),
    contract.CODE_EMPTY_CONDITION_QUOTE: (verifier.G3, verifier.CODE_CONDITION_NOT_IN_BLOCK),
    contract.CODE_EMPTY_RAW_VALUE: (verifier.G4, verifier.CODE_RAW_EMPTY),
    contract.CODE_RAW_NOT_IN_EVIDENCE: (verifier.G4, verifier.CODE_RAW_NOT_IN_QUOTE),
    contract.CODE_RAW_NOT_NUMERIC: (verifier.G5, verifier.CODE_RAW_NOT_NUMERIC),
    contract.CODE_RANGE_INCOMPLETE: (verifier.G5, verifier.CODE_RANGE_INCOMPLETE),
    contract.CODE_MISSING_SCOPE_QUOTE: (verifier.G7, verifier.CODE_SCOPE_UNRESOLVED),
}


def _contract_rejection(item: RejectedCandidate) -> verifier.RejectedLlmCandidate:
    gate, code = CONTRACT_GATE_CODES[item.code]
    return verifier.RejectedLlmCandidate(
        candidate_index=-1 - item.index, gate=gate, code=code, zone_symbol=item.zone_symbol, parameter=item.parameter,
        applicability="unresolved" if code == verifier.CODE_SCOPE_UNRESOLVED else None,
    )


def _usage(results: Sequence[StructuredExtractionResult]) -> tuple[int | None, int | None]:
    """Tokeny wejścia i wyjścia (wyjście z myśleniem, które jest rozliczane jak wyjście)."""
    output = _sum([
        None if r.output_tokens is None and r.thinking_tokens is None else (r.output_tokens or 0) + (r.thinking_tokens or 0)
        for r in results
    ])
    return _sum([r.input_tokens for r in results]), output


class MpzpLlmPipeline:
    """Cache → budżet → model → weryfikacja dla bloków stref jednego dokumentu."""

    def __init__(
        self,
        provider: StructuredExtractionProvider,
        *,
        limits: ExtractionLimits | None = None,
        cache: LlmExtractionCache | None = None,
        budget: LlmBudget | None = None,
        policy: verifier.VerifierPolicy | None = None,
        pricing: LlmPricing | None = None,
        params: Mapping[str, Any] | None = None,
        service_factory: Callable[[StructuredExtractionProvider, ExtractionLimits], LlmExtractionService] | None = None,
        tracker: BudgetTracker | None = None,
    ) -> None:
        self.provider = provider
        self.limits = limits or ExtractionLimits()
        self.cache = cache
        self.budget = budget or LlmBudget()
        self.policy = policy or verifier.VerifierPolicy()
        self.pricing = pricing or LlmPricing()
        self.params = dict(params or {})
        self._service_factory = service_factory or (lambda provider, limits_: LlmExtractionService(provider, limits_))
        # Wspólny licznik budżetu analizy (kilka dokumentów jednej analizy); bez niego — budżet per ``run``.
        self.tracker = tracker

    @property
    def model_id(self) -> str:
        return self.provider.model

    async def aclose(self) -> None:
        """Zamyka klienta HTTP dostawcy, jeżeli go ma (adapter tworzy klienta leniwie)."""
        close = getattr(self.provider, "aclose", None)
        if close is not None:
            await close()

    def cache_key_for(self, block: ZoneBlock, document_sha256: str) -> str:
        return block_cache_key(
            document_sha256=document_sha256,
            block_sha256=block.sha256,
            prompt_version=contract.PROMPT_VERSION,
            schema_version=contract.SCHEMA_VERSION,
            model_id=self.provider.model,
            params_hash=self._params_hash(block),
        )

    def _params_hash(self, block: ZoneBlock) -> str:
        return extraction_params_hash(provider=self.provider.provider_name, block=block, limits=self.limits, extra=self.params)

    async def run(
        self,
        blocks: Sequence[ZoneBlock],
        *,
        document_sha256: str,
        document: verifier.DocumentContext | None = None,
        targets: Mapping[str, frozenset[str]] | None = None,
    ) -> LlmPipelineOutcome:
        """Ekstrakcja i weryfikacja; ``targets`` ogranicza przyjęte wartości do par (strefa, parametr)."""
        tracker = self.tracker if self.tracker is not None else BudgetTracker(self.budget)
        totals = _Totals()
        runs: list[BlockRun] = []
        report = verifier.VerificationReport(accepted=(), rejected=())
        unique = list({block.block_id: block for block in blocks}.values())
        for block in unique:
            try:
                run, block_report = await self._run_block(block, document_sha256, document, tracker, totals)
            except Exception as exc:  # noqa: BLE001 - potok nie może przerwać analizy
                logger.warning("mpzp_llm block=%s failed: %s", block.block_id, type(exc).__name__)
                totals.reasons.append(REASON_PIPELINE_ERROR)
                runs.append(BlockRun(block.block_id, "", SOURCE_NONE, "failed", None, None, 0, (REASON_PIPELINE_ERROR,)))
                continue
            runs.append(run)
            report = report.merged(self._targeted(block_report, targets, totals))
        report = verifier.deduplicate_across_blocks(report)
        outcome = LlmPipelineOutcome(
            report=report,
            blocks=tuple(runs),
            calls=totals.calls,
            cache_hits=totals.cache_hits,
            not_targeted=totals.not_targeted,
            unavailable_reasons=tuple(dict.fromkeys(totals.reasons)),
            model_id=self.provider.model,
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            cost_usd=totals.cost_usd,
            latencies_ms=tuple(totals.latencies_ms),
        )
        self._publish(outcome)
        return outcome

    def _targeted(
        self,
        report: verifier.VerificationReport,
        targets: Mapping[str, frozenset[str]] | None,
        totals: _Totals,
    ) -> verifier.VerificationReport:
        if targets is None:
            return report
        kept = tuple(item for item in report.accepted if item.parameter in targets.get(item.zone_symbol, frozenset()))
        totals.not_targeted += len(report.accepted) - len(kept)
        return verifier.VerificationReport(accepted=kept, rejected=report.rejected)

    async def _run_block(
        self,
        block: ZoneBlock,
        document_sha256: str,
        document: verifier.DocumentContext | None,
        tracker: BudgetTracker,
        totals: _Totals,
    ) -> tuple[BlockRun, verifier.VerificationReport]:
        key = self.cache_key_for(block, document_sha256)
        extraction: BlockExtraction | None = None
        source, record_status, response_sha = SOURCE_NONE, None, None
        calls = 0
        cached = self._cached(key, totals)
        if cached is not None and cached.status == "ok":
            stored = _StoredResponseProvider(cached, self.provider.provider_name, self.provider.model)
            replayed = await self._service_factory(stored, self.limits).extract_block(block)  # type: ignore[arg-type]
            if replayed.status == "ok":
                extraction, source = replayed, SOURCE_CACHE
                record_status, response_sha = cached.status, cached.response_sha256
                totals.cache_hits += 1
        if extraction is None:
            recording = _RecordingProvider(self.provider, tracker, document_sha256)
            extraction = await self._service_factory(recording, self.limits).extract_block(block)  # type: ignore[arg-type]
            calls = recording.calls
            totals.calls += calls
            input_tokens, output_tokens = _usage(recording.results)
            totals.add_usage(input_tokens, output_tokens, self.pricing.estimate(input_tokens, output_tokens))
            # Dostawca bez pomiaru zgłasza 0,0 ms — to „brak pomiaru”, nie opóźnienie zerowe.
            totals.latencies_ms.extend(result.latency_ms for result in recording.results if result.latency_ms > 0)
            if recording.results or recording.errors:
                source = SOURCE_MODEL
                record_status, response_sha = self._save(key, block, document_sha256, extraction, recording, totals)
        if extraction.status != "ok":
            totals.reasons.extend(extraction.failures or (extraction.reason or extraction.status,))
        provenance = verifier.CandidateProvenance(
            model_id=self.provider.model,
            prompt_version=contract.PROMPT_VERSION,
            schema_version=contract.SCHEMA_VERSION,
            response_sha256=response_sha,
            provider=self.provider.provider_name,
        )
        block_report = verifier.verify_candidates(
            [record.candidate for record in extraction.candidates],
            block,
            document=document,
            policy=self.policy,
            provenance=provenance,
        )
        # Odrzucenia kontraktu (przed bramkami) też niosą bramkę i kod — każde odrzucenie jest opisane.
        block_report = verifier.VerificationReport(
            accepted=block_report.accepted,
            rejected=block_report.rejected + tuple(_contract_rejection(item) for item in extraction.rejected),
        )
        run = BlockRun(
            block_id=block.block_id,
            cache_key=key,
            source=source,
            status=extraction.status,
            record_status=record_status,
            response_sha256=response_sha,
            calls=calls,
            failures=extraction.failures,
        )
        return run, block_report

    def _cached(self, key: str, totals: _Totals) -> LlmExtractionRecord | None:
        if self.cache is None:
            return None
        try:
            return self.cache.get(key)
        except Exception as exc:  # noqa: BLE001 - awaria cache nie blokuje ekstrakcji
            logger.warning("mpzp_llm cache read failed: %s", type(exc).__name__)
            totals.reasons.append(REASON_CACHE_ERROR)
            return None

    def _save(
        self,
        key: str,
        block: ZoneBlock,
        document_sha256: str,
        extraction: BlockExtraction,
        recording: _RecordingProvider,
        totals: _Totals,
    ) -> tuple[str, str | None]:
        status, error_code = _record_status(extraction)
        chunks = [
            {
                "index": index,
                "request_sha256": result.request_sha256,
                "input_sha256": result.input_sha256,
                "response_sha256": result.response_sha256,
                "model_returned": result.model_returned,
                "finish_reason": result.finish_reason,
                "content": dict(result.content),
            }
            for index, result in enumerate(recording.results)
        ]
        response = {"chunks": chunks} if chunks else None
        response_sha = _sha256(canonical_json(response)) if response is not None else None
        input_tokens, output_tokens = _usage(recording.results)
        record = LlmExtractionRecord(
            cache_key=key,
            document_sha256=document_sha256,
            block_sha256=block.sha256,
            prompt_version=contract.PROMPT_VERSION,
            schema_version=contract.SCHEMA_VERSION,
            model_id=self.provider.model,
            params_hash=self._params_hash(block),
            status=status,
            response=response,
            response_sha256=response_sha,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=round(sum(r.latency_ms for r in recording.results), 1) if recording.results else None,
            cost_estimate_usd=self.pricing.estimate(input_tokens, output_tokens),
            error_code=error_code,
        )
        if self.cache is not None:
            try:
                self.cache.save(record)
            except Exception as exc:  # noqa: BLE001 - brak zapisu nie unieważnia wyniku tej analizy
                logger.warning("mpzp_llm cache write failed: %s", type(exc).__name__)
                totals.reasons.append(REASON_CACHE_ERROR)
        return status, response_sha

    def _publish(self, outcome: LlmPipelineOutcome) -> None:
        registry = llm_metrics.metrics
        registry.increment("llm.calls", outcome.calls)
        registry.increment("llm.cache.hit", outcome.cache_hits)
        registry.increment("llm.cache.miss", sum(1 for run in outcome.blocks if run.source == SOURCE_MODEL))
        registry.increment("llm.verifier.accepted", len(outcome.report.accepted))
        registry.add_all({f"llm.verifier.rejected.{gate}": count for gate, count in outcome.report.rejection_counts.items()})
        registry.add_all({f"llm.verifier.code.{code}": count for code, count in outcome.report.rejection_codes.items()})
        for reason in outcome.unavailable_reasons:
            registry.increment(f"llm.unavailable.{reason}")
        llm_metrics.observe_latencies(outcome.latencies_ms)
        llm_metrics.record_run(outcome.available)
        registry.increment("llm.tokens.input", outcome.input_tokens or 0)
        registry.increment("llm.tokens.output", outcome.output_tokens or 0)
        # Koszt szacowany w mikro-USD (liczniki są całkowite).
        registry.increment("llm.cost.estimated_microusd", round((outcome.cost_usd or 0.0) * 1_000_000))
        llm_metrics.log_event(
            "pipeline",
            blocks=len(outcome.blocks),
            calls=outcome.calls,
            cache_hits=outcome.cache_hits,
            accepted=len(outcome.report.accepted),
            rejected=len(outcome.report.rejected),
            not_targeted=outcome.not_targeted,
            unavailable=",".join(outcome.unavailable_reasons) or "none",
            input_tokens=outcome.input_tokens if outcome.input_tokens is not None else "unknown",
            output_tokens=outcome.output_tokens if outcome.output_tokens is not None else "unknown",
            cost_usd=outcome.cost_usd if outcome.cost_usd is not None else "unknown",
            **{f"rejected_{gate}": count for gate, count in outcome.report.rejection_counts.items()},
        )
