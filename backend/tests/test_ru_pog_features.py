"""Adapter WFS ``app-pog`` Rejestru Urbanistycznego (AU-105) na zamrożonych, rzeczywistych odpowiedziach usługi."""

from __future__ import annotations

import httpx
import pytest
import respx
from shapely.geometry import box

from app.modules.planning.application.ports import RequestBudgetExceeded, RuPogFeaturesSource
from app.modules.planning.application.ru_pog_discovery import ThreadSafeRequestBudget
from app.modules.planning.domain.pog_discovery import (
    ERROR_BUDGET_EXCEEDED,
    ERROR_CONTRACT,
    ERROR_LIMIT_EXCEEDED,
    ERROR_SERVICE_EXCEPTION,
    ERROR_TRANSPORT,
    GEOMETRY_AXIS_SUSPECT,
    POG_FEATURE_TYPES,
    observe_layer,
)
from app.modules.planning.infrastructure.ru_pog_features import _bbox_param, parse_pog_feature
from tests import ru_pog_fixtures as fx

LEGAL_FORCE = "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/legalForce"


def test_fixture_bytes_match_the_manifest() -> None:
    assert fx.verify_manifest_hashes() == []


def test_adapter_implements_the_port() -> None:
    assert isinstance(fx.adapter(), RuPogFeaturesSource)


def test_bbox_parameter_uses_the_north_east_axis_order_of_the_service() -> None:
    assert _bbox_param((503_200.0, 217_200.0, 503_300.0, 217_350.0)) == (
        "217200.000,503200.000,217350.000,503300.000,urn:ogc:def:crs:EPSG::2180"
    )


@respx.mock
def test_zones_of_a_binding_parcel_are_parsed_from_the_real_response() -> None:
    route = fx.mount(respx.mock)
    case = fx.BIELSKO
    tile = fx.tile(case)

    response = fx.adapter().fetch_features("StrefaPlanistyczna", tile)

    assert route.call_count == 1
    request = route.calls[0].request
    assert request.url.params["typeNames"] == "app-pog:StrefaPlanistyczna"  # jeden typ na żądanie
    assert request.url.params["bbox"] == _bbox_param(tile)
    assert request.url.params["srsName"] == "urn:ogc:def:crs:EPSG::2180"
    assert response.error_code is None and response.complete
    assert response.requests == 1 and response.origin == "network"
    assert len(response.features) == 7
    zone = next(item for item in response.features if item.geometry is not None and item.geometry.intersects(fx.parcel(case)))
    assert zone.status_href == LEGAL_FORCE
    assert zone.identifier.startswith("PL.ZIPPZP.9995/246101-POG/")
    # Odnośnik do aktu jest zapisany bez wersji — to klucz łączenia strefy z aktem.
    assert zone.plan_ref == "PL.ZIPPZP.9995/246101-POG/1POG"
    # Geometria jest w (wschód, północ) i leży w kaflu, nie w lustrzanym odbiciu współrzędnych.
    min_x, min_y, max_x, max_y = zone.geometry.bounds
    tile_min_x, tile_min_y, tile_max_x, tile_max_y = tile
    assert tile_min_x <= min_x and max_x <= tile_max_x and tile_min_y <= min_y and max_y <= tile_max_y


@respx.mock
def test_act_geometry_is_clipped_to_the_tile_so_the_cache_stays_small() -> None:
    fx.mount(respx.mock)
    tile = fx.tile(fx.BIELSKO)

    response = fx.adapter().fetch_features("AktPlanowaniaPrzestrzennego", tile)

    (act,) = response.features
    assert act.title == "Plan ogólny Bielska-Białej"
    assert act.status_href == LEGAL_FORCE
    assert act.valid_from is not None and act.valid_from.isoformat() == "2026-08-15"
    # Granica aktu całej gminy ma ponad 300 kB GML; w pamięci zostaje tylko część w kaflu.
    assert act.geometry is not None
    assert act.geometry.area <= (tile[2] - tile[0]) * (tile[3] - tile[1]) + 1e-6
    assert len(act.geometry.wkb) < 20_000


@respx.mock
def test_polygon_that_misses_the_tile_but_matches_by_envelope_is_dropped_without_a_problem() -> None:
    fx.mount(respx.mock)
    tile = fx.tile(fx.BIELSKO_WITH_OUZ)

    response = fx.adapter().fetch_features("ObszarUzupelnieniaZabudowy", tile)

    # RU filtruje po obwiedni: obiekt z odpowiedzi może nie dotykać kafla wielokątem. Taki obiekt nie jest
    # ani znaleziskiem, ani błędem — jego geometria po przycięciu jest pusta.
    assert response.error_code is None
    assert all(item.geometry_issue is None for item in response.features)
    observed = observe_layer("ouz", list(response.features), fx.parcel(fx.BIELSKO_WITH_OUZ))
    assert observed.state == "found" and observed.raw_legal_status == LEGAL_FORCE


@respx.mock
def test_pagination_follows_the_service_page_size_and_counts_every_request() -> None:
    route = fx.mount(respx.mock)
    case = fx.LEGNICA_TWO_PAGES

    response = fx.adapter().fetch_features("StrefaPlanistyczna", fx.tile(case))

    assert [call.request.url.params["startIndex"] for call in route.calls] == ["0", "10"]
    assert len(response.features) == 11 and response.complete and response.requests == 2


@respx.mock
def test_empty_answer_of_a_working_service_is_complete_and_has_no_error() -> None:
    fx.mount(respx.mock)

    response = fx.adapter().fetch_features("StrefaPlanistyczna", fx.tile(fx.KRAKOW_NO_POG))

    assert response.features == () and response.complete and response.error_code is None


@respx.mock
def test_service_exception_report_is_an_error_not_an_empty_answer() -> None:
    respx.get(fx.WFS_URL).mock(
        return_value=httpx.Response(
            400,
            content=b'<?xml version="1.0"?><ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1" version="2.0.0">'
            b'<ows:Exception exceptionCode="InvalidParameterValue"><ows:ExceptionText>x</ows:ExceptionText></ows:Exception>'
            b"</ows:ExceptionReport>",
            headers={"content-type": "application/xml"},
        )
    )

    response = fx.adapter().fetch_features("StrefaPlanistyczna", fx.tile(fx.BIELSKO))

    assert response.features == () and not response.complete
    assert response.error_code in {ERROR_TRANSPORT, ERROR_SERVICE_EXCEPTION}


@respx.mock
def test_http_500_and_timeout_are_transport_errors() -> None:
    route = respx.get(fx.WFS_URL).mock(return_value=httpx.Response(500))
    failed = fx.adapter().fetch_features("StrefaPlanistyczna", fx.tile(fx.BIELSKO))
    route.mock(side_effect=httpx.ReadTimeout("timeout"))
    timed_out = fx.adapter().fetch_features("StrefaPlanistyczna", fx.tile(fx.BIELSKO))

    for response in (failed, timed_out):
        assert response.features == () and not response.complete and response.error_code == ERROR_TRANSPORT


@respx.mock
def test_response_over_the_byte_limit_is_a_limit_error() -> None:
    fx.mount(respx.mock)

    response = fx.adapter(max_response_bytes=50_000).fetch_features(
        "AktPlanowaniaPrzestrzennego", fx.tile(fx.BIELSKO)
    )

    assert response.error_code == ERROR_LIMIT_EXCEEDED and response.features == ()


@respx.mock
def test_garbage_feature_is_a_contract_problem_and_the_response_is_not_complete() -> None:
    respx.get(fx.WFS_URL).mock(
        return_value=httpx.Response(
            200,
            content=(
                b'<?xml version="1.0"?><wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
                b'xmlns:gml="http://www.opengis.net/gml/3.2" numberMatched="1" numberReturned="1">'
                b"<wfs:member><x:Inny xmlns:x=\"urn:x\"/></wfs:member></wfs:FeatureCollection>"
            ),
            headers={"content-type": "application/gml+xml"},
        )
    )

    response = fx.adapter().fetch_features("StrefaPlanistyczna", fx.tile(fx.BIELSKO))

    assert response.features == ()
    assert ERROR_CONTRACT in response.issues and not response.complete


@respx.mock
def test_budget_is_charged_before_every_http_request_and_exhaustion_sends_nothing() -> None:
    route = fx.mount(respx.mock)
    budget = ThreadSafeRequestBudget(1)

    first = fx.adapter().fetch_features("StrefaPlanistyczna", fx.tile(fx.KRAKOW_NO_POG), budget)
    second = fx.adapter().fetch_features("AktPlanowaniaPrzestrzennego", fx.tile(fx.KRAKOW_NO_POG), budget)

    assert first.error_code is None and first.requests == 1
    assert second.error_code == ERROR_BUDGET_EXCEEDED and not second.complete and second.requests == 0
    assert route.call_count == 1  # drugie żądanie nie opuściło procesu
    assert budget.used == 1 and budget.denied == 1


@respx.mock
def test_budget_exhausted_between_pages_returns_no_features_and_is_not_complete() -> None:
    route = fx.mount(respx.mock)
    budget = ThreadSafeRequestBudget(1)  # druga strona stref nie zmieści się w budżecie

    response = fx.adapter().fetch_features("StrefaPlanistyczna", fx.tile(fx.LEGNICA_TWO_PAGES), budget)

    assert route.call_count == 1
    assert response.error_code == ERROR_BUDGET_EXCEEDED and not response.complete
    assert response.features == ()


def test_budget_object_is_thread_safe_and_never_exceeds_its_limit() -> None:
    import threading

    budget = ThreadSafeRequestBudget(6)
    granted: list[int] = []

    def worker() -> None:
        for _ in range(5):
            try:
                budget.acquire()
                granted.append(1)
            except RequestBudgetExceeded:
                pass

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(granted) == 6 and budget.used == 6 and budget.denied == 8 * 5 - 6


def test_budget_must_allow_at_least_one_request() -> None:
    with pytest.raises(ValueError):
        ThreadSafeRequestBudget(0)


@respx.mock
def test_zones_with_swapped_axes_are_flagged_instead_of_silently_missed() -> None:
    """Odpowiedź ze współrzędnymi w kolejności (wschód, północ) zamiast (północ, wschód) — wprost przyczyna błędu WMS.

    Wejściem jest rzeczywista odpowiedź usługi z zamienionymi liczbami w ``posList``: adapter ma ją zgłosić jako podejrzaną,
    a nie uznać za „brak obiektu na działce”.
    """
    original = fx.body(
        next(
            item["file"]
            for item in fx.manifest()["cases"][fx.BIELSKO]["requests"]
            if item["type_name"] == "StrefaPlanistyczna"
        )
    ).decode("utf-8")
    import re

    def swap(match: re.Match[str]) -> str:
        values = match.group(2).split()
        pairs = [(values[i], values[i + 1]) for i in range(0, len(values), 2)]
        return f"{match.group(1)}{' '.join(f'{b} {a}' for a, b in pairs)}</gml:posList>"

    swapped = re.sub(r"(<gml:posList[^>]*>)([^<]+)</gml:posList>", swap, original)
    assert swapped != original
    respx.get(fx.WFS_URL).mock(
        return_value=httpx.Response(200, content=swapped.encode(), headers={"content-type": "application/gml+xml"})
    )

    response = fx.adapter().fetch_features("StrefaPlanistyczna", fx.tile(fx.BIELSKO))

    assert response.features and all(item.geometry is None for item in response.features)
    assert {item.geometry_issue for item in response.features} == {GEOMETRY_AXIS_SUSPECT}
    observed = observe_layer("planning_zones", list(response.features), fx.parcel(fx.BIELSKO))
    assert observed.state == "unavailable"  # nigdy „empty”


def test_parse_ignores_a_feature_of_another_type() -> None:
    zone = fx.body(
        next(
            item["file"]
            for item in fx.manifest()["cases"][fx.BIELSKO]["requests"]
            if item["type_name"] == "StrefaPlanistyczna"
        )
    ).decode("utf-8")
    start = zone.index("<app-pog:StrefaPlanistyczna ")
    end = zone.index("</app-pog:StrefaPlanistyczna>") + len("</app-pog:StrefaPlanistyczna>")
    member = zone[start:end]
    namespaces = (
        'xmlns:app-pog="https://www.gov.pl/static/zagospodarowanieprzestrzenne/schemas/app/3.0" '
        'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:xlink="http://www.w3.org/1999/xlink"'
    )
    member = member.replace("<app-pog:StrefaPlanistyczna ", f"<app-pog:StrefaPlanistyczna {namespaces} ", 1)

    assert parse_pog_feature(member.encode(), "ObszarUzupelnieniaZabudowy", fx.tile(fx.BIELSKO)) is None
    parsed = parse_pog_feature(member.encode(), "StrefaPlanistyczna", fx.tile(fx.BIELSKO))
    assert parsed is not None and parsed.feature_type == "StrefaPlanistyczna"


def test_all_four_types_are_requested_by_the_catalog_types_list() -> None:
    assert set(POG_FEATURE_TYPES) == {
        "AktPlanowaniaPrzestrzennego",
        "ObszarZabudowySrodmiejskiej",
        "ObszarUzupelnieniaZabudowy",
        "StrefaPlanistyczna",
    }


def test_unused_geometry_helper_is_consistent_with_the_tile_math() -> None:
    parcel = fx.parcel(fx.BIELSKO)
    tile = fx.tile(fx.BIELSKO)
    assert box(*tile).contains(parcel)
