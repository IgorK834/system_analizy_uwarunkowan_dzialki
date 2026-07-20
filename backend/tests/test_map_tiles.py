from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.core.settings import Settings
from app.main import app
from app.routers.map_tiles import _load_tile_until_disconnect, get_wms_tile_proxy
from app.services.wms_tiles import (
    InvalidTileCoordinatesError,
    WmsTileProxy,
    WmsTileUnavailableError,
    web_mercator_tile_bbox,
)

WMS_URL = "https://wms.example.test/kimpzp"
PNG = b"\x89PNG\r\n\x1a\nproxy-tile-fixture"


def _settings(cache_dir: Path, **updates: object) -> Settings:
    values: dict[str, object] = {
        "kimpzp_wms_base_url": WMS_URL,
        "kimpzp_wms_layers": "plany_granice,raster",
        "map_tile_cache_dir": str(cache_dir),
        "map_tile_cache_ttl_seconds": 3600,
        "map_tile_stale_ttl_seconds": 7200,
        "map_tile_browser_ttl_seconds": 300,
        "map_tile_cache_max_bytes": 1024 * 1024,
        "map_tile_upstream_connect_timeout_seconds": 0.5,
        "map_tile_upstream_read_timeout_seconds": 0.5,
        "map_tile_upstream_max_concurrency": 4,
        "map_tile_min_zoom": 11,
        "map_tile_max_zoom": 18,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def test_web_mercator_tile_bbox_matches_known_warsaw_tile() -> None:
    bbox = web_mercator_tile_bbox(12, 2287, 1348)

    assert bbox == pytest.approx(
        (
            2338361.5693001114,
            6838973.794731289,
            2348145.508920614,
            6848757.734351791,
        )
    )


@pytest.mark.parametrize(
    ("z", "x", "y"),
    [(-1, 0, 0), (1, -1, 0), (1, 0, -1), (1, 2, 0), (1, 0, 2)],
)
def test_web_mercator_tile_bbox_rejects_invalid_coordinates(
    z: int, x: int, y: int
) -> None:
    with pytest.raises(InvalidTileCoordinatesError):
        web_mercator_tile_bbox(z, x, y)


@pytest.mark.asyncio
@respx.mock
async def test_proxy_fetches_png_once_and_then_uses_disk_cache(tmp_path: Path) -> None:
    route = respx.get(WMS_URL).mock(
        return_value=httpx.Response(
            200,
            content=PNG,
            headers={"content-type": "image/png"},
        )
    )
    proxy = WmsTileProxy(_settings(tmp_path))

    first = await proxy.get_tile(12, 2287, 1348)
    second = await proxy.get_tile(12, 2287, 1348)

    assert first.content == PNG
    assert first.cache_status == "MISS"
    assert second.cache_status == "HIT"
    assert second.etag == first.etag
    assert route.call_count == 1
    request = route.calls[0].request
    assert request.url.params["request"] == "GetMap"
    assert request.url.params["layers"] == "plany_granice,raster"
    assert request.url.params["srs"] == "EPSG:3857"
    assert request.url.params["width"] == "256"
    assert len(list(tmp_path.rglob("*.png"))) == 1

    await proxy.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_concurrent_misses_are_coalesced_to_one_upstream_request(
    tmp_path: Path,
) -> None:
    async def delayed_response(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.03)
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    route = respx.get(WMS_URL).mock(side_effect=delayed_response)
    proxy = WmsTileProxy(_settings(tmp_path))

    results = await asyncio.gather(
        *(proxy.get_tile(12, 2287, 1348) for _ in range(20))
    )

    assert route.call_count == 1
    assert sum(result.cache_status == "MISS" for result in results) == 1
    assert sum(result.cache_status == "HIT" for result in results) == 19
    await proxy.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_stale_tile_is_returned_immediately_and_survives_refresh_failure(
    tmp_path: Path,
) -> None:
    route = respx.get(WMS_URL).mock(
        side_effect=[
            httpx.Response(200, content=PNG, headers={"content-type": "image/png"}),
            httpx.TimeoutException("timeout"),
            httpx.TimeoutException("timeout"),
        ]
    )
    proxy = WmsTileProxy(
        _settings(
            tmp_path,
            map_tile_cache_ttl_seconds=1,
            map_tile_stale_ttl_seconds=60,
        )
    )
    await proxy.get_tile(12, 2287, 1348)
    cache_file = next(tmp_path.rglob("*.png"))
    stale_time = time.time() - 5
    os.utime(cache_file, (stale_time, stale_time))

    stale = await proxy.get_tile(12, 2287, 1348)
    await proxy.wait_for_background_tasks()

    assert stale.content == PNG
    assert stale.cache_status == "STALE"
    assert stale.age_seconds >= 4
    assert cache_file.read_bytes() == PNG
    assert route.call_count == 3
    await proxy.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_proxy_rejects_xml_or_non_png_upstream_response(tmp_path: Path) -> None:
    respx.get(WMS_URL).mock(
        return_value=httpx.Response(
            200,
            text="<ServiceException>bad bbox</ServiceException>",
            headers={"content-type": "application/vnd.ogc.se_xml"},
        )
    )
    proxy = WmsTileProxy(_settings(tmp_path))

    with pytest.raises(WmsTileUnavailableError):
        await proxy.get_tile(12, 2287, 1348)

    assert not list(tmp_path.rglob("*.png"))
    await proxy.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_http_endpoint_exposes_cache_headers_and_supports_etag(
    tmp_path: Path,
) -> None:
    route = respx.get(WMS_URL).mock(
        return_value=httpx.Response(
            200,
            content=PNG,
            headers={"content-type": "image/png"},
        )
    )
    proxy = WmsTileProxy(_settings(tmp_path))
    app.dependency_overrides[get_wms_tile_proxy] = lambda: proxy
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            first = await client.get("/api/v1/map/tiles/mpzp/12/2287/1348.png")
            unchanged = await client.get(
                "/api/v1/map/tiles/mpzp/12/2287/1348.png",
                headers={"If-None-Match": first.headers["etag"]},
            )
    finally:
        app.dependency_overrides.clear()
        await proxy.aclose()

    assert first.status_code == 200
    assert first.content == PNG
    assert first.headers["content-type"] == "image/png"
    assert first.headers["x-tile-cache"] == "MISS"
    assert "max-age=300" in first.headers["cache-control"]
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["etag"] == first.headers["etag"]
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_http_endpoint_rejects_zoom_outside_allowlist(tmp_path: Path) -> None:
    proxy = WmsTileProxy(_settings(tmp_path))
    app.dependency_overrides[get_wms_tile_proxy] = lambda: proxy
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/v1/map/tiles/mpzp/10/1/1.png")
    finally:
        app.dependency_overrides.clear()
        await proxy.aclose()

    assert response.status_code == 422
    assert "11–18" in response.json()["detail"]


@pytest.mark.asyncio
async def test_disconnected_map_client_cancels_pending_tile_work() -> None:
    cancelled = asyncio.Event()

    async def slow_tile(*_: int) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=True))
    proxy = SimpleNamespace(get_tile=slow_tile)

    result = await _load_tile_until_disconnect(request, proxy, 12, 2287, 1348)

    assert result is None
    assert cancelled.is_set()
