from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from app.schemas.analyze import (
    AddressAnalyzeRequest,
    MapAnalyzeRequest,
    ParcelIdAnalyzeRequest,
    SourceMetadata,
)
from app.services.geometry import CoordinatesOutsidePolandError
from app.services.initiation import resolve_parcel
from app.services.uldk import (
    InvalidParcelIdentifierError,
    ParcelLookupResult,
    ParcelNotFoundError,
    ULDK_BASE_URL,
    ULDK_NOT_FOUND_MESSAGE,
    UldkParcelResult,
    UldkServiceUnavailableError,
)

MOCK_WKT = "MULTIPOLYGON(((500000 200000,500100 200000,500100 200100,500000 200000)))"


@pytest.fixture
def mock_result() -> ParcelLookupResult:
    return ParcelLookupResult(
        parcel_identifier="122101_1.0001.1234",
        wkt=MOCK_WKT,
        teryt="122101",
        source_metadata=SourceMetadata(
            source_name="ULDK",
            source_url=ULDK_BASE_URL,
            fetched_at=datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc),
            confidence=1.0,
            manual_review_required=False,
        ),
    )


async def test_resolve_parcel_map_calls_to_puwg1992_and_get_parcel_by_xy(
    mock_result: ParcelLookupResult,
) -> None:
    payload = MapAnalyzeRequest(method="map", lon=19.01, lat=49.86)

    with (
        patch(
            "app.services.initiation.to_puwg1992", return_value=(500000.0, 200000.0)
        ) as mock_to_puwg,
        patch(
            "app.services.initiation.get_parcel_by_xy", new_callable=AsyncMock
        ) as mock_get_by_xy,
    ):
        mock_get_by_xy.return_value = mock_result

        result = await resolve_parcel(payload)

    mock_to_puwg.assert_called_once_with(19.01, 49.86)
    mock_get_by_xy.assert_awaited_once_with(500000.0, 200000.0)
    assert result == mock_result


async def test_resolve_parcel_address_uses_selected_coordinates(
    mock_result: ParcelLookupResult,
) -> None:
    # Analiza adresowa używa DOKŁADNIE wybranych współrzędnych sugestii (WGS84),
    # bez ponownego geokodowania i bez cichego wyboru pierwszego wyniku.
    payload = AddressAnalyzeRequest(
        method="address",
        query="Marki, Andersa 1",
        selected_lon=21.105,
        selected_lat=52.32,
    )

    with (
        patch(
            "app.services.initiation.to_puwg1992", return_value=(644234.29, 499514.03)
        ) as mock_to_puwg,
        patch(
            "app.services.initiation.get_parcel_by_xy", new_callable=AsyncMock
        ) as mock_get_by_xy,
    ):
        mock_get_by_xy.return_value = mock_result

        result = await resolve_parcel(payload)

    mock_to_puwg.assert_called_once_with(21.105, 52.32)
    mock_get_by_xy.assert_awaited_once_with(644234.29, 499514.03)
    assert result == mock_result


async def test_resolve_parcel_address_does_not_regeocode() -> None:
    # initiation nie może w ogóle importować geokodera — dowód, że nie ma ścieżki
    # ponownego geokodowania ani wyboru suggestions[0].
    import app.services.initiation as initiation_module

    assert not hasattr(initiation_module, "geocode_address")


async def test_resolve_parcel_address_propagates_outside_poland_error() -> None:
    payload = AddressAnalyzeRequest(
        method="address", query="Adres", selected_lon=13.0, selected_lat=50.0
    )

    with patch(
        "app.services.initiation.to_puwg1992",
        side_effect=CoordinatesOutsidePolandError("Poza granicami"),
    ):
        with pytest.raises(CoordinatesOutsidePolandError):
            await resolve_parcel(payload)


async def test_resolve_parcel_parcel_id_calls_get_parcel_by_id() -> None:
    payload = ParcelIdAnalyzeRequest(
        method="parcel_id", parcel_identifier="122101_1.0001.1"
    )
    raw = UldkParcelResult(
        parcel_identifier="122101_1.0001.1",
        geometry_wkt=MOCK_WKT,
        teryt="122101",
    )

    with patch(
        "app.services.initiation.get_parcel_by_id", new_callable=AsyncMock
    ) as mock_get_by_id:
        mock_get_by_id.return_value = raw

        result = await resolve_parcel(payload)

    mock_get_by_id.assert_awaited_once_with("122101_1.0001.1")
    assert isinstance(result, ParcelLookupResult)
    assert result.parcel_identifier == "122101_1.0001.1"
    assert result.source_metadata.source_name == "ULDK"


async def test_resolve_parcel_parcel_id_wraps_result_with_source_metadata() -> None:
    payload = ParcelIdAnalyzeRequest(
        method="parcel_id", parcel_identifier="122101_1.0001.1"
    )
    raw = UldkParcelResult(
        parcel_identifier="122101_1.0001.1",
        geometry_wkt=MOCK_WKT,
        teryt="122101",
    )

    with patch(
        "app.services.initiation.get_parcel_by_id", new_callable=AsyncMock
    ) as mock_get_by_id:
        mock_get_by_id.return_value = raw

        result = await resolve_parcel(payload)

    assert result.source_metadata.confidence == 1.0
    assert result.source_metadata.manual_review_required is False
    assert result.source_metadata.source_url == ULDK_BASE_URL
    assert result.wkt == MOCK_WKT


async def test_resolve_parcel_map_propagates_coordinates_outside_poland_error() -> None:
    payload = MapAnalyzeRequest(method="map", lon=13.0, lat=50.0)

    with patch(
        "app.services.initiation.to_puwg1992",
        side_effect=CoordinatesOutsidePolandError("Poza granicami"),
    ):
        with pytest.raises(CoordinatesOutsidePolandError):
            await resolve_parcel(payload)


async def test_resolve_parcel_map_propagates_parcel_not_found() -> None:
    payload = MapAnalyzeRequest(method="map", lon=19.01, lat=49.86)

    with (
        patch("app.services.initiation.to_puwg1992", return_value=(500000.0, 200000.0)),
        patch(
            "app.services.initiation.get_parcel_by_xy",
            new_callable=AsyncMock,
            side_effect=ParcelNotFoundError("Brak"),
        ),
    ):
        with pytest.raises(ParcelNotFoundError):
            await resolve_parcel(payload)


async def test_resolve_parcel_map_propagates_uldk_unavailable() -> None:
    payload = MapAnalyzeRequest(method="map", lon=19.01, lat=49.86)

    with (
        patch("app.services.initiation.to_puwg1992", return_value=(500000.0, 200000.0)),
        patch(
            "app.services.initiation.get_parcel_by_xy",
            new_callable=AsyncMock,
            side_effect=UldkServiceUnavailableError("Timeout"),
        ),
    ):
        with pytest.raises(UldkServiceUnavailableError):
            await resolve_parcel(payload)


async def test_resolve_parcel_parcel_id_propagates_parcel_not_found() -> None:
    payload = ParcelIdAnalyzeRequest(
        method="parcel_id", parcel_identifier="122101_1.0001.1"
    )

    with patch(
        "app.services.initiation.get_parcel_by_id",
        new_callable=AsyncMock,
        side_effect=ParcelNotFoundError("Brak"),
    ):
        with pytest.raises(ParcelNotFoundError):
            await resolve_parcel(payload)


async def test_resolve_parcel_parcel_id_propagates_invalid_identifier() -> None:
    payload = ParcelIdAnalyzeRequest(
        method="parcel_id", parcel_identifier="122101_1.0001.1"
    )

    with patch(
        "app.services.initiation.get_parcel_by_id",
        new_callable=AsyncMock,
        side_effect=InvalidParcelIdentifierError("Zły format"),
    ):
        with pytest.raises(InvalidParcelIdentifierError):
            await resolve_parcel(payload)


async def test_resolve_parcel_returns_parcel_lookup_result_for_all_methods(
    mock_result: ParcelLookupResult,
) -> None:
    with (
        patch("app.services.initiation.to_puwg1992", return_value=(500000.0, 200000.0)),
        patch(
            "app.services.initiation.get_parcel_by_xy", new_callable=AsyncMock
        ) as mock_get_by_xy,
    ):
        mock_get_by_xy.return_value = mock_result
        map_result = await resolve_parcel(
            MapAnalyzeRequest(method="map", lon=19.01, lat=49.86)
        )
        address_result = await resolve_parcel(
            AddressAnalyzeRequest(
                method="address",
                query="Marki, Andersa 1",
                selected_lon=21.1,
                selected_lat=52.3,
            )
        )

    with patch(
        "app.services.initiation.get_parcel_by_id", new_callable=AsyncMock
    ) as mock_get_by_id:
        mock_get_by_id.return_value = UldkParcelResult(
            parcel_identifier="122101_1.0001.1",
            geometry_wkt=MOCK_WKT,
            teryt="122101",
        )
        parcel_id_result = await resolve_parcel(
            ParcelIdAnalyzeRequest(
                method="parcel_id", parcel_identifier="122101_1.0001.1"
            )
        )

    assert isinstance(map_result, ParcelLookupResult)
    assert isinstance(address_result, ParcelLookupResult)
    assert isinstance(parcel_id_result, ParcelLookupResult)


# --- AU-002: „-1 brak wyników” z ULDK to 404 z uczciwym komunikatem, nie wyjątek nieobsłużony ----

_FROZEN_ULDK = Path(__file__).parent / "fixtures" / "source_contracts" / "uldk"


@pytest.mark.parametrize(
    "payload",
    [
        MapAnalyzeRequest(method="map", lon=18.5, lat=54.9),  # kliknięcie w Bałtyk
        AddressAnalyzeRequest(
            method="address", query="Bałtyk", selected_lon=18.5, selected_lat=54.9
        ),
        ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier="141801_4.0701.9999/9"),
    ],
    ids=["map", "address", "parcel_id"],
)
@respx.mock
async def test_resolve_parcel_turns_uldk_no_results_into_parcel_not_found(payload) -> None:
    route = respx.get(ULDK_BASE_URL).mock(
        return_value=httpx.Response(
            200, text=(_FROZEN_ULDK / "brak_wynikow_baltyk.txt").read_text(encoding="utf-8")
        )
    )

    with patch("app.services.uldk.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(ParcelNotFoundError) as raised:
            await resolve_parcel(payload)

    assert str(raised.value) == ULDK_NOT_FOUND_MESSAGE
    assert route.call_count == 2  # jedno ponowienie przed uznaniem braku działki


@respx.mock
async def test_resolve_parcel_survives_a_transient_no_results_on_an_existing_parcel() -> None:
    respx.get(ULDK_BASE_URL).mock(
        side_effect=[
            httpx.Response(200, text="-1 brak wyników\n"),
            httpx.Response(
                200, text=f"0\n122101_1.0001.1234|SRID=2180;{MOCK_WKT}|122101\n"
            ),
        ]
    )

    with patch("app.services.uldk.asyncio.sleep", new_callable=AsyncMock):
        result = await resolve_parcel(
            ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier="122101_1.0001.1234")
        )

    assert result.parcel_identifier == "122101_1.0001.1234"
    assert result.source_metadata.source_url == ULDK_BASE_URL
