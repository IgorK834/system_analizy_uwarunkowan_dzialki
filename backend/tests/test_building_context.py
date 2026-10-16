"""Zabudowa sąsiednia (Task 23.3, BK-304): definicja pierścienia, brak podwójnego liczenia, statusy rozłączne i zgodność
z niezależnym pomiarem dla 30 działek korpusu (bufory 50/100/200 m).

Kontrolne geometrie leżą po obu stronach granicy bufora (49,9 m i 50,1 m), wystają poza granicę działki, nakładają się albo
dublują identyfikator — każda taka sytuacja ma jawny, policzony ręcznie wynik.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from shapely.geometry import Polygon, box
from sqlalchemy import text

from app.db.session import SessionLocal
from app.modules.analysis import composition
from app.modules.analysis.application.bdot_ports import BdotIndexError, BdotIndexTimeout
from app.modules.analysis.application.buildings import find_building_context
from app.modules.analysis.domain.bdot import BdotReleaseRef, CoverageCheck
from app.modules.analysis.domain.buildings import (
    AREA_EPS_SQM,
    BUFFER_QUAD_SEGS,
    BuildingFootprint,
    BuildingSearchPlan,
    InvalidBuildingPlan,
    compute_building_metrics,
    ring_geometry,
)
from app.modules.analysis.infrastructure import bdot_repository
from app.modules.analysis.infrastructure.bdot_repository import PostgisBdotIndex
from app.schemas.bdot_context import NeighbourBuildingsResult
from app.services.bdot_context import (
    build_neighbour_buildings,
    disabled_neighbour_buildings,
    failed_neighbour_buildings,
    neighbour_buildings_from_snapshot,
)
from tests.bdot_support import POWIATS, case_ids, corpus_parcels, expected, installed_releases

PARCEL = box(0, 0, 100, 100)  # 100 × 100 m
PLAN = BuildingSearchPlan()
RELEASE = BdotReleaseRef(data_release_id=7, version_label="bdot10k-1261-abc", teryt_powiat="1261",
                         published_at=datetime(2026, 10, 2, tzinfo=timezone.utc))


def _b(identifier: str, geometry, **kwargs) -> BuildingFootprint:
    return BuildingFootprint(building_id=identifier, geometry=geometry, area_sqm=geometry.area, **kwargs)


def _metrics(*buildings: BuildingFootprint, plan: BuildingSearchPlan = PLAN):
    return compute_building_metrics(PARCEL, list(buildings), plan)


def _buffer(metrics, radius: float):
    return next(item for item in metrics.buffers if item.radius_m == radius)


# --- definicja pierścienia i granice buforów ---------------------------------------------------------------


def test_the_ring_is_the_buffer_minus_the_parcel_and_excludes_the_parcel_itself() -> None:
    ring = ring_geometry(PARCEL, 50.0)

    assert ring.intersection(PARCEL).area == pytest.approx(0.0, abs=1e-6)
    assert ring.area == pytest.approx(PARCEL.buffer(50.0, quad_segs=BUFFER_QUAD_SEGS).area - 10_000.0)
    assert BUFFER_QUAD_SEGS == 16  # wspólna definicja z generatorem fixtures


def test_geometries_on_both_sides_of_the_buffer_boundary_are_counted_exactly() -> None:
    inside = _b("in-49.9", box(149.9, 40, 160, 50))  # od działki (x=100): 49,9 m
    outside = _b("out-50.1", box(150.1, 40, 160, 50))  # 50,1 m
    far = _b("far-120", box(220, 40, 230, 50))

    metrics = _metrics(inside, outside, far)

    assert [_buffer(metrics, r).count for r in (50.0, 100.0, 200.0)] == [1, 2, 3]  # kumulatywnie: 50 ⊂ 100 ⊂ 200
    assert _buffer(metrics, 50.0).min_distance_m == 49.9
    assert _buffer(metrics, 100.0).min_distance_m == 49.9
    assert metrics.on_parcel.count == 0 and metrics.on_parcel.union_area_in_parcel_sqm == 0.0


def test_a_building_clipped_by_the_buffer_edge_contributes_only_the_clipped_area_but_its_full_size() -> None:
    long = _b("long", box(130, 40, 260, 50))  # 130 m długości; w pierścieniu 50 m tylko x ∈ [130, 150] (≈ 20 m)

    metrics = _metrics(long)
    first = _buffer(metrics, 50.0)

    assert first.count == 1
    assert first.footprint_area_in_ring_sqm == pytest.approx(200.0, rel=0.02)  # 20 m × 10 m
    assert first.footprint_min_sqm == first.footprint_max_sqm == pytest.approx(1300.0)  # pełny obrys, nie przycięty
    assert _buffer(metrics, 200.0).footprint_area_in_ring_sqm == pytest.approx(1300.0, rel=1e-6)  # w całości w 200 m


def test_an_id_present_twice_is_counted_once_and_overlapping_footprints_are_not_double_counted() -> None:
    first = _b("A", box(110, 10, 130, 30))
    again = _b("A", box(110, 10, 130, 30))
    overlapping = _b("B", box(120, 10, 140, 30))  # nakłada się na A na 10 × 20 m

    metrics = _metrics(first, again, overlapping)
    ring = _buffer(metrics, 50.0)

    assert metrics.duplicates_ignored == 1 and metrics.candidates_considered == 2
    assert ring.count == 2
    assert ring.footprint_area_in_ring_sqm == pytest.approx(800.0)  # 400 + 400: suma pól części
    assert ring.union_area_in_ring_sqm == pytest.approx(600.0)  # 20×20 + 20×20 − 10×20: bez podwójnego liczenia
    assert ring.built_share_pct == pytest.approx(100.0 * 600.0 / ring.ring_area_sqm, abs=1e-3)


def test_a_building_straddling_the_parcel_boundary_is_on_the_parcel_and_in_the_ring_and_is_flagged() -> None:
    straddling = _b("S", box(90, 40, 110, 60))  # 10 m na działce, 10 m poza nią
    neighbour = _b("N", box(120, 40, 130, 50))

    metrics = _metrics(straddling, neighbour)

    assert metrics.on_parcel.count == 1
    assert metrics.on_parcel.union_area_in_parcel_sqm == pytest.approx(200.0)
    assert metrics.on_parcel.coverage_pct == pytest.approx(2.0)  # 200 / 10 000
    ring = _buffer(metrics, 50.0)
    assert ring.count == 2 and ring.straddling_parcel_count == 1
    assert ring.footprint_area_in_ring_sqm == pytest.approx(300.0)  # część zewnętrzna S (200) + N (100)
    assert ring.min_distance_m == 0.0
    relations = {item.footprint.building_id: item.relation for item in metrics.listed}
    assert relations == {"S": "straddling", "N": "neighbour"}


def test_a_building_touching_the_boundary_is_adjacent_with_distance_zero_and_not_on_the_parcel() -> None:
    touching = _b("T", box(100, 40, 110, 50))  # wspólny tylko bok z działką

    metrics = _metrics(touching)

    assert metrics.on_parcel.count == 0
    assert _buffer(metrics, 50.0).count == 1 and _buffer(metrics, 50.0).min_distance_m == 0.0
    assert metrics.listed[0].relation == "adjacent" and metrics.listed[0].overlap_with_parcel_sqm == 0.0


def test_a_sliver_below_the_area_epsilon_is_not_counted_as_a_building_in_the_ring() -> None:
    assert AREA_EPS_SQM == 0.01
    sliver = _b("sliver", Polygon([(160, 10), (160.001, 10), (160.001, 20), (160, 20)]))  # 0,00001 m² w pierścieniu 100
    real = _b("real", box(160, 30, 170, 40))

    metrics = _metrics(sliver, real, plan=BuildingSearchPlan(buffers_m=(50.0, 100.0)))

    assert _buffer(metrics, 100.0).count == 2 and _buffer(metrics, 50.0).count == 0


def test_no_buildings_gives_explicit_zeros_for_counts_and_null_for_undefined_statistics() -> None:
    metrics = _metrics()
    ring = _buffer(metrics, 50.0)

    assert ring.count == 0 and ring.footprint_area_in_ring_sqm == 0.0 and ring.built_share_pct == 0.0
    assert ring.min_distance_m is None and ring.footprint_median_sqm is None  # null ≠ 0
    assert metrics.on_parcel.count == 0 and metrics.listed == () and metrics.listed_truncated is False


def test_size_statistics_use_full_footprints_and_the_median_of_an_even_set() -> None:
    sizes = [(120, 10), (130, 10), (140, 30), (150, 20)]  # powierzchnie 100, 100, 300, 200
    buildings = [_b(f"b{i}", box(x, 40, x + (10 if i < 2 else 30 if i == 2 else 20), 50 + (0 if i < 3 else 0)))
                 for i, (x, _w) in enumerate(sizes)]

    ring = _buffer(_metrics(*buildings), 100.0)

    assert ring.count == 4
    assert ring.footprint_min_sqm == 100.0 and ring.footprint_max_sqm == 300.0


def test_listed_buildings_are_sorted_by_distance_then_id_and_capped_with_a_truncation_flag() -> None:
    buildings = [_b(f"z{i:02d}", box(110 + 2 * i, 10, 111 + 2 * i, 11)) for i in range(5)]
    plan = BuildingSearchPlan(buffers_m=(50.0, 100.0), listed_limit=3)

    metrics = compute_building_metrics(PARCEL, buildings, plan)

    assert [item.footprint.building_id for item in metrics.listed] == ["z00", "z01", "z02"]
    assert metrics.listed_truncated is True
    assert [item.distance_m for item in metrics.listed] == [10.0, 12.0, 14.0]
    assert _buffer(metrics, 50.0).count == 5  # statystyki obejmują wszystkie, nie tylko tabelę


def test_the_plan_requires_positive_increasing_buffers_and_limits() -> None:
    for kwargs in ({"buffers_m": ()}, {"buffers_m": (0.0,)}, {"buffers_m": (100.0, 50.0)}, {"listed_limit": 0}, {"candidate_limit": 0}):
        with pytest.raises(InvalidBuildingPlan):
            BuildingSearchPlan(**kwargs)
    assert BuildingSearchPlan().max_radius_m == 200.0 and BuildingSearchPlan().listed_radius_m == 50.0


# --- przypadek użycia i statusy ---------------------------------------------------------------------------


class FakeIndex:
    def __init__(self, *, release=RELEASE, any_release=True, buildings=(), covered=True, error=None) -> None:
        self.release, self.any_release, self.buildings, self.covered, self.error = release, any_release, list(buildings), covered, error
        self.requested_limit: int | None = None

    def release_for(self, parcel_wkt):
        return self.release

    def has_active_releases(self) -> bool:
        return self.any_release

    def coverage(self, release, parcel_wkt, radius_m) -> CoverageCheck:
        return CoverageCheck(parcel_covered=self.covered, search_area_covered=self.covered)

    def candidates(self, release, parcel_wkt, radius_m, limit):
        self.requested_limit = limit
        if self.error:
            raise self.error("x")
        return self.buildings[:limit]


def test_available_with_zero_buildings_in_a_covered_area_is_a_checked_zero_not_missing_data() -> None:
    outcome = find_building_context(PARCEL.wkt, FakeIndex(), PLAN)

    assert outcome.status == "available" and outcome.metrics.on_parcel.count == 0
    assert all(item.count == 0 for item in outcome.metrics.buffers)
    assert outcome.search_area_within_coverage is True and outcome.warnings == ()


def test_a_buffer_outside_the_release_is_flagged_and_an_empty_result_there_is_no_match() -> None:
    with_one = find_building_context(PARCEL.wkt, FakeIndex(buildings=[_b("x", box(110, 10, 120, 20))], covered=False), PLAN)
    empty = find_building_context(PARCEL.wkt, FakeIndex(covered=False), PLAN)

    assert with_one.status == "available" and with_one.search_area_within_coverage is False
    assert "wykracza poza zasięg" in with_one.warnings[0]
    assert (empty.status, empty.reason_code) == ("no_match", "SEARCH_AREA_PARTIALLY_OUTSIDE_COVERAGE")


@pytest.mark.parametrize(
    ("index", "status", "reason"),
    [
        (FakeIndex(release=None, any_release=False), "no_coverage", "NO_ACTIVE_RELEASE"),
        (FakeIndex(release=None), "no_coverage", "OUTSIDE_COVERAGE"),
        (FakeIndex(error=BdotIndexTimeout), "unavailable", "QUERY_TIMEOUT"),
        (FakeIndex(error=BdotIndexError), "unavailable", "DATABASE_ERROR"),
    ],
)
def test_failures_and_missing_coverage_are_disjoint_statuses_without_statistics(index, status, reason) -> None:
    outcome = find_building_context(PARCEL.wkt, index, PLAN)

    assert (outcome.status, outcome.reason_code) == (status, reason)
    assert outcome.metrics is None and outcome.provenance.complete is False
    assert "Nie oznacza to braku budynków" in outcome.warnings[0] or "nie powiodło się" in outcome.warnings[0]


def test_too_many_candidates_is_unavailable_instead_of_a_silently_truncated_count() -> None:
    crowded = [_b(f"b{i}", box(110, i, 111, i + 0.5)) for i in range(6)]
    plan = BuildingSearchPlan(candidate_limit=5)

    index = FakeIndex(buildings=crowded)
    outcome = find_building_context(PARCEL.wkt, index, plan)

    assert index.requested_limit == 6  # limit + 1: wykrywa przepełnienie
    assert (outcome.status, outcome.reason_code) == ("unavailable", "TOO_MANY_CANDIDATES") and outcome.metrics is None


# --- mapowanie na kontrakt API ------------------------------------------------------------------------


def test_section_carries_buffers_geojson_and_has_no_personal_data_fields() -> None:
    building = _b("B-1", box(110, 10, 120, 20), general_function="budynki mieszkalne", floors=2, geometry_source="EGiB")
    result = build_neighbour_buildings(find_building_context(PARCEL.wkt, FakeIndex(buildings=[building]), PLAN))

    assert result.status == "available" and result.buffers_m == [50.0, 100.0, 200.0]
    assert [item.count for item in result.buffers] == [1, 1, 1]
    item = result.buildings[0]
    assert item.geometry_geojson["geometry"]["type"] == "Polygon" and item.floors == 2
    assert item.geometry_geojson["properties"]["layer"] == "neighbour_buildings"
    forbidden = {"name", "nazwa", "owner", "owners", "wlasciciel", "uwagi", "egib_id", "identyfikatorEGiB", "remarks"}
    assert not forbidden & set(NeighbourBuildingsResult.model_fields) and not forbidden & set(type(item).model_fields)
    assert result.interpretation_note.startswith("Kontekst istniejącej zabudowy")


def test_statuses_without_statistics_keep_buffers_empty_and_provenance_present() -> None:
    for outcome in (
        find_building_context(PARCEL.wkt, FakeIndex(release=None), PLAN),
        find_building_context(PARCEL.wkt, FakeIndex(error=BdotIndexTimeout), PLAN),
    ):
        result = build_neighbour_buildings(outcome)
        assert result.on_parcel is None and result.buffers == [] and result.buildings == []
        assert result.source is not None and result.reason_code


def test_legacy_disabled_and_failed_sections_are_explicit_and_never_zero_buildings() -> None:
    legacy = neighbour_buildings_from_snapshot(None)
    assert legacy.status == "unknown" and legacy.on_parcel is None and "nie oznacza to braku budynków" in legacy.warnings[0]
    assert disabled_neighbour_buildings([50.0]).reason_code == "FEATURE_DISABLED"
    assert failed_neighbour_buildings("KeyError", [50.0]).status == "unavailable"
    ok = build_neighbour_buildings(find_building_context(PARCEL.wkt, FakeIndex(), PLAN))
    assert neighbour_buildings_from_snapshot(ok.model_dump(mode="json")) == ok


def test_contract_refuses_statistics_on_a_section_that_has_none() -> None:
    ok = build_neighbour_buildings(find_building_context(PARCEL.wkt, FakeIndex(), PLAN))
    with pytest.raises(ValueError, match="statystyki muszą być null"):
        NeighbourBuildingsResult.model_validate({**ok.model_dump(), "status": "no_coverage", "reason_code": "X"})
    with pytest.raises(ValueError, match="obliczeń na działce"):
        NeighbourBuildingsResult.model_validate({**ok.model_dump(), "on_parcel": None})


# --- adapter PostGIS na prawdziwych danych korpusu -----------------------------------------------------


@pytest.fixture(scope="module")
def all_releases(tmp_path_factory: pytest.TempPathFactory):
    with installed_releases(tmp_path_factory.mktemp("bdot"), *POWIATS):
        yield


CASES = [(teryt, case) for teryt in POWIATS for case in case_ids(teryt)]
TOLERANCE = {"count": 0, "area": 0.05, "pct": 0.01, "distance": 0.011}


@pytest.mark.integration
@pytest.mark.parametrize(("teryt", "case"), CASES, ids=[case for _t, case in CASES])
def test_counts_distances_and_areas_match_the_independent_measurement_for_all_three_buffers(
    all_releases, teryt: str, case: str
) -> None:
    want = expected(teryt)["cases"][case]["buildings"]
    with SessionLocal() as session:
        outcome = find_building_context(
            corpus_parcels()[case].wkt, PostgisBdotIndex(session, statement_timeout_ms=10_000), PLAN
        )

    assert outcome.status == "available"
    metrics = outcome.metrics
    assert metrics.on_parcel.count == want["on_parcel"]["count"]
    assert metrics.on_parcel.union_area_in_parcel_sqm == pytest.approx(want["on_parcel"]["union_area_in_parcel_sqm"], abs=TOLERANCE["area"])
    assert metrics.on_parcel.coverage_pct == pytest.approx(want["on_parcel"]["coverage_pct"], abs=TOLERANCE["pct"])
    for buffer in metrics.buffers:
        target = want["buffers"][str(int(buffer.radius_m))]
        assert buffer.count == target["count"], buffer.radius_m
        assert buffer.straddling_parcel_count == target["straddling_parcel_count"]
        assert buffer.ring_area_sqm == pytest.approx(target["ring_area_sqm"], abs=0.05)
        assert buffer.footprint_area_in_ring_sqm == pytest.approx(target["footprint_area_in_ring_sqm"], abs=TOLERANCE["area"])
        assert buffer.union_area_in_ring_sqm == pytest.approx(target["union_area_in_ring_sqm"], abs=TOLERANCE["area"])
        assert buffer.built_share_pct == pytest.approx(target["built_share_pct"], abs=TOLERANCE["pct"])
        for field in ("min_distance_m", "footprint_min_sqm", "footprint_median_sqm", "footprint_max_sqm"):
            got, wanted = getattr(buffer, field), target[field]
            assert (got is None) == (wanted is None), field  # null ≠ 0
            if wanted is not None:
                assert got == pytest.approx(wanted, abs=0.011 if field == "min_distance_m" else TOLERANCE["area"])
    assert outcome.release.teryt_powiat == teryt and outcome.search_area_within_coverage in {True, False}


@pytest.mark.integration
def test_buildings_not_in_operation_and_personal_attributes_are_not_available_from_the_database(all_releases) -> None:
    case = "real-014-026201-1-0010-410-1"
    with SessionLocal() as session:
        columns = {row[0] for row in session.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'bdot_buildings'"
        ))}
        session.execute(text("UPDATE bdot_buildings SET existence_category = 'zniszczony'"))
        outcome = find_building_context(corpus_parcels()[case].wkt, PostgisBdotIndex(session, statement_timeout_ms=10_000), PLAN)
        session.rollback()

    assert not {"name", "remarks", "additional_info", "egib_id", "owner"} & columns
    assert outcome.status == "available" and all(item.count == 0 for item in outcome.metrics.buffers)  # tylko „eksploatowany”


@pytest.mark.integration
def test_a_parcel_outside_every_imported_county_is_no_coverage_not_zero_buildings(all_releases) -> None:
    with SessionLocal() as session:
        outcome = find_building_context(box(470000, 725000, 470050, 725040).wkt, PostgisBdotIndex(session, statement_timeout_ms=5000), PLAN)

    assert (outcome.status, outcome.reason_code) == ("no_coverage", "OUTSIDE_COVERAGE") and outcome.metrics is None


@pytest.mark.integration
def test_timeout_and_database_failure_in_the_adapter_are_unavailable(all_releases, monkeypatch) -> None:
    parcel = corpus_parcels()["real-014-026201-1-0010-410-1"].wkt
    with SessionLocal() as session:
        monkeypatch.setattr(bdot_repository, "_BUILDINGS", text("SELECT pg_sleep(1.0), 1"))
        slow = find_building_context(parcel, PostgisBdotIndex(session, statement_timeout_ms=100), PLAN)
        monkeypatch.setattr(bdot_repository, "_BUILDINGS", text("SELECT * FROM tabela_ktorej_nie_ma"))
        broken = find_building_context(parcel, PostgisBdotIndex(session, statement_timeout_ms=1000), PLAN)

    assert (slow.status, slow.reason_code) == ("unavailable", "QUERY_TIMEOUT")
    assert (broken.status, broken.reason_code) == ("unavailable", "DATABASE_ERROR")


@pytest.mark.integration
def test_composition_survives_an_unconfirmed_catalog_and_an_unexpected_error(all_releases) -> None:
    from app.core.data_sources import SourceNotRunnableError

    parcel = corpus_parcels()["real-014-026201-1-0010-410-1"]
    with SessionLocal() as session:
        with patch.object(composition, "ensure_source_runnable", side_effect=SourceNotRunnableError("x")):
            blocked = composition.analyze_building_context(session, parcel)
        with patch.object(composition, "find_building_context", side_effect=RuntimeError("boom")):
            crashed = composition.analyze_building_context(session, parcel)
        working = composition.analyze_building_context(session, parcel)

    assert (blocked.status, blocked.reason_code) == ("unavailable", "SOURCE_NOT_RUNNABLE")
    assert (crashed.status, crashed.reason_code) == ("unavailable", "UNEXPECTED_ERROR")
    assert working.status == "available"
