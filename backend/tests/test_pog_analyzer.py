from datetime import datetime, timezone

import pytest
from shapely.geometry import Polygon

from app.schemas.source import SourceMetadata
from app.services.pog_analyzer import (
    PogPlanningZoneType,
    analyze_pog_vectors,
    spatial_feature_count,
    to_pog_result,
    zones_cover_parcel,
)
from app.services.pog_fetch import PogVectorData, PogVectorFeature
from app.shared.planning_status import (
    PogStatusObservation,
    StatusEvidence,
    resolve_pog_status,
)

LEGAL_FORCE = "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/legalForce"


def _decision(result, raw_status: str | None = LEGAL_FORCE):
    now = datetime.now(timezone.utc)
    return resolve_pog_status(
        PogStatusObservation(
            source_responded=True,
            checked_at=now,
            raw_legal_status=raw_status,
            legal_evidence=StatusEvidence("RU", official=True, raw_value=raw_status),
            act_found=True,
            act_has_spatial_data=True,
            spatial_features_on_parcel=spatial_feature_count(result),
            zones_cover_parcel=zones_cover_parcel(result),
            response_complete=True,
        )
    )


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

    result = analyze_pog_vectors(parcel, _vector_data(zones=[zone]))
    api_result = to_pog_result(result, _decision(result))

    assert result.status == "analyzed"
    assert len(result.zones) == 1
    assert (
        result.zones[0].zone_type is PogPlanningZoneType.MULTIFUNCTIONAL_SINGLE_FAMILY
    )
    assert result.zones[0].area_sqm == pytest.approx(10_000.0)
    assert result.zones[0].area_ratio == pytest.approx(1.0)
    assert result.dominant_zone == result.zones[0]
    assert result.source_metadata.source_name == "POG_APP_VECTOR"
    assert api_result.planning_zone == "SJ"
    assert api_result.legal_status == "binding"
    assert api_result.coverage_status == "available"
    assert api_result.status == "binding"


def test_parcel_in_many_zones_returns_surface_ratios_and_dominant_zone() -> None:
    parcel = Polygon.from_bounds(0, 0, 100, 100)
    larger = _feature(
        Polygon.from_bounds(0, 0, 70, 100), "planning_zone", zone_type="SJ"
    )
    smaller = _feature(
        Polygon.from_bounds(70, 0, 100, 100), "planning_zone", zone_type="SU"
    )

    result = analyze_pog_vectors(parcel, _vector_data(zones=[smaller, larger]))

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

    result = analyze_pog_vectors(parcel, _vector_data(zones=[zone]))

    assert result.zones[0].zone_type is PogPlanningZoneType.UNKNOWN
    assert result.zones[0].source_zone_type == "LOKALNY_X"
    assert any(warning.code == "POG_UNKNOWN_ZONE_TYPE" for warning in result.warnings)


def test_ouz_surface_intersection_uses_area_and_percentage() -> None:
    parcel = Polygon.from_bounds(0, 0, 100, 100)
    ouz = _feature(Polygon.from_bounds(0, 0, 40, 100), "ouz")

    result = analyze_pog_vectors(parcel, _vector_data(ouz=[ouz]))

    assert result.ouz_intersection_area_sqm == pytest.approx(4_000.0)
    assert result.ouz_intersection_pct == pytest.approx(40.0)
    assert result.touches_ouz_boundary is False


def test_parcel_only_touching_ouz_sets_boundary_flag_without_area() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    ouz = _feature(Polygon.from_bounds(10, 0, 20, 10), "ouz")

    result = analyze_pog_vectors(parcel, _vector_data(ouz=[ouz]))

    assert result.ouz_intersection_area_sqm == 0.0
    assert result.ouz_intersection_pct == 0.0
    assert result.touches_ouz_boundary is True


def test_multiple_overlapping_ouz_features_are_not_double_counted() -> None:
    parcel = Polygon.from_bounds(0, 0, 100, 100)
    first = _feature(Polygon.from_bounds(0, 0, 60, 100), "ouz")
    second = _feature(Polygon.from_bounds(40, 0, 100, 100), "ouz")

    result = analyze_pog_vectors(parcel, _vector_data(ouz=[first, second]))

    assert result.ouz_intersection_area_sqm == pytest.approx(10_000.0)
    assert result.ouz_intersection_pct == pytest.approx(100.0)


def test_tiny_ouz_sliver_uses_shared_threshold_in_api_mapping() -> None:
    parcel = Polygon.from_bounds(0, 0, 100, 100)
    ouz = _feature(Polygon.from_bounds(0, 0, 0.005, 100), "ouz")

    result = analyze_pog_vectors(parcel, _vector_data(ouz=[ouz]))
    api_result = to_pog_result(result, _decision(result))

    assert result.ouz_intersection_area_sqm == pytest.approx(0.5)
    assert result.ouz_status.in_ouz is False
    assert result.touches_ouz_boundary is True
    assert api_result.in_ouz is False


def test_downtown_area_is_calculated_separately() -> None:
    parcel = Polygon.from_bounds(0, 0, 20, 20)
    downtown = _feature(Polygon.from_bounds(0, 0, 10, 20), "downtown_area")

    result = analyze_pog_vectors(parcel, _vector_data(downtown=[downtown]))

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

    result = analyze_pog_vectors(parcel, _vector_data(zones=[zone]))

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

    result = analyze_pog_vectors(parcel, _vector_data(zones=zones))

    assert sum(zone.area_ratio for zone in result.zones) == pytest.approx(2.0)
    assert any(warning.code == "POG_OVERLAPPING_ZONES" for warning in result.warnings)


def test_vector_fallback_returns_unknown_not_exception() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)

    result = analyze_pog_vectors(parcel, _vector_data(fallback=True))

    assert result.status == "unknown"
    assert result.zones == []
    assert result.source_metadata.confidence == 0.0
    assert any(
        warning.code == "POG_VECTOR_DATA_REQUIRED" for warning in result.warnings
    )


def test_missing_zone_intersection_is_analyzed_with_warning() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    remote_zone = _feature(
        Polygon.from_bounds(20, 20, 30, 30), "planning_zone", zone_type="SJ"
    )

    result = analyze_pog_vectors(parcel, _vector_data(zones=[remote_zone]))

    assert result.status == "analyzed"
    assert result.dominant_zone is None
    assert any(
        warning.code == "POG_ZONE_NOT_INTERSECTED" for warning in result.warnings
    )


def test_project_zones_are_mapped_without_binding_status() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    zone = _feature(parcel, "planning_zone", zone_type="SJ")
    result = analyze_pog_vectors(parcel, _vector_data(zones=[zone]))
    raw = "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/adoption"

    api_result = to_pog_result(result, _decision(result, raw))

    assert api_result.legal_status == "project"
    assert api_result.coverage_status == "available"
    assert api_result.manual_review_required is True


def test_zones_covering_part_of_parcel_are_partial_coverage() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    zone = _feature(Polygon.from_bounds(0, 0, 5, 10), "planning_zone", zone_type="SJ")
    result = analyze_pog_vectors(parcel, _vector_data(zones=[zone]))

    api_result = to_pog_result(result, _decision(result))

    assert zones_cover_parcel(result) is False
    assert api_result.legal_status == "binding"
    assert api_result.coverage_status == "partial"
    assert api_result.manual_review_required is True


# --- AU-110 / issue 303: strefy-okruchy na styku sąsiednich stref ---------------------------------------------------


def _sliver_scenario() -> tuple[Polygon, list[PogVectorFeature]]:
    """Działka z poważnym udziałem jednej strefy i dwoma okruchami styku (0,10 m² i 0,08 m² — liczby z audytu)."""
    parcel = Polygon.from_bounds(0, 0, 100, 100)
    main = _feature(Polygon.from_bounds(0, 0, 100, 99.99), "planning_zone", zone_type="SK", feature_id="SK-1")
    sw = _feature(Polygon.from_bounds(0, 99.99, 10, 100), "planning_zone", zone_type="SW", feature_id="SW-1")  # 0,10 m²
    su = _feature(Polygon.from_bounds(10, 99.99, 18, 100), "planning_zone", zone_type="SU", feature_id="SU-1")  # 0,08 m²
    return parcel, [sw, main, su]


def test_slivers_are_kept_out_of_the_zone_table_and_preserved_with_a_flag() -> None:
    parcel, features = _sliver_scenario()

    result = analyze_pog_vectors(parcel, _vector_data(zones=features))
    api_result = to_pog_result(result, _decision(result))

    assert [zone.zone_id for zone in result.zones] == ["SK-1"]
    assert sorted(zone.zone_id for zone in result.topological_slivers) == ["SU-1", "SW-1"]  # type: ignore[type-var]
    assert result.dominant_zone is not None and result.dominant_zone.zone_id == "SK-1"
    assert [zone.id for zone in api_result.zones] == ["SK-1"]
    assert all(zone.topological_sliver for zone in api_result.topological_slivers)
    assert not any(zone.topological_sliver for zone in api_result.zones)
    assert api_result.dominant_zone_id == "SK-1"
    # Powierzchnia okruchów nie przepada: do sumy wlicza się je razem ze strefami (scenariusz nie wypełnia paska 0,0082%),
    # więc pokrycie pozostaje „available”.
    covered = sum(zone.area_pct for zone in (*api_result.zones, *api_result.topological_slivers))
    assert covered == pytest.approx(99.9918, abs=1e-4)
    assert zones_cover_parcel(result) and api_result.coverage_status == "available"
    assert spatial_feature_count(result) == 3
    notes = [warning for warning in result.warnings if warning.code == "POG_TOPOLOGICAL_SLIVERS_OMITTED"]
    assert len(notes) == 1 and notes[0].severity == "info"
    assert "Pominięto w tabeli stref 2 artefakty styku" in notes[0].message and "0.18 m²" in notes[0].message


def test_a_zone_is_a_sliver_only_when_both_thresholds_hold() -> None:
    parcel = Polygon.from_bounds(0, 0, 1000, 1000)  # 1 000 000 m²: 0,5% = 5 000 m²
    main = _feature(Polygon.from_bounds(0, 0, 1000, 990), "planning_zone", zone_type="SK", feature_id="SK")
    # Udział < 0,5%, ale pole 2 000 m² ≫ 1 m² → prawdziwa, wąska strefa, nie artefakt.
    narrow = _feature(Polygon.from_bounds(0, 990, 1000, 992), "planning_zone", zone_type="SW", feature_id="narrow")
    # Pole < 1 m², ale to nadal poniżej 0,5% → okruch.
    crumb = _feature(Polygon.from_bounds(0, 999.9, 5, 1000), "planning_zone", zone_type="SU", feature_id="crumb")  # 0,5 m²

    result = analyze_pog_vectors(parcel, _vector_data(zones=[main, narrow, crumb]))

    assert [zone.zone_id for zone in result.zones] == ["SK", "narrow"]
    assert [zone.zone_id for zone in result.topological_slivers] == ["crumb"]

    # Mała działka: pole < 1 m², ale udział ≥ 0,5% → nie jest okruchem (nie spełnia obu progów).
    tiny_parcel = Polygon.from_bounds(0, 0, 1, 1)
    tiny = analyze_pog_vectors(
        tiny_parcel,
        _vector_data(
            zones=[
                _feature(Polygon.from_bounds(0, 0, 1, 0.9), "planning_zone", zone_type="SK", feature_id="a"),
                _feature(Polygon.from_bounds(0, 0.9, 1, 1), "planning_zone", zone_type="SW", feature_id="b"),
            ]
        ),
    )
    assert [zone.zone_id for zone in tiny.zones] == ["a", "b"] and tiny.topological_slivers == []


def test_when_every_intersection_is_a_sliver_none_is_dropped() -> None:
    parcel = Polygon.from_bounds(0, 0, 10_000, 10_000)  # 1e8 m²
    only = _feature(Polygon.from_bounds(0, 0, 0.5, 1), "planning_zone", zone_type="SK", feature_id="only")  # 0,5 m²

    result = analyze_pog_vectors(parcel, _vector_data(zones=[only]))

    assert [zone.zone_id for zone in result.zones] == ["only"] and result.topological_slivers == []
    assert not any(warning.code == "POG_TOPOLOGICAL_SLIVERS_OMITTED" for warning in result.warnings)


def test_sliver_thresholds_come_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.settings import settings

    parcel, features = _sliver_scenario()
    monkeypatch.setattr(settings, "pog_sliver_min_sqm", 0.09)  # tylko 0,08 m² mieści się pod progiem

    result = analyze_pog_vectors(parcel, _vector_data(zones=features))

    assert [zone.zone_id for zone in result.topological_slivers] == ["SU-1"]
    assert {zone.zone_id for zone in result.zones} == {"SK-1", "SW-1"}

    monkeypatch.setattr(settings, "pog_sliver_min_pct", 0.0)  # przy 0% żaden udział nie jest „poniżej”
    assert analyze_pog_vectors(parcel, _vector_data(zones=features)).topological_slivers == []


@pytest.mark.parametrize("area,pct,sliver", [(0.9999, 0.4999, True), (1.0, 0.4999, False), (0.9999, 0.5, False), (1.0, 0.5, False), (0, 0, True)])
def test_sliver_thresholds_are_strict_in_both_dimensions(area, pct, sliver):
    from app.services.pog_analyzer import PogZoneIntersection, is_topological_sliver
    zone = PogZoneIntersection(PogPlanningZoneType.MULTIFUNCTIONAL_MULTI_FAMILY, "SW", area, pct / 100, {}, False, False)
    assert is_topological_sliver(zone) is sliver


def test_real_bielsko_parcel_slivers_from_frozen_audit_geometry():
    import json
    import zipfile
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform
    from tests.repo_structure import find_repo_root
    path = find_repo_root() / "docs/evaluation/results/pog-national/live/audit-246101_1.0056.155-3.zip"
    with zipfile.ZipFile(path) as archive:
        parcel_json = json.loads(archive.read("parcel.geojson"))["features"][0]["geometry"]
        main_json = json.loads(archive.read("layers/pog_zones.geojson"))["features"][0]["geometry"]
    project = Transformer.from_crs(4326, 2180, always_xy=True).transform
    parcel = transform(project, shape(parcel_json))
    main = transform(project, shape(main_json))
    # Eksport ma geometrię strefy głównej i działki; okruch jest ich rzeczywistą różnicą,
    # nie ręcznie skonstruowanym prostokątem. Surowy zapis podaje SW i pole 0,1080956 m².
    zones = [
        PogVectorFeature(main, {"zone_type": "SK", "symbol": "SK"}, "EPSG:2180", "planning_zone"),
        PogVectorFeature(parcel.difference(main), {"zone_type": "SW", "symbol": "SW"}, "EPSG:2180", "planning_zone"),
    ]
    analysis = analyze_pog_vectors(parcel, _vector_data(zones=zones))
    assert [zone.zone_type for zone in analysis.zones] == [PogPlanningZoneType.TRANSPORT]
    assert len(analysis.topological_slivers) == 1  # Wersja RU 20260901T095300; zmiana względem audytu: 1 okruch 0,11 m².
    assert analysis.topological_slivers[0].area_sqm == pytest.approx(0.1080956, abs=0.0001)
    assert zones_cover_parcel(analysis)
