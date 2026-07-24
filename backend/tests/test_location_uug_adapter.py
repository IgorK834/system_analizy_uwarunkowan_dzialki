"""Testy produkcyjnego adaptera UUG/EMUiA (Faza 11.1).

Bez prawdziwej sieci — używamy respx i małego, zanonimizowanego fixture kontraktu
GetAddress. Weryfikują parsowanie, transformację EPSG:2180 -> WGS84, cache,
guard katalogu źródeł i mapowanie błędów na AddressSearchProviderError.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.core.data_sources import get_catalog
from app.modules.location.application.ports import AddressSearchProviderError
from app.modules.location.domain.models import AddressQuery, ResultType
from app.modules.location.infrastructure import uug_adapter
from app.modules.location.infrastructure.cache import BoundedTTLCache
from app.modules.location.infrastructure.uug_adapter import UugAddressSearchProvider

_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "uug" / "getaddress_sample.json"
)
_UUG_URL = uug_adapter._UUG_BASE_URL


@pytest.fixture(autouse=True)
def _clear_catalog_cache() -> None:
    get_catalog.cache_clear()
    yield
    get_catalog.cache_clear()


def _sample() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


@respx.mock
async def test_adapter_parses_and_transforms_to_wgs84() -> None:
    route = respx.get(_UUG_URL).mock(return_value=httpx.Response(200, json=_sample()))
    provider = UugAddressSearchProvider()

    candidates = await provider.search(AddressQuery(q="Marki Andersa"))

    assert len(candidates) == 2
    first = candidates[0]
    # EPSG:2180 (Mazowsze) -> WGS84 daje współrzędne w granicach Polski.
    assert 14.0 < first.point.lon < 25.0
    assert 49.0 < first.point.lat < 55.0
    assert first.result_type is ResultType.HOUSE_NUMBER
    # Drugi wynik ma ulicę bez numeru -> typ STREET.
    assert candidates[1].result_type is ResultType.STREET
    assert 0.0 <= first.provider_confidence <= 1.0
    assert first.parts.country == "Polska"
    request_params = route.calls[0].request.url.params
    assert request_params["accuracy"] == "0.5"
    assert request_params["exact_number"] == "0"


@respx.mock
async def test_adapter_accepts_partial_street_result_without_returned_count() -> None:
    # Rzeczywisty wariant kontraktu UUG dla zapytania częściowego: odpowiedź
    # zawiera wynik ulicy, ale nie zawiera pola "returned objects".
    partial_street_response = {
        "type": "street",
        "found objects": 1,
        "results": {
            "1": {
                "city": "Warszawa",
                "street": "ulica Marszałkowska",
                "number": None,
                "teryt": "146501",
                "x": "637394.68",
                "y": "486657.55",
                "accuracy": "0.83",
            }
        },
    }
    respx.get(_UUG_URL).mock(
        return_value=httpx.Response(200, json=partial_street_response)
    )
    provider = UugAddressSearchProvider()

    candidates = await provider.search(AddressQuery(q="Warszawa, Marsza"))

    assert len(candidates) == 1
    assert candidates[0].label == "Warszawa, ulica Marszałkowska"
    assert candidates[0].result_type is ResultType.STREET


@respx.mock
async def test_adapter_caches_repeated_queries() -> None:
    route = respx.get(_UUG_URL).mock(return_value=httpx.Response(200, json=_sample()))
    provider = UugAddressSearchProvider(
        cache=BoundedTTLCache(max_entries=8, ttl_seconds=60)
    )

    await provider.search(AddressQuery(q="Marki Andersa"))
    await provider.search(AddressQuery(q="Marki Andersa"))

    assert route.call_count == 1  # drugi odczyt z cache


@respx.mock
async def test_adapter_empty_result_returns_empty_list() -> None:
    respx.get(_UUG_URL).mock(
        return_value=httpx.Response(200, json={"returned objects": 0, "results": {}})
    )
    provider = UugAddressSearchProvider()
    assert await provider.search(AddressQuery(q="Nieistnieje 999")) == []


@respx.mock
async def test_adapter_timeout_raises_provider_error() -> None:
    respx.get(_UUG_URL).mock(side_effect=httpx.TimeoutException("timeout"))
    provider = UugAddressSearchProvider()
    with pytest.raises(AddressSearchProviderError):
        await provider.search(AddressQuery(q="Marki"))


@respx.mock
async def test_adapter_server_error_raises_provider_error() -> None:
    respx.get(_UUG_URL).mock(return_value=httpx.Response(503))
    provider = UugAddressSearchProvider()
    with pytest.raises(AddressSearchProviderError):
        await provider.search(AddressQuery(q="Marki"))


@respx.mock
async def test_adapter_does_not_leak_provider_contract_on_error() -> None:
    respx.get(_UUG_URL).mock(return_value=httpx.Response(500))
    provider = UugAddressSearchProvider()
    try:
        await provider.search(AddressQuery(q="Marki"))
    except AddressSearchProviderError as exc:
        # Komunikat nie ujawnia adresu ani parametrów dostawcy.
        assert "gugik" not in str(exc).lower()
        assert "GetAddress" not in str(exc)
