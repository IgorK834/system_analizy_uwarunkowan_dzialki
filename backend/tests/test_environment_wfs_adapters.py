from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from app.modules.analysis.infrastructure.environment_wfs import (
    GdosWfsAdapter,
    IsokWfsAdapter,
)
from app.shared.geometry import GeometryPayload

PARCEL = GeometryPayload(wkt="POLYGON((0 0,10 0,10 10,0 10,0 0))")


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_class", [IsokWfsAdapter, GdosWfsAdapter])
async def test_environment_wfs_adapter_converts_the_canonical_payload(adapter_class) -> None:
    expected = object()
    fetcher = AsyncMock(return_value=expected)
    async with httpx.AsyncClient() as client:
        adapter = adapter_class(client, fetcher=fetcher)
        result = await adapter.fetch(PARCEL)

    assert result is expected
    (geometry,) = fetcher.await_args.args
    assert geometry.wkt == "POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0))"
    assert fetcher.await_args.kwargs["client"] is client


@pytest.mark.asyncio
async def test_environment_wfs_adapter_rejects_a_non_metric_crs_before_io() -> None:
    fetcher = AsyncMock()
    async with httpx.AsyncClient() as client:
        adapter = IsokWfsAdapter(client, fetcher=fetcher)
        with pytest.raises(ValueError, match="EPSG:2180"):
            await adapter.fetch(GeometryPayload(wkt=PARCEL.wkt, crs="EPSG:4326"))

    fetcher.assert_not_awaited()
