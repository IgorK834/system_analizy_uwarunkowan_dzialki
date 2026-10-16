"""Domena discovery POG z WFS RU (AU-105): kafle zapytań, wybór statusu i obserwacja warstw."""

from __future__ import annotations

import math

import pytest
from shapely.geometry import MultiPolygon, Polygon, box

from app.modules.planning.domain.pog_discovery import (
    FEATURE_TYPE_BY_LAYER,
    GEOMETRY_AXIS_SUSPECT,
    GEOMETRY_UNUSABLE,
    LOGICAL_LAYER_BY_TYPE,
    MIN_PARCEL_OVERLAP_SQM,
    POG_FEATURE_TYPES,
    RESPONSE_PARTIAL,
    PogFeatureRecord,
    clip_to_tile,
    envelope_overlaps,
    observe_layer,
    preferred_status_href,
    snap_bbox_outward,
    tile_key,
)

LEGAL_FORCE = "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/legalForce"
ADOPTION = "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/adoption"
ELABORATION = "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/elaboration"
PARCEL = box(503_230.0, 217_210.0, 503_250.0, 217_240.0)  # 20 × 30 m


def _record(geometry, *, status: str | None = LEGAL_FORCE, feature_type="StrefaPlanistyczna", **extra):
    return PogFeatureRecord(
        feature_type=feature_type,
        identifier=f"PL.TEST/{feature_type}-{id(geometry)}",
        status_href=status,
        geometry=geometry,
        **extra,
    )


def test_every_feature_type_maps_to_exactly_one_logical_layer() -> None:
    assert set(POG_FEATURE_TYPES) == set(LOGICAL_LAYER_BY_TYPE)
    assert {FEATURE_TYPE_BY_LAYER[layer] for layer in LOGICAL_LAYER_BY_TYPE.values()} == set(POG_FEATURE_TYPES)
    assert sorted(LOGICAL_LAYER_BY_TYPE.values()) == ["downtown_area", "ouz", "planning_act", "planning_zones"]


@pytest.mark.parametrize(
    "bbox",
    [
        (503_224.04, 217_203.74, 503_256.17, 217_301.77),
        (0.1, 0.1, 0.2, 0.2),
        (503_200.0, 217_200.0, 503_250.0, 217_250.0),  # granice dokładnie na siatce
        (-30.0, -70.0, -10.0, -5.0),
    ],
)
def test_snapped_tile_contains_bbox_and_is_aligned(bbox) -> None:
    tile = snap_bbox_outward(bbox)

    assert tile[0] <= bbox[0] and tile[1] <= bbox[1] and tile[2] >= bbox[2] and tile[3] >= bbox[3]
    assert all(math.isclose(value % 50.0, 0.0, abs_tol=1e-9) or math.isclose(value % 50.0, 50.0) for value in tile)
    # Zaokrąglenie nie dokłada kafla, gdy granica leży dokładnie na linii siatki.
    assert tile[2] - tile[0] <= (bbox[2] - bbox[0]) + 100.0


def test_bbox_on_grid_line_is_not_widened() -> None:
    assert snap_bbox_outward((503_200.0, 217_200.0, 503_250.0, 217_250.0)) == (503_200.0, 217_200.0, 503_250.0, 217_250.0)


def test_degenerate_bbox_gets_a_positive_tile() -> None:
    tile = snap_bbox_outward((100.0, 200.0, 100.0, 200.0))
    assert tile[2] > tile[0] and tile[3] > tile[1]


@pytest.mark.parametrize("bbox", [(0.0, 0.0, math.nan, 1.0), (0.0, 0.0, math.inf, 1.0)])
def test_non_finite_bbox_is_rejected(bbox) -> None:
    with pytest.raises(ValueError):
        snap_bbox_outward(bbox)


def test_non_positive_tile_size_is_rejected() -> None:
    with pytest.raises(ValueError):
        snap_bbox_outward((0.0, 0.0, 1.0, 1.0), 0.0)


def test_neighbouring_parcels_in_one_tile_share_a_cache_key() -> None:
    first = tile_key("StrefaPlanistyczna", snap_bbox_outward((503_211.0, 217_203.0, 503_219.0, 217_217.0)))
    second = tile_key("StrefaPlanistyczna", snap_bbox_outward((503_226.0, 217_221.0, 503_244.0, 217_246.0)))
    other_type = tile_key("AktPlanowaniaPrzestrzennego", snap_bbox_outward((503_226.0, 217_221.0, 503_244.0, 217_246.0)))

    assert first == second
    assert other_type != first


def test_status_preference_is_binding_over_project_over_draft_and_returns_the_original_value() -> None:
    assert preferred_status_href([ELABORATION, LEGAL_FORCE, ADOPTION]) == LEGAL_FORCE
    assert preferred_status_href([ELABORATION, ADOPTION]) == ADOPTION
    assert preferred_status_href([None, ELABORATION]) == ELABORATION
    assert preferred_status_href([None, None]) is None
    # Wartość nierozpoznana nie wygrywa z rozpoznaną, ale bez rozpoznanej jest zachowana jako dowód.
    assert preferred_status_href(["zagadkowy", ADOPTION]) == ADOPTION
    assert preferred_status_href(["zagadkowy"]) == "zagadkowy"


def test_clip_keeps_only_the_tile_part_and_drops_a_polygon_outside_the_tile() -> None:
    huge = box(0.0, 0.0, 1_000_000.0, 1_000_000.0)
    tile = (503_200.0, 217_200.0, 503_300.0, 217_300.0)

    inside = huge.intersection(box(*tile))
    clipped = clip_to_tile(box(500_000.0, 200_000.0, 600_000.0, 300_000.0), tile)

    assert clipped is not None and clipped.area == pytest.approx(inside.area)
    assert clip_to_tile(box(0.0, 0.0, 10.0, 10.0), tile) is None


def test_clip_repairs_an_invalid_polygon_instead_of_raising() -> None:
    bowtie = Polygon([(503_210.0, 217_210.0), (503_290.0, 217_290.0), (503_290.0, 217_210.0), (503_210.0, 217_290.0)])
    assert not bowtie.is_valid

    clipped = clip_to_tile(bowtie, (503_200.0, 217_200.0, 503_300.0, 217_300.0))

    assert clipped is not None and clipped.area > 0


def test_envelope_overlap_detects_a_swapped_axis_response() -> None:
    tile = (503_200.0, 217_200.0, 503_300.0, 217_300.0)
    assert envelope_overlaps(box(503_210.0, 217_210.0, 503_220.0, 217_220.0), tile)
    assert not envelope_overlaps(box(217_210.0, 503_210.0, 217_220.0, 503_220.0), tile)


def test_feature_covering_the_parcel_is_found_with_the_official_status() -> None:
    result = observe_layer("planning_zones", [_record(box(503_000.0, 217_000.0, 503_500.0, 217_500.0))], PARCEL)

    assert result.state == "found"
    assert result.feature_count == 1
    assert result.raw_legal_status == LEGAL_FORCE
    assert result.feature_type == "StrefaPlanistyczna"


def test_empty_response_of_a_working_service_is_empty_not_unavailable() -> None:
    result = observe_layer("ouz", [], PARCEL)

    assert result.state == "empty"
    assert result.feature_count == 0 and result.raw_legal_status is None and result.issues == ()


def test_failed_request_is_unavailable_never_empty() -> None:
    result = observe_layer("ouz", None, PARCEL)

    assert result.state == "unavailable"


def test_feature_touching_only_the_parcel_boundary_is_not_present() -> None:
    neighbour = box(503_250.0, 217_210.0, 503_300.0, 217_240.0)  # styka się z krawędzią, bez wspólnej powierzchni
    sliver = box(503_249.9999, 217_210.0, 503_300.0, 217_240.0)  # 0,0001 m × 30 m = 0,003 m²

    assert observe_layer("planning_zones", [_record(neighbour)], PARCEL).state == "empty"
    assert observe_layer("planning_zones", [_record(sliver)], PARCEL).state == "empty"
    just_enough = box(503_250.0 - MIN_PARCEL_OVERLAP_SQM / 30.0 - 1e-6, 217_210.0, 503_300.0, 217_240.0)
    assert observe_layer("planning_zones", [_record(just_enough)], PARCEL).state == "found"


def test_only_features_on_the_parcel_count_and_statuses_come_from_them() -> None:
    on_parcel = _record(box(503_000.0, 217_000.0, 503_240.0, 217_500.0), status=ADOPTION)
    elsewhere = _record(box(504_000.0, 217_000.0, 504_500.0, 217_500.0), status=LEGAL_FORCE)

    result = observe_layer("planning_zones", [on_parcel, elsewhere], PARCEL)

    assert result.feature_count == 1
    assert result.raw_legal_status == ADOPTION  # status obiektu spoza działki nie przecieka do wyniku


def test_plan_references_and_titles_are_deduplicated_and_ordered() -> None:
    first = _record(box(503_000.0, 217_000.0, 503_240.0, 217_225.0), plan_ref="PL.X/1POG", title="Plan A")
    second = _record(box(503_000.0, 217_225.0, 503_240.0, 217_500.0), plan_ref="PL.X/1POG", title="Plan A")

    result = observe_layer("planning_zones", [first, second], PARCEL)

    assert result.feature_count == 2
    assert result.plan_refs == ("PL.X/1POG",)
    assert result.titles == ("Plan A",)


def test_unlocatable_feature_without_other_hits_makes_the_layer_unavailable() -> None:
    unusable = _record(None, geometry_issue=GEOMETRY_AXIS_SUSPECT)

    result = observe_layer("planning_zones", [unusable], PARCEL)

    assert result.state == "unavailable"
    assert GEOMETRY_AXIS_SUSPECT in result.issues


def test_feature_outside_the_tile_is_ignored_and_the_layer_stays_empty() -> None:
    outside = _record(None, outside_tile=True)

    result = observe_layer("ouz", [outside], PARCEL)

    assert result.state == "empty" and result.issues == () and result.feature_count == 0


def test_unlocatable_feature_without_a_reason_is_reported_as_unusable_geometry() -> None:
    result = observe_layer("planning_zones", [_record(None)], PARCEL)

    assert result.state == "unavailable" and GEOMETRY_UNUSABLE in result.issues


def test_unlocatable_feature_does_not_hide_a_located_one() -> None:
    located = _record(box(503_000.0, 217_000.0, 503_500.0, 217_500.0))
    unusable = _record(None, geometry_issue=GEOMETRY_UNUSABLE)

    result = observe_layer("planning_zones", [unusable, located], PARCEL)

    assert result.state == "found" and result.feature_count == 1


def test_incomplete_response_never_claims_absence_but_keeps_what_was_found() -> None:
    nothing = observe_layer("planning_zones", [], PARCEL, complete=False)
    found = observe_layer(
        "planning_zones", [_record(box(503_000.0, 217_000.0, 503_500.0, 217_500.0))], PARCEL, complete=False
    )

    assert nothing.state == "unavailable" and RESPONSE_PARTIAL in nothing.issues
    assert found.state == "found" and RESPONSE_PARTIAL in found.issues


def test_multipolygon_feature_is_matched_by_any_part() -> None:
    far = box(600_000.0, 300_000.0, 600_100.0, 300_100.0)
    near = box(503_000.0, 217_000.0, 503_500.0, 217_500.0)

    result = observe_layer("planning_zones", [_record(MultiPolygon([far, near]))], PARCEL)

    assert result.state == "found"


def test_tiny_parcel_can_still_be_matched_by_a_feature_that_covers_it() -> None:
    tiny = box(10.0, 10.0, 10.5, 10.5)  # 0,25 m² < MIN_PARCEL_OVERLAP_SQM

    result = observe_layer("planning_zones", [_record(box(0.0, 0.0, 100.0, 100.0))], tiny)

    assert result.state == "found"
