"""Usługa ekstrakcji parametrów strefy modelem językowym (PV3-11, ADR-012/ADR-013).

Jedno żądanie na blok strefy: blok wielosymbolowy jest wysyłany raz, a wynik jest rozpisany na
symbole (``zone_symbol`` kandydata). Blok większy niż limit jest dzielony na części (granice
na początku punktów listy, akapitów albo zdań), każda część dostaje nakładkę kontekstu z
poprzedniej i linię otwierającą blok, a kandydaci są scalani z deduplikacją po (symbol,
parametr, operator, surowa wartość, położenie cytatu w bloku) — żaden cytat nie ginie na
granicy części i żaden nie jest liczony dwa razy.

Usługa NIE weryfikuje wartości (to Task 20.12): zwraca kandydatów ze statusem ``ai_candidate``,
zakresami cytatów w tekście bloku (``None``, gdy cytatu tam nie ma) i pełnym provenance — także
dla wyniku niepełnego (część nie powiodła się). Nic nie jest tu „zweryfikowane”.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Final, Literal

from app.modules.planning.application.ports import (
    StructuredExtractionError,
    StructuredExtractionErrorCode,
    StructuredExtractionProvider,
    StructuredExtractionRequest,
    StructuredExtractionResult,
)
from app.modules.planning.domain import extraction_contract as contract
from app.modules.planning.domain.extraction_contract import (
    CandidateRecord,
    ContractViolation,
    DocumentTextError,
    ExtractionContractError,
    RejectedCandidate,
)
from app.modules.planning.domain.zone_blocks import ZoneBlock

logger = logging.getLogger(__name__)

BlockStatus = Literal["ok", "partial", "failed", "skipped"]
ChunkStatus = Literal["ok", "rejected", "error", "skipped"]

CODE_MODEL_MISMATCH: Final[str] = "model_mismatch"
CODE_BLOCK_TOO_LARGE: Final[str] = "block_too_large"
CODE_DOCUMENT_TEXT_UNSAFE: Final[str] = "document_text_unsafe"
CODE_EMPTY_BLOCK: Final[str] = "empty_block"
CODE_ABORTED: Final[str] = "aborted_after_provider_failure"

# Po tych błędach kolejne części bloku zawiodłyby tak samo, więc nie wydajemy kolejnych żądań.
ABORT_CODES: Final[frozenset[StructuredExtractionErrorCode]] = frozenset(
    {
        StructuredExtractionErrorCode.CONFIGURATION,
        StructuredExtractionErrorCode.UNAUTHORIZED,
        StructuredExtractionErrorCode.FORBIDDEN,
        StructuredExtractionErrorCode.MODEL_NOT_FOUND,
        StructuredExtractionErrorCode.CIRCUIT_OPEN,
        StructuredExtractionErrorCode.RATE_LIMITED,
        StructuredExtractionErrorCode.BUDGET_EXHAUSTED,
        StructuredExtractionErrorCode.DOCUMENT_BUDGET_EXHAUSTED,
        StructuredExtractionErrorCode.DAILY_LIMIT,
        StructuredExtractionErrorCode.MONTHLY_LIMIT,
        StructuredExtractionErrorCode.CONCURRENCY_LIMIT,
        StructuredExtractionErrorCode.LOCAL_RATE_LIMIT,
        StructuredExtractionErrorCode.DEADLINE_EXCEEDED,
        StructuredExtractionErrorCode.USAGE_LEDGER_UNAVAILABLE,
    }
)

_LIST_START: Final[re.Pattern[str]] = re.compile(r"(?:§\s*\d+|\d+[.)]|[a-ząćęłńóśźż]\)|[-–—•])\s")
_SENTENCE_END: Final[re.Pattern[str]] = re.compile(r"[.;:!?]\s")


@dataclass(frozen=True)
class ExtractionLimits:
    """Limity jednego żądania i podziału dużych bloków."""

    block_char_limit: int = 6_000
    chunk_overlap_chars: int = 400
    lead_chars: int = 240
    max_chunks: int = 6
    temperature: float = 0.0
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.block_char_limit < 1_000:
            raise ValueError("Limit znaków bloku musi wynosić co najmniej 1000.")
        if self.chunk_overlap_chars < 0 or self.lead_chars < 0:
            raise ValueError("Nakładka i linia otwierająca nie mogą być ujemne.")
        if 2 * (self.chunk_overlap_chars + self.lead_chars) >= self.block_char_limit:
            raise ValueError("Nakładka z linią otwierającą musi mieścić się w połowie limitu bloku.")
        if self.max_chunks < 1:
            raise ValueError("Liczba części bloku musi wynosić co najmniej 1.")


@dataclass(frozen=True)
class TextChunk:
    """Część tekstu bloku: ``[start, end)`` jest jej własnością, ``[context_start, start)`` to nakładka."""

    index: int
    start: int
    end: int
    context_start: int
    lead: str = ""

    def rendered(self, text: str) -> str:
        """Tekst dokumentu tak, jak trafia do modelu: [linia otwierająca, znacznik przerwy,] nakładka i własny tekst."""
        body = text[self.context_start : self.end]
        if self.lead and self.context_start > 0:
            return f"{self.lead}\n{contract.CONTEXT_GAP_MARKER}\n{body}"
        return body


def _boundary_kinds(text: str, position: int) -> int:
    """Siła granicy przed ``position``: 4 punkt listy, 3 pusty wiersz, 2 koniec zdania i nowy wiersz, 1 nowy wiersz, 0 brak."""
    if position <= 0 or position >= len(text) or text[position - 1] != "\n":
        return 0
    if _LIST_START.match(text, position):
        return 4
    if position >= 2 and text[position - 2] == "\n":
        return 3
    before = text[max(0, position - 3) : position - 1]
    if before and before[-1] in ".;:!?":
        return 2
    return 1


def _cut(text: str, start: int, hard_end: int) -> int:
    """Koniec własnej części: najmocniejsza granica w drugiej połowie zakresu, a bez niej zdanie, spacja, twarde cięcie."""
    if hard_end >= len(text):
        return len(text)
    low = start + max(1, (hard_end - start) // 2)
    best_position, best_kind = -1, 0
    position = text.rfind("\n", low, hard_end)
    while position >= low:
        kind = _boundary_kinds(text, position + 1)
        if kind > best_kind:
            best_position, best_kind = position + 1, kind
            if kind == 4:
                break
        position = text.rfind("\n", low, position)
    if best_position > 0:
        return best_position
    sentence_end = -1
    for match in _SENTENCE_END.finditer(text, low, hard_end):
        sentence_end = match.end()
    if sentence_end > start:
        return sentence_end
    space = text.rfind(" ", low, hard_end)
    return space + 1 if space >= low else hard_end


def _context_start(text: str, start: int, overlap: int) -> int:
    """Początek nakładki: najwcześniejsza dobra granica (punkt listy, akapit, wiersz, spacja) w oknie ``overlap``."""
    target = max(0, start - overlap)
    if target == 0 or overlap == 0:
        return target if overlap else start
    best_position, best_kind = -1, 0
    position = text.find("\n", target, start)
    while position != -1 and position < start:
        kind = _boundary_kinds(text, position + 1)
        if kind > best_kind:
            best_position, best_kind = position + 1, kind
        position = text.find("\n", position + 1, start)
    if best_position >= 0 and best_position < start:
        return best_position
    space = text.find(" ", target, start)
    return space + 1 if 0 <= space < start else target


def _lead(text: str, limit: int) -> str:
    """Linia otwierająca blok (zwykle „dla terenu X:”), powtarzana w kolejnych częściach, by znały zakres.

    To pierwsze wiersze aż do dwukropka albo do ``lead_chars`` znaków, nie więcej niż pierwszy akapit.
    """
    if limit <= 0:
        return ""
    head = text[:limit]
    paragraph = head.find("\n\n")
    if paragraph > 0:
        head = head[:paragraph]
    taken: list[str] = []
    for line in head.split("\n"):
        taken.append(line)
        if line.rstrip().endswith(":") or len(" ".join(taken)) >= 20:
            break
    lead = "\n".join(taken)
    if len(text) > limit and lead == head and " " in head:
        space = head.rfind(" ")
        lead = head[:space] if space > limit // 2 else head
    return lead.strip()


def plan_chunks(text: str, limits: ExtractionLimits | None = None) -> tuple[TextChunk, ...]:
    """Dzieli tekst bloku na części; własne zakresy tworzą rozbicie ``[0, len(text))`` bez luk i nakładania."""
    active = limits or ExtractionLimits()
    if len(text) <= active.block_char_limit:
        return (TextChunk(index=0, start=0, end=len(text), context_start=0),)
    lead = _lead(text, active.lead_chars)
    overhead = (len(lead) + len(contract.CONTEXT_GAP_MARKER) + 2) if lead else 0
    later_budget = active.block_char_limit - active.chunk_overlap_chars - overhead
    chunks: list[TextChunk] = []
    start = 0
    while start < len(text):
        index = len(chunks)
        budget = active.block_char_limit if index == 0 else later_budget
        end = _cut(text, start, min(len(text), start + budget))
        if end <= start:  # zabezpieczenie: zawsze postęp
            end = min(len(text), start + budget)
        context = 0 if index == 0 else _context_start(text, start, active.chunk_overlap_chars)
        chunks.append(TextChunk(index=index, start=start, end=end, context_start=context, lead=lead if index else ""))
        start = end
        if len(chunks) > 10_000:  # pragma: no cover - ochrona przed błędem arytmetyki
            raise RuntimeError("Podział bloku nie zbiega.")
    return tuple(chunks)


# --- wynik ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ChunkProvenance:
    """Provenance jednej części: skróty, tokeny, powód zakończenia, status i kod przyczyny (bez treści)."""

    index: int
    start: int
    end: int
    context_start: int
    input_sha256: str
    request_sha256: str
    status: ChunkStatus
    response_sha256: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    model_returned: str | None = None
    attempts: int = 0
    error_code: str | None = None
    violations: tuple[ContractViolation, ...] = ()
    candidates_returned: int = 0
    rejected: tuple[RejectedCandidate, ...] = ()


@dataclass(frozen=True)
class ExtractionProvenance:
    provider: str
    model_requested: str
    models_returned: tuple[str, ...]
    model_mismatch: bool
    prompt_version: str
    prompt_sha256: str
    schema_version: str
    schema_sha256: str
    temperature: float
    chunks: tuple[ChunkProvenance, ...]


@dataclass(frozen=True)
class BlockExtraction:
    """Wynik dla jednego bloku: kandydaci (nie zweryfikowani), jawne „nie znaleziono”, odrzucenia i provenance."""

    block_id: str
    symbols: tuple[str, ...]
    status: BlockStatus
    candidates: tuple[CandidateRecord, ...]
    not_found: tuple[tuple[str, str], ...]
    unaccounted: tuple[tuple[str, str], ...]
    rejected: tuple[RejectedCandidate, ...]
    failures: tuple[str, ...]
    provenance: ExtractionProvenance
    reason: str | None = None


@dataclass(frozen=True)
class PlannedRequest:
    chunk: TextChunk
    request: StructuredExtractionRequest


class LlmExtractionService:
    """Buduje żądania z bloków stref, woła port i składa kandydatów z provenance."""

    def __init__(self, provider: StructuredExtractionProvider, limits: ExtractionLimits | None = None) -> None:
        self.provider = provider
        self.limits = limits or ExtractionLimits()

    def plan(self, block: ZoneBlock) -> tuple[PlannedRequest, ...]:
        """Żądania dla bloku bez wywołań sieci (używane też do budowy fixtures i szacowania kosztu)."""
        chunks = plan_chunks(block.text, self.limits)
        total = len(chunks)
        planned = []
        for chunk in chunks:
            user_text = contract.render_user_text(
                symbols=block.symbols,
                path=block.path,
                text=chunk.rendered(block.text),
                part=(chunk.index + 1, total) if total > 1 else None,
            )
            planned.append(
                PlannedRequest(
                    chunk=chunk,
                    request=StructuredExtractionRequest(
                        system_instruction=contract.system_instruction(),
                        user_text=user_text,
                        response_schema=contract.RESPONSE_SCHEMA,
                        temperature=self.limits.temperature,
                        max_output_tokens=self.limits.max_output_tokens,
                        prompt_version=contract.PROMPT_VERSION,
                        prompt_sha256=contract.prompt_sha256(),
                        schema_version=contract.SCHEMA_VERSION,
                    ),
                )
            )
        return tuple(planned)

    def _provenance(self, chunks: list[ChunkProvenance]) -> ExtractionProvenance:
        returned = tuple(dict.fromkeys(item.model_returned for item in chunks if item.model_returned))
        return ExtractionProvenance(
            provider=self.provider.provider_name,
            model_requested=self.provider.model,
            models_returned=returned,
            model_mismatch=any(item.error_code == CODE_MODEL_MISMATCH for item in chunks),
            prompt_version=contract.PROMPT_VERSION,
            prompt_sha256=contract.prompt_sha256(),
            schema_version=contract.SCHEMA_VERSION,
            schema_sha256=contract.SCHEMA_SHA256,
            temperature=self.limits.temperature,
            chunks=tuple(chunks),
        )

    def _not_run(self, block: ZoneBlock, reason: str) -> BlockExtraction:
        return BlockExtraction(
            block_id=block.block_id,
            symbols=tuple(block.symbols),
            status="skipped",
            candidates=(),
            not_found=(),
            unaccounted=(),
            rejected=(),
            failures=(reason,),
            provenance=self._provenance([]),
            reason=reason,
        )

    async def extract_block(self, block: ZoneBlock) -> BlockExtraction:
        text = block.text
        if not text.strip():
            return self._not_run(block, CODE_EMPTY_BLOCK)
        if contract.DOCUMENT_BEGIN in text or contract.DOCUMENT_END in text:
            return self._not_run(block, CODE_DOCUMENT_TEXT_UNSAFE)
        chunks = plan_chunks(text, self.limits)
        if len(chunks) > self.limits.max_chunks:
            return self._not_run(block, CODE_BLOCK_TOO_LARGE)
        try:
            planned = self.plan(block)
        except DocumentTextError:
            return self._not_run(block, CODE_DOCUMENT_TEXT_UNSAFE)

        records: dict[tuple[object, ...], CandidateRecord] = {}
        explicit_not_found: list[tuple[str, str]] = []
        rejected: list[RejectedCandidate] = []
        failures: list[str] = []
        history: list[ChunkProvenance] = []
        aborted_by: str | None = None
        for item in planned:
            chunk, request = item.chunk, item.request
            base = ChunkProvenance(
                index=chunk.index,
                start=chunk.start,
                end=chunk.end,
                context_start=chunk.context_start,
                input_sha256=request.input_sha256,
                request_sha256=request.request_sha256,
                status="skipped",
            )
            if aborted_by is not None:
                history.append(replace(base, error_code=CODE_ABORTED))
                failures.append(CODE_ABORTED)
                continue
            try:
                result = await self.provider.extract_structured(request)
            except StructuredExtractionError as error:
                history.append(replace(base, status="error", error_code=error.code.value, attempts=error.attempts))
                failures.append(error.code.value)
                if error.code in ABORT_CODES:
                    aborted_by = error.code.value
                continue
            chunk_provenance, located, chunk_not_found = self._read_result(block, chunk, base, result)
            history.append(chunk_provenance)
            if chunk_provenance.status != "ok":
                failures.append(chunk_provenance.error_code or "rejected")
                continue
            rejected.extend(chunk_provenance.rejected)
            explicit_not_found.extend(chunk_not_found)
            for record in located:
                records.setdefault(record.dedupe_key, record)

        candidates = tuple(sorted(records.values(), key=_candidate_order))
        ok_chunks = [item for item in history if item.status == "ok"]
        found = {(record.zone_symbol, record.candidate.parameter) for record in candidates}
        every_pair = [(symbol, parameter) for symbol in block.symbols for parameter in contract.PARAMETERS]
        stated = set(explicit_not_found)
        not_found = tuple(pair for pair in every_pair if pair not in found and pair in stated) if ok_chunks else ()
        unaccounted = tuple(pair for pair in every_pair if pair not in found and pair not in stated) if ok_chunks else ()
        status: BlockStatus = "ok" if len(ok_chunks) == len(history) else ("partial" if ok_chunks else "failed")
        logger.info(
            "llm_extraction block=%s status=%s chunks=%d ok=%d candidates=%d rejected=%d failures=%d",
            block.block_id, status, len(history), len(ok_chunks), len(candidates), len(rejected), len(failures),
        )
        return BlockExtraction(
            block_id=block.block_id,
            symbols=tuple(block.symbols),
            status=status,
            candidates=candidates,
            not_found=not_found,
            unaccounted=unaccounted,
            rejected=tuple(rejected),
            failures=tuple(failures),
            provenance=self._provenance(history),
        )

    def _read_result(
        self,
        block: ZoneBlock,
        chunk: TextChunk,
        base: ChunkProvenance,
        result: StructuredExtractionResult,
    ) -> tuple[ChunkProvenance, tuple[CandidateRecord, ...], tuple[tuple[str, str], ...]]:
        common = replace(
            base,
            response_sha256=result.response_sha256,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            finish_reason=result.finish_reason,
            model_returned=result.model_returned,
            attempts=result.attempts,
        )
        if result.model_mismatch:
            # Inny model niż zmierzony i skalibrowany: jakość nieznana, więc odpowiedzi nie używamy.
            return replace(common, status="rejected", error_code=CODE_MODEL_MISMATCH), (), ()
        try:
            parsed = contract.parse_payload(result.content, block.symbols)
        except ExtractionContractError as error:
            return replace(common, status="rejected", error_code=error.code, violations=error.violations), (), ()
        located = tuple(_with_spans(record, block.text, chunk.index) for record in parsed.candidates)
        provenance = replace(
            common,
            status="ok",
            candidates_returned=len(parsed.candidates) + len(parsed.rejected),
            rejected=parsed.rejected,
        )
        return provenance, located, parsed.not_found


def _with_spans(record: CandidateRecord, text: str, chunk_index: int) -> CandidateRecord:
    return replace(
        record,
        evidence_span=contract.locate_quote(text, record.candidate.evidence_quote),
        scope_span=contract.locate_quote(text, record.candidate.scope_quote),
        chunk_index=chunk_index,
    )


def _candidate_order(record: CandidateRecord) -> tuple[object, ...]:
    start = record.evidence_span[0] if record.evidence_span else -1
    return (start, record.zone_symbol, record.candidate.parameter, record.candidate.operator, record.candidate.raw_value)
