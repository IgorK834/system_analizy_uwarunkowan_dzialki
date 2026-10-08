"""Endpoint wyszukiwania adresów: GET /api/v1/search/addresses.

Waliduje parametry przed zapytaniem zewnętrznym, stosuje limit zapytań (429 +
Retry-After) i zwraca błędy publiczne przez wspólny kontrakt ErrorResponse, bez
stack trace i szczegółów kontraktu dostawcy.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.core.rate_limit import rate_limit
from app.core.settings import settings
from app.modules.location.api.schemas import (
    AddressIndexStatusResponse,
    AddressSearchResponse,
    to_result_dto,
)
from app.modules.location.application.ports import AddressSearchProviderError
from app.modules.location.composition import (
    address_source_info,
    build_address_index_provider,
    build_address_search_service,
)
from app.modules.location.domain.models import (
    BBox,
    GeoPoint,
    AddressQuery,
    ResultType,
)
from app.schemas.analyze import ErrorResponse

router = APIRouter(prefix="/api/v1/search", tags=["search"])

# Wspólny limiter (AU-006): ten sam klucz klienta i odpowiedź 429 z ``Retry-After``
# co w pozostałych routerach; progi z ``settings``.
_address_search_limit = rate_limit(settings.rate_limit_address_search_per_minute)
_address_status_limit = rate_limit(settings.rate_limit_data_per_minute)


def _error(status_code: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(error=code, detail=detail, section="search").model_dump(),
    )


def _parse_bbox(raw: str | None) -> BBox | None:
    if raw is None:
        return None
    parts = raw.split(",")
    if len(parts) != 4:
        raise ValueError("bbox musi mieć format min_lon,min_lat,max_lon,max_lat.")
    try:
        min_lon, min_lat, max_lon, max_lat = (float(part) for part in parts)
    except ValueError as exc:
        raise ValueError("bbox zawiera wartości nieliczbowe.") from exc
    if min_lon > max_lon or min_lat > max_lat:
        raise ValueError("bbox ma odwróconą kolejność współrzędnych.")
    return BBox(min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat)


def _parse_types(raw: str | None) -> frozenset[ResultType]:
    if not raw:
        return frozenset()
    allowed = {result_type.value: result_type for result_type in ResultType}
    selected: set[ResultType] = set()
    for token in raw.split(","):
        key = token.strip().lower()
        if not key:
            continue
        if key not in allowed:
            raise ValueError(f"Nieznany typ wyniku: {token!r}.")
        selected.add(allowed[key])
    return frozenset(selected)


@router.get(
    "/addresses",
    response_model=AddressSearchResponse,
    dependencies=[Depends(_address_search_limit)],
    responses={
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    description=(
        "Wyszukiwarka adresów (EMUiA/UUG). Zwraca wyniki z punktem GeoJSON w "
        "WGS84, zakresami dopasowania w tekście, częściami adresu i źródłem. "
        "Wynik jest deterministycznie rankowany z uwzględnieniem bias point i bbox."
    ),
)
async def search_addresses(
    q: str = Query(min_length=3, max_length=200, description="Zapytanie adresowe."),
    limit: int = Query(10, ge=1, le=20, description="Maksymalna liczba wyników 1-20."),
    bias_lon: float | None = Query(None, ge=-180.0, le=180.0),
    bias_lat: float | None = Query(None, ge=-90.0, le=90.0),
    bbox: str | None = Query(
        None, description="min_lon,min_lat,max_lon,max_lat w WGS84."
    ),
    types: str | None = Query(
        None,
        description=(
            "Filtr typów: country,voivodeship,county,municipality,city,street,"
            "house_number (lista rozdzielona przecinkami)."
        ),
    ),
) -> JSONResponse | AddressSearchResponse:
    try:
        parsed_bbox = _parse_bbox(bbox)
        parsed_types = _parse_types(types)
        bias = None
        if bias_lon is not None and bias_lat is not None:
            bias = GeoPoint(lon=bias_lon, lat=bias_lat)
        query = AddressQuery(
            q=q, limit=limit, bias=bias, bbox=parsed_bbox, type_filter=parsed_types
        )
    except ValueError as exc:
        return _error(422, "VALIDATION_ERROR", str(exc))

    service = build_address_search_service()
    try:
        ranked = await service.search(query)
    except AddressSearchProviderError:
        return _error(
            503,
            "SOURCE_UNAVAILABLE",
            "Usługa wyszukiwania adresów jest chwilowo niedostępna.",
        )

    default_source = address_source_info()
    return AddressSearchResponse(
        query=q.strip(),
        results=[
            to_result_dto(
                item,
                address_source_info(item.source_id)
                if item.source_id
                else default_source,
            )
            for item in ranked
        ],
        total_returned=len(ranked),
    )


@router.get(
    "/addresses/status",
    response_model=AddressIndexStatusResponse,
    dependencies=[Depends(_address_status_limit)],
    responses={429: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    description="Gotowość i wersja lokalnego indeksu adresowego GUGiK.",
)
async def address_index_status() -> JSONResponse | AddressIndexStatusResponse:
    try:
        status = await build_address_index_provider().status()
    except AddressSearchProviderError:
        return _error(
            503,
            "INDEX_UNAVAILABLE",
            "Nie udało się sprawdzić lokalnego indeksu adresowego.",
        )
    return AddressIndexStatusResponse(
        ready=status.ready,
        source=address_source_info("prg_address_dictionary"),
        release_id=status.release_id,
        version_label=status.version_label,
        published_at=status.published_at,
        entry_count=status.entry_count,
        scopes=list(status.scopes),
    )
