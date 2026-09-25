"""BK-202: wersjonowane wydzielenia MPZP i przecięcia pełnego obrysu działki.

Każdy test importuje akty przez realny ``run_mpzp_import`` do PostGIS,
odpytuje ``find_mpzp_zone_intersections`` (GiST ``&&`` → ``ST_Intersects`` →
``ST_Intersection``/``ST_Area`` w EPSG:2180) i buduje wynik
``assess_vector_zones``. Transakcja jest wycofywana po teście.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from shapely import from_wkt, make_valid
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.versioned import LandUseArea
from app.modules.imports.domain.mpzp import stable_zone_identifier
from app.modules.imports.infrastructure.repository import (
    find_mpzp_zone_intersections,
    find_plan_intersections,
)
from app.services.analysis_orchestrator import _assess_mpzp_vectors_safely
from app.services.mpzp_zones import assess_vector_zones
from app.shared.geometry import GeometryPayload
from tests.mpzp_fixtures import act, import_acts, origin, rect, zone

pytestmark = pytest.mark.integration


@pytest.fixture
def session() -> Iterator[Session]:
    db = SessionLocal()
    db.execute(text("SELECT 1"))  # publikacja w SAVEPOINT, wycofywana po teście
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def _assess(session: Session, parcel_wkt: str, *, as_of: datetime | None = None, release_id=None):
    parcel = from_wkt(parcel_wkt)
    rows = find_mpzp_zone_intersections(
        session,
        GeometryPayload(parcel.wkt),
        as_of=as_of or datetime.now(timezone.utc),
        data_release_id=release_id,
    )
    return assess_vector_zones(parcel, rows), rows


def _split_act(identifier: str, x: float, y: float):
    return act(
        identifier,
        rect(x, y, x + 200, y + 100),
        (
            zone("1MN", rect(x, y, x + 60, y + 100), "single_family_housing"),
            zone("2U", rect(x + 60, y, x + 200, y + 100), "services"),
        ),
    )


def test_1000_sqm_parcel_split_600_400_is_independent_of_centroid(
    session: Session, tmp_path: Path
) -> None:
    x, y = origin()
    identifier = f"plan-{uuid4().hex[:8]}"
    import_acts(session, tmp_path, f"mpzp_{uuid4().hex[:8]}", _split_act(identifier, x, y))
    # Kształt L: 600 m² w 1MN (x∈[40,60]) i 400 m² w 2U (x∈[60,100]);
    # centroid ma x = 62, czyli leży w 2U — mimo to 1MN ma większy udział.
    parcel = from_wkt(rect(x + 40, y, x + 60, y + 30)).union(from_wkt(rect(x + 60, y, x + 100, y + 10)))
    assert parcel.area == pytest.approx(1000.0)
    assert parcel.centroid.x - x == pytest.approx(62.0)

    assessment, _rows = _assess(session, parcel.wkt)

    positive = assessment.positive_zones
    assert [(z.zone_symbol, round(z.intersection_area_sqm, 6), round(z.intersection_pct, 6)) for z in positive] == [
        ("1MN", 600.0, 60.0),
        ("2U", 400.0, 40.0),
    ]
    assert positive[0].is_dominant and not positive[1].is_dominant
    assert all(z.assignment_method == "vector_intersection" for z in positive)
    assert positive[0].primary_use == "single_family_housing"
    assert positive[0].act_identifier == identifier and positive[0].act_version
    assert positive[0].intersection_geojson["properties"]["zone_id"] == positive[0].zone_id
    assert assessment.complete_coverage and assessment.overlap_pct == pytest.approx(0.0, abs=1e-6)
    ids = [z.zone_id for z in positive]
    assert len(set(ids)) == 2 and all(i.startswith(f"{identifier}:") for i in ids)

    # Ponowny import tego samego aktu jako nowe wydanie zachowuje ID stref.
    second = import_acts(session, tmp_path, f"mpzp_{uuid4().hex[:8]}", _split_act(identifier, x, y), label="v2")
    assert second.status == "succeeded"
    again, _ = _assess(session, parcel.wkt)
    assert [z.zone_id for z in again.positive_zones] == ids


def test_boundary_touch_is_marked_separately(session: Session, tmp_path: Path) -> None:
    x, y = origin()
    import_acts(session, tmp_path, f"mpzp_{uuid4().hex[:8]}", _split_act(f"plan-{uuid4().hex[:8]}", x, y))
    # Działka w całości w 1MN, jedną krawędzią dotyka 2U (x = 60).
    assessment, rows = _assess(session, rect(x + 50, y + 10, x + 60, y + 20))
    assert len(rows) == 2
    assert [(z.zone_symbol, z.touches_boundary) for z in assessment.zones] == [("1MN", False), ("2U", True)]
    touching = assessment.zones[1]
    assert (touching.intersection_area_sqm, touching.intersection_pct, touching.is_dominant) == (0.0, 0.0, False)
    assert touching.intersection_geojson is None
    assert assessment.positive_zones[0].intersection_pct == pytest.approx(100.0)
    assert any(w.code == "MPZP_ZONE_BOUNDARY_TOUCH" for w in assessment.warnings)


def test_multipolygon_zone_counts_all_parts_once(session: Session, tmp_path: Path) -> None:
    x, y = origin()
    multi = f"MULTIPOLYGON((({x} {y},{x + 40} {y},{x + 40} {y + 100},{x} {y + 100},{x} {y})),(({x + 60} {y},{x + 100} {y},{x + 100} {y + 100},{x + 60} {y + 100},{x + 60} {y})))"
    import_acts(
        session, tmp_path, f"mpzp_{uuid4().hex[:8]}",
        act(
            f"plan-{uuid4().hex[:8]}",
            rect(x, y, x + 100, y + 100),
            (zone("3ZP", multi, "greenery"), zone("4KD", rect(x + 40, y, x + 60, y + 100), "transport")),
        ),
    )
    assessment, _ = _assess(session, rect(x + 20, y, x + 80, y + 10))
    by_symbol = {z.zone_symbol: z for z in assessment.positive_zones}
    assert by_symbol["3ZP"].intersection_area_sqm == pytest.approx(400.0)
    assert by_symbol["3ZP"].intersection_geojson["geometry"]["type"] == "MultiPolygon"
    assert by_symbol["4KD"].intersection_area_sqm == pytest.approx(200.0)
    assert len(assessment.positive_zones) == 2


def test_zone_with_hole_excludes_hole_area(session: Session, tmp_path: Path) -> None:
    x, y = origin()
    holed = (
        f"POLYGON(({x} {y},{x + 100} {y},{x + 100} {y + 100},{x} {y + 100},{x} {y}),"
        f"({x + 40} {y + 40},{x + 60} {y + 40},{x + 60} {y + 60},{x + 40} {y + 60},{x + 40} {y + 40}))"
    )
    import_acts(
        session, tmp_path, f"mpzp_{uuid4().hex[:8]}",
        act(f"plan-{uuid4().hex[:8]}", rect(x, y, x + 100, y + 100), (zone("1MN", holed),)),
    )
    # Działka częściowo w otworze: 20x20, z czego 10x20 w otworze.
    assessment, _ = _assess(session, rect(x + 30, y + 40, x + 50, y + 60))
    (only,) = assessment.positive_zones
    assert only.intersection_area_sqm == pytest.approx(200.0)
    assert only.intersection_pct == pytest.approx(50.0)
    assert any(w.code == "MPZP_PARTIAL_COVERAGE" for w in assessment.warnings)
    # Działka w całości wewnątrz otworu nie ma żadnego przecięcia.
    inside, rows = _assess(session, rect(x + 45, y + 45, x + 55, y + 55))
    assert rows == [] and inside.zones == []


def test_repaired_zone_geometry_and_invalid_parcel(session: Session, tmp_path: Path) -> None:
    x, y = origin()
    # Wydzielenie typu „kokarda” jest naprawiane przy imporcie (repair_geometry).
    bowtie = f"POLYGON(({x} {y},{x + 100} {y + 100},{x + 100} {y},{x} {y + 100},{x} {y}))"
    outcome = import_acts(
        session, tmp_path, f"mpzp_{uuid4().hex[:8]}",
        act(f"plan-{uuid4().hex[:8]}", rect(x, y, x + 100, y + 100), (zone("1MN", bowtie),)),
    )
    assert outcome.stats["repaired"] >= 1
    strip = rect(x + 40, y, x + 60, y + 100)
    assessment, _ = _assess(session, strip)
    (only,) = assessment.positive_zones
    expected = make_valid(from_wkt(bowtie)).intersection(from_wkt(strip)).area
    assert expected == pytest.approx(200.0)
    assert only.intersection_area_sqm == pytest.approx(expected)

    invalid_parcel = from_wkt(f"POLYGON(({x + 40} {y},{x + 60} {y + 100},{x + 60} {y},{x + 40} {y + 100},{x + 40} {y}))")
    assert not invalid_parcel.is_valid
    result, warnings = _assess_mpzp_vectors_safely(session, invalid_parcel, datetime.now(timezone.utc))
    assert result is not None and result.positive_zones
    assert any(w.code == "MPZP_PARCEL_GEOMETRY_REPAIRED" for w in warnings)


def test_overlapping_plans_are_reported_not_normalized(session: Session, tmp_path: Path) -> None:
    x, y = origin()
    import_acts(
        session, tmp_path, f"mpzp_{uuid4().hex[:8]}",
        act(f"plan-a-{uuid4().hex[:8]}", rect(x, y, x + 100, y + 100), (zone("1MN", rect(x, y, x + 100, y + 100)),)),
        act(f"plan-b-{uuid4().hex[:8]}", rect(x + 50, y, x + 150, y + 100), (zone("1U", rect(x + 50, y, x + 150, y + 100)),)),
    )
    assessment, _ = _assess(session, rect(x + 40, y, x + 60, y + 10))
    pcts = sorted(z.intersection_pct for z in assessment.positive_zones)
    assert pcts == pytest.approx([50.0, 100.0])
    assert sum(pcts) == pytest.approx(150.0)  # bez normalizacji do 100%
    assert assessment.overlap_pct == pytest.approx(50.0)
    codes = {w.code for w in assessment.warnings}
    assert {"MPZP_ZONES_OVERLAP", "MPZP_MULTIPLE_ACTS"} <= codes
    assert len(assessment.act_identifiers) == 2
    boundaries = find_plan_intersections(session, GeometryPayload(rect(x + 40, y, x + 60, y + 10)))
    assert {row["act_identifier"] for row in boundaries} == set(assessment.act_identifiers)


def test_area_outside_vector_coverage_is_partial(session: Session, tmp_path: Path) -> None:
    x, y = origin()
    import_acts(session, tmp_path, f"mpzp_{uuid4().hex[:8]}", _split_act(f"plan-{uuid4().hex[:8]}", x, y))
    # Połowa działki leży poza zasięgiem planu (x > 200).
    assessment, _ = _assess(session, rect(x + 190, y, x + 210, y + 10))
    assert assessment.covered_pct == pytest.approx(50.0)
    assert not assessment.complete_coverage
    (only,) = assessment.positive_zones
    assert only.intersection_pct == pytest.approx(50.0)
    partial = next(w for w in assessment.warnings if w.code == "MPZP_PARTIAL_COVERAGE")
    assert "brak danych nie oznacza braku planu" in partial.message


def test_as_of_and_release_pin_keep_old_selection(session: Session, tmp_path: Path) -> None:
    x, y = origin()
    identifier = f"plan-{uuid4().hex[:8]}"
    source_id = f"mpzp_{uuid4().hex[:8]}"
    first = import_acts(session, tmp_path, source_id, _split_act(identifier, x, y))
    parcel = rect(x + 50, y, x + 70, y + 10)
    before = datetime.now(timezone.utc)
    old, _ = _assess(session, parcel, as_of=before)

    # Nowa wersja aktu: granica stref przesunięta na x = 65.
    changed = act(
        identifier,
        rect(x, y, x + 200, y + 100),
        (zone("1MN", rect(x, y, x + 65, y + 100)), zone("2U", rect(x + 65, y, x + 200, y + 100))),
    )
    second = import_acts(session, tmp_path, source_id, changed, label="v2")
    assert second.data_release_id != first.data_release_id

    now, _ = _assess(session, parcel)
    assert [round(z.intersection_pct) for z in now.positive_zones] == [75, 25]
    pinned, _ = _assess(session, parcel, as_of=before)
    assert [(z.zone_id, z.intersection_pct) for z in pinned.positive_zones] == [
        (z.zone_id, z.intersection_pct) for z in old.positive_zones
    ]
    by_release, _ = _assess(session, parcel, release_id=first.data_release_id, as_of=before)
    assert {z.data_release_id for z in by_release.positive_zones} == {first.data_release_id}


def test_import_assigns_deterministic_and_source_identifiers(session: Session, tmp_path: Path) -> None:
    x, y = origin()
    identifier = f"plan-{uuid4().hex[:8]}"
    from app.modules.imports.domain.mpzp import ZoneRecord

    with_source_id = ZoneRecord("5MW", None, GeometryPayload(rect(x, y, x + 50, y + 100)), {}, source_identifier="OBJ-77")
    import_acts(
        session, tmp_path, f"mpzp_{uuid4().hex[:8]}",
        act(identifier, rect(x, y, x + 100, y + 100), (with_source_id, zone("6U", rect(x + 50, y, x + 100, y + 100)))),
    )
    stored = set(session.scalars(select(LandUseArea.zone_identifier).where(LandUseArea.zone_identifier.like(f"{identifier}:%"))))
    assert f"{identifier}:OBJ-77" in stored
    assert any(value.startswith(f"{identifier}:6U:") and len(value.split(":")[-1]) == 16 for value in stored)
    assert stable_zone_identifier(identifier, with_source_id, "f" * 64) == f"{identifier}:OBJ-77"
