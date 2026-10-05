"""Złote testy odtwarzania (PV3-11): układy zakresu 1–6 i przypadek „nie znaleziono”, bez internetu.

Odpowiedzi w ``tests/fixtures/mpzp_evaluation/llm_replay`` są złote (składa je
``scripts/build_llm_replay_fixtures.py`` z adnotacji korpusu), a NIE nagrane z modelu. Testują potok:
klucz cache (instrukcja, schemat, model, wejście), kontrakt, lokalizację cytatów, scalanie i provenance.
"""

from __future__ import annotations

import socket
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from app.modules.planning.application.llm_extraction import BlockExtraction, ExtractionLimits, LlmExtractionService
from app.modules.planning.domain import extraction_contract as contract
from app.modules.planning.domain.zone_blocks import ZoneBlock
from app.modules.planning.infrastructure.llm.fake_provider import ReplayStructuredExtractionProvider
from app.shared.zone_symbol import same_zone_symbol
from scripts import build_llm_replay_fixtures as golden

REPLAY_DIR = golden.DEFAULT_OUTPUT_DIR


@pytest.fixture(scope="module")
def cases() -> dict[str, list[ZoneBlock]]:
    return {case.case_id: golden.case_blocks(case) for case in golden.CASES}


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("test odtwarzania nie może używać sieci")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def service(directory: Path = REPLAY_DIR) -> tuple[LlmExtractionService, ReplayStructuredExtractionProvider]:
    provider = ReplayStructuredExtractionProvider(directory, model=golden.MODEL)
    return LlmExtractionService(provider, ExtractionLimits()), provider


def expected_from_annotations(case: golden.GoldenCase, block: ZoneBlock) -> set[tuple[str, str, str, float]]:
    """Co powinno zostać zwrócone dla bloku wg adnotacji korpusu (niezależnie od generatora odpowiedzi)."""
    expected = set()
    for zone in golden.sample_zones(case):
        for symbol in block.symbols:
            if not same_zone_symbol(zone["symbol"], symbol):
                continue
            for annotation in zone["annotations"]:
                evidence, raw, op = annotation.get("evidence") or "", annotation.get("raw_value") or "", annotation["operator"]
                spec = contract.CATALOG.get(annotation["parameter"])
                if (
                    spec is None or op not in spec.operators or annotation.get("normalized_value") is None
                    or annotation.get("status") not in {"required", "acceptable"}
                    or contract.locate_quote(block.text, evidence) is None or not contract.quote_contains(evidence, raw)
                    or contract.derive_value(op, raw) != annotation["normalized_value"]
                ):
                    continue
                anchor = annotation.get("anchor") or ""
                if contract.locate_quote(block.text, anchor) is None and block.scope_kind != "fallback":
                    first = block.text.strip().splitlines()[0][:160]
                    if contract.locate_quote(block.text, first) is None:
                        continue
                expected.add((symbol, annotation["parameter"], op, annotation["normalized_value"]))
    return expected


async def extract_all(cases: dict[str, list[ZoneBlock]]) -> dict[tuple[str, str], BlockExtraction]:
    runner, _ = service()
    return {(case_id, block.block_id): await runner.extract_block(block) for case_id, blocks in cases.items() for block in blocks}


def test_the_stored_responses_are_up_to_date_with_the_prompt_schema_and_corpus() -> None:
    """Zmiana instrukcji, szablonu, schematu albo bloków unieważnia zapisane odpowiedzi (inny klucz)."""
    problems = golden.stale_files(REPLAY_DIR, golden.build_records(REPLAY_DIR))
    assert problems == [], (
        "Złote odpowiedzi są nieaktualne (prompt, schemat, model albo bloki się zmieniły): "
        "uruchom `python3 scripts/build_llm_replay_fixtures.py`.\n" + "\n".join(problems[:10])
    )


def test_the_cases_cover_layouts_1_to_6_and_the_not_found_scan(cases: dict[str, list[ZoneBlock]]) -> None:
    strategies = {block.strategy for blocks in cases.values() for block in blocks}
    assert strategies == {0, 1, 2, 3, 4, 5, 6}
    by_case = {case_id: {block.strategy for block in blocks} for case_id, blocks in cases.items()}
    assert 1 in by_case["L1"] and 2 in by_case["L2"] and 3 in by_case["L3"] and 4 in by_case["L4"]
    assert 5 in by_case["L5"] and 6 in by_case["L6"] and by_case["N1"] == {0}
    assert any(len(block.symbols) > 1 for block in cases["L2"])  # blok wielosymbolowy


async def test_every_golden_block_is_replayed_without_network(cases: dict[str, list[ZoneBlock]], no_network: None) -> None:
    outcomes = await extract_all(cases)
    assert len(outcomes) == 13
    for (case_id, _), outcome in outcomes.items():
        assert outcome.status == "ok", (case_id, outcome.failures)
        assert outcome.failures == () and len(outcome.provenance.chunks) == 1
        assert outcome.provenance.prompt_sha256 == contract.prompt_sha256()
        assert outcome.provenance.schema_sha256 == contract.SCHEMA_SHA256
        assert outcome.provenance.model_mismatch is False and outcome.unaccounted == ()
        assert all(record.review_status == "ai_candidate" and record.quote_located for record in outcome.candidates)
        assert {c.status for c in outcome.provenance.chunks} == {"ok"}


async def test_the_replayed_candidates_equal_the_annotations_for_each_block(cases: dict[str, list[ZoneBlock]]) -> None:
    outcomes = await extract_all(cases)
    by_id = {case.case_id: case for case in golden.CASES}
    total = 0
    for (case_id, block_id), outcome in outcomes.items():
        block = next(b for b in cases[case_id] if b.block_id == block_id)
        got = {(r.zone_symbol, r.candidate.parameter, r.candidate.operator, r.derived_value) for r in outcome.candidates}
        assert got == expected_from_annotations(by_id[case_id], block), (case_id, block_id)
        assert all(not r.value_ignored for r in outcome.candidates)
        for record in outcome.candidates:
            start, end = record.evidence_span or (0, 0)
            assert contract.normalize_text(block.text[start:end]) == contract.normalize_text(record.candidate.evidence_quote)
            assert record.candidate.applicability == {"zone_section": "zone_section", "general_clause": "general_clause",
                                                      "residual_clause": "residual_clause", "fallback": "unresolved"}[block.scope_kind]
        total += len(outcome.candidates)
    assert total >= 40  # przypadki niosą realne wartości, nie tylko pustą odpowiedź


async def test_a_shared_block_is_one_request_with_results_per_symbol(cases: dict[str, list[ZoneBlock]]) -> None:
    runner, provider = service()
    (shared,) = [b for b in cases["L2"] if len(b.symbols) == 2 and b.scope_kind == "zone_section"]
    outcome = await runner.extract_block(shared)
    assert provider.calls == 1
    assert {r.zone_symbol for r in outcome.candidates} == {"1Up", "2Up"}
    per_symbol = Counter(r.zone_symbol for r in outcome.candidates)
    assert per_symbol["1Up"] > 0 and per_symbol["2Up"] > 0


async def test_the_scan_without_catalog_values_is_an_explicit_not_found(cases: dict[str, list[ZoneBlock]]) -> None:
    (scan,) = cases["N1"]
    outcome = await service()[0].extract_block(scan)
    assert outcome.status == "ok" and outcome.candidates == ()
    assert set(outcome.not_found) == {(symbol, parameter) for symbol in scan.symbols for parameter in contract.PARAMETERS}
    assert outcome.unaccounted == () and scan.strategy == 0 and scan.scope_kind == "fallback"


async def test_conditional_values_keep_their_conditions(cases: dict[str, list[ZoneBlock]]) -> None:
    outcomes = await extract_all(cases)
    conditional = [r for o in outcomes.values() for r in o.candidates if r.candidate.conditions]
    assert conditional, "złote przypadki powinny zawierać wartość warunkową"
    for record in conditional:
        assert all(contract.locate_quote(record.candidate.evidence_quote, c.quote) or c.quote == record.candidate.evidence_quote
                   for c in record.candidate.conditions)


async def test_changing_the_prompt_the_schema_version_or_the_input_misses_the_stored_answers(
    cases: dict[str, list[ZoneBlock]], monkeypatch: pytest.MonkeyPatch
) -> None:
    block = cases["L1"][0]
    runner, _ = service()
    assert (await runner.extract_block(block)).status == "ok"

    original = contract.system_instruction()
    monkeypatch.setattr(contract, "system_instruction", lambda: original + "Extra rule.\n")
    changed_prompt = await runner.extract_block(block)
    assert changed_prompt.status == "failed" and changed_prompt.failures == ("replay_miss",)
    assert changed_prompt.provenance.prompt_sha256 != PINNED_HASH()  # provenance zapisane także dla porażki
    monkeypatch.undo()

    monkeypatch.setattr(contract, "SCHEMA_VERSION", "mpzp-extraction-schema/2")
    assert (await runner.extract_block(block)).failures == ("replay_miss",)
    monkeypatch.undo()

    edited = ZoneBlock(block_id=block.block_id, scope_kind=block.scope_kind, symbols=block.symbols, text=block.text + " ",
                       char_span=(0, len(block.text) + 1), pages=block.pages, path=block.path, scope_confidence=0.9)
    assert (await runner.extract_block(edited)).failures == ("replay_miss",)
    other_model = LlmExtractionService(ReplayStructuredExtractionProvider(REPLAY_DIR, model="gemini-3.7-flash"))
    assert (await other_model.extract_block(block)).failures == ("replay_miss",)


def PINNED_HASH() -> str:  # noqa: N802 - czytelność asercji
    from tests.test_llm_extraction_contract import PINNED_PROMPTS

    return PINNED_PROMPTS[contract.PROMPT_VERSION]


def test_the_fixtures_are_labelled_as_golden_not_recorded_from_a_model() -> None:
    import json

    records: list[dict[str, Any]] = [json.loads(path.read_text("utf-8")) for path in sorted(REPLAY_DIR.glob("*.json"))]
    assert len(records) == 13 and {r["origin"] for r in records} == {"golden_fixture"}
    assert {r["case"] for r in records} == {case.case_id for case in golden.CASES}
    assert (REPLAY_DIR / "README.md").is_file()
