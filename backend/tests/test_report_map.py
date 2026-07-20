"""Testy jednostkowe generatora miniatury mapy PNG dla raportu.

Testy nie wymagają bazy danych ani WeasyPrint — sprawdzają wyłącznie
deterministyczne renderowanie PNG z geometrii GeoJSON, dlatego działają także
lokalnie poza kontenerem.
"""

from __future__ import annotations

import io

from PIL import Image

from app.core.report_config import MAP_IMAGE_HEIGHT, MAP_IMAGE_WIDTH
from app.schemas.analyze import (
    AnalyzeResponse,
    GeometryMetrics,
    InfrastructureResult,
    ParcelGeometryResponse,
    RiskResult,
)
from app.schemas.source import SourceMetadata
from app.services.report_map import (
    _extract_primitives,
    png_to_data_uri,
    render_analysis_map_png,
)

from datetime import datetime, timezone

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_FETCHED_AT = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)


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
    png_bytes = render_analysis_map_png(_response_with_parcel())

    assert png_bytes is not None
    assert len(png_bytes) > 0
    assert png_bytes.startswith(_PNG_SIGNATURE)

    image = Image.open(io.BytesIO(png_bytes))
    assert image.format == "PNG"
    assert image.size == (MAP_IMAGE_WIDTH, MAP_IMAGE_HEIGHT)


def test_render_respects_custom_dimensions() -> None:
    png_bytes = render_analysis_map_png(_response_with_parcel(), width=400, height=300)

    assert png_bytes is not None
    image = Image.open(io.BytesIO(png_bytes))
    assert image.size == (400, 300)


def test_render_returns_none_without_any_geometry() -> None:
    assert render_analysis_map_png(_empty_response()) is None


def test_render_centers_geometry_with_bbox_margin() -> None:
    # Działka wypełniająca cały kadr musi mieć 10% marginesu, więc narożniki
    # obrazu pozostają w kolorze tła, a geometria jest wycentrowana.
    png_bytes = render_analysis_map_png(_response_with_parcel())
    assert png_bytes is not None
    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")

    background = (245, 247, 249)
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

    parcel_png = render_analysis_map_png(parcel_only)
    context_png = render_analysis_map_png(with_remote_risk)

    # Warstwa całkowicie poza kadrem jest przycięta. Nie może zmienić skali ani
    # położenia działki zaznaczonej przez użytkownika.
    assert parcel_png is not None
    assert context_png == parcel_png


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
    png_bytes = render_analysis_map_png(response)
    assert png_bytes is not None
    assert png_bytes.startswith(_PNG_SIGNATURE)


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
    png_bytes = render_analysis_map_png(response)
    assert png_bytes is not None
    image = Image.open(io.BytesIO(png_bytes))
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
    assert render_analysis_map_png(response) is None


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
    png_bytes = render_analysis_map_png(_response_with_parcel())
    assert png_bytes is not None
    data_uri = png_to_data_uri(png_bytes)
    assert data_uri.startswith("data:image/png;base64,")

    import base64

    payload = data_uri.split(",", 1)[1]
    decoded = base64.b64decode(payload)
    assert decoded == png_bytes
    assert decoded.startswith(_PNG_SIGNATURE)
