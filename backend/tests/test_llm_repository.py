"""Cache i provenance wywołań modelu (PV3-13): klucz, idempotencja, retencja, brak treści żądania.

Testy potoku używają cache'u w pamięci zgodnego z portem; testy ``integration`` — prawdziwego
repozytorium na PostGIS (tabela z migracji 029). Model to dostawca skryptowy: bez sieci i klucza.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import delete, inspect, select, text

from app.modules.planning import __main__ as planning_cli
from app.modules.planning.application import llm_pipeline as pipeline_module
from app.modules.planning.application.llm_extraction import ExtractionLimits
from app.modules.planning.application.llm_pipeline import (
    LlmBudget,
    LlmPricing,
    MpzpLlmPipeline,
    block_cache_key,
    extraction_params_hash,
)
from app.modules.planning.application.ports import (
    LlmExtractionRecord,
    StructuredExtractionError,
    StructuredExtractionErrorCode,
)
from app.modules.planning.domain import extraction_contract as contract
from app.modules.planning.domain.zone_blocks import ZoneBlock
from tests.test_mpzp_parser_modes import GOOD_RESPONSE, MODEL, TEXT, ScriptedProvider

BLOCK = ZoneBlock(
    block_id="zb-cache",
    scope_kind="zone_section",
    symbols=("1MN",),
    text=TEXT,
    char_span=(0, len(TEXT)),
    pages=(1,),
    path="§ 5",
    scope_confidence=0.9,
)
DOCUMENT_SHA = "d" * 64


class MemoryCache:
    """Cache zgodny z portem ``LlmExtractionCache`` (te same reguły co repozytorium)."""

    def __init__(self, *, retention_days: int = 180, now: datetime | None = None) -> None:
        self.rows: dict[str, LlmExtractionRecord] = {}
        self.retention = timedelta(days=retention_days)
        self.now = now or datetime(2026, 10, 5, tzinfo=timezone.utc)
        self.gets = 0
        self.fail_reads = False
        self.fail_writes = False

    def get(self, cache_key: str) -> LlmExtractionRecord | None:
        self.gets += 1
        if self.fail_reads:
            raise RuntimeError("baza niedostępna")
        row = self.rows.get(cache_key)
        if row is None or row.created_at is None or row.created_at < self.now - self.retention:
            return None
        return row

    def save(self, record: LlmExtractionRecord) -> LlmExtractionRecord:
        if self.fail_writes:
            raise RuntimeError("baza niedostępna")
        existing = self.rows.get(record.cache_key)
        fresh = existing is not None and existing.created_at is not None and existing.created_at >= self.now - self.retention
        if existing is not None and existing.status == "ok" and fresh:
            return existing
        self.rows[record.cache_key] = replace(record, created_at=self.now)
        return self.rows[record.cache_key]

    def purge(self, *, older_than: datetime) -> int:
        stale = [key for key, row in self.rows.items() if row.created_at is not None and row.created_at < older_than]
        for key in stale:
            del self.rows[key]
        return len(stale)


async def run(provider: Any, cache: MemoryCache | None, **kwargs: Any):
    return await MpzpLlmPipeline(provider, cache=cache, **kwargs).run([BLOCK], document_sha256=DOCUMENT_SHA)


# --- klucz -------------------------------------------------------------------------------------------------


def test_the_cache_key_covers_document_block_prompt_schema_model_and_parameters() -> None:
    params = extraction_params_hash(provider="gemini", block=BLOCK, limits=ExtractionLimits())
    base = dict(document_sha256=DOCUMENT_SHA, block_sha256=BLOCK.sha256, prompt_version="p/1",
                schema_version="s/1", model_id=MODEL, params_hash=params)
    key = block_cache_key(**base)
    assert len(key) == 64 and key == block_cache_key(**base)
    for field, value in (("document_sha256", "e" * 64), ("block_sha256", "f" * 64), ("prompt_version", "p/2"),
                         ("schema_version", "s/2"), ("model_id", "gemini-3.7-flash"), ("params_hash", "0" * 64)):
        assert block_cache_key(**{**base, field: value}) != key, field
    assert extraction_params_hash(provider="gemini", block=BLOCK, limits=ExtractionLimits(block_char_limit=5000)) != params
    assert extraction_params_hash(provider="fake", block=BLOCK, limits=ExtractionLimits()) != params
    assert extraction_params_hash(provider="gemini", block=BLOCK, limits=ExtractionLimits(), extra={"thinking_level": "high"}) != params
    other_symbols = replace(BLOCK, symbols=("1MN", "2MN"))
    assert extraction_params_hash(provider="gemini", block=other_symbols, limits=ExtractionLimits()) != params


# --- trafienie bez sieci, unieważnienie ----------------------------------------------------------------------


async def test_the_same_key_is_a_hit_without_calling_the_model() -> None:
    cache = MemoryCache()
    first_provider = ScriptedProvider()
    first = await run(first_provider, cache)
    assert first_provider.calls == 1 and first.cache_hits == 0 and len(cache.rows) == 1
    spy = ScriptedProvider(error=AssertionError("trafienie cache nie może wołać modelu"))
    second = await run(spy, cache)
    assert spy.calls == 0 and second.calls == 0 and second.cache_hits == 1 and second.available
    assert [(a.parameter, a.value, a.evidence_span) for a in second.report.accepted] == [
        (a.parameter, a.value, a.evidence_span) for a in first.report.accepted]
    record = next(iter(cache.rows.values()))
    assert {a.provenance.response_sha256 for a in second.report.accepted} == {record.response_sha256}
    assert second.blocks[0].source == "cache" and first.blocks[0].source == "model"


@pytest.mark.parametrize("change", ["model", "prompt", "schema", "limits"])
async def test_a_change_of_model_prompt_schema_or_parameters_invalidates_the_cache(
    change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = MemoryCache()
    await run(ScriptedProvider(), cache)
    provider = ScriptedProvider()
    kwargs: dict[str, Any] = {}
    if change == "model":
        provider.model = "gemini-3.7-flash"
        provider.content = {"candidates": [], "not_found": []}
    elif change == "prompt":
        monkeypatch.setattr(contract, "PROMPT_VERSION", "mpzp-extraction/2")
    elif change == "schema":
        monkeypatch.setattr(contract, "SCHEMA_VERSION", "mpzp-extraction-schema/2")
    else:
        kwargs["limits"] = ExtractionLimits(block_char_limit=5000)
    outcome = await run(provider, cache, **kwargs)
    assert provider.calls == 1 and outcome.cache_hits == 0 and len(cache.rows) == 2


async def test_the_record_holds_only_model_output_and_hashes_never_the_request() -> None:
    cache = MemoryCache()
    provider = ScriptedProvider()
    await run(provider, cache, pricing=LlmPricing(input_usd_per_mtok=1.5, output_usd_per_mtok=7.5))
    record = next(iter(cache.rows.values()))
    stored = json.dumps(record.response, ensure_ascii=False)
    request = provider.requests[0]
    assert request.user_text not in stored and request.system_instruction[:200] not in stored
    assert "Zone symbols:" not in stored and "BEGIN DOCUMENT TEXT" not in stored
    assert record.response == {"chunks": [{
        "index": 0, "request_sha256": request.request_sha256, "input_sha256": request.input_sha256,
        "response_sha256": contract.sha256_text(contract.canonical_json(GOOD_RESPONSE)), "model_returned": MODEL,
        "finish_reason": "STOP", "content": GOOD_RESPONSE,
    }]}
    assert record.response_sha256 == contract.sha256_text(contract.canonical_json(record.response))
    assert (record.status, record.input_tokens, record.output_tokens) == ("ok", 900, 300)
    assert record.cost_estimate_usd == pytest.approx((900 * 1.5 + 300 * 7.5) / 1e6)
    assert (record.document_sha256, record.block_sha256, record.model_id) == (DOCUMENT_SHA, BLOCK.sha256, MODEL)
    assert (record.prompt_version, record.schema_version) == (contract.PROMPT_VERSION, contract.SCHEMA_VERSION)


async def test_errors_and_schema_rejections_are_recorded_but_never_served_and_a_later_success_replaces_them() -> None:
    cache = MemoryCache()
    timeout = await run(ScriptedProvider(error=StructuredExtractionError(StructuredExtractionErrorCode.TIMEOUT)), cache)
    record = next(iter(cache.rows.values()))
    assert (record.status, record.error_code, record.response) == ("error", "timeout", None)
    assert timeout.unavailable_reasons == ("timeout",)
    rejected = await run(ScriptedProvider({"candidates": "zły", "not_found": []}), cache)
    record = next(iter(cache.rows.values()))
    assert (record.status, record.error_code) == ("rejected_schema", "schema_violation") and not rejected.available
    provider = ScriptedProvider()
    ok = await run(provider, cache)
    assert provider.calls == 1 and ok.available and next(iter(cache.rows.values())).status == "ok"
    again = ScriptedProvider(content={"candidates": [], "not_found": []})
    await run(again, cache)
    assert again.calls == 0 and next(iter(cache.rows.values())).response["chunks"][0]["content"] == GOOD_RESPONSE


async def test_expired_records_are_not_hits() -> None:
    cache = MemoryCache(retention_days=30)
    await run(ScriptedProvider(), cache)
    cache.now += timedelta(days=31)
    provider = ScriptedProvider()
    await run(provider, cache)
    assert provider.calls == 1  # zapis poza retencją nie jest trafieniem…
    assert next(iter(cache.rows.values())).created_at == cache.now  # …i nowsze wywołanie go odświeża
    spy = ScriptedProvider(error=AssertionError("odświeżony zapis jest trafieniem"))
    await run(spy, cache)
    assert spy.calls == 0
    cache.now += timedelta(days=400)
    assert cache.purge(older_than=cache.now - timedelta(days=30)) == 1


async def test_a_tampered_record_is_not_trusted_and_the_model_is_called_again() -> None:
    cache = MemoryCache()
    await run(ScriptedProvider(), cache)
    key, record = next(iter(cache.rows.items()))
    chunk = dict(record.response["chunks"][0])
    chunk["content"] = {"candidates": [], "not_found": []}  # skrót już nie pasuje
    cache.rows[key] = replace(record, response={"chunks": [chunk]})
    provider = ScriptedProvider()
    outcome = await run(provider, cache)
    assert provider.calls == 1 and outcome.cache_hits == 0


async def test_cache_failures_do_not_block_the_extraction_but_are_reported() -> None:
    cache = MemoryCache()
    cache.fail_reads = cache.fail_writes = True
    provider = ScriptedProvider()
    outcome = await run(provider, cache)
    assert provider.calls == 1 and sorted(a.parameter for a in outcome.report.accepted) == ["max_building_height_m", "max_storeys"]
    assert outcome.unavailable_reasons == ("cache_error",)


async def test_without_a_cache_every_analysis_calls_the_model_and_budget_is_per_analysis() -> None:
    provider = ScriptedProvider()
    await run(provider, None)
    await run(provider, None)
    assert provider.calls == 2
    blocked = ScriptedProvider()
    outcome = await run(blocked, MemoryCache(), budget=LlmBudget(max_requests=0))
    assert blocked.calls == 0 and outcome.unavailable_reasons == ("budget_exhausted",)
    with pytest.raises(ValueError):
        LlmBudget(max_requests=-1)
    assert LlmPricing().estimate(None, None) is None


async def test_a_cache_hit_does_not_consume_the_budget() -> None:
    cache = MemoryCache()
    await run(ScriptedProvider(), cache)
    outcome = await run(ScriptedProvider(), cache, budget=LlmBudget(max_requests=0, max_input_tokens=0))
    assert outcome.cache_hits == 1 and outcome.available


def test_records_validate_their_status() -> None:
    with pytest.raises(ValueError):
        LlmExtractionRecord(cache_key="k", document_sha256="d", block_sha256="b", prompt_version="p",
                            schema_version="s", model_id="m", params_hash="h", status="verified")


def test_the_purge_command_reports_the_number_of_removed_records(capsys: pytest.CaptureFixture[str]) -> None:
    assert planning_cli.main(["purge-llm-cache"], purge=lambda: 3) == 0
    assert "usunięto 3" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        planning_cli.main([])


def test_the_pipeline_module_has_a_versioned_cache_key() -> None:
    assert pipeline_module.CACHE_KEY_VERSION == "mpzp-llm-cache/1"


# --- repozytorium na PostGIS (migracja 029) ------------------------------------------------------------------


def _record(**overrides: Any) -> LlmExtractionRecord:
    data: dict[str, Any] = dict(
        cache_key=uuid4().hex + uuid4().hex, document_sha256="a" * 64, block_sha256="b" * 64,
        prompt_version=contract.PROMPT_VERSION, schema_version=contract.SCHEMA_VERSION, model_id=MODEL,
        params_hash="c" * 64, status="ok", response={"chunks": [{"content": GOOD_RESPONSE}]},
        response_sha256="e" * 64, input_tokens=900, output_tokens=300, latency_ms=812.5, cost_estimate_usd=0.0036,
    )
    data.update(overrides)
    return LlmExtractionRecord(**data)


@pytest.fixture
def repository():
    from app.db.session import SessionLocal
    from app.models.mpzp_llm_extraction import MpzpLlmExtraction
    from app.modules.planning.infrastructure.llm.repository import SqlAlchemyLlmExtractionCache

    clock = {"now": datetime.now(timezone.utc)}
    repo = SqlAlchemyLlmExtractionCache(SessionLocal, retention_days=30, clock=lambda: clock["now"])
    keys: list[str] = []
    yield repo, clock, keys
    with SessionLocal() as session:
        session.execute(delete(MpzpLlmExtraction).where(MpzpLlmExtraction.cache_key.in_(keys)))
        session.commit()


@pytest.mark.integration
def test_the_repository_writes_idempotently_and_never_overwrites_an_ok_record(repository) -> None:  # noqa: ANN001
    repo, _clock, keys = repository
    record = _record()
    keys.append(record.cache_key)
    saved = repo.save(record)
    assert saved.created_at is not None and repo.get(record.cache_key) == saved
    assert repo.save(replace(record, response_sha256="f" * 64, status="error")) == saved  # ``ok`` zostaje
    failing = _record(status="error", response=None, response_sha256=None, error_code="timeout")
    keys.append(failing.cache_key)
    repo.save(failing)
    replaced = repo.save(replace(failing, status="ok", response={"chunks": []}, response_sha256="9" * 64, error_code=None))
    assert (replaced.status, replaced.response_sha256) == ("ok", "9" * 64)
    assert repo.get("0" * 64) is None
    with pytest.raises(ValueError):
        type(repo)(lambda: None, retention_days=0)  # type: ignore[arg-type, return-value]
    assert repo.retention_days == 30


@pytest.mark.integration
def test_the_repository_applies_retention_and_purges_expired_records(repository) -> None:  # noqa: ANN001
    repo, clock, keys = repository
    old = _record()
    keys.append(old.cache_key)
    clock["now"] = datetime.now(timezone.utc) - timedelta(days=45)
    repo.save(old)
    clock["now"] = datetime.now(timezone.utc)
    fresh = _record()
    keys.append(fresh.cache_key)
    repo.save(fresh)
    assert repo.get(old.cache_key) is None and repo.get(fresh.cache_key) is not None
    stale = _record()
    keys.append(stale.cache_key)
    clock["now"] = datetime.now(timezone.utc) - timedelta(days=60)
    repo.save(stale)
    clock["now"] = datetime.now(timezone.utc)
    refreshed = repo.save(replace(stale, response_sha256="7" * 64))  # ``ok`` poza retencją jest odświeżany
    assert refreshed.response_sha256 == "7" * 64 and repo.get(stale.cache_key) is not None
    keys.remove(stale.cache_key)
    from app.db.session import SessionLocal as _Session
    from app.models.mpzp_llm_extraction import MpzpLlmExtraction as _Row

    with _Session() as session:
        session.execute(delete(_Row).where(_Row.cache_key == stale.cache_key))
        session.commit()
    assert repo.purge_expired() >= 1
    from app.db.session import SessionLocal
    from app.models.mpzp_llm_extraction import MpzpLlmExtraction

    with SessionLocal() as session:
        remaining = set(session.scalars(select(MpzpLlmExtraction.cache_key).where(MpzpLlmExtraction.cache_key.in_(keys))))
    assert remaining == {fresh.cache_key}


@pytest.mark.integration
def test_the_table_has_no_column_for_request_content_or_user_data() -> None:
    from app.db.session import engine

    columns = {column["name"] for column in inspect(engine).get_columns("mpzp_llm_extractions")}
    assert columns == {
        "id", "cache_key", "document_sha256", "block_sha256", "prompt_version", "schema_version", "model_id",
        "params_hash", "response_sha256", "response", "input_tokens", "output_tokens", "latency_ms",
        "cost_estimate_usd", "status", "error_code", "created_at",
    }
    forbidden = ("parcel", "analysis", "user", "request", "prompt_text", "user_text", "address", "geometry")
    assert not any(fragment in column for column in columns for fragment in forbidden)


@pytest.mark.integration
async def test_a_new_analysis_with_the_same_key_uses_the_database_cache_without_calling_the_model(repository) -> None:  # noqa: ANN001
    repo, _clock, keys = repository
    document_sha = uuid4().hex + uuid4().hex
    first = ScriptedProvider()
    outcome = await MpzpLlmPipeline(first, cache=repo).run([BLOCK], document_sha256=document_sha)
    keys.extend(run.cache_key for run in outcome.blocks)
    spy = ScriptedProvider(error=AssertionError("trafienie cache nie woła modelu"))
    again = await MpzpLlmPipeline(spy, cache=repo).run([BLOCK], document_sha256=document_sha)
    assert first.calls == 1 and spy.calls == 0 and again.cache_hits == 1
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        row = session.execute(
            text("SELECT response::text, status FROM mpzp_llm_extractions WHERE cache_key = :k"), {"k": keys[0]}
        ).one()
    assert row.status == "ok" and "Dla terenu 1MN ustala się:\n1)" not in row.response
    assert first.requests[0].user_text not in row.response


@pytest.mark.integration
def test_reading_a_saved_analysis_equals_the_snapshot_and_never_calls_the_adapter() -> None:
    from app.db.session import SessionLocal
    from app.models.analysis import Analysis
    from app.models.mpzp_parameter import MpzpParameter as ParameterRow
    from app.models.parcel import Parcel
    from app.schemas import analyze as analyze_schemas
    from app.schemas.source import SourceMetadata
    from app.services.mpzp_zones import unassigned_share_zone
    from app.services.persistence import add_mpzp_zone_snapshot, build_analyze_response_from_analysis

    ai = analyze_schemas.MpzpParameterEvidence(
        name="max_storeys", normalized_value=3.0, raw_value="3 kondygnacje", unit=None,
        evidence_text="zabudowa nie wyższa aniżeli 3 kondygnacje nadziemne", page_number=1, confidence=0.6,
        extraction_method="llm_verified", manual_review_required=True, review_status="ai_candidate",
        model_id=MODEL, prompt_version=contract.PROMPT_VERSION, response_sha256="e" * 64,
    )
    deterministic = analyze_schemas.MpzpParameterEvidence(
        name="max_building_height_m", normalized_value=12.0, raw_value="12 m", unit="m",
        evidence_text="maksymalna wysokość zabudowy: 12 m", page_number=1, confidence=0.9, extraction_method="pdf_text",
    )
    zone = unassigned_share_zone("1MN", SourceMetadata(source_name="MPZP_BIP", source_url=None, confidence=0.9,
                                                       manual_review_required=False)).model_copy(
        update={"parameters": [deterministic, ai], "manual_review_required": True, "max_building_height_m": 12.0})

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("odczyt snapshotu nie może tworzyć ani wołać adaptera modelu")

    with SessionLocal() as db:
        parcel = Parcel(parcel_identifier=f"LLM13_{uuid4().hex[:8]}",
                        geometry="SRID=2180;MULTIPOLYGON(((0 0,10 0,10 10,0 10,0 0)))")
        db.add(parcel)
        db.flush()
        analysis = Analysis(parcel_id=parcel.id, status="partial")
        db.add(analysis)
        db.flush()
        record = add_mpzp_zone_snapshot(db, analysis.id, zone)
        db.flush()
        db.expire_all()  # odczyt z bazy, nie z obiektów tej sesji
        rows = db.scalars(select(ParameterRow).where(ParameterRow.mpzp_zone_id == record.id).order_by(ParameterRow.id)).all()
        assert [(r.extraction_method, r.review_status, r.model_id, r.prompt_version, r.response_sha256) for r in rows] == [
            ("pdf_text", None, None, None, None),
            ("llm_verified", "ai_candidate", MODEL, contract.PROMPT_VERSION, "e" * 64),
        ]
        with (
            patch("app.modules.planning.composition.build_structured_extraction_provider", side_effect=refuse),
            patch("app.modules.planning.infrastructure.llm.gemini_provider.GeminiStructuredExtractionProvider.extract_structured",
                  side_effect=refuse),
            patch("app.services.mpzp_parser_options.build_mpzp_llm_pipeline", side_effect=refuse),
        ):
            response = build_analyze_response_from_analysis(analysis, db)
        restored = response.mpzp_zones[0]
        assert restored.model_dump(mode="json") == zone.model_dump(mode="json")
        assert restored.parameters[1].review_status == "ai_candidate" and restored.max_building_height_m == 12.0
        assert "model_id" not in restored.parameters[0].model_dump(mode="json")
        db.rollback()


@pytest.mark.integration
def test_the_database_rejects_a_model_value_marked_verified() -> None:
    from sqlalchemy.exc import IntegrityError

    from app.db.session import SessionLocal

    with SessionLocal() as db:
        parcel_id = db.execute(text(
            "INSERT INTO parcels (parcel_identifier, geometry) VALUES (:p, ST_Multi(ST_GeomFromText("
            "'POLYGON((0 0,1 0,1 1,0 1,0 0))',2180))) RETURNING id"), {"p": f"LLM13V_{uuid4().hex[:8]}"}).scalar_one()
        analysis_id = db.execute(text("INSERT INTO analyses (parcel_id, status) VALUES (:p,'partial') RETURNING id"),
                                 {"p": parcel_id}).scalar_one()
        zone_id = db.execute(text(
            "INSERT INTO mpzp_zones (analysis_id, zone_symbol, is_dominant) VALUES (:a,'1MN',false) RETURNING id"),
            {"a": analysis_id}).scalar_one()
        with pytest.raises(IntegrityError):
            db.execute(text(
                "INSERT INTO mpzp_parameters (mpzp_zone_id, parameter_name, manual_review_required, extraction_method, "
                "review_status) VALUES (:z, 'max_storeys', true, 'llm_verified', 'verified')"), {"z": zone_id})
        db.rollback()
