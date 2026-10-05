"""Usługa ekstrakcji bloku strefy (PV3-11): żądania, podział dużych bloków, scalanie, provenance, błędy.

Dostawca to atrapa skryptowa (port ``StructuredExtractionProvider``); żaden test nie używa sieci.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

import pytest

from app.modules.planning.application import llm_extraction as svc
from app.modules.planning.application.llm_extraction import (
    ExtractionLimits,
    LlmExtractionService,
    plan_chunks,
)
from app.modules.planning.application.ports import (
    StructuredExtractionError,
    StructuredExtractionErrorCode as Code,
    StructuredExtractionRequest,
    StructuredExtractionResult,
)
from app.modules.planning.domain import extraction_contract as contract
from app.modules.planning.domain.zone_blocks import ZoneBlock

MODEL = "gemini-3.8-flash"
ITEM = "{i}) maksymalna wysokość zabudowy: {i} m;\n"


def block(text: str, *, symbols: tuple[str, ...] = ("1MN",), path: str = "§ 5 > ust. 2", block_id: str = "blk-1",
          scope_kind: str = "zone_section") -> ZoneBlock:
    return ZoneBlock(
        block_id=block_id, scope_kind=scope_kind, symbols=symbols, text=text,  # type: ignore[arg-type]
        char_span=(0, len(text)), pages=(1,), path=path, scope_confidence=0.9,
    )


def doc_text(request: StructuredExtractionRequest) -> str:
    return request.user_text.split(contract.DOCUMENT_BEGIN + "\n", 1)[1].rsplit("\n" + contract.DOCUMENT_END, 1)[0]


def result(content: dict[str, Any], **overrides: Any) -> StructuredExtractionResult:
    fields: dict[str, Any] = {
        "content": content, "provider": "fake", "model_requested": MODEL, "model_returned": MODEL, "model_mismatch": False,
        "response_sha256": contract.sha256_text(contract.canonical_json(content)), "request_sha256": "r" * 64,
        "input_sha256": "i" * 64, "finish_reason": "STOP", "input_tokens": 100, "output_tokens": 20, "attempts": 1,
    }
    fields.update(overrides)
    return StructuredExtractionResult(**fields)


def cand(symbol: str, parameter: str, raw: str, evidence: str, **extra: Any) -> dict[str, Any]:
    value = float(re.sub(r"[^\d,.]", "", raw).replace(",", ".") or 0)
    base: dict[str, Any] = {
        "zone_symbol": symbol, "parameter": parameter, "operator": "max", "raw_value": raw, "value": value,
        "unit": "m", "applicability": "zone_section", "conditions": [], "evidence_quote": evidence,
        "scope_quote": "Dla terenu 1MN:",
    }
    base.update(extra)
    return base


class Scripted:
    """Atrapa portu: ``handler(request, index)`` zwraca treść odpowiedzi, wynik albo wyjątek."""

    provider_name = "fake"
    model = MODEL

    def __init__(self, handler: Callable[[StructuredExtractionRequest, int], Any]) -> None:
        self.handler = handler
        self.requests: list[StructuredExtractionRequest] = []

    async def extract_structured(self, request: StructuredExtractionRequest) -> StructuredExtractionResult:
        index = len(self.requests)
        self.requests.append(request)
        outcome = self.handler(request, index)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome if isinstance(outcome, StructuredExtractionResult) else result(outcome)


def answer(candidates: list[dict[str, Any]] | None = None, not_found: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    return {"candidates": candidates or [], "not_found": [{"zone_symbol": s, "parameter": p} for s, p in not_found or []]}


def echo(request: StructuredExtractionRequest, index: int) -> dict[str, Any]:
    """Model idealny dla testów podziału: cytuje każdy punkt „N) … N m” z tekstu, który dostał."""
    found = re.findall(r"\d+\) maksymalna wysokość zabudowy: (\d+) m;", doc_text(request))
    return answer([cand("1MN", "max_building_height_m", f"{n} m", f"maksymalna wysokość zabudowy: {n} m") for n in found])


# --- planowanie części ------------------------------------------------------------------------------------


def long_list(count: int = 320) -> str:
    return "§ 5. Dla terenu 1MN:\n" + "".join(ITEM.format(i=i) for i in range(1, count + 1))


def sentences(count: int = 300) -> str:
    return " ".join(f"Zdanie {i} otwiera. Zdanie {i} podaje wysokość {i} m." for i in range(1, count + 1))


@pytest.mark.parametrize("text", [long_list(), sentences(), "x" * 20_000, ("słowo " * 4000).strip(), long_list(40) + sentences(30)])
def test_chunks_partition_the_text_without_gaps_or_overlaps_and_respect_the_limit(text: str) -> None:
    limits = ExtractionLimits(block_char_limit=3000, chunk_overlap_chars=300, lead_chars=120)
    chunks = plan_chunks(text, limits)
    assert chunks[0].start == 0 and chunks[-1].end == len(text) and len(chunks) > 1
    assert all(a.end == b.start for a, b in zip(chunks, chunks[1:], strict=False))
    assert all(chunk.start < chunk.end for chunk in chunks)
    assert "".join(text[c.start : c.end] for c in chunks) == text
    for chunk in chunks:
        assert len(chunk.rendered(text)) <= limits.block_char_limit
        assert chunk.start - chunk.context_start <= limits.chunk_overlap_chars
        assert chunk.context_start <= chunk.start
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert plan_chunks(text, limits) == chunks  # deterministyczne


def test_a_block_within_the_limit_is_one_untouched_chunk() -> None:
    text = long_list(10)
    (only,) = plan_chunks(text)
    assert (only.start, only.end, only.context_start, only.lead) == (0, len(text), 0, "")
    assert only.rendered(text) == text


def test_cuts_prefer_list_markers_then_paragraphs_so_clauses_are_not_split() -> None:
    text = long_list(400)
    chunks = plan_chunks(text, ExtractionLimits(block_char_limit=2500, chunk_overlap_chars=250, lead_chars=60))
    for chunk in chunks[1:]:
        assert re.match(r"\d+\) ", text[chunk.start :]), text[chunk.start : chunk.start + 20]  # nowa część zaczyna się od punktu
        assert text[chunk.context_start - 1] == "\n" or chunk.context_start == 0
    paragraphs = "\n\n".join(f"Akapit {i}. " + "treść akapitu " * 30 for i in range(40))
    for chunk in plan_chunks(paragraphs, ExtractionLimits(block_char_limit=2500, chunk_overlap_chars=250, lead_chars=60))[1:]:
        assert paragraphs[chunk.start - 2 : chunk.start] == "\n\n"


def test_later_chunks_repeat_the_opening_line_and_mark_the_gap() -> None:
    text = long_list(400)
    first, second, *_ = plan_chunks(text, ExtractionLimits(block_char_limit=2500, chunk_overlap_chars=250, lead_chars=60))
    assert first.lead == "" and second.lead == "§ 5. Dla terenu 1MN:"
    rendered = second.rendered(text)
    assert rendered.startswith("§ 5. Dla terenu 1MN:\n[…]\n") and rendered.endswith(text[second.context_start : second.end])


def test_limits_are_validated() -> None:
    for kwargs in (
        {"block_char_limit": 500}, {"chunk_overlap_chars": -1}, {"lead_chars": -1}, {"max_chunks": 0},
        {"block_char_limit": 1000, "chunk_overlap_chars": 400, "lead_chars": 200},
    ):
        with pytest.raises(ValueError):
            ExtractionLimits(**kwargs)
    assert ExtractionLimits().block_char_limit == 6000


def test_overlap_zero_and_no_lead_still_partition_the_text() -> None:
    text = sentences(200)
    chunks = plan_chunks(text, ExtractionLimits(block_char_limit=1500, chunk_overlap_chars=0, lead_chars=0))
    assert all(c.context_start == c.start for c in chunks[1:]) and all(c.lead == "" for c in chunks)
    assert "".join(text[c.start : c.end] for c in chunks) == text


# --- żądania ----------------------------------------------------------------------------------------------------


async def test_one_request_per_block_with_all_symbols_and_a_privacy_safe_message() -> None:
    text = "§ 7. Dla terenów 1Up, 2Up:\n1) maksymalna wysokość zabudowy: 12 m;"
    provider = Scripted(lambda r, i: answer(not_found=[("1Up", "setback_m")]))
    service = LlmExtractionService(provider)
    outcome = await service.extract_block(block(text, symbols=("1Up", "2Up"), path="Rozdział 2 > § 7", block_id="analysis-9999-parcel-123"))
    assert len(provider.requests) == 1  # blok wielosymbolowy jest wysyłany raz
    request = provider.requests[0]
    assert request.system_instruction == contract.system_instruction()
    assert request.response_schema == contract.RESPONSE_SCHEMA and request.temperature == 0.0
    assert (request.prompt_version, request.prompt_sha256, request.schema_version) == (
        contract.PROMPT_VERSION, contract.prompt_sha256(), contract.SCHEMA_VERSION)
    lines = request.user_text.split("\n")
    assert lines[0] == "Zone symbols: 1Up, 2Up" and lines[1] == "Heading path: Rozdział 2 > § 7"
    assert doc_text(request) == text
    assert "analysis-9999" not in request.user_text and "parcel-123" not in request.user_text  # bez identyfikatorów
    assert outcome.symbols == ("1Up", "2Up")


async def test_plan_returns_exactly_the_requests_that_are_sent() -> None:
    big = block(long_list())
    provider = Scripted(echo)
    service = LlmExtractionService(provider, ExtractionLimits(block_char_limit=3000, chunk_overlap_chars=300, lead_chars=60))
    planned = service.plan(big)
    await service.extract_block(big)
    assert [item.request for item in planned] == provider.requests
    assert len(planned) > 1 and "Part: 1 of" in planned[0].request.user_text and f"Part: {len(planned)} of {len(planned)}" in planned[-1].request.user_text


async def test_parameters_flow_to_the_requests() -> None:
    provider = Scripted(lambda r, i: answer())
    await LlmExtractionService(provider, ExtractionLimits(temperature=0.0, max_output_tokens=2048)).extract_block(block("Dla terenu 1MN: brak liczb."))
    assert provider.requests[0].max_output_tokens == 2048 and provider.requests[0].temperature == 0.0


# --- wynik: kandydaci, provenance --------------------------------------------------------------------------------


TEXT = (
    "§ 5. Dla terenu 1MN:\n"
    "1) maksymalna wysokość zabudowy: 11 m;\n"
    "2) minimalny udział powierzchni biologicznie czynnej: 40%;\n"
    "3) dla budynków przekrytych dachem płaskim: 9,5 m."
)


async def test_candidates_carry_spans_status_and_provenance() -> None:
    first = cand("1MN", "max_building_height_m", "11 m", "maksymalna wysokość zabudowy: 11 m")
    second = cand("1MN", "min_biologically_active_percent", "40%", "minimalny udział powierzchni biologicznie czynnej: 40%",
                  operator="min", unit="percent")
    third = cand("1MN", "max_building_height_m", "9,5 m", "dla budynków przekrytych dachem płaskim: 9,5 m",
                 conditions=[{"kind": "roof_type", "label": "dach płaski", "quote": "dla budynków przekrytych dachem płaskim"}])
    provider = Scripted(lambda r, i: result(answer([second, first, third], [("1MN", "setback_m")]), input_tokens=321, output_tokens=45))
    outcome = await LlmExtractionService(provider).extract_block(block(TEXT))

    assert outcome.status == "ok" and outcome.failures == () and outcome.reason is None
    assert [r.candidate.raw_value for r in outcome.candidates] == ["11 m", "40%", "9,5 m"]  # po położeniu cytatu
    for record in outcome.candidates:
        start, end = record.evidence_span or (0, 0)
        assert contract.normalize_text(TEXT[start:end]) == contract.normalize_text(record.candidate.evidence_quote)
        assert record.quote_located and record.scope_span is not None and record.review_status == "ai_candidate"
        assert record.chunk_index == 0 and not hasattr(record, "verified")
    assert outcome.candidates[2].candidate.conditions[0].kind == "roof_type"
    assert ("1MN", "setback_m") in outcome.not_found
    assert ("1MN", "max_storeys") in outcome.unaccounted  # odpowiedź nie wspomina o parze
    assert ("1MN", "max_building_height_m") not in outcome.not_found + outcome.unaccounted

    prov = outcome.provenance
    assert (prov.provider, prov.model_requested, prov.models_returned, prov.model_mismatch) == ("fake", MODEL, (MODEL,), False)
    assert (prov.prompt_version, prov.prompt_sha256) == (contract.PROMPT_VERSION, contract.prompt_sha256())
    assert (prov.schema_version, prov.schema_sha256, prov.temperature) == (contract.SCHEMA_VERSION, contract.SCHEMA_SHA256, 0.0)
    (chunk,) = prov.chunks
    assert chunk.status == "ok" and chunk.input_tokens == 321 and chunk.output_tokens == 45 and chunk.finish_reason == "STOP"
    assert chunk.input_sha256 == provider.requests[0].input_sha256 and chunk.request_sha256 == provider.requests[0].request_sha256
    assert chunk.response_sha256 and chunk.candidates_returned == 3 and (chunk.start, chunk.end) == (0, len(TEXT))


async def test_a_fabricated_quote_is_kept_as_unlocated_and_never_verified() -> None:
    fake = cand("1MN", "max_building_height_m", "11 m", "wysokość zabudowy wynosi 11 m", scope_quote="Dla terenu 1MN:")
    outcome = await LlmExtractionService(Scripted(lambda r, i: answer([fake]))).extract_block(block(TEXT))
    (record,) = outcome.candidates
    assert record.evidence_span is None and record.quote_located is False and record.review_status == "ai_candidate"


async def test_contract_breaking_candidates_are_dropped_with_codes_and_kept_in_provenance() -> None:
    good = cand("1MN", "max_building_height_m", "11 m", "maksymalna wysokość zabudowy: 11 m")
    unknown = cand("9ZZ", "max_building_height_m", "11 m", "maksymalna wysokość zabudowy: 11 m")
    wrong_op = cand("1MN", "max_building_height_m", "11 m", "maksymalna wysokość zabudowy: 11 m", operator="min")
    outcome = await LlmExtractionService(Scripted(lambda r, i: answer([unknown, good, wrong_op]))).extract_block(block(TEXT))
    assert [r.candidate.raw_value for r in outcome.candidates] == ["11 m"]
    assert [(item.code, item.index) for item in outcome.rejected] == [("unknown_symbol", 0), ("operator_not_allowed", 2)]
    assert outcome.provenance.chunks[0].candidates_returned == 3 and len(outcome.provenance.chunks[0].rejected) == 2
    assert outcome.status == "ok"


async def test_a_schema_breaking_answer_is_rejected_with_a_code_but_provenance_is_kept() -> None:
    bad = {"candidates": [cand("1MN", "max_building_height_m", "11 m", "SECRET maksymalna wysokość zabudowy: 11 m") | {"value": "11"}],
           "not_found": []}
    outcome = await LlmExtractionService(Scripted(lambda r, i: bad)).extract_block(block(TEXT))
    assert outcome.status == "failed" and outcome.candidates == () and outcome.failures == ("schema_violation",)
    (chunk,) = outcome.provenance.chunks
    assert chunk.status == "rejected" and chunk.error_code == "schema_violation" and chunk.response_sha256
    assert [v.path for v in chunk.violations] == ["$.candidates[0].value"]
    assert "SECRET" not in repr(outcome.provenance)
    assert outcome.not_found == () and outcome.unaccounted == ()  # brak odpowiedzi ≠ brak ograniczenia


async def test_an_answer_from_another_model_than_configured_is_not_used() -> None:
    good = cand("1MN", "max_building_height_m", "11 m", "maksymalna wysokość zabudowy: 11 m")
    provider = Scripted(lambda r, i: result(answer([good]), model_returned="gemini-3.8-flash-lite", model_mismatch=True))
    outcome = await LlmExtractionService(provider).extract_block(block(TEXT))
    assert outcome.status == "failed" and outcome.candidates == () and outcome.failures == ("model_mismatch",)
    assert outcome.provenance.model_mismatch is True and outcome.provenance.models_returned == ("gemini-3.8-flash-lite",)
    assert outcome.provenance.chunks[0].error_code == "model_mismatch" and outcome.provenance.model_requested == MODEL


# --- duże bloki ------------------------------------------------------------------------------------------------


LIMITS = ExtractionLimits(block_char_limit=3000, chunk_overlap_chars=300, lead_chars=60, max_chunks=20)


async def test_a_block_over_the_limit_loses_no_quote_and_duplicates_none() -> None:
    text = long_list(320)
    provider = Scripted(echo)
    outcome = await LlmExtractionService(provider, LIMITS).extract_block(block(text))
    assert len(provider.requests) > 2 and outcome.status == "ok"
    returned = sum(len(re.findall(r"\d+\) maksymalna", doc_text(request))) for request in provider.requests)
    assert returned > 320  # nakładki powtarzają punkty, więc model zwrócił duplikaty…
    values = [r.candidate.raw_value for r in outcome.candidates]
    assert sorted(values, key=lambda v: int(v.split()[0])) == [f"{i} m" for i in range(1, 321)]  # …a wynik ma każdy dokładnie raz
    assert len(set(values)) == len(values) == 320
    assert all(record.quote_located for record in outcome.candidates)
    for record in outcome.candidates:  # cytat leży dokładnie tam, gdzie punkt w bloku
        start, end = record.evidence_span or (0, 0)
        assert text[start:end] == record.candidate.evidence_quote
    chunk_ids = {record.chunk_index for record in outcome.candidates}
    assert len(chunk_ids) > 1  # wartości pochodzą z różnych części
    assert [c.status for c in outcome.provenance.chunks] == ["ok"] * len(provider.requests)
    assert outcome.provenance.chunks[-1].end == len(text) and outcome.provenance.chunks[0].start == 0


async def test_quotes_that_straddle_a_cut_are_found_once_through_the_overlap() -> None:
    text = sentences(300)
    pair = re.compile(r"(Zdanie (\d+) otwiera\. Zdanie \2 podaje wysokość (\d+) m\.)")

    def handler(request: StructuredExtractionRequest, index: int) -> dict[str, Any]:
        return answer([cand("1MN", "max_building_height_m", f"{m.group(3)} m", m.group(1)) for m in pair.finditer(doc_text(request))])

    chunks = plan_chunks(text, LIMITS)
    straddling = [m for m in pair.finditer(text) if any(m.start() < c.start < m.end() for c in chunks)]
    assert straddling, "test nie ćwiczy cięcia w środku cytatu"
    outcome = await LlmExtractionService(Scripted(handler), LIMITS).extract_block(block(text))
    assert sorted(int(r.candidate.raw_value.split()[0]) for r in outcome.candidates) == list(range(1, 301))
    for match in straddling:
        record = next(r for r in outcome.candidates if r.candidate.raw_value == f"{match.group(3)} m")
        assert record.evidence_span == (match.start(), match.end())


async def test_the_same_clause_for_different_symbols_or_parameters_is_not_collapsed() -> None:
    text = "Dla terenów 1MN, 2MN: maksymalna wysokość zabudowy: 11 m oraz udział powierzchni zabudowy: 30%."
    evidence = "maksymalna wysokość zabudowy: 11 m"
    items = [
        cand("1MN", "max_building_height_m", "11 m", evidence), cand("2MN", "max_building_height_m", "11 m", evidence),
        cand("1MN", "max_building_height_m", "11 m", evidence),  # dokładny duplikat
        cand("1MN", "max_building_coverage_percent", "30%", "udział powierzchni zabudowy: 30%", unit="percent"),
    ]
    outcome = await LlmExtractionService(Scripted(lambda r, i: answer(items))).extract_block(
        block(text, symbols=("1MN", "2MN")))
    assert sorted((r.zone_symbol, r.candidate.parameter) for r in outcome.candidates) == [
        ("1MN", "max_building_coverage_percent"), ("1MN", "max_building_height_m"), ("2MN", "max_building_height_m")]


async def test_a_block_that_would_need_too_many_requests_is_skipped_without_calls() -> None:
    provider = Scripted(echo)
    outcome = await LlmExtractionService(provider, ExtractionLimits(block_char_limit=3000, chunk_overlap_chars=300, lead_chars=60, max_chunks=2)).extract_block(
        block(long_list(320)))
    assert provider.requests == [] and outcome.status == "skipped" and outcome.reason == "block_too_large"
    assert outcome.candidates == () and outcome.failures == ("block_too_large",) and outcome.provenance.chunks == ()
    assert outcome.provenance.prompt_sha256 == contract.prompt_sha256()  # provenance także dla pominiętego bloku


# --- błędy dostawcy: wynik niepełny, ale z provenance -----------------------------------------------------------


async def test_a_failed_part_gives_a_partial_result_with_provenance() -> None:
    def handler(request: StructuredExtractionRequest, index: int) -> Any:
        if index == 1:
            return StructuredExtractionError(Code.SERVER_ERROR, status=503, attempts=3, retryable=True)
        return echo(request, index)

    provider = Scripted(handler)
    outcome = await LlmExtractionService(provider, LIMITS).extract_block(block(long_list(320)))
    assert outcome.status == "partial" and len(provider.requests) == len(outcome.provenance.chunks) > 2
    statuses = [c.status for c in outcome.provenance.chunks]
    assert statuses[1] == "error" and statuses.count("ok") == len(statuses) - 1
    assert outcome.provenance.chunks[1].error_code == "server_error" and outcome.provenance.chunks[1].attempts == 3
    assert outcome.failures == ("server_error",)
    values = {int(r.candidate.raw_value.split()[0]) for r in outcome.candidates}
    assert 1 in values and 320 in values and len(values) < 320  # części, które się powiodły, zostają
    assert outcome.not_found == () or ("1MN", "max_building_height_m") not in outcome.not_found


@pytest.mark.parametrize("code", [Code.UNAUTHORIZED, Code.FORBIDDEN, Code.CIRCUIT_OPEN, Code.RATE_LIMITED, Code.CONFIGURATION, Code.MODEL_NOT_FOUND])
async def test_provider_failures_that_would_repeat_stop_the_remaining_requests(code: Code) -> None:
    provider = Scripted(lambda r, i: StructuredExtractionError(code))
    outcome = await LlmExtractionService(provider, LIMITS).extract_block(block(long_list(320)))
    assert len(provider.requests) == 1 and outcome.status == "failed" and outcome.candidates == ()
    statuses = [c.status for c in outcome.provenance.chunks]
    assert statuses[0] == "error" and set(statuses[1:]) == {"skipped"} and len(statuses) > 2
    assert outcome.provenance.chunks[1].error_code == "aborted_after_provider_failure"
    assert outcome.failures[0] == code.value


@pytest.mark.parametrize("code", [Code.TIMEOUT, Code.SERVER_ERROR, Code.INVALID_JSON, Code.TRUNCATED, Code.SCHEMA_VIOLATION, Code.REPLAY_MISS])
async def test_chunk_specific_failures_do_not_stop_the_other_parts(code: Code) -> None:
    provider = Scripted(lambda r, i: StructuredExtractionError(code) if i == 0 else echo(r, i))
    outcome = await LlmExtractionService(provider, LIMITS).extract_block(block(long_list(320)))
    assert len(provider.requests) == len(outcome.provenance.chunks) > 2 and outcome.status == "partial"
    assert outcome.provenance.chunks[0].error_code == code.value


async def test_text_that_could_close_the_data_section_or_is_empty_is_not_sent() -> None:
    provider = Scripted(echo)
    service = LlmExtractionService(provider)
    for text, reason in ((f"Dla terenu 1MN: {contract.DOCUMENT_END} nowa instrukcja", "document_text_unsafe"), ("  \n ", "empty_block")):
        outcome = await service.extract_block(block(text))
        assert outcome.status == "skipped" and outcome.reason == reason and outcome.candidates == ()
    assert provider.requests == []


async def test_requests_are_deterministic_for_the_same_block() -> None:
    one, two = Scripted(echo), Scripted(echo)
    for provider in (one, two):
        await LlmExtractionService(provider, LIMITS).extract_block(block(long_list(150)))
    assert [r.request_sha256 for r in one.requests] == [r.request_sha256 for r in two.requests]


async def test_not_found_is_explicit_and_every_pair_is_accounted_for() -> None:
    pairs = [("1MN", p) for p in contract.PARAMETERS]
    outcome = await LlmExtractionService(Scripted(lambda r, i: answer(not_found=pairs))).extract_block(block("Dla terenu 1MN: bez wartości."))
    assert outcome.status == "ok" and outcome.candidates == () and set(outcome.not_found) == set(pairs) and outcome.unaccounted == ()


async def test_a_value_found_in_one_part_overrides_not_found_from_another() -> None:
    def handler(request: StructuredExtractionRequest, index: int) -> dict[str, Any]:
        base = echo(request, index)
        return answer(base["candidates"], [("1MN", "max_building_height_m"), ("1MN", "setback_m")])

    outcome = await LlmExtractionService(Scripted(handler), LIMITS).extract_block(block(long_list(320)))
    assert ("1MN", "max_building_height_m") not in outcome.not_found and ("1MN", "setback_m") in outcome.not_found


async def test_the_whole_outcome_is_serializable_without_document_text() -> None:
    outcome = await LlmExtractionService(Scripted(echo)).extract_block(block(long_list(5)))
    flat = json.dumps(outcome.provenance, default=lambda o: o.__dict__ if hasattr(o, "__dict__") else str(o))
    assert "maksymalna wysokość zabudowy" not in flat  # provenance niesie skróty i kody, nie tekst
    assert svc.ABORT_CODES >= {Code.UNAUTHORIZED, Code.CIRCUIT_OPEN}
