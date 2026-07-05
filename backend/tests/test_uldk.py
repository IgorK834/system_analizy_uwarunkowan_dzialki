import httpx
import pytest
import respx

from app.services.uldk import (
    InvalidParcelIdentifierError,
    InvalidUldkResponseError,
    ParcelNotFoundError,
    ULDK_BASE_URL,
    UldkParcelResult,
    UldkServiceUnavailableError,
    get_parcel_by_id,
)


VALID_ID = "122101_1.0001.1234"
VALID_ID_WITH_SHEET = "122101_1.0001.AR_1.1234"
VALID_ID_WITH_FRACTION = "226301_5.0001.1234/5"
SAMPLE_WKT = (
    "MULTIPOLYGON(((500000 200000,500100 200000,"
    "500100 200100,500000 200000)))"
)
SAMPLE_WKT_EWKT = f"SRID=2180;{SAMPLE_WKT}"
SAMPLE_TERYT = "122101"
SAMPLE_RESPONSE = f"0\n{VALID_ID}|{SAMPLE_WKT}|{SAMPLE_TERYT}\n"
NOT_FOUND_RESPONSE = "0\n\n"
ULDK_ERROR_RESPONSE = "-1\nPodany identyfikator działki jest niepoprawny.\n"
MALFORMED_RESPONSE = "0\nno_pipe_separator\n"


@respx.mock
async def test_get_parcel_by_id_returns_uldk_parcel_result() -> None:
    respx.get(ULDK_BASE_URL).mock(return_value=httpx.Response(200, text=SAMPLE_RESPONSE))

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
        return_value=httpx.Response(200, text=f"0\n{VALID_ID}|POINT(500000 200000)|122101\n")
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
    respx.get(ULDK_BASE_URL).mock(return_value=httpx.Response(200, text=SAMPLE_RESPONSE))

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
