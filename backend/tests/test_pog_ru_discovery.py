"""Discovery POG w Rejestrze Urbanistycznym przez WFS (AU-105): budżet żądań, cache, deduplikacja, błędy.

Odpowiedzi usługi są zamrożonymi, rzeczywistymi odpowiedziami RU (``tests/ru_pog_fixtures``) dla działek korpusu
referencyjnego. Licznik żądań to ``route.call_count`` trasy ``respx`` — czyli faktyczna liczba żądań HTTP do
``rejestr-urbanistyczny.gov.pl``.
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
import respx
from shapely.geometry import MultiPolygon, box

from app.core.metrics import upstream_metrics
from app.core.settings import settings
from app.modules.planning.composition import clear_ru_pog_query_cache
from app.modules.planning.domain.pog_discovery import snap_bbox_outward
from app.services.mpzp import _build_sample_points
from app.services.pog import PogGminaSources, discover_pog
from tests import ru_pog_fixtures as fx

BUDGET = 6
LEGAL_FORCE = "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/legalForce"


@pytest.fixture(autouse=True)
def ru_online(monkeypatch: pytest.MonkeyPatch) -> None:
    """Włącza kanał RU (domyślnie wyłączony w testach) i podstawia publiczny DNS dla allowlisty hostów."""
    monkeypatch.setattr(settings, "ru_pog_discovery_enabled", True)
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (fx.PUBLIC_IP, 0))]
    )
    upstream_metrics.reset()
    clear_ru_pog_query_cache()


def _discover(case: str, teryt: str | None = None):  # noqa: ANN202
    return asyncio.run(discover_pog(fx.parcel(case), PogGminaSources(teryt=teryt or fx.manifest()["cases"][case]["teryt"])))


@respx.mock
def test_binding_parcel_is_recognised_from_the_four_wfs_layers() -> None:
    route = fx.mount(respx.mock)

    result = _discover(fx.BIELSKO_WITH_OUZ)

    assert result.legal_status == "binding" and result.source_responded
    assert result.raw_legal_status == LEGAL_FORCE
    assert result.legal_evidence is not None and result.legal_evidence.official
    assert result.planning_act.status == "found" and result.planning_act.matched_wfs_type == "AktPlanowaniaPrzestrzennego"
    assert result.planning_zones.status == "found" and result.planning_zones.feature_count >= 1
    assert result.ouz.status == "found" and result.downtown_area.status == "found"
    assert result.planning_act.matched_wms_layer is None  # WMS nie bierze udziału w discovery
    assert result.is_discovery_only and result.links == []
    assert result.source_metadata.source_id == "pog_app"
    assert result.source_metadata.source_name == "REJESTR_URBANISTYCZNY_WFS"
    assert result.source_metadata.source_url == fx.WFS_URL
    assert {warning.code for warning in result.warnings} >= {"POG_RU_CATALOG_SOURCE", "POG_DISCOVERY_ONLY"}
    assert route.call_count == 4


@respx.mock
def test_object_whose_envelope_hits_the_tile_but_polygon_does_not_is_not_a_failure() -> None:
    """Regresja z live smoke 2026-10-12: obiekt OUZ z obwiednią w kaflu (RU filtruje po obwiedni), ale wielokątem poza nim,
    czynił warstwę ``unavailable`` (fałszywy ``POG_RU_LAYER_UNAVAILABLE``); powinna być ``empty``."""
    fx.mount(respx.mock)

    result = _discover(fx.BIELSKO)

    assert result.ouz.status == "empty" and result.downtown_area.status == "empty"
    codes = {warning.code for warning in result.warnings}
    assert "POG_RU_LAYER_UNAVAILABLE" not in codes and "POG_RU_GEOMETRY_UNUSABLE" not in codes


@respx.mock
def test_empty_answers_are_not_found_and_never_a_statement_of_absence() -> None:
    fx.mount(respx.mock)

    result = _discover(fx.KRAKOW_NO_POG)

    assert result.legal_status == "unknown" and result.source_responded
    assert all(
        section.status == "empty"
        for section in (result.planning_act, result.downtown_area, result.ouz, result.planning_zones)
    )
    empty = next(warning for warning in result.warnings if warning.code == "POG_NOT_FOUND_IN_SOURCE")
    assert "nie oznacza braku planu" in empty.message


@respx.mock
def test_wms_of_the_registry_is_never_queried_for_discovery() -> None:
    fx.mount(respx.mock)
    wms = respx.get(url__startswith=fx.WMS_URL).mock(return_value=httpx.Response(500))

    for case in (fx.BIELSKO, fx.KRAKOW_NO_POG, fx.LEGNICA_TWO_PAGES):
        _discover(case)

    assert wms.call_count == 0


@pytest.mark.parametrize("case", fx.case_ids())
@respx.mock
def test_every_corpus_parcel_stays_within_the_request_budget(case: str) -> None:
    unexpected: list[str] = []
    route = fx.mount(respx.mock, unexpected)

    result = _discover(case)

    assert unexpected == []  # cały ruch jest obsłużony z zamrożonego manifestu
    assert result.source_responded
    assert route.call_count <= BUDGET
    for call in route.calls:
        assert call.request.url.host == "rejestr-urbanistyczny.gov.pl"
        assert call.request.url.params["typeNames"].count(",") == 0


@respx.mock
def test_request_count_does_not_depend_on_the_number_of_sample_points() -> None:
    """Przed AU-105 liczba żądań rosła z liczbą punktów próbki (2–6 punktów × ~48 warstw); teraz nie zależy od niej."""
    corpus_counts = {case: len(_build_sample_points(fx.parcel(case))) for case in fx.case_ids()}
    few = min(corpus_counts, key=corpus_counts.get)
    many = max(corpus_counts, key=corpus_counts.get)
    assert corpus_counts[few] == 2 and corpus_counts[many] >= 6, corpus_counts
    route = fx.mount(respx.mock)

    _discover(few)
    clear_ru_pog_query_cache()
    calls_few = route.call_count
    _discover(many)
    calls_many = route.call_count - calls_few

    assert calls_few <= BUDGET and calls_many <= BUDGET
    assert calls_few == 4 and calls_many >= 4


@respx.mock
def test_multipart_parcel_with_many_sample_points_is_still_one_query_per_layer() -> None:
    """Działka wieloczęściowa (punkt próbki dla każdej części + ćwiartki) — nadal jedno zapytanie na warstwę."""
    base = fx.parcel(fx.BIELSKO)
    extra_part = box(503_270.0, 217_320.0, 503_280.0, 217_330.0)  # druga część w tym samym kaflu 50 m
    multi = MultiPolygon([base, extra_part])
    assert len(_build_sample_points(multi)) >= 3  # centroid, punkt reprezentatywny i punkt każdej części
    assert snap_bbox_outward(multi.bounds) == fx.tile(fx.BIELSKO)
    unexpected: list[str] = []
    route = fx.mount(respx.mock, unexpected)

    result = asyncio.run(discover_pog(multi, PogGminaSources(teryt="2461011")))

    assert unexpected == [] and route.call_count == 4
    assert result.legal_status == "binding"


@respx.mock
def test_same_tile_is_served_from_cache_for_the_next_analysis() -> None:
    route = fx.mount(respx.mock)

    first = _discover(fx.BIELSKO)
    cold = route.call_count
    second = _discover(fx.BIELSKO)

    assert cold == 4 and route.call_count == 4  # druga analiza nie wysłała żadnego żądania
    assert second.legal_status == first.legal_status == "binding"
    assert second.source_metadata.fetched_at == first.source_metadata.fetched_at  # data pobrania, nie odczytu z cache
    counters = upstream_metrics.snapshot()
    assert counters["ru_pog.cache_miss"] == 4 and counters["ru_pog.cache_hit"] == 4
    assert counters["ru_pog.requests"] == 4


@respx.mock
def test_parallel_analyses_of_one_parcel_share_the_queries() -> None:
    route = fx.mount(respx.mock)

    async def run() -> list:
        return await asyncio.gather(
            *(discover_pog(fx.parcel(fx.BIELSKO), PogGminaSources(teryt="2461011")) for _ in range(5))
        )

    results = asyncio.run(run())

    assert {result.legal_status for result in results} == {"binding"}
    assert route.call_count == 4  # pięć równoległych analiz = jedno pobranie każdej warstwy


@respx.mock
def test_exhausted_budget_makes_layers_unavailable_and_raises_the_alarm(monkeypatch: pytest.MonkeyPatch) -> None:
    route = fx.mount(respx.mock)
    monkeypatch.setattr(settings, "ru_pog_request_budget", 2)

    result = _discover(fx.KRAKOW_NO_POG)

    assert route.call_count == 2  # żądania ponad budżet nie opuściły procesu
    statuses = {
        section.status for section in (result.planning_act, result.downtown_area, result.ouz, result.planning_zones)
    }
    assert statuses == {"empty", "unavailable"}
    # Dwie warstwy nie zostały sprawdzone, więc „pusto” nie może zostać uznane za odpowiedź źródła.
    assert result.source_responded is False
    assert any(warning.code == "POG_SOURCE_UNAVAILABLE" for warning in result.warnings)
    assert upstream_metrics.snapshot()["ru_pog.budget_exceeded"] == 1


@respx.mock
def test_exhausted_budget_keeps_what_was_found_and_flags_the_missing_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    fx.mount(respx.mock)
    monkeypatch.setattr(settings, "ru_pog_request_budget", 3)

    result = _discover(fx.BIELSKO)

    sections = (result.planning_act, result.ouz, result.planning_zones, result.downtown_area)
    assert any(section.status == "unavailable" for section in sections)
    assert upstream_metrics.snapshot()["ru_pog.budget_exceeded"] == 1
    if result.source_responded:
        assert any(warning.code == "POG_RU_LAYER_UNAVAILABLE" for warning in result.warnings)
    else:
        assert result.legal_status == "unknown"


@respx.mock
def test_service_failure_is_unknown_with_source_not_responding_never_absence() -> None:
    respx.get(fx.WFS_URL).mock(return_value=httpx.Response(500))

    result = _discover(fx.BIELSKO)

    assert result.legal_status == "unknown" and result.source_responded is False
    assert all(
        section.status == "unavailable"
        for section in (result.planning_act, result.downtown_area, result.ouz, result.planning_zones)
    )
    assert any(warning.code == "POG_SOURCE_UNAVAILABLE" for warning in result.warnings)
    assert not any(warning.code == "POG_NOT_FOUND_IN_SOURCE" for warning in result.warnings)


@respx.mock
def test_one_failing_layer_does_not_erase_the_others() -> None:
    real = fx.respond_from_fixtures()

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.params["typeNames"] == "app-pog:AktPlanowaniaPrzestrzennego":
            return httpx.Response(500)
        return real(request)

    respx.get(fx.WFS_URL).mock(side_effect=respond)

    result = _discover(fx.BIELSKO)

    assert result.planning_act.status == "unavailable"
    assert result.planning_zones.status == "found"
    assert result.source_responded and result.legal_status == "binding"  # status z obiektów stref
    assert any(warning.code == "POG_RU_LAYER_UNAVAILABLE" for warning in result.warnings)


@respx.mock
def test_service_error_is_remembered_briefly_and_does_not_hammer_the_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ru_pog_request_budget", 20)  # ponowienia po 5xx nie mogą wyczerpać budżetu
    route = respx.get(fx.WFS_URL).mock(return_value=httpx.Response(500))

    _discover(fx.BIELSKO)
    after_first = route.call_count
    _discover(fx.BIELSKO)

    assert after_first == 8  # cztery warstwy × (próba + jedno ponowienie)
    assert route.call_count == after_first  # druga analiza w oknie ujemnego TTL nie wysłała nic


@respx.mock
def test_retries_after_5xx_consume_the_budget_and_never_exceed_it() -> None:
    route = respx.get(fx.WFS_URL).mock(return_value=httpx.Response(500))

    result = _discover(fx.BIELSKO)

    assert route.call_count == BUDGET  # budżet obejmuje także ponowienia
    assert result.source_responded is False and result.legal_status == "unknown"


def test_disabled_channel_is_unknown_not_absence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ru_pog_discovery_enabled", False)

    result = _discover(fx.BIELSKO)

    assert result.legal_status == "unknown" and result.source_responded is False
    assert any(warning.code == "POG_NO_CONFIRMED_SOURCE" for warning in result.warnings)


@respx.mock
def test_the_registry_is_asked_in_north_east_axis_order() -> None:
    """Regresja błędu sprzed AU-105: WMS i WFS RU oczekują dla EPSG:2180 osi północ,wschód; kod wysyłał wschód,północ."""
    route = fx.mount(respx.mock)
    parcel = fx.parcel(fx.BIELSKO)

    _discover(fx.BIELSKO)

    min_x, min_y, max_x, max_y = parcel.bounds
    for call in route.calls:
        north_min, east_min, north_max, east_max, crs = call.request.url.params["bbox"].split(",")
        assert crs == "urn:ogc:def:crs:EPSG::2180"
        assert float(north_min) <= min_y and float(north_max) >= max_y  # pierwsza para to północ
        assert float(east_min) <= min_x and float(east_max) >= max_x
