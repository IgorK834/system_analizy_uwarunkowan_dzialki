"""Kontekst drogowy (Task 23.1–23.2, BK-305): domena, przypadek użycia, adapter PostGIS i mapowanie na kontrakt API.

Wartości oczekiwane dla 30 działek korpusu (``tests/fixtures/bdot10k/*/expected.json``) policzono niezależnie od
aplikacji (współrzędne z ``gml:posList``, Shapely). Adapter dostaje te same dane przez import i zapytania PostGIS.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from shapely.geometry import LineString, box
from sqlalchemy import text

from app.db.session import SessionLocal
from app.modules.analysis import composition
from app.modules.analysis.application.bdot_ports import BdotIndexError, BdotIndexTimeout
from app.modules.analysis.application.roads import find_road_context
from app.modules.analysis.domain.bdot import BdotReleaseRef, CoverageCheck
from app.modules.analysis.domain.roads import (
    ROAD_ACCESS_DISCLAIMER,
    InvalidRoadSearchPlan,
    RoadCandidate,
    RoadSearchPlan,
    classify_relation,
    round_distance,
    select_nearest,
)
from app.modules.analysis.infrastructure import bdot_repository
from app.modules.analysis.infrastructure.bdot_repository import PostgisBdotIndex
from app.schemas.bdot_context import RoadContextResult
from app.services.bdot_context import (
    build_road_context,
    disabled_road_context,
    failed_road_context,
    road_context_from_snapshot,
)
from tests.bdot_support import POWIATS, case_ids, corpus_parcels, expected, installed_releases

PLAN = RoadSearchPlan(radii_m=(25.0, 50.0, 100.0, 250.0, 500.0), candidate_limit=25)
RELEASE = BdotReleaseRef(
    data_release_id=7, version_label="bdot10k-1261-abc", teryt_powiat="1261",
    published_at=datetime(2026, 10, 2, tzinfo=timezone.utc), data_as_of=None,
)
PARCEL = box(0, 0, 10, 10)


class FakeIndex:
    """Port indeksu dróg w pamięci; każdy krok można zamienić na błąd (failure injection)."""

    def __init__(self, *, release=RELEASE, any_release=True, roads=(), covered=True, error=None) -> None:
        self.release, self.any_release, self.roads, self.covered, self.error = release, any_release, list(roads), covered, error
        self.calls: list[tuple] = []

    def release_for(self, parcel_wkt: str):
        if self.error == "release":
            raise BdotIndexTimeout("x")
        return self.release

    def has_active_releases(self) -> bool:
        return self.any_release

    def coverage(self, release, parcel_wkt, radius_m) -> CoverageCheck:
        return CoverageCheck(parcel_covered=self.covered, search_area_covered=self.covered)

    def nearest(self, release, parcel_wkt, radius_m, limit):
        self.calls.append(("nearest", radius_m))
        if self.error == "nearest":
            raise BdotIndexError("db", "DATABASE_ERROR")
        return [road for road in self.roads if road.distance_m <= radius_m][:limit]

    def adjacency_counts(self, release, parcel_wkt):
        return (2, 1)


def _candidate(distance: float, road_id: str = "R1", **kwargs) -> RoadCandidate:
    return RoadCandidate(
        road_id=road_id, distance_m=distance, intersects=kwargs.pop("intersects", distance == 0.0),
        touches=kwargs.pop("touches", False), category="gminna", road_class="droga lokalna",
        road_wkt=LineString([(0, -5), (20, -5)]).wkt, link_wkt=LineString([(5, 0), (5, -5)]).wkt, **kwargs,
    )


# --- domena -------------------------------------------------------------------------------------------


def test_relation_is_a_geometric_fact_and_touching_wins_over_intersecting() -> None:
    assert classify_relation(False, False) == "disjoint"
    assert classify_relation(True, False) == "intersects"
    assert classify_relation(True, True) == "touches"  # wspólny tylko brzeg: oś biegnie po granicy działki


def test_nearest_is_deterministic_and_ties_prefer_the_stronger_relation_then_the_id() -> None:
    close = _candidate(5.0004, "B")
    same = _candidate(5.0, "A")
    stronger = _candidate(5.0, "Z", intersects=True)  # remis w granicach 1 mm → silniejsza relacja

    assert select_nearest([close, same, stronger]) is stronger
    assert select_nearest([same, close]) is same
    assert select_nearest([_candidate(3.0, "B"), _candidate(3.0, "A")]).road_id == "A"
    assert select_nearest([]) is None
    assert round_distance(1.004999) == 1.0 and round_distance(1.005001) == 1.01
    with pytest.raises(ValueError):
        round_distance(-0.1)


@pytest.mark.parametrize("radii", [(), (0.0, 10.0), (10.0, 10.0), (50.0, 25.0)])
def test_plan_requires_positive_strictly_increasing_radii(radii: tuple[float, ...]) -> None:
    with pytest.raises(InvalidRoadSearchPlan):
        RoadSearchPlan(radii_m=radii)
    with pytest.raises(InvalidRoadSearchPlan):
        RoadSearchPlan(radii_m=(10.0,), candidate_limit=0)


def test_the_distance_reference_is_explicit_and_the_disclaimer_is_the_mandatory_sentence() -> None:
    from app.modules.analysis.domain.roads import DISTANCE_REFERENCE

    assert DISTANCE_REFERENCE == "parcel_polygon_to_carriageway_axis"
    assert "nie jest potwierdzeniem prawnego dostępu działki do drogi publicznej" in ROAD_ACCESS_DISCLAIMER


# --- przypadek użycia: rozłączne statusy i metadane wyszukiwania ---------------------------------------


def test_the_search_widens_the_buffer_until_a_road_is_found_and_reports_the_radius() -> None:
    outcome = find_road_context(PARCEL.wkt, FakeIndex(roads=[_candidate(70.0)]), PLAN)

    assert outcome.status == "available" and outcome.nearest.distance_m == 70.0
    assert outcome.search_radius_m == 100.0 and outcome.searched_radii_m == (25.0, 50.0, 100.0)
    assert outcome.max_search_radius_m == 500.0 and outcome.candidate_limit == 25
    assert outcome.provenance.source_id == "bdot10k" and outcome.provenance.data_release_id == 7
    assert outcome.search_area_within_coverage is True and outcome.warnings == ()


def test_a_road_touching_the_parcel_reports_the_adjacency_counts() -> None:
    outcome = find_road_context(PARCEL.wkt, FakeIndex(roads=[_candidate(0.0)]), PLAN)

    assert outcome.nearest.relation == "intersects"
    assert (outcome.intersecting_road_count, outcome.touching_road_count) == (2, 1)


def test_an_empty_bounded_radius_is_no_match_and_not_proof_that_there_is_no_road() -> None:
    outcome = find_road_context(PARCEL.wkt, FakeIndex(roads=[_candidate(900.0)]), PLAN)

    assert outcome.status == "no_match" and outcome.reason_code == "SEARCH_RADIUS_EXHAUSTED"
    assert outcome.nearest is None and outcome.search_radius_m == 500.0
    assert outcome.searched_radii_m == PLAN.radii_m
    assert "nie dowodzi braku drogi" in outcome.warnings[0]
    assert outcome.provenance.complete is True


def test_a_search_area_partly_outside_the_release_is_flagged_and_never_called_complete() -> None:
    found = find_road_context(PARCEL.wkt, FakeIndex(roads=[_candidate(10.0)], covered=False), PLAN)
    empty = find_road_context(PARCEL.wkt, FakeIndex(roads=[], covered=False), PLAN)

    assert found.status == "available" and found.search_area_within_coverage is False
    assert "poza zasięgiem" in found.warnings[0]
    assert empty.status == "no_match" and empty.reason_code == "SEARCH_AREA_PARTIALLY_OUTSIDE_COVERAGE"


def test_no_coverage_distinguishes_an_empty_registry_from_a_parcel_outside_imported_counties() -> None:
    none_imported = find_road_context(PARCEL.wkt, FakeIndex(release=None, any_release=False), PLAN)
    elsewhere = find_road_context(PARCEL.wkt, FakeIndex(release=None, any_release=True), PLAN)

    assert (none_imported.status, none_imported.reason_code) == ("no_coverage", "NO_ACTIVE_RELEASE")
    assert (elsewhere.status, elsewhere.reason_code) == ("no_coverage", "OUTSIDE_COVERAGE")
    for outcome in (none_imported, elsewhere):
        assert outcome.nearest is None and outcome.release is None
        assert "Nie oznacza to braku drogi" in outcome.warnings[0]


@pytest.mark.parametrize(("step", "error", "reason"), [("nearest", BdotIndexTimeout, "QUERY_TIMEOUT"), ("nearest", BdotIndexError, "DATABASE_ERROR")])
def test_timeout_and_database_errors_are_unavailable_never_a_missing_road(step, error, reason) -> None:
    class Failing(FakeIndex):
        def nearest(self, *args, **kwargs):
            raise error("x")

    outcome = find_road_context(PARCEL.wkt, Failing(roads=[_candidate(1.0)]), PLAN)

    assert outcome.status == "unavailable" and outcome.reason_code == reason
    assert outcome.nearest is None and outcome.provenance.complete is False
    assert outcome.provenance.error_code == reason


# --- mapowanie na kontrakt API ------------------------------------------------------------------------


def test_available_outcome_becomes_a_section_with_geojson_distance_and_the_mandatory_note() -> None:
    result = build_road_context(find_road_context(PARCEL.wkt, FakeIndex(roads=[_candidate(12.345)]), PLAN))

    assert result.status == "available" and result.nearest.distance_m == 12.35 and result.nearest.relation == "disjoint"
    assert result.nearest.road_geojson["geometry"]["type"] == "LineString"
    assert result.nearest.road_geojson["properties"]["layer"] == "road_context"
    assert result.nearest.link_geojson["properties"]["layer"] == "road_context_link"
    assert result.interpretation_note == ROAD_ACCESS_DISCLAIMER
    assert result.source.source_id == "bdot10k" and result.source.data_release_id == 7
    assert result.distance_reference == "parcel_polygon_to_carriageway_axis"


@pytest.mark.parametrize("status", ["no_match", "no_coverage", "unavailable"])
def test_statuses_without_a_road_have_no_nearest_and_still_carry_provenance(status: str) -> None:
    index = {
        "no_match": FakeIndex(roads=[]),
        "no_coverage": FakeIndex(release=None),
        "unavailable": FakeIndex(error="nearest"),
    }[status]

    result = build_road_context(find_road_context(PARCEL.wkt, index, PLAN))

    assert result.status == status and result.nearest is None and result.reason_code
    assert result.source is not None and result.interpretation_note == ROAD_ACCESS_DISCLAIMER
    if status == "unavailable":
        assert result.source.manual_review_required is True
    if status == "no_coverage":
        # Bez wydania nic nie odczytano: czasu pobrania nie ma (świeżość „nieustalona”, nie „aktualna” względem próby).
        assert result.source.fetched_at is None and result.source.data_release_id is None
    else:
        assert result.source.fetched_at is not None  # wydanie ustalono — czas importu jest czasem pobrania


def test_contract_refuses_inconsistent_sections() -> None:
    good = build_road_context(find_road_context(PARCEL.wkt, FakeIndex(roads=[_candidate(1.0)]), PLAN))
    with pytest.raises(ValueError, match="najbliższej drogi"):
        good.model_copy(update={"nearest": None}).model_validate(good.model_copy(update={"nearest": None}).model_dump())
    with pytest.raises(ValueError, match="nearest musi być null"):
        RoadContextResult.model_validate({**good.model_dump(), "status": "no_match", "reason_code": "SEARCH_RADIUS_EXHAUSTED"})
    with pytest.raises(ValueError, match="provenance"):
        RoadContextResult.model_validate({**good.model_dump(), "source": None})


def test_legacy_snapshot_is_unknown_not_a_missing_road_and_disabled_feature_is_explicit() -> None:
    legacy = road_context_from_snapshot(None)
    assert legacy.status == "unknown" and legacy.nearest is None and legacy.source is None
    assert "nie oznacza to braku drogi" in legacy.warnings[0]
    assert disabled_road_context().status == "unavailable" and disabled_road_context().reason_code == "FEATURE_DISABLED"
    assert failed_road_context("ValueError").reason_code == "UNEXPECTED_ERROR"
    roundtrip = road_context_from_snapshot(build_road_context(find_road_context(PARCEL.wkt, FakeIndex(roads=[_candidate(2.0)]), PLAN)).model_dump(mode="json"))
    assert roundtrip.status == "available"


def test_the_contract_has_no_legal_access_field_anywhere() -> None:
    """Kryterium 308: w schemacie wyniku nie istnieje pole prawnego dostępu do drogi."""
    from app.schemas.analyze import AnalyzeResponse

    schema = str(AnalyzeResponse.model_json_schema())
    assert "legal_public_road_access" not in schema and "legal_access" not in schema
    names = {name for model in (RoadContextResult,) for name in model.model_fields}
    assert not {name for name in names if "legal" in name}


# --- adapter PostGIS na prawdziwych danych korpusu ----------------------------------------------------


@pytest.fixture(scope="module")
def all_releases(tmp_path_factory: pytest.TempPathFactory):
    with installed_releases(tmp_path_factory.mktemp("bdot"), *POWIATS):
        yield


CASES = [(teryt, case) for teryt in POWIATS for case in case_ids(teryt)]


@pytest.mark.integration
@pytest.mark.parametrize(("teryt", "case"), CASES, ids=[case for _t, case in CASES])
def test_distance_relation_and_attributes_match_the_independent_measurement(all_releases, teryt: str, case: str) -> None:
    want = expected(teryt)["cases"][case]["roads"]
    with SessionLocal() as session:
        index = PostgisBdotIndex(session, statement_timeout_ms=5000)
        outcome = find_road_context(corpus_parcels()[case].wkt, index, PLAN)

    assert outcome.status == "available" and want["status"] == "available"
    assert outcome.search_radius_m == want["search_radius_m"]
    assert outcome.nearest.distance_m == pytest.approx(want["distance_m"], abs=0.05)  # kryterium 308: tolerancja 0,05 m
    assert round_distance(outcome.nearest.distance_m) == pytest.approx(want["distance_m"], abs=0.011)
    assert outcome.nearest.relation == want["relation"]
    assert outcome.nearest.road_id == want["road_id"]
    assert (outcome.nearest.category, outcome.nearest.road_class) == (want["management_category"], want["road_class"])
    if want["distance_m"] == 0.0:
        assert (outcome.intersecting_road_count, outcome.touching_road_count) == (
            want["intersecting_road_count"], want["touching_road_count"],
        )
    assert outcome.release.teryt_powiat == teryt and outcome.search_area_within_coverage in {True, False}


@pytest.mark.integration
def test_a_parcel_outside_every_imported_county_is_no_coverage_not_a_missing_road(all_releases) -> None:
    gdansk = box(470000, 725000, 470050, 725040)  # poza sześcioma zaimportowanymi powiatami
    with SessionLocal() as session:
        outcome = find_road_context(gdansk.wkt, PostgisBdotIndex(session, statement_timeout_ms=5000), PLAN)

    assert (outcome.status, outcome.reason_code) == ("no_coverage", "OUTSIDE_COVERAGE")


@pytest.mark.integration
def test_a_small_radius_without_roads_is_a_bounded_no_match_with_the_search_metadata(all_releases) -> None:
    case = "real-006-126105-9-0001-49-2"  # najbliższa droga 30,21 m
    with SessionLocal() as session:
        outcome = find_road_context(
            corpus_parcels()[case].wkt, PostgisBdotIndex(session, statement_timeout_ms=5000),
            RoadSearchPlan(radii_m=(5.0, 10.0, 20.0), candidate_limit=25),
        )

    assert outcome.status == "no_match" and outcome.reason_code == "SEARCH_RADIUS_EXHAUSTED"
    assert outcome.searched_radii_m == (5.0, 10.0, 20.0) and outcome.search_radius_m == 20.0


@pytest.mark.integration
def test_the_shortest_link_matches_the_reported_distance(all_releases) -> None:
    case = "real-006-126105-9-0001-49-2"
    with SessionLocal() as session:
        outcome = find_road_context(corpus_parcels()[case].wkt, PostgisBdotIndex(session, statement_timeout_ms=5000), PLAN)

    link = LineString(list(__import__("shapely").from_wkt(outcome.nearest.link_wkt).coords))
    assert link.length == pytest.approx(outcome.nearest.distance_m, abs=1e-6)
    assert outcome.nearest.road_wkt.startswith("MULTILINESTRING")
    assert outcome.nearest.position and outcome.nearest.feature_version


@pytest.mark.integration
def test_roads_not_in_operation_are_ignored(all_releases) -> None:
    with SessionLocal() as session:
        session.execute(text("UPDATE bdot_roads SET existence_category = 'w budowie'"))
        outcome = find_road_context(corpus_parcels()["real-005-126105-9-0001-580-4"].wkt, PostgisBdotIndex(session, statement_timeout_ms=5000), PLAN)
        session.rollback()

    assert outcome.status == "no_match"  # nic nie jest „eksploatowany”: ani przecięcie, ani sąsiedztwo nie liczą się jako droga


# --- awarie źródła: żadna nie kończy się „brakiem drogi” ----------------------------------------------


@pytest.mark.integration
def test_query_timeout_is_a_controlled_unavailable_result_and_leaves_the_session_usable(all_releases, monkeypatch) -> None:
    monkeypatch.setattr(bdot_repository, "_NEAREST", text("SELECT pg_sleep(1.0), 1"))
    with SessionLocal() as session:
        outcome = find_road_context(
            corpus_parcels()["real-005-126105-9-0001-580-4"].wkt, PostgisBdotIndex(session, statement_timeout_ms=100), PLAN
        )
        assert session.execute(text("SELECT 1")).scalar_one() == 1  # sesja po wycofaniu działa dalej

    assert (outcome.status, outcome.reason_code) == ("unavailable", "QUERY_TIMEOUT")


@pytest.mark.integration
def test_a_broken_query_is_unavailable_with_a_database_reason(all_releases, monkeypatch) -> None:
    monkeypatch.setattr(bdot_repository, "_NEAREST", text("SELECT * FROM tabela_ktorej_nie_ma"))
    with SessionLocal() as session:
        outcome = find_road_context(
            corpus_parcels()["real-005-126105-9-0001-580-4"].wkt, PostgisBdotIndex(session, statement_timeout_ms=1000), PLAN
        )

    assert (outcome.status, outcome.reason_code) == ("unavailable", "DATABASE_ERROR")


@pytest.mark.integration
def test_composition_turns_an_unconfirmed_catalog_and_unexpected_errors_into_unavailable(all_releases) -> None:
    from app.core.data_sources import SourceNotRunnableError

    parcel = corpus_parcels()["real-005-126105-9-0001-580-4"]
    with SessionLocal() as session:
        with patch.object(composition, "ensure_source_runnable", side_effect=SourceNotRunnableError("x")):
            blocked = composition.analyze_road_context(session, parcel)
        with patch.object(composition, "find_road_context", side_effect=RuntimeError("boom")):
            crashed = composition.analyze_road_context(session, parcel)
        working = composition.analyze_road_context(session, parcel)

    assert (blocked.status, blocked.reason_code) == ("unavailable", "SOURCE_NOT_RUNNABLE")
    assert (crashed.status, crashed.reason_code) == ("unavailable", "UNEXPECTED_ERROR")
    assert working.status == "available"


@pytest.mark.integration
def test_without_any_release_the_status_says_that_nothing_was_imported(tmp_path: Path) -> None:
    """Ostatni test modułu: czyści wszystkie wydania, więc musi być po testach korzystających z ``all_releases``."""
    with installed_releases(tmp_path):  # czyste środowisko bez wydań
        with SessionLocal() as session:
            outcome = find_road_context(corpus_parcels()["real-005-126105-9-0001-580-4"].wkt, PostgisBdotIndex(session, statement_timeout_ms=5000), PLAN)

    assert (outcome.status, outcome.reason_code) == ("no_coverage", "NO_ACTIVE_RELEASE")
