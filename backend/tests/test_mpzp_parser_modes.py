"""Tryby parsera MPZP i orkiestracja hybrydowa (PV3-14).

Bez sieci i bez klucza: model to dostawca skryptowy, odtwarzanie złotych odpowiedzi
(``tests/fixtures/mpzp_evaluation/llm_replay`` — składane z adnotacji, nie nagrane) albo prawdziwy
adapter Gemini na zamrożonej odpowiedzi ``respx``. Regresja trybów deterministycznych porównuje wynik
z plikiem zamrożonym na kodzie sprzed PV3-14 (``scripts/freeze_mpzp_parser_modes.py``).
"""

from __future__ import annotations

import json
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from pydantic import SecretStr, ValidationError

from app.core.settings import Settings
from app.modules.planning.application import llm_metrics
from app.modules.planning.application.llm_pipeline import LlmBudget, MpzpLlmPipeline
from app.modules.planning.application.ports import (
    StructuredExtractionError,
    StructuredExtractionErrorCode,
    StructuredExtractionRequest,
    StructuredExtractionResult,
)
from app.modules.planning.domain import candidate_verifier as cv
from app.modules.planning.domain import extraction_contract as contract
from app.modules.planning.domain.zone_blocks import ZoneBlock
from app.modules.planning.infrastructure.llm.fake_provider import ReplayStructuredExtractionProvider
from app.modules.planning.infrastructure.llm.gemini_provider import (
    GEMINI_BASE_URL,
    GeminiConfig,
    GeminiStructuredExtractionProvider,
)
from app.modules.planning.infrastructure.llm.resilience import RetryPolicy
from app.schemas.mpzp import MpzpParameter, MpzpZoneResult
from app.schemas.source import SourceMetadata
from app.services import cache as analysis_cache
from app.services import mpzp_parser_hybrid as hybrid
from app.services.mpzp_fetch import DocumentBlob
from app.services.mpzp_parser import parse_mpzp_document
from app.services.mpzp_parser_extract import TextExtractionResult
from app.services.mpzp_parser_options import build_mpzp_parser_options
from app.services.mpzp_zones import apply_parser_zone, unassigned_share_zone
from scripts import build_llm_replay_fixtures as golden
from scripts import evaluate_mpzp_parser as ev
from scripts import freeze_mpzp_parser_modes as freeze

FROZEN = json.loads(freeze.OUTPUT.read_text(encoding="utf-8"))
MODEL = "gemini-3.8-flash"
SOURCE = SourceMetadata(source_name="MPZP_BIP", source_url=None, confidence=0.9, manual_review_required=False)

# Rdzeń znajduje wysokość, ale nie rozpoznaje „nie wyższa aniżeli 3 kondygnacje” (para bez wartości).
TEXT = (
    "§ 5. Dla terenu 1MN ustala się:\n"
    "1) maksymalna wysokość zabudowy: 12 m;\n"
    "2) zabudowa nie wyższa aniżeli 3 kondygnacje nadziemne;\n"
)


def _candidate(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "zone_symbol": "1MN",
        "parameter": "max_storeys",
        "operator": "max",
        "raw_value": "3 kondygnacje",
        "value": 3,
        "unit": "count",
        "applicability": "zone_section",
        "conditions": [],
        "evidence_quote": "zabudowa nie wyższa aniżeli 3 kondygnacje nadziemne",
        "scope_quote": "Dla terenu 1MN ustala się:",
    }
    data.update(overrides)
    return data


GOOD_RESPONSE: dict[str, Any] = {
    "candidates": [
        _candidate(),
        _candidate(parameter="max_building_height_m", raw_value="12 m", value=12, unit="m",
                   evidence_quote="maksymalna wysokość zabudowy: 12 m"),
        # Zmyślony cytat — odrzuci go G3, nie trafi do wyniku.
        _candidate(parameter="max_building_coverage_percent", raw_value="40%", value=40, unit="percent",
                   evidence_quote="maksymalna powierzchnia zabudowy: 40%"),
    ],
    "not_found": [],
}


class ScriptedProvider:
    """Dostawca skryptowy: ta sama odpowiedź dla każdego żądania albo zadany błąd."""

    provider_name = "fake"

    def __init__(self, content: Mapping[str, Any] | None = None, *, error: Exception | None = None) -> None:
        self.model = MODEL
        self.content = dict(content or GOOD_RESPONSE)
        self.error = error
        self.calls = 0
        self.requests: list[StructuredExtractionRequest] = []
        self.closed = False

    async def extract_structured(self, request: StructuredExtractionRequest) -> StructuredExtractionResult:
        self.calls += 1
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return StructuredExtractionResult(
            content=self.content,
            provider="fake",
            model_requested=MODEL,
            model_returned=MODEL,
            model_mismatch=False,
            response_sha256=contract.sha256_text(contract.canonical_json(self.content)),
            request_sha256=request.request_sha256,
            input_sha256=request.input_sha256,
            finish_reason="STOP",
            input_tokens=900,
            output_tokens=300,
        )

    async def aclose(self) -> None:
        self.closed = True


def extraction(text: str = TEXT, method: str = "pdf_text") -> TextExtractionResult:
    return TextExtractionResult(
        pages=[text], tables=[], page_qualities=[1.0], blocks=[[]], quality_score=1.0, needs_ocr=False,
        ocr_used=method == "ocr", extraction_method=method, ocr_engine_version=None, manual_review_required=False,
        warnings=[],
    )


def blob(text: str = TEXT) -> DocumentBlob:
    return DocumentBlob(content=text.encode("utf-8"), media_type="application/pdf", filename="u.pdf", source_metadata=SOURCE)


async def parse(mode: str | None, llm: MpzpLlmPipeline | None = None, *, text: str = TEXT, **kwargs: Any):
    with patch("app.services.mpzp_parser.extract_document_text", new=AsyncMock(return_value=extraction(text))):
        options: dict[str, Any] = {"mode": mode, "llm": llm, **kwargs} if mode is not None else {}
        return await parse_mpzp_document(blob(text), ["1MN"], **options)


def pipeline(provider: Any, **kwargs: Any) -> MpzpLlmPipeline:
    return MpzpLlmPipeline(provider, **kwargs)


def dump(result) -> dict[str, Any]:  # noqa: ANN001
    return result.model_dump(mode="json")


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    llm_metrics.metrics.reset()


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("test nie może używać sieci")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


# --- regresja trybów deterministycznych (zamrożony wynik sprzed PV3-14) ----------------------------------


@pytest.mark.parametrize("fixture_dir", freeze.FIXTURE_DIRS, ids=lambda path: path.name)
async def test_legacy_and_default_results_are_identical_to_the_frozen_snapshot(fixture_dir: Path) -> None:
    frozen = FROZEN[f"{fixture_dir.name}:legacy"]
    assert await freeze.parse_fixture(fixture_dir, "legacy") == frozen  # bez trybu: jak przed PV3-14
    assert await freeze.parse_fixture(fixture_dir, "legacy", mode="legacy") == frozen
    # Tryb deterministyczny ignoruje potok modelu (i nie woła go).
    provider = ScriptedProvider()
    assert await freeze.parse_fixture(fixture_dir, "blocks", mode="legacy", llm=pipeline(provider)) == frozen
    assert provider.calls == 0 and provider.closed


@pytest.mark.parametrize("fixture_dir", freeze.FIXTURE_DIRS, ids=lambda path: path.name)
async def test_v3_and_shadow_results_are_identical_to_the_frozen_block_snapshot(fixture_dir: Path) -> None:
    frozen = FROZEN[f"{fixture_dir.name}:blocks"]
    assert await freeze.parse_fixture(fixture_dir, "legacy", mode="v3") == frozen
    provider = ScriptedProvider()
    assert await freeze.parse_fixture(fixture_dir, "legacy", mode="hybrid_shadow", llm=pipeline(provider)) == frozen
    await hybrid.drain_shadow_tasks()


def test_the_frozen_snapshot_file_is_up_to_date_with_the_deterministic_modes() -> None:
    assert freeze.main(["--check"]) == 0


# --- macierz trybów × dostępność modelu ------------------------------------------------------------------


async def test_hybrid_with_an_available_model_adds_verified_ai_candidates_only_for_missing_pairs(no_network: None) -> None:
    v3 = await parse("v3")
    provider = ScriptedProvider()
    result = await parse("hybrid", pipeline(provider))
    deterministic = {(p.name, p.normalized_value) for p in v3.zones[0].parameters}
    added = [p for p in result.zones[0].parameters if p.review_status == "ai_candidate"]
    assert [(p.name, p.normalized_value) for p in added] == [("max_storeys", 3.0)]
    assert {(p.name, p.normalized_value) for p in result.zones[0].parameters if p.review_status is None} == deterministic
    storeys = added[0]
    assert storeys.extraction_method == "llm_verified" and storeys.manual_review_required
    assert storeys.model_id == MODEL and storeys.prompt_version == contract.PROMPT_VERSION
    assert storeys.response_sha256 and len(storeys.response_sha256) == 64
    assert storeys.source_text == "zabudowa nie wyższa aniżeli 3 kondygnacje nadziemne"
    assert TEXT[storeys.char_start : storeys.char_end] == "3 kondygnacje" and storeys.page_number == 1
    assert storeys.confidence_band != "high" and storeys.normalization_flags == []
    # Zmyślony cytat (G3) jest odrzucony jawnie: ostrzeżenie z licznikiem bramki i ``partial`` (PV3-15).
    assert v3.status == "complete" and result.status == "partial"
    rejected = [w for w in result.warnings if w.code == "MPZP_LLM_CANDIDATES_REJECTED"]
    assert len(rejected) == 1 and "G3: 1" in rejected[0].message
    assert not any(w.code == "MPZP_LLM_UNAVAILABLE" for w in result.warnings)
    assert provider.calls == 1 and provider.closed
    counts = llm_metrics.metrics.snapshot()
    assert counts["llm.verifier.rejected.G3"] == 1 and counts["llm.verifier.accepted"] == 1
    # Wysokość (wartość deterministyczna o wysokiej pewności zakresu) nie była parą do modelu.
    assert all(p.name != "max_building_height_m" for p in added)
    # Dane wysłane do modelu: wyłącznie tekst aktu i symbol; bez identyfikatorów działki i analizy.
    sent = provider.requests[0].user_text
    assert "1MN" in sent and "maksymalna wysokość zabudowy" in sent and "parcel" not in sent.lower()


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (StructuredExtractionError(StructuredExtractionErrorCode.TIMEOUT, retryable=True), "timeout"),
        (StructuredExtractionError(StructuredExtractionErrorCode.RATE_LIMITED, retryable=True), "rate_limited"),
        (RuntimeError("bug w adapterze"), "pipeline_error"),
    ],
)
async def test_hybrid_degrades_to_the_deterministic_result_with_a_warning_when_the_model_fails(
    error: Exception, reason: str
) -> None:
    v3 = await parse("v3")
    result = await parse("hybrid", pipeline(ScriptedProvider(error=error)))
    assert result.zones == v3.zones  # wynik deterministyczny bez zmian
    assert result.status == "partial"
    warning = next(w for w in result.warnings if w.code == "MPZP_LLM_UNAVAILABLE")
    assert reason in warning.message and warning.severity == "warning"
    assert llm_metrics.metrics.snapshot()[f"llm.unavailable.{reason}"] >= 1


async def test_hybrid_with_an_exhausted_budget_sends_nothing_and_warns() -> None:
    provider = ScriptedProvider()
    result = await parse("hybrid", pipeline(provider, budget=LlmBudget(max_requests=0)))
    assert provider.calls == 0 and result.status == "partial"
    assert "budget_exhausted" in next(w.message for w in result.warnings if w.code == "MPZP_LLM_UNAVAILABLE")
    tokens = ScriptedProvider()
    tight = await parse("hybrid", pipeline(tokens, budget=LlmBudget(max_requests=6, max_input_tokens=100)))
    assert tokens.calls == 0 and any(w.code == "MPZP_LLM_UNAVAILABLE" for w in tight.warnings)


async def test_hybrid_without_a_model_pipeline_warns_with_the_reason() -> None:
    result = await parse("hybrid", None)
    assert result.status == "partial"
    assert "llm_disabled" in next(w.message for w in result.warnings if w.code == "MPZP_LLM_UNAVAILABLE")
    configured = await parse("hybrid", None, llm_unavailable_reason="configuration")
    assert "configuration" in next(w.message for w in configured.warnings if w.code == "MPZP_LLM_UNAVAILABLE")


@pytest.mark.parametrize(
    "error",
    [None, StructuredExtractionError(StructuredExtractionErrorCode.TIMEOUT), RuntimeError("x")],
    ids=["available", "timeout", "crash"],
)
async def test_shadow_mode_never_changes_the_response_and_compares_in_the_background(error: Exception | None) -> None:
    v3 = await parse("v3")
    provider = ScriptedProvider(error=error)
    shadow = await parse("hybrid_shadow", pipeline(provider))
    assert dump(shadow) == dump(v3)  # odpowiedź = rdzeń, także gdy model zawodzi
    await hybrid.drain_shadow_tasks()
    assert provider.calls == 1 and provider.closed
    counts = llm_metrics.metrics.snapshot()
    if error is None:
        assert counts["llm.shadow.runs"] == 1 and counts["llm.shadow.llm_only"] == 1  # liczba kondygnacji
        assert counts.get("llm.shadow.det_only", 0) == 0 and counts["llm.shadow.agree"] == 1  # wysokość
    else:
        assert counts.get("llm.shadow.llm_only", 0) == 0
    assert dump(shadow) == dump(v3)  # zadanie w tle nie zmieniło zwróconego obiektu


async def test_shadow_without_a_model_is_skipped_and_returns_v3() -> None:
    assert dump(await parse("hybrid_shadow", None)) == dump(await parse("v3"))
    assert llm_metrics.metrics.snapshot()["llm.shadow.skipped"] == 1


async def test_a_failed_parse_returns_failed_and_closes_the_pipeline() -> None:
    provider = ScriptedProvider()
    with patch("app.services.mpzp_parser.extract_document_text", new=AsyncMock(side_effect=RuntimeError("pdf"))):
        result = await parse_mpzp_document(blob(), ["1MN"], mode="hybrid", llm=pipeline(provider))
    assert result.status == "failed" and provider.calls == 0 and provider.closed


async def test_without_zone_symbols_there_is_nothing_to_ask_the_model() -> None:
    provider = ScriptedProvider()
    with patch("app.services.mpzp_parser.extract_document_text", new=AsyncMock(return_value=extraction("bez symboli"))):
        result = await parse_mpzp_document(blob("bez symboli"), [], mode="hybrid", llm=pipeline(provider))
    assert provider.calls == 0 and not any(w.code == "MPZP_LLM_UNAVAILABLE" for w in result.warnings)


async def test_hybrid_through_the_real_gemini_adapter_on_a_frozen_respx_response(no_network: None) -> None:
    body = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(GOOD_RESPONSE)}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 1500, "candidatesTokenCount": 400, "thoughtsTokenCount": 20},
        "modelVersion": MODEL,
    }
    provider = GeminiStructuredExtractionProvider(
        SecretStr("AIzaSyTEST-key_0123456789abcdefghij"),
        GeminiConfig(model=MODEL, retry=RetryPolicy(max_retries=0)),
    )
    url = f"{GEMINI_BASE_URL}/v1beta/models/{MODEL}:generateContent"
    with respx.mock(assert_all_called=True) as router:
        route = router.post(url).mock(return_value=httpx.Response(200, json=body))
        result = await parse("hybrid", pipeline(provider))
    assert route.call_count == 1
    assert [(p.name, p.normalized_value) for p in result.zones[0].parameters if p.review_status] == [("max_storeys", 3.0)]
    with respx.mock(assert_all_called=True) as router:
        router.post(url).mock(side_effect=httpx.ReadTimeout("timeout"))
        timed_out = await parse("hybrid", pipeline(GeminiStructuredExtractionProvider(
            SecretStr("AIzaSyTEST-key_0123456789abcdefghij"), GeminiConfig(model=MODEL, retry=RetryPolicy(max_retries=0)))))
    assert "timeout" in next(w.message for w in timed_out.warnings if w.code == "MPZP_LLM_UNAVAILABLE")


# --- kontrola kosztu: licznik wywołań ----------------------------------------------------------------------


def _golden_parse_args(case: golden.GoldenCase) -> tuple[TextExtractionResult, DocumentBlob]:
    manifest, base = golden.corpus()
    sample = next(item for item in manifest["samples"] if item["sample_id"] == case.sample_id)
    loaded = ev.load_document(base, manifest["documents"][sample["document_id"]])
    extracted, source = ev.build_extraction(loaded)
    return extracted, ev._blob(source)


class CountingReplay(ReplayStructuredExtractionProvider):
    def __init__(self) -> None:
        super().__init__(golden.DEFAULT_OUTPUT_DIR, model=golden.MODEL)
        self.sent_inputs: list[str] = []

    async def extract_structured(self, request: StructuredExtractionRequest) -> StructuredExtractionResult:
        self.sent_inputs.append(request.input_sha256)
        return await super().extract_structured(request)


async def _golden(case: golden.GoldenCase, mode: str, provider: CountingReplay):
    extracted, document = _golden_parse_args(case)
    with patch("app.services.mpzp_parser.extract_document_text", new=AsyncMock(return_value=extracted)):
        result = await parse_mpzp_document(document, list(case.symbols), mode=mode, llm=pipeline(provider))
    await hybrid.drain_shadow_tasks()
    return result


@pytest.mark.parametrize("case", golden.CASES, ids=lambda case: case.case_id)
async def test_model_calls_are_limited_to_blocks_of_the_targeted_pairs(case: golden.GoldenCase, no_network: None) -> None:
    v3_result = await _golden(case, "v3", CountingReplay())
    targets = hybrid.select_targets(v3_result.zones)
    blocks = [block for block in golden.case_blocks(case)]
    resolution_blocks = {block.block_id for block in blocks if any(s in targets for s in block.symbols)}
    hybrid_provider = CountingReplay()
    result = await _golden(case, "hybrid", hybrid_provider)
    assert hybrid_provider.calls == len(resolution_blocks)
    shadow_provider = CountingReplay()
    await _golden(case, "hybrid_shadow", shadow_provider)
    assert shadow_provider.calls == len(blocks) >= hybrid_provider.calls  # cień liczy wszystko (ewaluacja)
    added = [(z.zone_symbol, p.name) for z in result.zones for p in z.parameters if p.review_status]
    assert all(name in targets.get(symbol, frozenset()) for symbol, name in added)
    assert not any(w.code == "MPZP_LLM_UNAVAILABLE" for w in result.warnings)


async def test_no_targeted_pair_means_no_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hybrid, "select_targets", lambda zones, threshold=0.6: {})
    provider = ScriptedProvider()
    result = await parse("hybrid", pipeline(provider))
    assert provider.calls == 0 and provider.closed and result == await parse("v3")


async def test_a_multi_symbol_block_is_requested_once() -> None:
    case = next(case for case in golden.CASES if case.case_id == "L2")
    provider = CountingReplay()
    await _golden(case, "hybrid_shadow", provider)
    assert provider.calls == len(golden.case_blocks(case)) == len(set(provider.sent_inputs))


def test_select_targets_follows_the_cost_policy() -> None:
    def param(name: str, value: float, **extra: Any) -> MpzpParameter:
        fields: dict[str, Any] = {"scope_kind": "zone_section", "scope_confidence": 0.9, **extra}
        return MpzpParameter(name=name, normalized_value=value, confidence=0.8, manual_review_required=False, **fields)

    zone = MpzpZoneResult(zone_symbol="1MN", parameters=[
        param("max_building_height_m", 12.0),
        param("max_storeys", 2.0, value_kind="conflict"),
        param("setback_m", 6.0, scope_confidence=0.4),
        param("max_intensity", 0.9, scope_kind="fallback"),
    ])
    targets = hybrid.select_targets([zone])["1MN"]
    assert "max_building_height_m" not in targets
    assert {"max_storeys", "setback_m", "max_intensity", "min_intensity", "roof_angle_min_deg"} <= targets
    complete = MpzpZoneResult(zone_symbol="2MN", parameters=[param(name, 1.0) for name in contract.PARAMETERS])
    assert hybrid.select_targets([complete]) == {}


# --- polityka scalania ------------------------------------------------------------------------------------


def _context() -> hybrid.BlocksContext:
    from app.services.mpzp_parser_structure import build_tree_from_extraction, structure_view
    from app.modules.planning.domain.zone_scope import resolve_zone_scope

    tree = build_tree_from_extraction(extraction())
    view = structure_view(tree)
    return hybrid.BlocksContext(tree=tree, view=view, resolution=resolve_zone_scope(view, ["1MN"]), symbols=("1MN",),
                                extraction_method="pdf_text", quality_score=1.0, document_sha256="d" * 64,
                                parser_version="mpzp-parser/3.1-det+scope.1")


def _accepted(raw: str, value: float, quote: str, parameter: str = "max_building_height_m") -> cv.AcceptedCandidate:
    block = ZoneBlock(block_id="zb-x", scope_kind="zone_section", symbols=("1MN",), text=TEXT + quote + ";\n",
                      char_span=(0, len(TEXT) + len(quote) + 2), pages=(1,), path="§ 5", scope_confidence=0.9)
    outcome = cv.verify_candidate(contract.LlmCandidate.model_validate(_candidate(
        parameter=parameter, raw_value=raw, value=value, unit="m", evidence_quote=quote)), block)
    assert isinstance(outcome, cv.AcceptedCandidate), outcome
    return outcome


def test_merge_keeps_the_deterministic_value_first_and_both_values_on_disagreement() -> None:
    context = _context()
    deterministic = MpzpParameter(name="max_building_height_m", normalized_value=12.0, unit="m", confidence=0.85,
                                  manual_review_required=False, scope_kind="zone_section", scope_confidence=0.9)
    zone = MpzpZoneResult(zone_symbol="1MN", parameters=[deterministic])
    agree = _accepted("12 m", 12.0, "maksymalna wysokość zabudowy: 12 m")
    merged, added, disagreements = hybrid.merge_llm_candidates([zone], [agree], context)
    assert (added, disagreements) == (0, 0) and merged[0].parameters == [deterministic]
    differ = _accepted("15 m", 15.0, "wysokość zabudowy do 15 m")
    merged, added, disagreements = hybrid.merge_llm_candidates([zone], [differ], context)
    assert (added, disagreements) == (1, 1)
    kept = merged[0].parameters
    assert [(p.normalized_value, p.review_status, p.manual_review_required) for p in kept] == [
        (12.0, None, True), (15.0, "ai_candidate", True)]
    api_zone, _skipped, conflicts = apply_parser_zone(unassigned_share_zone("1MN", SOURCE), merged[0])
    assert api_zone.max_building_height_m is None and conflicts == ["max_building_height_m"]
    assert api_zone.manual_review_required
    evidence = [p for p in api_zone.parameters if p.review_status == "ai_candidate"][0]
    assert evidence.extraction_method == "llm_verified"


def test_a_model_value_alone_never_fills_the_flat_api_field() -> None:
    only_model = MpzpZoneResult(zone_symbol="1MN", parameters=[])
    merged, added, _ = hybrid.merge_llm_candidates([only_model], [_accepted("12 m", 12.0, "maksymalna wysokość zabudowy: 12 m")], _context())
    api_zone, _skipped, conflicts = apply_parser_zone(unassigned_share_zone("1MN", SOURCE), merged[0])
    assert added == 1 and api_zone.max_building_height_m is None and conflicts == []
    assert api_zone.manual_review_required and api_zone.parameters[0].review_status == "ai_candidate"
    dumped = api_zone.parameters[0].model_dump(mode="json")
    assert dumped["review_status"] == "ai_candidate" and dumped["extraction_method"] == "llm_verified"


def test_deterministic_evidence_json_has_no_llm_provenance_keys() -> None:
    parameter = MpzpParameter(name="max_building_height_m", normalized_value=12.0, confidence=0.8, manual_review_required=False)
    assert not {"review_status", "model_id", "prompt_version", "response_sha256"} & set(parameter.model_dump(mode="json"))
    llm = parameter.model_copy(update={"review_status": "ai_candidate", "model_id": MODEL})
    assert llm.model_dump(mode="json")["review_status"] == "ai_candidate"
    assert "response_sha256" not in llm.model_dump(mode="json")


# --- konfiguracja, opcje i sygnatura cache -------------------------------------------------------------------


def test_the_default_parser_mode_is_legacy_and_unknown_modes_are_rejected() -> None:
    assert Settings(_env_file=None).mpzp_parser_mode == "legacy"  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, mpzp_parser_mode="llm_only")  # type: ignore[call-arg]


def test_deterministic_modes_build_no_model_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("tryb deterministyczny nie tworzy potoku modelu")

    monkeypatch.setattr("app.services.mpzp_parser_options.build_mpzp_llm_pipeline", refuse)
    for mode in ("legacy", "v3"):
        options = build_mpzp_parser_options(Settings(_env_file=None, mpzp_parser_mode=mode, mpzp_llm_enabled=True))  # type: ignore[call-arg]
        assert options.kwargs() == {"mode": mode}


def test_model_modes_report_why_the_model_is_unavailable(tmp_path: Path) -> None:
    disabled = build_mpzp_parser_options(Settings(_env_file=None, mpzp_parser_mode="hybrid"))  # type: ignore[call-arg]
    assert disabled.llm is None and disabled.llm_unavailable_reason == "llm_disabled"
    no_key = build_mpzp_parser_options(Settings(_env_file=None, mpzp_parser_mode="hybrid", mpzp_llm_enabled=True,  # type: ignore[call-arg]
                                                gemini_api_key=None))
    assert no_key.llm is None and no_key.llm_unavailable_reason == "configuration"
    fake = build_mpzp_parser_options(Settings(_env_file=None, mpzp_parser_mode="hybrid_shadow", mpzp_llm_enabled=True,  # type: ignore[call-arg]
                                              mpzp_llm_provider="fake", mpzp_llm_replay_dir=str(tmp_path),
                                              mpzp_llm_cache_enabled=False))
    assert isinstance(fake.llm, MpzpLlmPipeline) and fake.llm.cache is None
    assert fake.kwargs()["mode"] == "hybrid_shadow" and fake.kwargs()["llm"] is fake.llm
    with patch("app.services.mpzp_parser_options.build_mpzp_llm_pipeline", side_effect=ValueError("x")):
        broken = build_mpzp_parser_options(Settings(_env_file=None, mpzp_parser_mode="hybrid", mpzp_llm_enabled=True))  # type: ignore[call-arg]
    assert broken.llm_unavailable_reason == "configuration"


def test_the_cache_signature_contains_mode_parser_version_prompt_version_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = analysis_cache.mpzp_parser_signature(Settings(_env_file=None))  # type: ignore[call-arg]
    assert legacy == {"mode": "legacy", "parser_version": "mpzp-parser/3.1-det", "prompt_version": None,
                      "schema_version": None, "model_id": None, "llm_enabled": None}
    v3 = analysis_cache.mpzp_parser_signature(Settings(_env_file=None, mpzp_parser_mode="v3"))  # type: ignore[call-arg]
    hybrid_signature = analysis_cache.mpzp_parser_signature(
        Settings(_env_file=None, mpzp_parser_mode="hybrid", mpzp_llm_enabled=True))  # type: ignore[call-arg]
    other_model = analysis_cache.mpzp_parser_signature(
        Settings(_env_file=None, mpzp_parser_mode="hybrid", mpzp_llm_enabled=True, mpzp_llm_model="gemini-3.7-flash"))  # type: ignore[call-arg]
    shadow = analysis_cache.mpzp_parser_signature(Settings(_env_file=None, mpzp_parser_mode="hybrid_shadow"))  # type: ignore[call-arg]
    assert v3["parser_version"] == "mpzp-parser/3.1-det+scope.1" and v3["model_id"] is None
    assert hybrid_signature["prompt_version"] == contract.PROMPT_VERSION and hybrid_signature["model_id"] == MODEL
    assert len({json.dumps(item, sort_keys=True) for item in (legacy, v3, hybrid_signature, other_model, shadow)}) == 5
    monkeypatch.setattr(analysis_cache, "PROMPT_VERSION", "mpzp-extraction/2")
    bumped = analysis_cache.mpzp_parser_signature(Settings(_env_file=None, mpzp_parser_mode="hybrid", mpzp_llm_enabled=True))  # type: ignore[call-arg]
    assert bumped != hybrid_signature


def test_the_default_mode_is_documented_and_passed_by_compose_as_legacy() -> None:
    import yaml

    from tests.parcel_fixtures_config import find_repo_root

    root = find_repo_root()
    lines = (root / ".env.example").read_text("utf-8").splitlines()
    assert "MPZP_PARSER_MODE=legacy" in lines
    compose = yaml.safe_load((root / "docker-compose.yml").read_text("utf-8"))
    assert compose["services"]["backend"]["environment"]["MPZP_PARSER_MODE"] == "${MPZP_PARSER_MODE:-legacy}"


async def test_one_budget_is_shared_by_all_documents_of_an_analysis(tmp_path: Path) -> None:
    from app.modules.planning.application.llm_pipeline import BudgetTracker

    shared = BudgetTracker(LlmBudget(max_requests=1))
    first, second = ScriptedProvider(), ScriptedProvider()
    await parse("hybrid", pipeline(first, tracker=shared))
    result = await parse("hybrid", pipeline(second, tracker=shared))  # drugi dokument tej samej analizy
    assert (first.calls, second.calls) == (1, 0)
    assert "budget_exhausted" in next(w.message for w in result.warnings if w.code == "MPZP_LLM_UNAVAILABLE")
    options = build_mpzp_parser_options(
        Settings(_env_file=None, mpzp_parser_mode="hybrid", mpzp_llm_enabled=True, mpzp_llm_provider="fake",  # type: ignore[call-arg]
                 mpzp_llm_replay_dir=str(tmp_path), mpzp_llm_cache_enabled=False),
        budget=shared,
    )
    assert options.llm is not None and options.llm.tracker is shared
