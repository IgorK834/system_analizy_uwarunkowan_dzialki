import httpx
import pytest
import respx

from app.services.geocoding import (
    GeocodeSuggestion,
    GeocodingServiceUnavailableError,
    UUG_BASE_URL,
    UUG_MAX_RESULTS,
    geocode_address,
)

UUG_SINGLE_RESULT = {
    "returned objects": 1,
    "found objects": 15,
    "results": {
        "1": {
            "city": "Marki",
            "citypart": None,
            "street": "Generała Władysława Andersa",
            "number": "1",
            "teryt": "143402",
            "x": "644234.29",
            "y": "499514.029999999",
            "accuracy": "0.444444",
            "id": 1,
        }
    },
}
UUG_MULTIPLE_RESULTS = {
    "returned objects": 2,
    "results": {
        "1": {
            "city": "Marki",
            "street": "Andersa",
            "number": "1",
            "teryt": "143402",
            "x": "644234.29",
            "y": "499514.03",
            "accuracy": "0.9",
        },
        "2": {
            "city": "Marki",
            "street": "Andersa",
            "number": "1A",
            "teryt": "143402",
            "x": "644300.0",
            "y": "499600.0",
            "accuracy": "0.7",
        },
    },
}
UUG_EMPTY_RESULT = {"returned objects": 0, "found objects": 0, "results": {}}
UUG_CITY_ONLY = {
    "returned objects": 1,
    "results": {
        "1": {
            "city": "Warszawa",
            "street": None,
            "number": None,
            "teryt": "146501",
            "x": "637123.0",
            "y": "482000.0",
            "accuracy": "1.0",
        }
    },
}


@respx.mock
async def test_geocode_address_returns_suggestion_for_valid_address() -> None:
    respx.get(UUG_BASE_URL).mock(
        return_value=httpx.Response(200, json=UUG_SINGLE_RESULT)
    )

    result = await geocode_address("Marki, Andersa 1")

    assert len(result) == 1
    assert isinstance(result[0], GeocodeSuggestion)
    assert result[0].x == 644234.29
    assert result[0].y == pytest.approx(499514.03, abs=0.01)
    assert result[0].confidence == pytest.approx(0.444444, abs=1e-4)


@respx.mock
async def test_geocode_address_label_contains_city_street_number() -> None:
    respx.get(UUG_BASE_URL).mock(
        return_value=httpx.Response(200, json=UUG_SINGLE_RESULT)
    )

    result = await geocode_address("Marki, Andersa 1")

    assert "Marki" in result[0].label
    assert "Andersa" in result[0].label
    assert "1" in result[0].label


@respx.mock
async def test_geocode_address_city_only_label() -> None:
    respx.get(UUG_BASE_URL).mock(return_value=httpx.Response(200, json=UUG_CITY_ONLY))

    result = await geocode_address("Warszawa")

    assert result[0].label == "Warszawa"


@respx.mock
async def test_geocode_address_returns_multiple_suggestions() -> None:
    respx.get(UUG_BASE_URL).mock(
        return_value=httpx.Response(200, json=UUG_MULTIPLE_RESULTS)
    )

    result = await geocode_address("Marki, Andersa 1")

    assert len(result) == 2
    assert result[0].confidence > result[1].confidence


@respx.mock
async def test_geocode_address_returns_empty_list_for_no_results() -> None:
    respx.get(UUG_BASE_URL).mock(
        return_value=httpx.Response(200, json=UUG_EMPTY_RESULT)
    )

    result = await geocode_address("Nieistniejący adres")

    assert result == []


@respx.mock
async def test_geocode_address_timeout_raises_geocoding_service_unavailable() -> None:
    respx.get(UUG_BASE_URL).mock(side_effect=httpx.TimeoutException("timeout"))

    with pytest.raises(GeocodingServiceUnavailableError):
        await geocode_address("Marki, Andersa 1")


@respx.mock
async def test_geocode_address_http_500_raises_geocoding_service_unavailable() -> None:
    respx.get(UUG_BASE_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(GeocodingServiceUnavailableError):
        await geocode_address("Marki, Andersa 1")


@respx.mock
async def test_geocode_address_connection_error_raises_geocoding_service_unavailable() -> (
    None
):
    respx.get(UUG_BASE_URL).mock(side_effect=httpx.ConnectError("connect error"))

    with pytest.raises(GeocodingServiceUnavailableError):
        await geocode_address("Marki, Andersa 1")


async def test_geocode_address_empty_query_returns_empty_list() -> None:
    result = await geocode_address("   ")

    assert result == []


@respx.mock
async def test_geocode_address_x_and_y_are_floats_not_strings() -> None:
    respx.get(UUG_BASE_URL).mock(
        return_value=httpx.Response(200, json=UUG_SINGLE_RESULT)
    )

    result = await geocode_address("Marki, Andersa 1")

    assert isinstance(result[0].x, float)
    assert isinstance(result[0].y, float)


@respx.mock
async def test_geocode_address_teryt_is_set() -> None:
    respx.get(UUG_BASE_URL).mock(
        return_value=httpx.Response(200, json=UUG_SINGLE_RESULT)
    )

    result = await geocode_address("Marki, Andersa 1")

    assert result[0].teryt == "143402"


@respx.mock
async def test_geocode_address_normalizes_extra_whitespace() -> None:
    respx.get(UUG_BASE_URL).mock(
        return_value=httpx.Response(200, json=UUG_SINGLE_RESULT)
    )

    await geocode_address("  Marki,   Andersa   1  ")
    params = respx.calls.last.request.url.params

    assert params["address"] == "Marki, Andersa 1"


@respx.mock
async def test_geocode_address_sends_get_address_request_param() -> None:
    respx.get(UUG_BASE_URL).mock(
        return_value=httpx.Response(200, json=UUG_SINGLE_RESULT)
    )

    await geocode_address("Marki, Andersa 1")
    params = respx.calls.last.request.url.params

    assert params["request"] == "GetAddress"


@respx.mock
async def test_geocode_address_limits_to_uug_max_results() -> None:
    many_results = {
        "returned objects": 7,
        "results": {
            str(index): {
                "city": "Marki",
                "street": "Andersa",
                "number": str(index),
                "teryt": "143402",
                "x": str(644000 + index),
                "y": str(499000 + index),
                "accuracy": "0.8",
            }
            for index in range(1, 8)
        },
    }
    respx.get(UUG_BASE_URL).mock(return_value=httpx.Response(200, json=many_results))

    result = await geocode_address("Marki, Andersa")

    assert len(result) <= UUG_MAX_RESULTS


@respx.mock
async def test_geocode_address_street_none_when_city_only() -> None:
    respx.get(UUG_BASE_URL).mock(return_value=httpx.Response(200, json=UUG_CITY_ONLY))

    result = await geocode_address("Warszawa")

    assert result[0].street is None
