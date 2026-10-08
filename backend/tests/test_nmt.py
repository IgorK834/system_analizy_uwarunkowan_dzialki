"""Kontrakt usługi NMT GetMinMaxByPolygon na zamrożonych odpowiedziach (BK-301).

Sprawdza cztery rozłączne zachowania adaptera: pomiar, jawny brak pokrycia
(sentinel ``Hmin=2500``/``Hmax=0``), błąd zgłaszany z HTTP 200 oraz awarię
transportu — każde z provenance zapytania i bez „naprawiania” do zera.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
import shapely
from shapely.geometry import Polygon

from app.core.settings import settings
from app.services.nmt import (
    REASON_HTTP_ERROR,
    REASON_INVALID_RESPONSE,
    REASON_REPORTED_ERROR,
    REASON_TIMEOUT,
    NmtServiceUnavailableError,
    TerrainExtremes,
    TerrainNoCoverage,
    fetch_terrain_extremes,
)

FIXTURES = Path(__file__).parent / "fixtures" / "source_contracts"
WARSAW = Polygon.from_bounds(637000, 486000, 637100, 486100)


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _response_text(hmin: str, hmax: str, area: str = "10000") -> str:
    return (
        "Polygon:\tPOLYGON((0 0,1 0,1 1,0 1,0 0))\n"
        f"Polygon area:\t{area}\n"
        "Points count:\t676\n"
        "Grid size [m]:\t4\n"
        f"Hmin:\t{hmin}\n"
        f"Hmax:\t{hmax}\n"
    )


@pytest.mark.asyncio
@respx.mock
async def test_control_measurement_gives_3_4_m_with_provenance() -> None:
    route = respx.get(settings.nmt_base_url).mock(
        return_value=httpx.Response(200, text=_fixture("nmt_getminmaxbypolygon.txt"))
    )

    result = await fetch_terrain_extremes(WARSAW)

    assert isinstance(result, TerrainExtremes)
    assert (result.min_height_m, result.max_height_m) == (112.3, 115.7)
    assert result.height_difference_m == 3.4
    assert (result.grid_size_m, result.sampled_points) == (4.0, 676)
    source = result.source_metadata
    assert source.source_id == "nmt"
    assert source.source_name == "NMT"
    assert source.response_status == 200
    assert source.fetched_at is not None
    assert source.artifact_sha256 is not None and len(source.artifact_sha256) == 64
    assert source.manual_review_required is False
    assert "GetMinMaxByPolygon" in (source.source_url or "")
    # WKT jest wysyłany bez transformacji osi (easting, northing).
    assert route.calls.last.request.url.params["polygon"] == WARSAW.wkt


@pytest.mark.asyncio
@respx.mock
async def test_sentinel_is_explicit_no_coverage_not_minus_2500_or_zero() -> None:
    respx.get(settings.nmt_base_url).mock(
        return_value=httpx.Response(
            200, text=_fixture("nmt_getminmaxbypolygon_brak_pokrycia.txt")
        )
    )

    result = await fetch_terrain_extremes(
        Polygon.from_bounds(400000, 900000, 400100, 900100)
    )

    assert isinstance(result, TerrainNoCoverage)
    assert not hasattr(result, "height_difference_m")
    assert (result.grid_size_m, result.sampled_points) == (4.0, 676)
    assert result.source_metadata.response_status == 200
    assert result.source_metadata.manual_review_required is False
    assert "nie oznacza płaskiego terenu" in result.warnings[0]


@pytest.mark.asyncio
@respx.mock
async def test_real_zero_height_difference_is_available_measurement() -> None:
    respx.get(settings.nmt_base_url).mock(
        return_value=httpx.Response(200, text=_response_text("101.2", "101.2"))
    )

    result = await fetch_terrain_extremes(WARSAW)

    assert isinstance(result, TerrainExtremes)
    assert result.height_difference_m == 0.0


@pytest.mark.asyncio
@respx.mock
async def test_negative_heights_are_valid_measurements() -> None:
    respx.get(settings.nmt_base_url).mock(
        return_value=httpx.Response(200, text=_response_text("-1,8", "0,4"))
    )

    result = await fetch_terrain_extremes(WARSAW)

    assert isinstance(result, TerrainExtremes)
    assert (result.min_height_m, result.max_height_m) == (-1.8, 0.4)
    assert result.height_difference_m == 2.2


@pytest.mark.asyncio
@respx.mock
async def test_error_row_with_http_200_is_unavailable_not_zero() -> None:
    respx.get(settings.nmt_base_url).mock(
        return_value=httpx.Response(200, text="error\tNiepoprawny poligon\n")
    )

    with pytest.raises(NmtServiceUnavailableError) as raised:
        await fetch_terrain_extremes(WARSAW)

    assert raised.value.reason_code == REASON_REPORTED_ERROR
    source = raised.value.source_metadata
    assert source is not None
    assert source.response_status == 200
    assert source.confidence == 0.0
    assert source.manual_review_required is True
    assert source.artifact_sha256 is not None


@pytest.mark.asyncio
@respx.mock
async def test_timeout_keeps_attempt_provenance() -> None:
    respx.get(settings.nmt_base_url).mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(NmtServiceUnavailableError) as raised:
        await fetch_terrain_extremes(WARSAW)

    assert raised.value.reason_code == REASON_TIMEOUT
    source = raised.value.source_metadata
    assert source is not None
    assert source.fetched_at is not None
    assert source.response_status is None
    assert "GetMinMaxByPolygon" in (source.source_url or "")


@pytest.mark.asyncio
@respx.mock
async def test_http_error_status_is_recorded() -> None:
    respx.get(settings.nmt_base_url).mock(return_value=httpx.Response(503))

    with pytest.raises(NmtServiceUnavailableError) as raised:
        await fetch_terrain_extremes(WARSAW)

    assert raised.value.reason_code == REASON_HTTP_ERROR
    assert raised.value.source_metadata is not None
    assert raised.value.source_metadata.response_status == 503


@pytest.mark.asyncio
@respx.mock
async def test_transport_error_is_unavailable() -> None:
    respx.get(settings.nmt_base_url).mock(side_effect=httpx.ConnectError("dns"))

    with pytest.raises(NmtServiceUnavailableError) as raised:
        await fetch_terrain_extremes(WARSAW)

    assert raised.value.reason_code == REASON_HTTP_ERROR
    assert raised.value.source_metadata is not None


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "body",
    ["", "brak tabulatora\n", "Hmin:\t112.3\n", "Hmin:\tabc\nHmax:\t115\n"],
)
async def test_response_without_heights_is_invalid(body: str) -> None:
    respx.get(settings.nmt_base_url).mock(return_value=httpx.Response(200, text=body))

    with pytest.raises(NmtServiceUnavailableError) as raised:
        await fetch_terrain_extremes(WARSAW)

    assert raised.value.reason_code == REASON_INVALID_RESPONSE
    assert raised.value.source_metadata is not None


@pytest.mark.asyncio
@respx.mock
async def test_area_mismatch_lowers_confidence_and_requires_review() -> None:
    respx.get(settings.nmt_base_url).mock(
        return_value=httpx.Response(200, text=_response_text("112.3", "115.7", "5000"))
    )

    result = await fetch_terrain_extremes(WARSAW)

    assert isinstance(result, TerrainExtremes)
    assert result.source_metadata.manual_review_required is True
    assert result.source_metadata.confidence == 0.5
    assert "ręcznej weryfikacji" in result.warnings[0]


@pytest.mark.asyncio
@respx.mock
async def test_shared_client_is_used_when_provided() -> None:
    respx.get(settings.nmt_base_url).mock(
        return_value=httpx.Response(200, text=_fixture("nmt_getminmaxbypolygon.txt"))
    )

    async with httpx.AsyncClient() as client:
        result = await fetch_terrain_extremes(WARSAW, client=client)

    assert isinstance(result, TerrainExtremes)


# --- AU-001: provenance nie przenosi wielokąta działki ----------------------------------------

LONG_GEOMETRY = Path(__file__).parent / "fixtures" / "parcels" / "long_geometry"
REAL_PARCELS = [
    pytest.param(
        "real-001-146510-8-0502-1-3", 76, (109.1, 113.7), id="146510_8.0502.1/3-76-vertices"
    ),
    pytest.param(
        "real-005-126105-9-0001-580-4", 134, (209.4, 212.0), id="126105_9.0001.580/4-134-vertices"
    ),
]


def _real_parcel(case: str):
    wkt = (LONG_GEOMETRY / f"{case}.wkt").read_text(encoding="utf-8").strip()
    return shapely.from_wkt(wkt), wkt


@pytest.mark.asyncio
@pytest.mark.parametrize(("case", "vertices", "heights"), REAL_PARCELS)
@respx.mock
async def test_real_long_parcel_provenance_url_is_short_and_carries_polygon_hash_and_vertex_count(
    case: str, vertices: int, heights: tuple[float, float]
) -> None:
    geometry, wkt = _real_parcel(case)
    nmt_text = (LONG_GEOMETRY / f"{case}.nmt.txt").read_text(encoding="utf-8")
    route = respx.get(settings.nmt_base_url).mock(
        return_value=httpx.Response(200, text=nmt_text)
    )

    result = await fetch_terrain_extremes(geometry)

    assert isinstance(result, TerrainExtremes)
    # Usługa nadal dostaje pełny wielokąt — zmieniło się wyłącznie to, co zapisujemy jako źródło.
    sent_url = route.calls.last.request.url
    assert sent_url.params["polygon"] == wkt
    assert len(str(sent_url)) > 2500

    source_url = result.source_metadata.source_url
    assert source_url is not None and len(source_url) < 300
    assert source_url.startswith(settings.nmt_base_url + "?")
    assert "polygon=" not in source_url and "POLYGON" not in source_url
    parts = urlsplit(source_url)
    query = parse_qs(parts.query)
    assert query == {
        "request": ["GetMinMaxByPolygon"],
        "polygon_sha256": [hashlib.sha256(wkt.encode("utf-8")).hexdigest()],
        "vertex_count": [str(vertices)],
    }
    # Skrócenie adresu źródła nie zmienia pomiaru: wysokości pochodzą z odpowiedzi usługi.
    assert (result.min_height_m, result.max_height_m) == heights


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("dns"),
        httpx.Response(503),
    ],
    ids=["timeout", "transport", "http_503"],
)
@respx.mock
async def test_failed_attempt_provenance_url_is_short_too(outcome) -> None:
    geometry, _ = _real_parcel("real-005-126105-9-0001-580-4")
    route = respx.get(settings.nmt_base_url)
    if isinstance(outcome, Exception):
        route.mock(side_effect=outcome)
    else:
        route.mock(return_value=outcome)

    with pytest.raises(NmtServiceUnavailableError) as raised:
        await fetch_terrain_extremes(geometry)

    source = raised.value.source_metadata
    assert source is not None and source.source_url is not None
    assert len(source.source_url) < 300
    assert "vertex_count=134" in source.source_url
    # Komunikat błędu też nie przenosi wielokąta (komunikat httpx zawiera pełny adres).
    assert len(str(raised.value)) < 200
    assert "polygon" not in str(raised.value).lower()


@pytest.mark.asyncio
@respx.mock
async def test_provenance_hash_is_deterministic_and_geometry_specific() -> None:
    respx.get(settings.nmt_base_url).mock(
        return_value=httpx.Response(200, text=_response_text("112.3", "115.7"))
    )
    other = Polygon.from_bounds(637000, 486000, 637100, 486101)

    first = await fetch_terrain_extremes(WARSAW)
    again = await fetch_terrain_extremes(WARSAW)
    different = await fetch_terrain_extremes(other)

    assert first.source_metadata.source_url == again.source_metadata.source_url
    assert first.source_metadata.source_url != different.source_metadata.source_url


@pytest.mark.asyncio
@respx.mock
async def test_full_request_url_is_logged_only_at_debug_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    geometry, wkt = _real_parcel("real-001-146510-8-0502-1-3")
    respx.get(settings.nmt_base_url).mock(
        return_value=httpx.Response(200, text=_response_text("112.3", "115.7", area="15893"))
    )

    with caplog.at_level(logging.INFO, logger="app.services.nmt"):
        await fetch_terrain_extremes(geometry)
    assert "GetMinMaxByPolygon pełny adres" not in caplog.text
    assert wkt.split("((")[1][:20] not in caplog.text

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="app.services.nmt"):
        await fetch_terrain_extremes(geometry)
    assert "GetMinMaxByPolygon pełny adres zapytania" in caplog.text
    assert "polygon=POLYGON" in caplog.text
