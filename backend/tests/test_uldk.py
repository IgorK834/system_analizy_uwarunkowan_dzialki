from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from app.core.metrics import upstream_metrics
from app.services.uldk import (
    InvalidParcelIdentifierError,
    InvalidUldkResponseError,
    ParcelLookupResult,
    ParcelNotFoundError,
    ULDK_BASE_URL,
    ULDK_MAX_RETRIES,
    ULDK_NO_RESULTS_RETRY_DELAY_S,
    ULDK_NOT_FOUND_MESSAGE,
    UldkNoResultsError,
    UldkParcelResult,
    UldkServiceUnavailableError,
    _parse_uldk_response,
    get_parcel_by_id,
    get_parcel_by_xy,
)

VALID_ID = "122101_1.0001.1234"
VALID_ID_WITH_SHEET = "122101_1.0001.AR_1.1234"
VALID_ID_WITH_FRACTION = "226301_5.0001.1234/5"
SAMPLE_WKT = (
    "MULTIPOLYGON(((500000 200000,500100 200000," "500100 200100,500000 200000)))"
)
SAMPLE_WKT_EWKT = f"SRID=2180;{SAMPLE_WKT}"
SAMPLE_TERYT = "122101"
SAMPLE_RESPONSE = f"0\n{VALID_ID}|{SAMPLE_WKT}|{SAMPLE_TERYT}\n"
SAMPLE_X = 500000.0
SAMPLE_Y = 200000.0
SAMPLE_XY_RESPONSE = f"0\n{VALID_ID}|{SAMPLE_WKT}|{SAMPLE_TERYT}\n"
NOT_FOUND_RESPONSE = "0\n\n"
NOT_FOUND_XY_RESPONSE = "0\n\n"
ULDK_ERROR_RESPONSE = "-1\nPodany identyfikator działki jest niepoprawny.\n"
MALFORMED_RESPONSE = "0\nno_pipe_separator\n"


@respx.mock
async def test_get_parcel_by_id_returns_uldk_parcel_result() -> None:
    respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(200, text=SAMPLE_RESPONSE)
    )

    result = await get_parcel_by_id(VALID_ID)

    assert isinstance(result, UldkParcelResult)
    assert result.parcel_identifier == VALID_ID
    assert result.geometry_wkt == SAMPLE_WKT
    assert result.teryt == SAMPLE_TERYT


@respx.mock
async def test_get_parcel_by_id_strips_srid_prefix_from_wkt() -> None:
    response_text = f"0\n{VALID_ID}|{SAMPLE_WKT_EWKT}|{SAMPLE_TERYT}\n"
    respx.get(ULDK_BASE_URL).mock(return_value=httpx.Response(200, text=response_text))

    result = await get_parcel_by_id(VALID_ID)

    assert result.geometry_wkt.startswith("MULTIPOLYGON")
    assert "SRID" not in result.geometry_wkt


@respx.mock
async def test_get_parcel_by_id_polygon_wkt_accepted() -> None:
    polygon_wkt = "POLYGON((500000 200000,500100 200000,500000 200000))"
    response_text = f"0\n122101_1.0001.1|{polygon_wkt}|{SAMPLE_TERYT}\n"
    respx.get(ULDK_BASE_URL).mock(return_value=httpx.Response(200, text=response_text))

    result = await get_parcel_by_id("122101_1.0001.1")

    assert result.geometry_wkt.startswith("POLYGON")


@respx.mock
async def test_get_parcel_by_id_not_found_raises_parcel_not_found_error() -> None:
    respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(200, text=NOT_FOUND_RESPONSE)
    )

    with pytest.raises(ParcelNotFoundError):
        await get_parcel_by_id(VALID_ID)


@respx.mock
async def test_parcel_not_found_error_contains_identifier() -> None:
    respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(200, text=NOT_FOUND_RESPONSE)
    )

    with pytest.raises(ParcelNotFoundError) as exc_info:
        await get_parcel_by_id(VALID_ID)

    assert VALID_ID in str(exc_info.value)


@respx.mock
async def test_get_parcel_by_id_uldk_error_status_raises_service_unavailable() -> None:
    respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(200, text=ULDK_ERROR_RESPONSE)
    )

    with pytest.raises(UldkServiceUnavailableError):
        await get_parcel_by_id(VALID_ID)


@respx.mock
async def test_get_parcel_by_id_timeout_raises_service_unavailable() -> None:
    respx.get(ULDK_BASE_URL).mock(side_effect=httpx.TimeoutException("timeout"))

    with pytest.raises(UldkServiceUnavailableError):
        await get_parcel_by_id(VALID_ID)


@respx.mock
async def test_get_parcel_by_id_http_500_raises_service_unavailable() -> None:
    respx.get(ULDK_BASE_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(UldkServiceUnavailableError):
        await get_parcel_by_id(VALID_ID)


@respx.mock
async def test_get_parcel_by_id_empty_response_raises_invalid_response() -> None:
    respx.get(ULDK_BASE_URL).mock(return_value=httpx.Response(200, text=""))

    with pytest.raises(InvalidUldkResponseError):
        await get_parcel_by_id(VALID_ID)


@respx.mock
async def test_get_parcel_by_id_malformed_data_line_raises_invalid_response() -> None:
    respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(200, text=MALFORMED_RESPONSE)
    )

    with pytest.raises(InvalidUldkResponseError):
        await get_parcel_by_id(VALID_ID)


@respx.mock
async def test_get_parcel_by_id_point_wkt_raises_invalid_response() -> None:
    respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(
            200, text=f"0\n{VALID_ID}|POINT(500000 200000)|122101\n"
        )
    )

    with pytest.raises(InvalidUldkResponseError):
        await get_parcel_by_id(VALID_ID)


@respx.mock
async def test_invalid_identifier_raises_before_http() -> None:
    with pytest.raises(InvalidParcelIdentifierError):
        await get_parcel_by_id("niepoprawny")

    assert respx.calls.call_count == 0


async def test_empty_identifier_raises_invalid_format() -> None:
    with pytest.raises(InvalidParcelIdentifierError):
        await get_parcel_by_id("")


async def test_identifier_too_short_teryt_raises() -> None:
    with pytest.raises(InvalidParcelIdentifierError):
        await get_parcel_by_id("12345_1.0001.1234")


@respx.mock
async def test_valid_id_with_map_sheet_accepted() -> None:
    response_text = f"0\n{VALID_ID_WITH_SHEET}|{SAMPLE_WKT}|{SAMPLE_TERYT}\n"
    respx.get(ULDK_BASE_URL).mock(return_value=httpx.Response(200, text=response_text))

    result = await get_parcel_by_id(VALID_ID_WITH_SHEET)

    assert result.parcel_identifier == VALID_ID_WITH_SHEET


@respx.mock
async def test_valid_id_with_fraction_accepted() -> None:
    response_text = f"0\n{VALID_ID_WITH_FRACTION}|{SAMPLE_WKT}|226301\n"
    respx.get(ULDK_BASE_URL).mock(return_value=httpx.Response(200, text=response_text))

    result = await get_parcel_by_id(VALID_ID_WITH_FRACTION)

    assert result.parcel_identifier == VALID_ID_WITH_FRACTION


@respx.mock
async def test_get_parcel_by_id_sends_correct_request_params() -> None:
    respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(200, text=SAMPLE_RESPONSE)
    )

    await get_parcel_by_id(VALID_ID)
    params = respx.calls.last.request.url.params

    assert params["request"] == "GetParcelById"
    assert params["id"] == VALID_ID
    assert params["result"] == "id,geom_wkt,teryt"


@respx.mock
async def test_get_parcel_by_id_unknown_status_raises_invalid_response() -> None:
    respx.get(ULDK_BASE_URL).mock(return_value=httpx.Response(200, text="2\ndane\n"))

    with pytest.raises(InvalidUldkResponseError):
        await get_parcel_by_id(VALID_ID)


@respx.mock
async def test_get_parcel_by_id_empty_wkt_raises_invalid_response() -> None:
    respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(200, text=f"0\n{VALID_ID}||{SAMPLE_TERYT}\n")
    )

    with pytest.raises(InvalidUldkResponseError):
        await get_parcel_by_id(VALID_ID)


@respx.mock
async def test_get_parcel_by_xy_returns_parcel_lookup_result() -> None:
    respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(200, text=SAMPLE_XY_RESPONSE)
    )

    result = await get_parcel_by_xy(SAMPLE_X, SAMPLE_Y)

    assert isinstance(result, ParcelLookupResult)
    assert result.parcel_identifier == VALID_ID
    assert result.wkt == SAMPLE_WKT
    assert result.teryt == SAMPLE_TERYT


@respx.mock
async def test_get_parcel_by_xy_source_metadata_has_uldk_name() -> None:
    respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(200, text=SAMPLE_XY_RESPONSE)
    )

    result = await get_parcel_by_xy(SAMPLE_X, SAMPLE_Y)

    assert result.source_metadata.source_name == "ULDK"


@respx.mock
async def test_get_parcel_by_xy_source_metadata_fetched_at_is_datetime() -> None:
    respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(200, text=SAMPLE_XY_RESPONSE)
    )

    result = await get_parcel_by_xy(SAMPLE_X, SAMPLE_Y)

    assert isinstance(result.source_metadata.fetched_at, datetime)


@respx.mock
async def test_get_parcel_by_xy_source_metadata_confidence_is_one() -> None:
    respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(200, text=SAMPLE_XY_RESPONSE)
    )

    result = await get_parcel_by_xy(SAMPLE_X, SAMPLE_Y)

    assert result.source_metadata.confidence == 1.0


@respx.mock
async def test_get_parcel_by_xy_sends_correct_params() -> None:
    respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(200, text=SAMPLE_XY_RESPONSE)
    )

    await get_parcel_by_xy(SAMPLE_X, SAMPLE_Y)
    params = respx.calls.last.request.url.params

    assert params["request"] == "GetParcelByXY"
    assert params["xy"] == f"{SAMPLE_X},{SAMPLE_Y},2180"
    assert params["result"] == "id,geom_wkt,teryt"


@respx.mock
async def test_get_parcel_by_xy_not_found_raises_parcel_not_found() -> None:
    respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(200, text=NOT_FOUND_XY_RESPONSE)
    )

    with pytest.raises(ParcelNotFoundError):
        await get_parcel_by_xy(SAMPLE_X, SAMPLE_Y)


@respx.mock
async def test_get_parcel_by_xy_uldk_error_raises_service_unavailable() -> None:
    respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(200, text=ULDK_ERROR_RESPONSE)
    )

    with pytest.raises(UldkServiceUnavailableError):
        await get_parcel_by_xy(SAMPLE_X, SAMPLE_Y)


@respx.mock
async def test_get_parcel_by_xy_http_500_raises_service_unavailable() -> None:
    respx.get(ULDK_BASE_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(UldkServiceUnavailableError):
        await get_parcel_by_xy(SAMPLE_X, SAMPLE_Y)


@respx.mock
async def test_get_parcel_by_xy_timeout_raises_service_unavailable() -> None:
    respx.get(ULDK_BASE_URL).mock(side_effect=httpx.TimeoutException("timeout"))

    with patch("app.services.uldk.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(UldkServiceUnavailableError):
            await get_parcel_by_xy(SAMPLE_X, SAMPLE_Y)


@respx.mock
async def test_get_parcel_by_xy_timeout_retries_max_retries_plus_one_times() -> None:
    route = respx.get(ULDK_BASE_URL).mock(side_effect=httpx.TimeoutException("timeout"))

    with patch("app.services.uldk.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(UldkServiceUnavailableError):
            await get_parcel_by_xy(SAMPLE_X, SAMPLE_Y)

    assert route.call_count == ULDK_MAX_RETRIES + 1
    assert mock_sleep.call_count == ULDK_MAX_RETRIES


@respx.mock
async def test_get_parcel_by_xy_retries_then_succeeds() -> None:
    route = respx.get(ULDK_BASE_URL).mock(
        side_effect=[
            httpx.TimeoutException("timeout"),
            httpx.Response(200, text=SAMPLE_XY_RESPONSE),
        ]
    )

    with patch("app.services.uldk.asyncio.sleep", new_callable=AsyncMock):
        result = await get_parcel_by_xy(SAMPLE_X, SAMPLE_Y)

    assert route.call_count == 2
    assert isinstance(result, ParcelLookupResult)


@respx.mock
async def test_get_parcel_by_xy_http_500_retries_then_succeeds() -> None:
    route = respx.get(ULDK_BASE_URL).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, text=SAMPLE_XY_RESPONSE),
        ]
    )

    with patch("app.services.uldk.asyncio.sleep", new_callable=AsyncMock):
        result = await get_parcel_by_xy(SAMPLE_X, SAMPLE_Y)

    assert route.call_count == 2
    assert isinstance(result, ParcelLookupResult)


@respx.mock
async def test_get_parcel_by_id_retries_on_timeout_then_succeeds() -> None:
    route = respx.get(ULDK_BASE_URL).mock(
        side_effect=[
            httpx.TimeoutException("timeout"),
            httpx.Response(200, text=SAMPLE_RESPONSE),
        ]
    )

    with patch("app.services.uldk.asyncio.sleep", new_callable=AsyncMock):
        result = await get_parcel_by_id(VALID_ID)

    assert route.call_count == 2
    assert result.parcel_identifier == VALID_ID


# --- AU-002: klasyfikacja kodu statusu, ponowienie po „-1 brak wyników”, metryki ----------------

FROZEN = Path(__file__).parent / "fixtures" / "source_contracts" / "uldk"
NO_RESULTS = "-1 brak wyników\nbłędny format odpowiedzi XML\n"


def _frozen(name: str) -> str:
    return (FROZEN / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _fresh_metrics() -> None:
    upstream_metrics.reset()


@pytest.fixture
def sleep_mock():
    with patch("app.services.uldk.asyncio.sleep", new_callable=AsyncMock) as mock:
        yield mock


_ENTRY_POINTS = [
    pytest.param(lambda: get_parcel_by_id("141801_4.0701.9999/9"), id="by_id"),
    pytest.param(lambda: get_parcel_by_xy(467946.0, 781952.0), id="by_xy"),
]


@pytest.mark.parametrize("call", _ENTRY_POINTS)
@pytest.mark.parametrize(
    ("fixture", "expected", "calls"),
    [
        ("brak_wynikow_baltyk.txt", ParcelNotFoundError, 2),
        ("brak_wynikow_nieistniejaca_dzialka.txt", ParcelNotFoundError, 2),
        ("zero_bez_danych.txt", ParcelNotFoundError, 1),
        ("odpowiedz_pusta.txt", InvalidUldkResponseError, 1),
        ("niepoprawny_parametr.txt", InvalidUldkResponseError, 1),
        ("blad_z_komunikatem.txt", UldkServiceUnavailableError, 1),
    ],
)
@respx.mock
async def test_frozen_uldk_responses_are_classified(
    call, fixture: str, expected: type[Exception], calls: int, sleep_mock
) -> None:
    """Rzeczywiste odpowiedzi ULDK (patrz fixtures/source_contracts/uldk/README.md)."""
    route = respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(200, text=_frozen(fixture))
    )

    with pytest.raises(expected) as raised:
        await call()

    assert type(raised.value) is not UldkNoResultsError  # ``-1`` wychodzi jako zwykłe 404
    assert route.call_count == calls
    if fixture.startswith("brak_wynikow"):
        # Jedno ponowienie po ~300 ms: brak działki nie da się odróżnić od przejściowego błędu.
        sleep_mock.assert_awaited_once_with(ULDK_NO_RESULTS_RETRY_DELAY_S)
        assert str(raised.value) == ULDK_NOT_FOUND_MESSAGE
    else:
        sleep_mock.assert_not_awaited()


@respx.mock
async def test_frozen_valid_response_returns_the_parcel() -> None:
    respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(200, text=_frozen("poprawna_146510_8.0502.1_3.txt"))
    )

    result = await get_parcel_by_id("146510_8.0502.1/3")

    assert result.parcel_identifier == "146510_8.0502.1/3"
    assert result.geometry_wkt.startswith("POLYGON((")


def test_not_found_message_is_honest_about_a_possible_transient_source_error() -> None:
    assert "brak działki" in ULDK_NOT_FOUND_MESSAGE
    assert "chwilowy błąd źródła" in ULDK_NOT_FOUND_MESSAGE


@respx.mock
async def test_transient_no_results_is_recovered_by_the_single_retry(sleep_mock) -> None:
    """Zaobserwowane dla istniejącej działki ``022104_2.0002.352``: pierwsze ``-1``, drugie poprawne."""
    route = respx.get(ULDK_BASE_URL).mock(
        side_effect=[
            httpx.Response(200, text=_frozen("brak_wynikow_nieistniejaca_dzialka.txt")),
            httpx.Response(200, text=SAMPLE_RESPONSE),
        ]
    )

    result = await get_parcel_by_id(VALID_ID)

    assert result.parcel_identifier == VALID_ID
    assert route.call_count == 2
    sleep_mock.assert_awaited_once_with(ULDK_NO_RESULTS_RETRY_DELAY_S)
    counters = upstream_metrics.snapshot()
    assert counters["uldk.no_results.retry"] == 1
    assert counters["uldk.no_results.recovered"] == 1
    assert "uldk.no_results.confirmed" not in counters


@respx.mock
async def test_no_results_is_retried_only_once(sleep_mock) -> None:
    route = respx.get(ULDK_BASE_URL).mock(return_value=httpx.Response(200, text=NO_RESULTS))

    with pytest.raises(ParcelNotFoundError):
        await get_parcel_by_id(VALID_ID)

    assert route.call_count == 2
    assert sleep_mock.await_count == 1


@respx.mock
async def test_other_minus_one_error_on_the_retry_is_service_unavailable_not_not_found(
    sleep_mock,
) -> None:
    respx.get(ULDK_BASE_URL).mock(
        side_effect=[
            httpx.Response(200, text=NO_RESULTS),
            httpx.Response(200, text=_frozen("blad_z_komunikatem.txt")),
        ]
    )

    with pytest.raises(UldkServiceUnavailableError) as raised:
        await get_parcel_by_id(VALID_ID)

    assert "błąd wewnętrzny usługi powiatowej" in str(raised.value)


@respx.mock
async def test_other_minus_one_error_is_not_retried(sleep_mock) -> None:
    route = respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(200, text="-1 przekroczono limit zapytań\n")
    )

    with pytest.raises(UldkServiceUnavailableError):
        await get_parcel_by_id(VALID_ID)

    assert route.call_count == 1
    sleep_mock.assert_not_awaited()


@pytest.mark.parametrize(
    "text",
    [
        "-1 brak wyników",
        "-1 brak wyników\nkomunikat usługi",
        "-1   BRAK WYNIKÓW  \n",
        "\ufeff-1 brak wyników\n",
        "-1\nbrak wyników\n",  # starsza forma: kod i komunikat w osobnych liniach
    ],
)
def test_no_results_is_recognised_in_every_known_shape(text: str) -> None:
    with pytest.raises(UldkNoResultsError) as raised:
        _parse_uldk_response(text, "punkt")

    assert isinstance(raised.value, ParcelNotFoundError)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0\n122101_1.0001.1234|POINT(1 2)|122101\n", InvalidUldkResponseError),
        ("-2 coś nowego\n", InvalidUldkResponseError),
        ("<!DOCTYPE html>\n<html>", InvalidUldkResponseError),
        ("", InvalidUldkResponseError),
        ("   \n\n", InvalidUldkResponseError),
        ("-1\n", UldkServiceUnavailableError),
    ],
)
def test_parser_classifies_malformed_and_unknown_codes(text: str, expected: type) -> None:
    with pytest.raises(expected):
        _parse_uldk_response(text, "punkt")


@respx.mock
async def test_metrics_count_responses_per_uldk_status_code(sleep_mock) -> None:
    respx.get(ULDK_BASE_URL).mock(
        side_effect=[
            httpx.Response(200, text=SAMPLE_RESPONSE),
            httpx.Response(200, text=NO_RESULTS),
            httpx.Response(200, text=NO_RESULTS),
            httpx.Response(200, text=_frozen("niepoprawny_parametr.txt")),
            httpx.Response(200, text=""),
        ]
    )

    await get_parcel_by_id(VALID_ID)
    with pytest.raises(ParcelNotFoundError):
        await get_parcel_by_id(VALID_ID)
    with pytest.raises(InvalidUldkResponseError):
        await get_parcel_by_id(VALID_ID)
    with pytest.raises(InvalidUldkResponseError):
        await get_parcel_by_id(VALID_ID)

    counters = upstream_metrics.snapshot()
    assert counters["uldk.response.0"] == 1
    assert counters["uldk.response.-1"] == 2
    # Tekst zamiast kodu nie może tworzyć nowej etykiety (ograniczona kardynalność).
    assert counters["uldk.response.other"] == 1
    assert counters["uldk.response.empty"] == 1
    assert counters["uldk.no_results.retry"] == 1
    assert counters["uldk.no_results.confirmed"] == 1
    assert all(name.startswith("uldk.") for name in counters)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("odmowa połączenia"),
        httpx.ReadError("zerwane połączenie"),
        httpx.RemoteProtocolError("niepoprawna odpowiedź"),
    ],
)
@respx.mock
async def test_transport_errors_are_service_unavailable_not_a_raw_httpx_exception(
    error: Exception, sleep_mock
) -> None:
    route = respx.get(ULDK_BASE_URL).mock(side_effect=error)

    with pytest.raises(UldkServiceUnavailableError):
        await get_parcel_by_xy(SAMPLE_X, SAMPLE_Y)

    assert route.call_count == ULDK_MAX_RETRIES + 1
    assert upstream_metrics.snapshot()["uldk.transport_error"] == ULDK_MAX_RETRIES + 1


@respx.mock
async def test_http_4xx_is_not_retried(sleep_mock) -> None:
    route = respx.get(ULDK_BASE_URL).mock(return_value=httpx.Response(400))

    with pytest.raises(UldkServiceUnavailableError):
        await get_parcel_by_id(VALID_ID)

    assert route.call_count == 1
    assert upstream_metrics.snapshot() == {"uldk.http_4xx": 1}
