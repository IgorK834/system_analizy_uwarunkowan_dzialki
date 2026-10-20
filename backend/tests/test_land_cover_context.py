"""AU-204: zamrożone rzeczywiste GML trzech powiatów, niezależny pomiar i wstrzykiwanie awarii."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from shapely.geometry import shape
from sqlalchemy import text

from app.db.session import SessionLocal
from app.modules.analysis.application.bdot_ports import BdotIndexError, BdotIndexTimeout
from app.modules.analysis.application.landcover import find_land_cover
from app.modules.analysis.composition import analyze_land_cover
from app.modules.analysis.domain.bdot import CoverageCheck
from app.modules.analysis.infrastructure.landcover import PostgisLandCoverIndex
from app.services.land_cover import build_land_cover, land_cover_from_snapshot, missing_land_cover
from app.schemas.land_cover import LandCoverResult
from tests.bdot_support import import_package, purge_bdot

FIXTURES = Path(__file__).parent / "fixtures/source_contracts/bdot_land_cover"


def test_pdf_does_not_place_parcel_outside_coverage_when_only_forest_search_crosses_county():
    """Rzeczywista odpowiedź po imporcie pełnej paczki 2461, nie geometria zastępcza."""
    from app.services.report import _land_cover_context

    frozen = json.loads((FIXTURES / "2461-partial-forest.json").read_text())
    value = LandCoverResult.model_validate(frozen["land_cover"])
    assert value.status == "available" and value.parcel.classified_share_pct == 100
    assert value.forest.status == "no_coverage" and value.forest.distance_m is None
    assert value.forest.reason_code == "SEARCH_AREA_PARTIALLY_OUTSIDE_COVERAGE"
    context = _land_cover_context(value)
    assert context["forest_status_label"] == "brak pełnego pokrycia zaimportowanymi danymi BDOT10k"
    assert "działka poza" not in context["forest_status_label"]
    assert context["distance"] == "nie określono"


@pytest.fixture(params=["1261", "2816", "1418"])
def land_release(request, tmp_path):
    county = request.param
    with SessionLocal() as db:
        purge_bdot(db)
        outcome = import_package(db, county, tmp_path, package=FIXTURES / county / f"{county}_GML.zip")
        assert outcome.status == "succeeded"
    try:
        yield county, json.loads((FIXTURES / county / "expected.json").read_text())
    finally:
        with SessionLocal() as db:
            purge_bdot(db)


def test_shares_forest_buffers_roads_and_buildings_match_independent_real_geometry(land_release):
    from app.modules.analysis.composition import analyze_building_context, analyze_road_context
    from app.services.bdot_context import build_neighbour_buildings, build_road_context

    county, expected = land_release
    with SessionLocal() as db:
        for case, want in expected["cases"].items():
            parcel = shape(want["parcel"])
            result = build_land_cover(analyze_land_cover(db, parcel))
            assert result.status == "available", (case, result)
            assert result.source.source_id == "bdot10k" and result.source.data_as_of
            assert result.release.teryt_powiat == county
            for key in ("parcel", "surroundings"):
                zone = getattr(result, key)
                assert sum(c.share_pct for c in zone.classes) == pytest.approx(100, abs=0.01)
                assert zone.area_sqm == pytest.approx(want["zones"][key]["area_sqm"], abs=0.05)
                for row in zone.classes:
                    target = want["zones"][key]["classes"][row.class_code]
                    assert row.area_sqm == pytest.approx(target["area_sqm"], abs=0.05)
                    assert row.share_pct == pytest.approx(target["share_pct"], abs=0.001)
            assert result.forest.status == "available"
            assert result.forest.distance_m == pytest.approx(want["forest_distance_m"], abs=0.05)
            for b in result.forest.buffers:
                assert b.overlap_with_parcel_sqm == pytest.approx(
                    want["forest_buffers"][str(int(b.radius_m))], abs=0.05
                )
            assert land_cover_from_snapshot(result.model_dump(mode="json")) == result
            road = build_road_context(analyze_road_context(db, parcel))
            assert road.nearest.distance_m == pytest.approx(want["road"]["distance_m"], abs=0.05)
            assert road.nearest.relation == want["road"]["relation"]
            if county == "1418":
                buildings = build_neighbour_buildings(analyze_building_context(db, parcel))
                observed = {b.building_id: b for b in buildings.buildings}
                assert len(observed) == len(want["buildings_50m"]) == 10
                for b in want["buildings_50m"]:
                    assert observed[b["id"]].distance_m == pytest.approx(b["distance_m"], abs=0.05)
                assert result.forest.distance_m == pytest.approx(12.73, abs=0.01)


def test_failure_injection_never_means_no_restriction(land_release):
    county, expected = land_release
    parcel = shape(next(iter(expected["cases"].values()))["parcel"])
    with SessionLocal() as db:
        index = PostgisLandCoverIndex(db, statement_timeout_ms=10000)
        for exc in (BdotIndexTimeout("timeout"), BdotIndexError("schema drift")):
            with patch.object(index, "cover", side_effect=exc):
                result = build_land_cover(find_land_cover(parcel.wkt, index))
                assert result.status == "unavailable" and result.parcel is None
                assert result.forest.distance_m is None and result.source
        with patch.object(index, "cover", return_value=[]):
            result = build_land_cover(find_land_cover(parcel.wkt, index))
            assert result.status == "unknown" and result.parcel is None
            assert result.reason_code == "LAND_COVER_GEOMETRY_GAPS_OR_OVERLAPS"
        with patch.object(index, "land_cover_ready", return_value=False):
            result = build_land_cover(find_land_cover(parcel.wkt, index))
            assert result.status == "no_coverage" and result.reason_code == "LAND_COVER_NOT_IMPORTED"
            assert result.forest.status == "no_coverage" and result.forest.reason_code == "LAND_COVER_NOT_IMPORTED"
        with patch.object(index, "coverage", return_value=CoverageCheck(True, False)):
            assert find_land_cover(parcel.wkt, index).status == "no_coverage"
        with patch.object(index, "forest", return_value=None):
            with patch.object(index, "coverage", return_value=CoverageCheck(True, True)):
                result = build_land_cover(find_land_cover(parcel.wkt, index))
                assert result.forest.status == "no_match" and result.forest.distance_m is None
        with patch.object(index, "forest", return_value=None):
            with patch.object(index, "coverage", side_effect=[CoverageCheck(True, True), CoverageCheck(True, False)]):
                result = build_land_cover(find_land_cover(parcel.wkt, index))
                assert result.forest.status == "no_coverage" and result.forest.distance_m is None
                assert result.status == "available" and result.source.manual_review_required
                assert result.source.confidence == 0.6 and result.warnings
                from tests.report_map_reference import load_fixture
                from app.services.section_quality import build_section_quality
                from app.services.completeness import build_completeness

                response, _ = load_fixture("multizone")
                response = response.model_copy(update={"land_cover": result, "section_quality": None})
                quality = build_section_quality(response)
                assessed = next(item for item in quality.sections if item.section == "land_cover")
                assert assessed.status == "partial" and assessed.manual_review_required
                completeness = build_completeness(response, matrix=quality)
                assert next(item for item in completeness.sections if item.section == "land_cover").state == "missing"
        with pytest.raises(BdotIndexError, match="limit"):
            release = index.release_for(parcel.wkt)
            index.cover(release, parcel.wkt, 200, 0)


def test_missing_and_old_release_and_catalog_failure(tmp_path):
    parcel_data = json.loads((FIXTURES / "1261/expected.json").read_text())
    parcel = shape(next(iter(parcel_data["cases"].values()))["parcel"])
    with SessionLocal() as db:
        purge_bdot(db)
        result = build_land_cover(analyze_land_cover(db, parcel))
        assert result.status == "no_coverage" and result.reason_code == "NO_ACTIVE_RELEASE"
        assert result.forest.status == "no_coverage" and result.forest.reason_code == "NO_ACTIVE_RELEASE"
        import_package(db, "1261", tmp_path)
        assert build_land_cover(analyze_land_cover(db, parcel)).reason_code == "LAND_COVER_NOT_IMPORTED"
        with patch("app.modules.analysis.composition._bdot_runnable", return_value=False):
            assert analyze_land_cover(db, parcel).reason_code == "SOURCE_NOT_RUNNABLE"
        with patch("app.modules.analysis.application.landcover.find_land_cover", side_effect=ValueError("schema")):
            assert analyze_land_cover(db, parcel).status == "unavailable"
        db.execute(text("UPDATE data_releases SET is_active=false"))
        db.commit()
        assert analyze_land_cover(db, parcel).reason_code == "NO_ACTIVE_RELEASE"
        purge_bdot(db)
    assert land_cover_from_snapshot(None).status == "unknown"
    assert missing_land_cover("unavailable", "FEATURE_DISABLED").parcel is None
    assert missing_land_cover("unavailable", "FEATURE_DISABLED").forest.status == "unavailable"
    from app.services.report import _land_cover_context

    assert _land_cover_context(land_cover_from_snapshot(None))["forest_status_label"] == "nieustalony — brak pełnych danych"
    with pytest.raises(ValueError):
        LandCoverResult(status="available")


def test_reader_rejects_changed_crs_in_real_frozen_package(tmp_path):
    import zipfile
    from app.core.data_sources import get_catalog
    from app.modules.imports.infrastructure.bdot10k.reader import ZipBdot10kReader, Bdot10kReadError

    source = FIXTURES / "1418/1418_GML.zip"
    altered = tmp_path / "changed.zip"
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(altered, "w", zipfile.ZIP_DEFLATED) as out:
        for name in src.namelist():
            data = src.read(name)
            if "__OT_PTLZ_A" in name:
                data = data.replace(b"EPSG:2180", b"EPSG:4326")
            out.writestr(name, data)
    with pytest.raises(Bdot10kReadError, match="EPSG:4326"):
        ZipBdot10kReader(altered, teryt_powiat="1418", mapping=get_catalog().get("bdot10k").field_mapping).read()


def test_invalid_snapshot_degrades_instead_of_raising_validation_error():
    result = land_cover_from_snapshot({"status": "available", "parcel": "schema changed"})
    assert result.status == "unavailable" and result.reason_code == "SNAPSHOT_SCHEMA_INVALID"


def test_geometry_gap_and_overlap_are_not_normalized_to_a_hundred_percent(land_release):
    from dataclasses import replace
    from app.modules.analysis.domain.landcover import coverage_is_complete

    _, expected = land_release
    p = shape(next(iter(expected["cases"].values()))["parcel"])
    with SessionLocal() as db:
        index = PostgisLandCoverIndex(db, statement_timeout_ms=10000)
        rows = index.cover(index.release_for(p.wkt), p.wkt, 200, 20000)
        assert coverage_is_complete(rows)
        # Jeden rzeczywisty obiekt jest zgłoszony dwa razy pod inną klasą.
        assert not coverage_is_complete([*rows, replace(rows[0], class_code="OT_PTNZ_A")])
        assert not coverage_is_complete([r for r in rows if r != rows[0]])


def test_missing_pt_layer_is_explicit_no_coverage_not_a_zero_share(tmp_path):
    import zipfile
    from app.modules.analysis.composition import analyze_land_cover

    altered = tmp_path / "missing-pt.zip"
    with (
        zipfile.ZipFile(FIXTURES / "1418/1418_GML.zip") as src,
        zipfile.ZipFile(altered, "w", zipfile.ZIP_DEFLATED) as out,
    ):
        for name in src.namelist():
            if "__OT_PTLZ_A" not in name:
                out.writestr(name, src.read(name))
    with SessionLocal() as db:
        purge_bdot(db)
        import_package(db, "1418", tmp_path / "artifacts", package=altered)
        want = json.loads((FIXTURES / "1418/expected.json").read_text())
        p = shape(next(iter(want["cases"].values()))["parcel"])
        result = build_land_cover(analyze_land_cover(db, p))
        assert result.status == "no_coverage" and result.reason_code == "LAND_COVER_NOT_IMPORTED"
        assert result.parcel is None and result.forest.distance_m is None
        purge_bdot(db)
