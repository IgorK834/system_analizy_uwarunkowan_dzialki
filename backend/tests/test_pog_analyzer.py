from datetime import datetime, timezone

import pytest
from shapely.geometry import Polygon

from app.schemas.source import SourceMetadata
from app.services.pog_analyzer import (
    PogPlanningZoneType,
    analyze_pog_adopted,
    to_pog_result,
)
from app.services.pog_fetch import PogVectorData, PogVectorFeature


def _source() -> SourceMetadata:
    return SourceMetadata(
        source_name="POG_APP_VECTOR",
        source_url="https://bip.example.test/pog.gml",
        fetched_at=datetime.now(timezone.utc),
        response_status=200,
        confidence=0.75,
        manual_review_required=False,
    )


def _feature(
    geometry: Polygon,
    layer_type: str,
    **attributes: object,
) -> PogVectorFeature:
    return PogVectorFeature(
        geometry=geometry,
        attributes=attributes,
        source_crs="EPSG:2180",
        layer_type=layer_type,  # type: ignore[arg-type]
    )


def _vector_data(
    *,
    zones: list[PogVectorFeature] | None = None,
    ouz: list[PogVectorFeature] | None = None,
    downtown: list[PogVectorFeature] | None = None,
    fallback: bool = False,
) -> PogVectorData:
    return PogVectorData(
        planning_zones=zones or [],
        ouz_areas=ouz or [],
        downtown_areas=downtown or [],
        app_metadata={"uchwala_nr": "X/20/2026"},
        status="wms_fallback_required" if fallback else "available",
        wms_fallback_required=fallback,
        source_metadata=_source(),
        warnings=[],
    )


def test_parcel_in_one_zone_has_ratio_near_one_and_api_mapping() -> None:
    parcel = Polygon.from_bounds(0, 0, 100, 100)
    zone = _feature(parcel, "planning_zone", zone_type="SJ")

    result = analyze_pog_adopted(parcel, _vector_data(zones=[zone]))
    api_result = to_pog_result(result)

    assert result.status == "adopted"
    assert len(result.zones) == 1
    assert (
        result.zones[0].zone_type is PogPlanningZoneType.MULTIFUNCTIONAL_SINGLE_FAMILY
    )
    assert result.zones[0].area_sqm == pytest.approx(10_000.0)
    assert result.zones[0].area_ratio == pytest.approx(1.0)
    assert result.dominant_zone == result.zones[0]
    assert result.source_metadata.source_name == "POG_APP_VECTOR"
    assert api_result.planning_zone == "SJ"
    assert api_result.status == "adopted"


def test_parcel_in_many_zones_returns_surface_ratios_and_dominant_zone() -> None:
    parcel = Polygon.from_bounds(0, 0, 100, 100)
    larger = _feature(
        Polygon.from_bounds(0, 0, 70, 100), "planning_zone", zone_type="SJ"
    )
    smaller = _feature(
        Polygon.from_bounds(70, 0, 100, 100), "planning_zone", zone_type="SU"
    )

    result = analyze_pog_adopted(parcel, _vector_data(zones=[smaller, larger]))

    assert [zone.area_ratio for zone in result.zones] == pytest.approx([0.7, 0.3])
    assert result.dominant_zone is not None
    assert (
        result.dominant_zone.zone_type
        is PogPlanningZoneType.MULTIFUNCTIONAL_SINGLE_FAMILY
    )
    assert sum(zone.area_ratio for zone in result.zones) == pytest.approx(1.0)


def test_unknown_zone_type_is_preserved_with_warning() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    zone = _feature(parcel, "planning_zone", zone_type="LOKALNY_X")

    result = analyze_pog_adopted(parcel, _vector_data(zones=[zone]))

    assert result.zones[0].zone_type is PogPlanningZoneType.UNKNOWN
    assert result.zones[0].source_zone_type == "LOKALNY_X"
    assert any(warning.code == "POG_UNKNOWN_ZONE_TYPE" for warning in result.warnings)


def test_ouz_surface_intersection_uses_area_and_percentage() -> None:
    parcel = Polygon.from_bounds(0, 0, 100, 100)
    ouz = _feature(Polygon.from_bounds(0, 0, 40, 100), "ouz")

    result = analyze_pog_adopted(parcel, _vector_data(ouz=[ouz]))

    assert result.ouz_intersection_area_sqm == pytest.approx(4_000.0)
    assert result.ouz_intersection_pct == pytest.approx(40.0)
    assert result.touches_ouz_boundary is False


def test_parcel_only_touching_ouz_sets_boundary_flag_without_area() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    ouz = _feature(Polygon.from_bounds(10, 0, 20, 10), "ouz")

    result = analyze_pog_adopted(parcel, _vector_data(ouz=[ouz]))

    assert result.ouz_intersection_area_sqm == 0.0
    assert result.ouz_intersection_pct == 0.0
    assert result.touches_ouz_boundary is True


def test_multiple_overlapping_ouz_features_are_not_double_counted() -> None:
    parcel = Polygon.from_bounds(0, 0, 100, 100)
    first = _feature(Polygon.from_bounds(0, 0, 60, 100), "ouz")
    second = _feature(Polygon.from_bounds(40, 0, 100, 100), "ouz")

    result = analyze_pog_adopted(parcel, _vector_data(ouz=[first, second]))

    assert result.ouz_intersection_area_sqm == pytest.approx(10_000.0)
    assert result.ouz_intersection_pct == pytest.approx(100.0)


def test_downtown_area_is_calculated_separately() -> None:
    parcel = Polygon.from_bounds(0, 0, 20, 20)
    downtown = _feature(Polygon.from_bounds(0, 0, 10, 20), "downtown_area")

    result = analyze_pog_adopted(parcel, _vector_data(downtown=[downtown]))

    assert result.downtown_intersection_area_sqm == pytest.approx(200.0)
    assert result.downtown_intersection_pct == pytest.approx(50.0)


def test_pdf_parameters_are_informational_and_lower_confidence() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    zone = _feature(
        parcel,
        "planning_zone",
        zone_type="SU",
        max_building_height_m=12,
        parameter_source="PDF uzasadnienie",
    )

    result = analyze_pog_adopted(parcel, _vector_data(zones=[zone]))

    assert result.zones[0].parameters["max_building_height_m"] == 12
    assert result.zones[0].parameters_informational is True
    assert result.zones[0].manual_review_required is True
    assert result.source_metadata.confidence == 0.45
    assert result.source_metadata.manual_review_required is True
    assert any(warning.code == "POG_PARAMETERS_FROM_PDF" for warning in result.warnings)


def test_overlapping_zones_add_warning() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    zones = [
        _feature(parcel, "planning_zone", zone_type="SJ"),
        _feature(parcel, "planning_zone", zone_type="SU"),
    ]

    result = analyze_pog_adopted(parcel, _vector_data(zones=zones))

    assert sum(zone.area_ratio for zone in result.zones) == pytest.approx(2.0)
    assert any(warning.code == "POG_OVERLAPPING_ZONES" for warning in result.warnings)


def test_vector_fallback_returns_unknown_not_exception() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)

    result = analyze_pog_adopted(parcel, _vector_data(fallback=True))

    assert result.status == "unknown"
    assert result.zones == []
    assert result.source_metadata.confidence == 0.0
    assert any(
        warning.code == "POG_VECTOR_DATA_REQUIRED" for warning in result.warnings
    )


def test_missing_zone_intersection_is_adopted_with_warning() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    remote_zone = _feature(
        Polygon.from_bounds(20, 20, 30, 30), "planning_zone", zone_type="SJ"
    )

    result = analyze_pog_adopted(parcel, _vector_data(zones=[remote_zone]))

    assert result.status == "adopted"
    assert result.dominant_zone is None
    assert any(
        warning.code == "POG_ZONE_NOT_INTERSECTED" for warning in result.warnings
    )
