from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.analyze import (
    AddressAnalyzeRequest,
    MapAnalyzeRequest,
    ParcelIdAnalyzeRequest,
    SourceMetadata,
)
from app.services.geometry import to_puwg1992
from app.services.uldk import (
    ParcelLookupResult,
    ULDK_BASE_URL,
    get_parcel_by_id,
    get_parcel_by_xy,
)


class AddressNotFoundError(Exception):
    """Podany adres nie zwrócił żadnych wyników geokodowania UUG."""


async def resolve_parcel(
    payload: MapAnalyzeRequest | AddressAnalyzeRequest | ParcelIdAnalyzeRequest,
) -> ParcelLookupResult:
    """
    Normalizuje trzy ścieżki wejścia użytkownika do jednego ParcelLookupResult.

    Routing jest deterministyczny i oparty wyłącznie o isinstance(), bez heurystyk
    ani interpretowania tekstowych pól metody. Ścieżki mapy i adresu używają
    jawnie wybranych współrzędnych (WGS84) i mogą propagować
    CoordinatesOutsidePolandError, ParcelNotFoundError i UldkServiceUnavailableError.
    Ścieżka identyfikatora może propagować InvalidParcelIdentifierError,
    ParcelNotFoundError i UldkServiceUnavailableError.

    Analiza adresowa NIE geokoduje ponownie tekstu zapytania i nigdy nie wybiera
    po cichu pierwszej sugestii — wymaga współrzędnych jawnie wybranego wyniku.
    """
    if isinstance(payload, MapAnalyzeRequest):
        x, y = to_puwg1992(payload.lon, payload.lat)
        return await get_parcel_by_xy(x, y)

    if isinstance(payload, AddressAnalyzeRequest):
        # Analiza używa DOKŁADNIE wybranej przez użytkownika sugestii — nie
        # geokoduje ponownie tekstu ani nie wybiera po cichu pierwszego wyniku.
        # Współrzędne wybranej sugestii są w WGS84 i tu przeliczane do EPSG:2180.
        x, y = to_puwg1992(payload.selected_lon, payload.selected_lat)
        return await get_parcel_by_xy(x, y)

    if isinstance(payload, ParcelIdAnalyzeRequest):
        raw = await get_parcel_by_id(payload.parcel_identifier)
        source = SourceMetadata(
            source_id="uldk",
            source_name="ULDK",
            source_url=ULDK_BASE_URL,
            fetched_at=datetime.now(timezone.utc),
            confidence=1.0,
            manual_review_required=False,
        )
        return ParcelLookupResult(
            parcel_identifier=raw.parcel_identifier,
            wkt=raw.geometry_wkt,
            teryt=raw.teryt,
            source_metadata=source,
        )

    raise TypeError(f"Nieobsługiwany typ żądania analizy: {type(payload)!r}")
