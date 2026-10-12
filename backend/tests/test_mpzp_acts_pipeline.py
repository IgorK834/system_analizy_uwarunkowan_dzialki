"""Kanał ``mpzp_ru`` w pełnym przebiegu analizy: odpowiedź API, snapshot w bazie, macierz jakości (AU-101, AU-103).

KIMPZP i RU to rzeczywiste, zamrożone odpowiedzi; geometrie działek — rzeczywiste wielokąty ULDK z manifestu RU.
Pozostałe źródła (kontekst, KIUT, POG) są zaślepione jak w ``test_analysis_orchestrator``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from sqlalchemy import delete, select

from app.core.settings import settings
from app.db.session import SessionLocal
from app.models.analysis import Analysis
from app.models.infrastructure import Infrastructure
from app.models.mpzp_parameter import MpzpParameter
from app.models.mpzp_zone import MpzpZone
from app.models.parcel import Parcel
from app.models.pog_data import PogData
from app.models.risk import Risk
from app.models.source_record import SourceRecord
from app.modules.planning.application.ports import RuActsResponse
from app.schemas.analyze import ParcelIdAnalyzeRequest, UtilitiesPreviewResult
from app.services import analysis_orchestrator as orchestrator
from app.services import mpzp_acts as service
from app.services.analysis_orchestrator import run_analysis
from app.services.cache import RESULT_CONTRACT_VERSION
from app.services.persistence import build_analyze_response_from_analysis
from app.services.uldk import ParcelLookupResult
from tests.ru_mpzp_fixtures import (
    BIELSKO,
    DYGOWO_EMPTY,
    GORA_KALWARIA,
    WARSZAWA_EMPTY,
    adapter,
    manifest,
    mount_case,
)
from tests.test_analysis_orchestrator import _empty_context, _source

pytestmark = pytest.mark.integration

_PREFIX = "ACTS_PIPELINE_TEST_"
_KIMPZP_FIXTURES = Path(__file__).resolve().parent / "fixtures/source_contracts/kimpzp"


def _cleanup() -> None:
    with SessionLocal() as db:
        parcel_ids = select(Parcel.id).where(Parcel.parcel_identifier.like(f"{_PREFIX}%"))
        analysis_ids = select(Analysis.id).where(Analysis.parcel_id.in_(parcel_ids))
        zone_ids = select(MpzpZone.id).where(MpzpZone.analysis_id.in_(analysis_ids))
        db.execute(delete(MpzpParameter).where(MpzpParameter.mpzp_zone_id.in_(zone_ids)))
        for model in (SourceRecord, Risk, Infrastructure, PogData, MpzpZone):
            db.execute(delete(model).where(model.analysis_id.in_(analysis_ids)))
        db.execute(delete(Analysis).where(Analysis.id.in_(analysis_ids)))
        db.execute(delete(Parcel).where(Parcel.id.in_(parcel_ids)))
        db.commit()


@pytest.fixture(autouse=True)
def _rows():
    _cleanup()
    yield
    _cleanup()


@pytest.fixture(autouse=True)
def _kiut(monkeypatch: pytest.MonkeyPatch) -> None:
    coverage = AsyncMock(
        return_value=UtilitiesPreviewResult(
            coverage_status="covered",
            county_name="powiat testowy",
            layer_available=True,
            note="Podgląd.",
            source=_source("KIUT (GUGiK)", "https://kiut.example.test/wms"),
        )
    )
    monkeypatch.setattr("app.services.analysis_orchestrator.check_kiut_coverage_for_geometry", coverage)


@pytest.fixture(autouse=True)
def _pog(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_analysis_orchestrator import _unknown_pog_discovery

    monkeypatch.setattr(
        "app.services.analysis_orchestrator.discover_pog",
        AsyncMock(return_value=_unknown_pog_discovery()),
    )


async def _analyze(
    identifier: str,
    slug: str,
    kimpzp_html: str,
    *,
    ru_error: bool = False,
):
    wkt = manifest()["cases"][slug]["parcel_wkt_epsg2180"]
    lookup = ParcelLookupResult(
        parcel_identifier=identifier,
        wkt=wkt,
        teryt="146101",
        source_metadata=_source("ULDK", "https://uldk.example.test"),
    )

    async def ru_channel(parcel_geometry, **_kwargs):  # noqa: ANN001, ANN202
        if ru_error:
            failing = RuActsResponse(acts=(), complete=False, error_code="MPZP_RU_TRANSPORT_ERROR")

            class Failing:
                def fetch_acts(self, bbox):  # noqa: ANN001, ANN201
                    return failing

                def fetch_related(self, *args):  # noqa: ANN002, ANN201
                    raise AssertionError

            return await service.fetch_ru_channel(parcel_geometry, source=Failing(), as_of=date(2026, 10, 10))
        return await service.fetch_ru_channel(parcel_geometry, source=adapter(), as_of=date(2026, 10, 10))

    fetch = AsyncMock(side_effect=AssertionError("dokument nie może być pobrany"))
    with (
        respx.mock(assert_all_called=False) as router,
        patch("app.services.analysis_orchestrator.resolve_parcel", new=AsyncMock(return_value=lookup)),
        patch("app.services.analysis_orchestrator.analyze_context", new=AsyncMock(return_value=_empty_context())),
        patch("app.services.analysis_orchestrator.fetch_mpzp_document", new=fetch),
        patch("app.services.analysis_orchestrator.fetch_ru_channel", new=ru_channel),
        SessionLocal() as db,
    ):
        mount_case(router, slug)
        router.get(settings.kimpzp_wms_base_url).mock(
            return_value=httpx.Response(200, text=(_KIMPZP_FIXTURES / kimpzp_html).read_text(encoding="utf-8"))
        )
        response = await run_analysis(
            ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier=identifier), db
        )
    return response, fetch


def _quality(response, section: str):  # noqa: ANN001, ANN202
    assert response.section_quality is not None
    return next(item for item in response.section_quality.sections if item.section == section)


@pytest.mark.asyncio
async def test_audit_parcel_gora_kalwaria_names_iv_30_2024_and_describes_the_rejected_part_ii() -> None:
    response, fetch = await _analyze(
        f"{_PREFIX}GORA_KALWARIA", GORA_KALWARIA, "gora_kalwaria_141801_4.0701.23_8.html"
    )

    assert [act.resolution_number for act in response.mpzp_acts] == ["IV/30/2024", "576/XLVII/2010"]
    first = response.mpzp_acts[0]
    assert first.sources == ["kimpzp"] and first.legal_status == "binding"
    assert first.document_url == "http://mpzp.gorakalwaria.pl/portal/mpzp/uch/IV_30_2024.pdf"
    assert first.intersection_area_sqm is None and first.intersection_pct is None  # null ≠ 0
    assert first.precedence == "unresolved" and first.manual_review_required is True
    state = response.mpzp_status
    assert state is not None
    assert (state.status, state.reason_code) == ("ambiguous", "MPZP_ACTS_UNRESOLVED")
    assert state.label == "Wiele planów — brak rozstrzygnięcia"
    assert state.precedence_rule_version == "mpzp-act-precedence/1.0"
    (rejected,) = state.rejected_acts
    assert rejected.reason == "no_overlap" and "cz. II" in (rejected.title or "")
    assert rejected.distance_m == pytest.approx(5.994, abs=0.01)
    codes = [warning.code for warning in response.warnings]
    assert "MPZP_MULTIPLE_ACTS_AT_POINT" in codes and "MPZP_CHANNEL_MISMATCH" not in codes
    assert response.mpzp_zones == [] and response.status == "partial"
    fetch.assert_not_called()  # bez cichego wyboru dokumentu jednego z aktów
    quality = _quality(response, "mpzp")
    assert quality.status == "partial" and quality.source_id == "kimpzp"
    assert quality.reason_codes[:2] == ["MPZP_ACT_WITHOUT_ZONE", "MPZP_MULTIPLE_ACTS_AT_POINT"]
    assert "MPZP_ACTS_UNRESOLVED" in quality.reason_codes

    with SessionLocal() as db:
        analysis = db.get(Analysis, response.analysis_id)
        assert analysis.mpzp_acts[0]["resolution_number"] == "IV/30/2024"
        assert analysis.mpzp_status["status"] == "ambiguous"
        assert f"mpzp-v{orchestrator_version()}" in (analysis.result_contract_version or "")
        restored = build_analyze_response_from_analysis(analysis, db)
    assert restored.mpzp_acts == response.mpzp_acts and restored.mpzp_status == response.mpzp_status


def orchestrator_version() -> str:
    from app.schemas.analyze import MPZP_RESULT_SCHEMA_VERSION

    assert f"mpzp-v{MPZP_RESULT_SCHEMA_VERSION}" in RESULT_CONTRACT_VERSION
    return MPZP_RESULT_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_ru_act_is_reported_when_kimpzp_has_a_service_error() -> None:
    """Gmina, w której KIMPZP zawodzi, a RU wskazuje obowiązujący plan (rzeczywiste Bielsko-Biała)."""
    response, _ = await _analyze(
        f"{_PREFIX}BIELSKO", BIELSKO, "bielsko_biala_246101_1.0056.155_3.html"
    )

    (act,) = response.mpzp_acts
    assert act.resolution_number == "LIII/749/1998" and act.sources == ["mpzp_ru"]
    assert act.intersection_pct == pytest.approx(11.84, abs=0.02)
    assert act.intersection_area_sqm == pytest.approx(167.44, abs=0.05)
    assert act.precedence == "applies" and act.legal_status == "binding"
    assert act.intersection_geojson is not None and act.boundary_geojson is not None
    state = response.mpzp_status
    assert state is not None and (state.status, state.reason_code) == ("available", "MPZP_ACT_FOUND")
    assert state.source is not None and state.source.source_id == "mpzp_ru"
    assert {c.channel: c.status for c in state.channels} == {"kimpzp": "unavailable", "mpzp_ru": "available"}
    assert any(source.source_id == "mpzp_ru" for source in response.sources)
    warning = next(w for w in response.warnings if w.code == "MPZP_DISCOVERY_UNAVAILABLE")
    assert warning.severity == "error"  # błąd KIMPZP nie znika, gdy RU odpowiada
    assert "MPZP_NOT_FOUND" not in [w.code for w in response.warnings]
    quality = _quality(response, "mpzp")
    assert quality.status == "partial" and quality.source_id == "mpzp_ru"  # nie „brak źródła”
    assert response.mpzp_zones == []  # akt RU nie jest strefą


@pytest.mark.asyncio
async def test_no_coverage_state_reaches_the_response_and_the_quality_matrix() -> None:
    response, _ = await _analyze(f"{_PREFIX}NO_SERVICE", DYGOWO_EMPTY, "dygowo_321606_2.0029.362.html")
    state = response.mpzp_status
    assert state is not None and (state.status, state.reason_code) == ("no_coverage", "KIMPZP_NO_SERVICE_FOR_AREA")
    assert response.mpzp_acts == []
    quality = _quality(response, "mpzp")
    assert quality.status == "no_coverage" and quality.source_id == "kimpzp"


@pytest.mark.asyncio
async def test_kimpzp_error_with_empty_ru_is_unavailable_never_no_match() -> None:
    response, _ = await _analyze(f"{_PREFIX}SERVICE_ERROR", WARSZAWA_EMPTY, "warszawa_146510_8.0502.1_3.html")
    state = response.mpzp_status
    assert state is not None and state.status == "unavailable"
    quality = _quality(response, "mpzp")
    assert quality.status == "unavailable" and quality.source_id == "kimpzp"
    assert "MPZP_SOURCE_UNAVAILABLE" in quality.reason_codes


@pytest.mark.asyncio
async def test_ru_failure_does_not_fail_the_analysis_and_is_visible() -> None:
    response, _ = await _analyze(
        f"{_PREFIX}RU_DOWN", GORA_KALWARIA, "gora_kalwaria_141801_4.0701.23_8.html", ru_error=True
    )
    assert response.status == "partial"
    state = response.mpzp_status
    assert state is not None
    assert {c.channel: c.status for c in state.channels}["mpzp_ru"] == "unavailable"
    assert "MPZP_RU_UNAVAILABLE" in state.channels[1].reason_codes
    assert [act.resolution_number for act in response.mpzp_acts] == ["IV/30/2024", "576/XLVII/2010"]


@pytest.mark.asyncio
async def test_cache_hit_returns_acts_and_status_without_new_requests() -> None:
    identifier = f"{_PREFIX}CACHED"
    first, _ = await _analyze(identifier, BIELSKO, "bielsko_biala_246101_1.0056.155_3.html")
    lookup = ParcelLookupResult(
        parcel_identifier=identifier,
        wkt=manifest()["cases"][BIELSKO]["parcel_wkt_epsg2180"],
        teryt="146101",
        source_metadata=_source("ULDK", "https://uldk.example.test"),
    )
    with (
        SessionLocal() as db,
        patch("app.services.analysis_orchestrator.resolve_parcel", new=AsyncMock(return_value=lookup)),
        patch("app.services.analysis_orchestrator.fetch_ru_channel", new=AsyncMock(side_effect=AssertionError)),
        patch("app.services.analysis_orchestrator.discover_mpzp", new=AsyncMock(side_effect=AssertionError)),
    ):
        second = await run_analysis(ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier=identifier), db)
    assert second.analysis_id == first.analysis_id
    assert second.mpzp_acts == first.mpzp_acts and second.mpzp_status == first.mpzp_status


# --- odporność na błędy wewnętrzne -------------------------------------------------------------------------------


def test_resolution_error_degrades_to_unknown_instead_of_a_500(monkeypatch: pytest.MonkeyPatch) -> None:
    from shapely import box

    def boom(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("boom")

    monkeypatch.setattr(orchestrator, "resolve_mpzp_acts", boom)
    parcel = box(0, 0, 10, 10)
    resolution = orchestrator._resolve_mpzp_acts_safely(None, service.RuChannelResult(queried=False), parcel)
    assert resolution.records == ()
    state = orchestrator._mpzp_status_safely(resolution, has_zones=False, manual_zone_required=False)
    assert state.status == "unknown"


def test_status_builder_error_degrades_to_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    real = orchestrator.build_mpzp_status

    def flaky(resolution, **kwargs):  # noqa: ANN001, ANN003, ANN202
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real(resolution, **kwargs)

    monkeypatch.setattr(orchestrator, "build_mpzp_status", flaky)
    state = orchestrator._mpzp_status_safely(service.unresolved_resolution(), has_zones=True, manual_zone_required=False)
    assert state.status == "unknown"


def test_contract_conversion_error_gives_an_empty_list_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator, "acts_to_contract", lambda _resolution: (_ for _ in ()).throw(ValueError("x")))
    assert orchestrator._acts_contract_safely(service.unresolved_resolution()) == []


def test_selected_act_replaces_the_missing_choice_only_when_precedence_resolved_it() -> None:
    from dataclasses import replace

    from tests.test_mpzp_acts_service import _discovery, _kimpzp_act

    old = _kimpzp_act("576/XLVII/2010", valid_from=date(2010, 8, 24), repealed_on=date(2024, 6, 26))
    new = _kimpzp_act("IV/30/2024", valid_from=date(2024, 6, 26), zone_symbols=("1MN",))
    discovery = _discovery([new, old])
    assert discovery.multiple_acts_at_point is True and discovery.plan_id is None
    resolution = service.resolve_mpzp_acts(discovery, service.RuChannelResult(queried=False), as_of=date(2026, 10, 10))
    chosen = orchestrator._discovery_with_selected_act(discovery, resolution)
    assert chosen.plan_id == "IV/30/2024" and chosen.uchwala_url == new.document_url
    assert chosen.candidate_zone_symbols == ["1MN"] and not chosen.multiple_acts_at_point
    # Bez rozstrzygnięcia (dwa obowiązujące akty) dyskretny wybór nie następuje.
    both = _discovery([new, replace(old, repealed_on=None)])
    unresolved = service.resolve_mpzp_acts(both, service.RuChannelResult(queried=False), as_of=date(2026, 10, 10))
    assert orchestrator._discovery_with_selected_act(both, unresolved) is both
    assert orchestrator._discovery_with_selected_act(None, unresolved) is None


def test_resume_replaces_the_pending_reason_code_and_keeps_the_state() -> None:
    from app.services.analysis_resume import _status_after_manual_zone

    stored = {"status": "available", "reason_code": "MPZP_MANUAL_ZONE_REQUIRED", "reason_codes": ["MPZP_MANUAL_ZONE_REQUIRED", "X"]}
    updated = _status_after_manual_zone(stored)
    assert updated == {"status": "available", "reason_code": "MPZP_MANUAL_ZONE_SELECTED", "reason_codes": ["MPZP_MANUAL_ZONE_SELECTED", "X"]}
    assert stored["reason_code"] == "MPZP_MANUAL_ZONE_REQUIRED"  # oryginał nietknięty
    assert _status_after_manual_zone(None) is None
