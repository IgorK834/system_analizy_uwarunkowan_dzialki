"""Publiczny, wersjonowany kontrakt rastrowych kafelków mapowych."""

import asyncio

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from app.core.rate_limit import rate_limit
from app.core.settings import settings
from app.schemas.analyze import UtilitiesPreviewResult
from app.services.kiut_coverage import check_kiut_coverage_wgs84
from app.services.wms_tiles import (
    InvalidTileCoordinatesError,
    WmsTileProxy,
    WmsTilePreviewRegistry,
    WmsTileUnavailableError,
    wms_tile_registry,
)

router = APIRouter(prefix="/api/v1/map", tags=["map-tiles"])
PreviewSourceKey = Literal["mpzp", "pog", "kiut"]

_coverage_limit = rate_limit(settings.rate_limit_coverage_per_minute)


class PreviewSourceResponse(BaseModel):
    source_key: PreviewSourceKey
    label: str
    attribution: str
    min_zoom: int
    max_zoom: int
    tile_size: int
    tile_url_template: str
    legal_note: str
    info_url: str
    catalog_status: str


def get_wms_tile_registry() -> WmsTilePreviewRegistry:
    return wms_tile_registry


async def _load_tile_until_disconnect(
    request: Request,
    proxy: WmsTileProxy,
    z: int,
    x: int,
    y: int,
):
    """Anuluje kosztowny MISS, gdy MapLibre porzuci kafel podczas zoomowania."""

    tile_task = asyncio.create_task(proxy.get_tile(z, x, y))

    async def wait_for_disconnect() -> None:
        while not await request.is_disconnected():
            await asyncio.sleep(0.05)

    disconnect_task = asyncio.create_task(wait_for_disconnect())
    done, _ = await asyncio.wait(
        {tile_task, disconnect_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if tile_task in done:
        disconnect_task.cancel()
        await asyncio.gather(disconnect_task, return_exceptions=True)
        return await tile_task

    tile_task.cancel()
    await asyncio.gather(tile_task, return_exceptions=True)
    return None


@router.get("/preview-sources", response_model=list[PreviewSourceResponse])
async def get_preview_sources(
    registry: Annotated[WmsTilePreviewRegistry, Depends(get_wms_tile_registry)],
) -> list[PreviewSourceResponse]:
    """Zwraca bezpieczne metadane allowlisty podglądowych źródeł WMS."""

    return [
        PreviewSourceResponse(
            source_key=cast(PreviewSourceKey, source.source_key),
            label=source.label,
            attribution=source.attribution,
            min_zoom=source.min_zoom,
            max_zoom=source.max_zoom,
            tile_size=source.tile_size,
            tile_url_template=(
                f"/api/v1/map/tiles/{source.source_key}/{{z}}/{{x}}/{{y}}.png"
            ),
            legal_note=source.legal_note,
            info_url=source.info_url,
            catalog_status=source.catalog_status,
        )
        for source in registry.sources
    ]


@router.get(
    "/coverage/kiut",
    response_model=UtilitiesPreviewResult,
    dependencies=[Depends(_coverage_limit)],
    description=(
        "Sprawdza przez publiczną warstwę WMS gesut, czy powiat publikuje "
        "podgląd uzbrojenia. Wynik nie jest analizą obecności ani odległości sieci."
    ),
)
async def get_kiut_coverage(
    lon: Annotated[float, Query(ge=-180.0, le=180.0)],
    lat: Annotated[float, Query(ge=-90.0, le=90.0)],
) -> UtilitiesPreviewResult:
    return await check_kiut_coverage_wgs84(lon, lat)


@router.get(
    "/tiles/{source}/{z}/{x}/{y}.png",
    responses={
        200: {"content": {"image/png": {}}},
        304: {"description": "Kafelek nie zmienił się od podanego ETag."},
        422: {"description": "Nieprawidłowe współrzędne kafelka."},
        503: {"description": "WMS i cache kafelków są niedostępne."},
    },
    description="Zwraca cache'owany kafelek z wybranej oficjalnej usługi WMS.",
)
async def get_wms_tile(
    source: PreviewSourceKey,
    z: int,
    x: int,
    y: int,
    request: Request,
    registry: Annotated[WmsTilePreviewRegistry, Depends(get_wms_tile_registry)],
) -> Response:
    proxy = registry.get(source)
    try:
        result = await _load_tile_until_disconnect(request, proxy, z, x, y)
    except InvalidTileCoordinatesError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except WmsTileUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Nakładka {proxy.source.label} jest chwilowo niedostępna.",
        ) from exc

    if result is None:
        # Nginx używa 499 dla przerwanego połączenia klienta. Odpowiedź zwykle
        # nie dotrze już do przeglądarki, ale kończy handler bez pracy upstream.
        return Response(status_code=499)

    headers = proxy.response_headers(result)
    if request.headers.get("if-none-match") == result.etag:
        return Response(status_code=304, headers=headers)
    return Response(content=result.content, media_type="image/png", headers=headers)
