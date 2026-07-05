from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.analyze import (
    AddressAnalyzeRequest,
    MapAnalyzeRequest,
    ParcelIdAnalyzeRequest,
    SourceMetadata,
)
from app.services.geocoding import GeocodeSuggestion, GeocodingServiceUnavailableError
from app.services.geometry import CoordinatesOutsidePolandError
from app.services.initiation import AddressNotFoundError, resolve_parcel
from app.services.uldk import (
    InvalidParcelIdentifierError,
    ParcelLookupResult,
    ParcelNotFoundError,
    ULDK_BASE_URL,
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


def _suggestion(x: float = 644234.29, y: float = 499514.03) -> GeocodeSuggestion:
    return GeocodeSuggestion(
        label="Marki, Andersa 1",
        x=x,
        y=y,
        confidence=0.9,
        teryt="143402",
        city="Marki",
        street="Andersa",
        number="1",
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


async def test_resolve_parcel_address_calls_geocode_then_get_parcel_by_xy(
    mock_result: ParcelLookupResult,
) -> None:
    payload = AddressAnalyzeRequest(method="address", query="Marki, Andersa 1")

    with (
        patch(
            "app.services.initiation.geocode_address", new_callable=AsyncMock
        ) as mock_geocode,
        patch(
            "app.services.initiation.get_parcel_by_xy", new_callable=AsyncMock
        ) as mock_get_by_xy,
    ):
        mock_geocode.return_value = [_suggestion()]
        mock_get_by_xy.return_value = mock_result

        result = await resolve_parcel(payload)

    mock_geocode.assert_awaited_once_with("Marki, Andersa 1")
    mock_get_by_xy.assert_awaited_once_with(644234.29, 499514.03)
    assert result == mock_result


async def test_resolve_parcel_address_uses_first_suggestion(
    mock_result: ParcelLookupResult,
) -> None:
    payload = AddressAnalyzeRequest(method="address", query="Marki, Andersa 1")

    with (
        patch(
            "app.services.initiation.geocode_address", new_callable=AsyncMock
        ) as mock_geocode,
        patch(
            "app.services.initiation.get_parcel_by_xy", new_callable=AsyncMock
        ) as mock_get_by_xy,
    ):
        mock_geocode.return_value = [_suggestion(1.0, 2.0), _suggestion(3.0, 4.0)]
        mock_get_by_xy.return_value = mock_result

        await resolve_parcel(payload)

    mock_get_by_xy.assert_awaited_once_with(1.0, 2.0)


async def test_resolve_parcel_address_empty_geocoding_raises_address_not_found() -> (
    None
):
    payload = AddressAnalyzeRequest(method="address", query="Nieistniejące miejsce 999")

    with patch(
        "app.services.initiation.geocode_address", new_callable=AsyncMock
    ) as mock_geocode:
        mock_geocode.return_value = []

        with pytest.raises(AddressNotFoundError):
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


async def test_resolve_parcel_address_propagates_geocoding_unavailable() -> None:
    payload = AddressAnalyzeRequest(method="address", query="Marki, Andersa 1")

    with patch(
        "app.services.initiation.geocode_address",
        new_callable=AsyncMock,
        side_effect=GeocodingServiceUnavailableError("Timeout"),
    ):
        with pytest.raises(GeocodingServiceUnavailableError):
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

    with (
        patch(
            "app.services.initiation.geocode_address", new_callable=AsyncMock
        ) as mock_geocode,
        patch(
            "app.services.initiation.get_parcel_by_xy", new_callable=AsyncMock
        ) as mock_get_by_xy,
    ):
        mock_geocode.return_value = [_suggestion()]
        mock_get_by_xy.return_value = mock_result
        address_result = await resolve_parcel(
            AddressAnalyzeRequest(method="address", query="Marki, Andersa 1")
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
