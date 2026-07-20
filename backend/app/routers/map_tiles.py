"""Publiczny, wersjonowany kontrakt rastrowych kafelków mapowych."""

import asyncio

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.services.wms_tiles import (
    InvalidTileCoordinatesError,
    WmsTileProxy,
    WmsTileUnavailableError,
    wms_tile_proxy,
)

router = APIRouter(prefix="/api/v1/map/tiles", tags=["map-tiles"])


def get_wms_tile_proxy() -> WmsTileProxy:
    return wms_tile_proxy


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


@router.get(
    "/mpzp/{z}/{x}/{y}.png",
    responses={
        200: {"content": {"image/png": {}}},
        304: {"description": "Kafelek nie zmienił się od podanego ETag."},
        422: {"description": "Nieprawidłowe współrzędne kafelka."},
        503: {"description": "WMS i cache kafelków są niedostępne."},
    },
    description="Zwraca cache'owany kafelek MPZP z oficjalnej usługi KIMPZP.",
)
async def get_mpzp_tile(
    z: int,
    x: int,
    y: int,
    request: Request,
    proxy: Annotated[WmsTileProxy, Depends(get_wms_tile_proxy)],
) -> Response:
    try:
        result = await _load_tile_until_disconnect(request, proxy, z, x, y)
    except InvalidTileCoordinatesError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except WmsTileUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Nakładka MPZP jest chwilowo niedostępna.",
        ) from exc

    if result is None:
        # Nginx używa 499 dla przerwanego połączenia klienta. Odpowiedź zwykle
        # nie dotrze już do przeglądarki, ale kończy handler bez pracy upstream.
        return Response(status_code=499)

    headers = proxy.response_headers(result)
    if request.headers.get("if-none-match") == result.etag:
        return Response(status_code=304, headers=headers)
    return Response(content=result.content, media_type="image/png", headers=headers)
