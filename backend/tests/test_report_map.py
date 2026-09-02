"""Testy jednostkowe generatora miniatury mapy PNG dla raportu.

Testy nie wymagają bazy danych ani WeasyPrint — sprawdzają wyłącznie
deterministyczne renderowanie PNG z geometrii GeoJSON, dlatego działają także
lokalnie poza kontenerem. Wywołań WMS w CI nie ma — basemap jest mockowany
przez respx albo wyłączany fixturem autouse.
"""

from __future__ import annotations

import io

import httpx
import pytest
import respx
from PIL import Image

from app.core.report_config import MAP_BACKGROUND_RGB, MAP_IMAGE_HEIGHT, MAP_IMAGE_WIDTH
from app.core.settings import Settings, settings
from app.schemas.analyze import (
    AnalyzeResponse,
    GeometryMetrics,
    InfrastructureResult,
    ParcelGeometryResponse,
    RiskResult,
    UtilitiesPreviewResult,
)
from app.schemas.source import SourceMetadata
from app.services.report_map import (
    BASEMAP_FAILURE_WARNING,
    _BasemapProjector,
    _Bounds,
    _extract_primitives,
    png_to_data_uri,
    render_analysis_map_png,
)
from app.services.report_map_basemap import (
    BasemapLayout,
    bounds_to_web_mercator_bbox,
    compute_basemap_layout,
    fetch_report_basemap_png,
)
from datetime import datetime, timezone

from pyproj import Transformer

from app.services.wms_tiles import WmsPreviewSource

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_OSM_WMS_URL = "https://wms.example.test/report-osm"
_KIMPZP_WMS_URL = "https://wms.example.test/report-kimpzp"
_FETCHED_AT = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _disable_basemap_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Domyślnie testujemy ścieżkę MVP offline bez sieci."""
    monkeypatch.setattr(settings, "report_map_basemap_enabled", False)
    monkeypatch.setattr(settings, "report_map_kimpzp_overlay_enabled", False)
    monkeypatch.setattr(settings, "report_map_kiut_overlay_enabled", False)


def _basemap_settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "report_map_basemap_enabled": True,
        "report_map_wms_base_url": _OSM_WMS_URL,
        "report_map_wms_layers": "OSM-WMS",
        "report_map_kimpzp_overlay_enabled": False,
        "report_map_kiut_overlay_enabled": False,
        "kimpzp_wms_base_url": _KIMPZP_WMS_URL,
        "kimpzp_wms_layers": "raster",
        "report_map_wms_timeout_seconds": 2.0,
        "report_map_wms_max_response_bytes": 8 * 1024 * 1024,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def _make_color_png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _source() -> SourceMetadata:
    return SourceMetadata(
        source_name="ULDK",
        source_url="https://uldk.example.test",
        fetched_at=_FETCHED_AT,
        confidence=0.95,
        manual_review_required=False,
    )


def _parcel_feature(coords: list[list[float]]) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [coords]},
        "properties": {"layer": "parcel"},
    }


def _response_with_parcel(coords: list[list[float]] | None = None) -> AnalyzeResponse:
    ring = coords or [
        [19.940, 50.060],
        [19.945, 50.060],
        [19.945, 50.064],
        [19.940, 50.064],
        [19.940, 50.060],
    ]
    return AnalyzeResponse(
        status="complete",
        analyzed_at=_FETCHED_AT,
        parcel=ParcelGeometryResponse(
            parcel_identifier="TEST.1",
            geometry_geojson=_parcel_feature(ring),
            metrics=GeometryMetrics(
                area_sqm=1000.0,
                area_ha=0.1,
                perimeter_m=130.0,
                is_valid=True,
                geometry_repaired=False,
            ),
            source=_source(),
        ),
        mpzp_zones=[],
        pog=None,
        infrastructure=[],
        risks=[],
        buildable_area_sqm=None,
        warnings=[],
        sources=[],
    )


def _empty_response() -> AnalyzeResponse:
    return AnalyzeResponse(
        status="partial",
        analyzed_at=_FETCHED_AT,
        parcel=None,
        mpzp_zones=[],
        pog=None,
        infrastructure=[],
        risks=[],
        buildable_area_sqm=None,
        warnings=[],
        sources=[],
    )


def test_render_returns_png_with_valid_signature_and_dimensions() -> None:
    result = render_analysis_map_png(_response_with_parcel())

    assert result.png_bytes is not None
    assert len(result.png_bytes) > 0
    assert result.png_bytes.startswith(_PNG_SIGNATURE)
    assert result.basemap_used is False
    assert result.warning is None

    image = Image.open(io.BytesIO(result.png_bytes))
    assert image.format == "PNG"
    assert image.size == (MAP_IMAGE_WIDTH, MAP_IMAGE_HEIGHT)


def test_render_respects_custom_dimensions() -> None:
    result = render_analysis_map_png(_response_with_parcel(), width=400, height=300)

    assert result.png_bytes is not None
    image = Image.open(io.BytesIO(result.png_bytes))
    assert image.size == (400, 300)


def test_render_returns_none_without_any_geometry() -> None:
    result = render_analysis_map_png(_empty_response())
    assert result.png_bytes is None


def test_render_centers_geometry_with_bbox_margin() -> None:
    # Działka wypełniający cały kadr musi mieć 10% marginesu, więc narożniki
    # obrazu pozostają w kolorze tła, a geometria jest wycentrowana.
    result = render_analysis_map_png(_response_with_parcel())
    assert result.png_bytes is not None
    image = Image.open(io.BytesIO(result.png_bytes)).convert("RGB")

    background = MAP_BACKGROUND_RGB
    # Narożniki kadru powinny pozostać tłem dzięki marginesowi BBOX i letterbox.
    assert image.getpixel((2, 2)) == background
    assert image.getpixel((MAP_IMAGE_WIDTH - 3, MAP_IMAGE_HEIGHT - 3)) == background
    # Środek kadru powinien być pokolorowany warstwą działki (nie tło).
    assert image.getpixel((MAP_IMAGE_WIDTH // 2, MAP_IMAGE_HEIGHT // 2)) != background


def test_remote_context_layer_does_not_change_parcel_centered_viewport() -> None:
    parcel_only = _response_with_parcel()
    with_remote_risk = parcel_only.model_copy(
        update={
            "risks": [
                RiskResult(
                    risk_type="remote_protection_area",
                    description="Rozległa geometria poza otoczeniem działki.",
                    geometry_geojson={
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [21.0, 52.0],
                                [21.5, 52.0],
                                [21.5, 52.5],
                                [21.0, 52.5],
                                [21.0, 52.0],
                            ]
                        ],
                    },
                    source=_source(),
                )
            ]
        }
    )

    parcel_result = render_analysis_map_png(parcel_only)
    context_result = render_analysis_map_png(with_remote_risk)

    # Warstwa całkowicie poza kadrem jest przycięta. Nie może zmienić skali ani
    # położenia działki zaznaczonej przez użytkownika.
    assert parcel_result.png_bytes is not None
    assert context_result.png_bytes == parcel_result.png_bytes


def test_render_draws_all_layers_without_error() -> None:
    response = _response_with_parcel().model_copy(
        update={
            "infrastructure": [
                InfrastructureResult(
                    network_type="water",
                    buffer_m=4.0,
                    zone_area_sqm=100.0,
                    affects_buildable_area=True,
                    network_geometry_geojson={
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[19.941, 50.061], [19.944, 50.063]],
                        },
                        "properties": {"layer": "network"},
                    },
                    protection_zone_geojson={
                        "type": "Feature",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [19.941, 50.061],
                                    [19.943, 50.061],
                                    [19.943, 50.063],
                                    [19.941, 50.063],
                                    [19.941, 50.061],
                                ]
                            ],
                        },
                        "properties": {"layer": "protection_zone"},
                    },
                    source=_source(),
                )
            ],
            "risks": [
                RiskResult(
                    risk_type="flood_zone",
                    description="Strefa zagrożenia powodziowego.",
                    geometry_geojson={
                        "type": "MultiPolygon",
                        "coordinates": [
                            [
                                [
                                    [19.940, 50.060],
                                    [19.942, 50.060],
                                    [19.942, 50.062],
                                    [19.940, 50.062],
                                    [19.940, 50.060],
                                ]
                            ]
                        ],
                    },
                    source=_source(),
                )
            ],
        }
    )
    result = render_analysis_map_png(response)
    assert result.png_bytes is not None
    assert result.png_bytes.startswith(_PNG_SIGNATURE)


def test_render_handles_single_point_geometry() -> None:
    response = _empty_response().model_copy(
        update={
            "risks": [
                RiskResult(
                    risk_type="point_risk",
                    description="Ryzyko punktowe.",
                    geometry_geojson={
                        "type": "Point",
                        "coordinates": [19.94, 50.06],
                    },
                    source=_source(),
                )
            ]
        }
    )
    result = render_analysis_map_png(response)
    assert result.png_bytes is not None
    image = Image.open(io.BytesIO(result.png_bytes))
    assert image.size == (MAP_IMAGE_WIDTH, MAP_IMAGE_HEIGHT)


def test_render_ignores_broken_geometry_without_raising() -> None:
    response = _empty_response().model_copy(
        update={
            "risks": [
                RiskResult(
                    risk_type="broken",
                    description="Uszkodzona geometria.",
                    geometry_geojson={
                        "type": "Polygon",
                        "coordinates": "to nie są współrzędne",
                    },
                    source=_source(),
                )
            ]
        }
    )
    # Uszkodzona geometria jest jedyną warstwą, więc miniatura jest pusta,
    # ale generator nie może rzucić wyjątku.
    result = render_analysis_map_png(response)
    assert result.png_bytes is None


def test_extract_primitives_supports_feature_collection() -> None:
    collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[19.94, 50.06], [19.95, 50.07]],
                },
                "properties": {},
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": [
                        [[19.94, 50.06], [19.95, 50.07]],
                        [[19.96, 50.08], [19.97, 50.09]],
                    ],
                },
                "properties": {},
            },
        ],
    }
    primitives = _extract_primitives(collection)
    assert len(primitives.lines) == 3
    assert not primitives.polygons
    assert not primitives.points


def test_extract_primitives_supports_polygon_with_holes() -> None:
    polygon = {
        "type": "Polygon",
        "coordinates": [
            [[0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0], [0.0, 0.0]],
            [[1.0, 1.0], [2.0, 1.0], [2.0, 2.0], [1.0, 2.0], [1.0, 1.0]],
        ],
    }
    primitives = _extract_primitives(polygon)
    assert len(primitives.polygons) == 1
    exterior, holes = primitives.polygons[0]
    assert len(exterior) == 5
    assert len(holes) == 1


def test_extract_primitives_ignores_non_dict() -> None:
    assert _extract_primitives(None).is_empty()
    assert _extract_primitives("not geojson").is_empty()  # type: ignore[arg-type]


def test_png_to_data_uri_is_valid_base64_png() -> None:
    result = render_analysis_map_png(_response_with_parcel())
    assert result.png_bytes is not None
    data_uri = png_to_data_uri(result.png_bytes)
    assert data_uri.startswith("data:image/png;base64,")

    import base64

    payload = data_uri.split(",", 1)[1]
    decoded = base64.b64decode(payload)
    assert decoded == result.png_bytes
    assert decoded.startswith(_PNG_SIGNATURE)


def _mock_png_response(request: httpx.Request) -> httpx.Response:
    width = int(request.url.params.get("width", MAP_IMAGE_WIDTH))
    height = int(request.url.params.get("height", MAP_IMAGE_HEIGHT))
    color = (30, 120, 200)
    url = str(request.url).casefold()
    if "kiut" in url:
        color = (0, 200, 80)
    elif "kimpzp" in url or "krajowaintegracja" in url:
        color = (255, 0, 0)
    return httpx.Response(
        200,
        content=_make_color_png(width, height, color),
        headers={"content-type": "image/png"},
    )


def test_basemap_layout_fills_canvas_and_expands_bbox() -> None:
    bounds = _Bounds(min_lon=19.94, min_lat=50.06, max_lon=19.945, max_lat=50.064)
    core_bbox = bounds_to_web_mercator_bbox(bounds)
    layout = compute_basemap_layout(bounds, MAP_IMAGE_WIDTH, MAP_IMAGE_HEIGHT)

    span_x = layout.bbox_3857[2] - layout.bbox_3857[0]
    span_y = layout.bbox_3857[3] - layout.bbox_3857[1]

    assert layout.map_width == MAP_IMAGE_WIDTH
    assert layout.map_height == MAP_IMAGE_HEIGHT
    assert layout.offset_x == 0.0
    assert layout.offset_y == 0.0
    assert span_x / span_y == pytest.approx(
        MAP_IMAGE_WIDTH / MAP_IMAGE_HEIGHT,
        rel=0.01,
    )
    assert layout.bbox_3857[0] < core_bbox[0]
    assert layout.bbox_3857[2] > core_bbox[2]


def test_basemap_projector_maps_expanded_bbox_corners_to_canvas_edges() -> None:
    bounds = _Bounds(min_lon=19.94, min_lat=50.06, max_lon=19.945, max_lat=50.064)
    layout = compute_basemap_layout(bounds, 900, 600)
    projector = _BasemapProjector(layout)
    transformer = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    min_x, min_y, max_x, max_y = layout.bbox_3857

    nw_lon, nw_lat = transformer.transform(min_x, max_y)
    se_lon, se_lat = transformer.transform(max_x, min_y)

    nw = projector.to_pixel(nw_lon, nw_lat)
    se = projector.to_pixel(se_lon, se_lat)

    assert nw[0] == pytest.approx(0.0, abs=2.0)
    assert nw[1] == pytest.approx(0.0, abs=2.0)
    assert se[0] == pytest.approx(900.0, abs=2.0)
    assert se[1] == pytest.approx(600.0, abs=2.0)


@respx.mock
def test_fetch_report_basemap_png_validates_wms_request() -> None:
    route = respx.get(_OSM_WMS_URL).mock(side_effect=_mock_png_response)
    bounds = _Bounds(min_lon=19.94, min_lat=50.06, max_lon=19.945, max_lat=50.064)
    layout = compute_basemap_layout(bounds, 900, 600)
    cfg = _basemap_settings()

    result = fetch_report_basemap_png(layout, config=cfg)

    assert result is not None
    assert result.startswith(_PNG_SIGNATURE)
    assert route.call_count == 1
    params = route.calls[0].request.url.params
    assert params["request"] == "GetMap"
    assert params["version"] == "1.1.1"
    assert params["srs"] == "EPSG:3857"
    assert params["layers"] == "OSM-WMS"
    assert params["width"] == str(layout.map_width)
    assert params["height"] == str(layout.map_height)
    assert params["bbox"].startswith("2219")


@respx.mock
def test_render_with_mocked_basemap_uses_non_uniform_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "report_map_basemap_enabled", True)
    monkeypatch.setattr(settings, "report_map_wms_base_url", _OSM_WMS_URL)
    monkeypatch.setattr(settings, "report_map_wms_layers", "OSM-WMS")
    monkeypatch.setattr(settings, "report_map_kimpzp_overlay_enabled", False)

    basemap_color = (30, 120, 200)
    respx.get(_OSM_WMS_URL).mock(side_effect=_mock_png_response)

    result = render_analysis_map_png(_response_with_parcel())

    assert result.png_bytes is not None
    assert result.basemap_used is True
    assert result.warning is None

    image = Image.open(io.BytesIO(result.png_bytes)).convert("RGB")
    assert image.getpixel((8, 8)) == basemap_color


@respx.mock
def test_render_basemap_failure_falls_back_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "report_map_basemap_enabled", True)
    monkeypatch.setattr(settings, "report_map_wms_base_url", _OSM_WMS_URL)
    monkeypatch.setattr(settings, "report_map_wms_layers", "OSM-WMS")
    monkeypatch.setattr(settings, "report_map_kimpzp_overlay_enabled", False)

    respx.get(_OSM_WMS_URL).mock(return_value=httpx.Response(503))

    result = render_analysis_map_png(_response_with_parcel())

    assert result.png_bytes is not None
    assert result.basemap_used is False
    assert result.warning == BASEMAP_FAILURE_WARNING

    image = Image.open(io.BytesIO(result.png_bytes)).convert("RGB")
    assert image.getpixel((2, 2)) == MAP_BACKGROUND_RGB
    assert image.getpixel((MAP_IMAGE_WIDTH // 2, MAP_IMAGE_HEIGHT // 2)) != MAP_BACKGROUND_RGB


@respx.mock
def test_fetch_report_basemap_png_returns_none_on_invalid_content() -> None:
    respx.get(_OSM_WMS_URL).mock(
        return_value=httpx.Response(
            200,
            text="<ServiceException>error</ServiceException>",
            headers={"content-type": "application/vnd.ogc.se_xml"},
        )
    )
    bounds = _Bounds(min_lon=19.0, min_lat=50.0, max_lon=20.0, max_lat=51.0)
    layout = compute_basemap_layout(bounds, 900, 600)
    cfg = _basemap_settings()

    assert fetch_report_basemap_png(layout, config=cfg) is None


@respx.mock
def test_fetch_report_basemap_composites_kimpzp_overlay() -> None:
    respx.get(_OSM_WMS_URL).mock(side_effect=_mock_png_response)
    respx.get(_KIMPZP_WMS_URL).mock(side_effect=_mock_png_response)
    bounds = _Bounds(min_lon=19.94, min_lat=50.06, max_lon=19.945, max_lat=50.064)
    layout = compute_basemap_layout(bounds, 900, 600)
    cfg = _basemap_settings(
        report_map_kimpzp_overlay_enabled=True,
        kimpzp_wms_base_url=_KIMPZP_WMS_URL,
    )

    result = fetch_report_basemap_png(layout, config=cfg)

    assert result is not None
    image = Image.open(io.BytesIO(result)).convert("RGB")
    sample_x = int(layout.offset_x + 10)
    sample_y = int(layout.offset_y + 10)
    assert image.getpixel((sample_x, sample_y)) == (255, 0, 0)


_KIUT_WMS_URL = "https://wms.example.test/kiut"
_KIUT_WORKER_URL = (
    "https://integracja02.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu"
)


def _kiut_source() -> WmsPreviewSource:
    return WmsPreviewSource(
        source_key="kiut",
        base_url=_KIUT_WMS_URL,
        layers="przewod_wodociagowy,przewod_elektroenergetyczny",
        version="1.1.1",
        min_zoom=16,
        max_zoom=20,
        tile_size=512,
        fresh_ttl_s=21600,
        stale_ttl_s=172800,
        upstream_concurrency=4,
        read_timeout_s=8.0,
        source_id="kiut_wms",
        label="Uzbrojenie terenu",
        attribution="KIUT, GUGiK",
        legal_note="Podgląd poglądowy.",
        info_url=_KIUT_WMS_URL,
        catalog_status="production",
        allowed_redirect_host_suffixes=(".gugik.gov.pl",),
        max_redirects=3,
    )


def _covered_preview() -> UtilitiesPreviewResult:
    return UtilitiesPreviewResult(
        coverage_status="covered",
        county_name="powiat bielski",
        layer_available=True,
        note="Powiat publikuje dane GESUT w KIUT.",
        source=SourceMetadata(
            source_name="KIUT (GUGiK)",
            source_url=_KIUT_WMS_URL,
            fetched_at=_FETCHED_AT,
            response_status=200,
            confidence=0.9,
            manual_review_required=False,
        ),
    )


@respx.mock
def test_fetch_report_basemap_composites_kiut_on_top_of_kimpzp() -> None:
    respx.get(_OSM_WMS_URL).mock(side_effect=_mock_png_response)
    respx.get(_KIMPZP_WMS_URL).mock(side_effect=_mock_png_response)
    respx.get(_KIUT_WMS_URL).mock(side_effect=_mock_png_response)
    bounds = _Bounds(min_lon=19.94, min_lat=50.06, max_lon=19.945, max_lat=50.064)
    layout = compute_basemap_layout(bounds, 900, 600)
    cfg = _basemap_settings(
        report_map_kimpzp_overlay_enabled=True,
        report_map_kiut_overlay_enabled=True,
        kimpzp_wms_base_url=_KIMPZP_WMS_URL,
    )

    result = fetch_report_basemap_png(
        layout, config=cfg, kiut_source=_kiut_source()
    )

    assert result is not None
    image = Image.open(io.BytesIO(result)).convert("RGB")
    sample_x = int(layout.offset_x + 10)
    sample_y = int(layout.offset_y + 10)
    assert image.getpixel((sample_x, sample_y)) == (0, 200, 80)


@respx.mock
def test_fetch_report_basemap_follows_allowed_kiut_redirect() -> None:
    respx.get(_OSM_WMS_URL).mock(side_effect=_mock_png_response)
    respx.get(_KIUT_WMS_URL).mock(
        return_value=httpx.Response(
            302, headers={"Location": _KIUT_WORKER_URL}
        )
    )
    worker = respx.get(_KIUT_WORKER_URL).mock(
        return_value=httpx.Response(
            200,
            content=_make_color_png(900, 600, (0, 200, 80)),
            headers={"content-type": "image/png"},
        )
    )
    bounds = _Bounds(min_lon=19.94, min_lat=50.06, max_lon=19.945, max_lat=50.064)
    layout = compute_basemap_layout(bounds, 900, 600)
    cfg = _basemap_settings(report_map_kiut_overlay_enabled=True)

    result = fetch_report_basemap_png(
        layout, config=cfg, kiut_source=_kiut_source()
    )

    assert result is not None
    assert worker.call_count == 1
    image = Image.open(io.BytesIO(result)).convert("RGB")
    assert image.getpixel((10, 10)) == (0, 200, 80)


@respx.mock
def test_fetch_report_basemap_keeps_osm_when_kiut_redirect_is_rejected() -> None:
    respx.get(_OSM_WMS_URL).mock(side_effect=_mock_png_response)
    respx.get(_KIUT_WMS_URL).mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://evil.example/steal"}
        )
    )
    evil = respx.get("https://evil.example/steal").mock(side_effect=_mock_png_response)
    bounds = _Bounds(min_lon=19.94, min_lat=50.06, max_lon=19.945, max_lat=50.064)
    layout = compute_basemap_layout(bounds, 900, 600)
    cfg = _basemap_settings(report_map_kiut_overlay_enabled=True)

    result = fetch_report_basemap_png(
        layout, config=cfg, kiut_source=_kiut_source()
    )

    assert result is not None
    assert evil.call_count == 0
    image = Image.open(io.BytesIO(result)).convert("RGB")
    assert image.getpixel((10, 10)) == (30, 120, 200)


def test_kiut_overlay_source_only_for_covered_counties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.report_map import _kiut_overlay_source

    monkeypatch.setattr(settings, "report_map_kiut_overlay_enabled", True)
    covered = _response_with_parcel().model_copy(
        update={"utilities_preview": _covered_preview()}
    )
    not_covered = _response_with_parcel().model_copy(
        update={
            "utilities_preview": UtilitiesPreviewResult(
                coverage_status="not_covered",
                county_name=None,
                layer_available=False,
                note="KIUT nie potwierdził publikacji.",
                source=SourceMetadata(
                    source_name="KIUT (GUGiK)",
                    source_url=_KIUT_WMS_URL,
                    fetched_at=_FETCHED_AT,
                    response_status=200,
                    confidence=0.9,
                    manual_review_required=False,
                ),
            )
        }
    )

    source = _kiut_overlay_source(covered)
    assert source is not None
    assert source.source_key == "kiut"
    assert _kiut_overlay_source(not_covered) is None
    assert _kiut_overlay_source(_response_with_parcel()) is None


@respx.mock
def test_render_marks_kiut_overlay_when_county_is_covered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "report_map_basemap_enabled", True)
    monkeypatch.setattr(settings, "report_map_wms_base_url", _OSM_WMS_URL)
    monkeypatch.setattr(settings, "report_map_wms_layers", "OSM-WMS")
    monkeypatch.setattr(settings, "report_map_kimpzp_overlay_enabled", False)
    monkeypatch.setattr(settings, "report_map_kiut_overlay_enabled", True)
    monkeypatch.setattr(
        "app.services.report_map._kiut_overlay_source",
        lambda _response: _kiut_source(),
    )
    respx.get(_OSM_WMS_URL).mock(side_effect=_mock_png_response)
    respx.get(_KIUT_WMS_URL).mock(side_effect=_mock_png_response)

    result = render_analysis_map_png(
        _response_with_parcel().model_copy(
            update={"utilities_preview": _covered_preview()}
        )
    )

    assert result.png_bytes is not None
    assert result.basemap_used is True
    assert result.kiut_overlay_used is True
    image = Image.open(io.BytesIO(result.png_bytes)).convert("RGB")
    assert image.getpixel((8, 8)) == (0, 200, 80)
