"""Checks for the BK-602 centroid versus intersection experiment.

Control cases have hand-calculated expectations; corpus checks use the frozen
shares of the reference corpus; the zone-layer freezing tool is tested against
the real RU WFS fixture with the network mocked.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from shapely.geometry import Point, Polygon, box, shape
from shapely.ops import unary_union

from scripts import compare_centroid_intersection as exp
from scripts import freeze_pog_zone_layers as freeze
from tests.parcel_fixtures_config import find_repo_root

ROOT = find_repo_root()
CORPUS = ROOT / "backend/tests/fixtures/reference_corpus/manifest.json"
RU_ZONE_FIXTURE = ROOT / "backend/tests/fixtures/ru/wfs_pog_getfeature_zone.xml"


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    return json.loads(CORPUS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def experiment(manifest: dict[str, Any]) -> dict[str, Any]:
    return exp.run_experiment(manifest, CORPUS.parent)


# --- controls: rectangle 60/40, concave polygon, hole, centroid on boundary -------


def test_control_cases_match_hand_calculation() -> None:
    specs = exp.control_cases()
    results = [exp.compare_case(spec) for spec in specs]
    assert exp.check_controls(results, specs) == []
    by_id = {item["case_id"]: item for item in results}

    rectangle = by_id["control-rectangle-60-40"]
    assert [(i["zone_id"], round(i["pct"], 9)) for i in rectangle["intersections"]] == [("A", 60.0), ("B", 40.0)]
    assert rectangle["centroid_zone_ids"] == ["A"] and rectangle["missing_ids"] == ["B"]
    assert rectangle["omitted_area_pct"] == pytest.approx(40.0)
    assert rectangle["centroid_is_dominant"] is True

    concave = by_id["control-concave-l-shape"]
    assert concave["centroid"]["location"] == "outside_parcel"
    assert concave["centroid"]["x"] == pytest.approx(116 / 36)
    assert concave["centroid_zone_ids"] == ["N"] and concave["phantom_ids"] == ["N"]
    assert concave["missing_ids"] == ["S", "W"]
    assert concave["omitted_area_pct"] == pytest.approx(100.0)
    assert concave["centroid_is_dominant"] is False and concave["dominant_id"] == "S"

    hole = by_id["control-hole"]
    assert hole["centroid"]["location"] == "in_hole"
    assert [round(i["pct"], 6) for i in hole["intersections"]] == [pytest.approx(45 / 84 * 100, abs=1e-6), pytest.approx(39 / 84 * 100, abs=1e-6)]
    assert hole["centroid_zone_ids"] == ["A"] and hole["centroid_is_dominant"] is True

    tie = by_id["control-boundary-tie"]
    assert tie["centroid_status"] == "tie" and tie["centroid_zone_ids"] == ["A", "B"]
    assert tie["dominant_id"] is None and tie["dominant_tie"] is True
    assert tie["centroid_is_dominant"] is None
    assert tie["omitted_area_pct"] == pytest.approx(0.0)


def test_controls_show_a_user_visible_parameter_difference() -> None:
    result = exp.compare_case(exp.control_cases()[0])
    difference = result["parameter_differences"][0]
    assert difference["parameter"] == "max_building_height_m"
    assert difference["centroid_values"] == [9.0]
    assert [(v["zone_id"], v["value"], round(v["share_pct"], 6)) for v in difference["intersection_values"]] == [
        ("A", 9.0, 60.0), ("B", 12.0, 40.0)
    ]
    assert difference["differs"] is True


def test_control_check_reports_a_wrong_expectation() -> None:
    specs = exp.control_cases()
    results = [exp.compare_case(spec) for spec in specs]
    specs[0].expected = {**specs[0].expected, "missing_ids": []}
    failures = exp.check_controls(results, specs)
    assert any("missing_ids" in failure for failure in failures)


def test_both_production_analyzers_agree_on_controls() -> None:
    rows = exp.engine_agreement(exp.control_cases())
    assert rows and all(row["same_zones_and_areas"] for row in rows)
    with pytest.raises(ValueError):
        exp.production_intersections(box(0, 0, 1, 1), [], "unknown")


# --- centroid rules -----------------------------------------------------------------


def test_centroid_is_kept_where_it_falls_and_uses_covers() -> None:
    l_shape = unary_union([box(0, 0, 10, 2), box(0, 2, 2, 10)])
    assert not l_shape.covers(l_shape.centroid)
    assert l_shape.representative_point().within(l_shape)
    zones = [exp.Zone("N", "N", box(2, 2, 20, 20)), exp.Zone("S", "S", box(-1, -1, 20, 2))]
    assignment = exp.centroid_assignment(l_shape, zones)
    assert assignment["zone_ids"] == ["N"]  # not the representative point's zone
    # boundary-inclusive rule: a point exactly on the shared edge is covered by both
    square = box(0, 0, 10, 10)
    tie = exp.centroid_assignment(square, [exp.Zone("A", "A", box(0, 0, 5, 10)), exp.Zone("B", "B", box(5, 0, 10, 10))])
    assert tie["status"] == "tie" and tie["zone_ids"] == ["A", "B"]
    none = exp.centroid_assignment(square, [exp.Zone("A", "A", box(20, 20, 30, 30))])
    assert none["status"] == "none" and none["zone_ids"] == []


def test_centroid_location_classes() -> None:
    assert exp.centroid_location(box(0, 0, 4, 4), Point(2, 2)) == "inside_parcel"
    assert exp.centroid_location(box(0, 0, 4, 4), Point(0, 2)) == "on_parcel_boundary"
    assert exp.centroid_location(box(0, 0, 4, 4), Point(9, 9)) == "outside_parcel"
    holed = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], holes=[[(4, 4), (6, 4), (6, 6), (4, 6)]])
    assert exp.centroid_location(holed, Point(5, 5)) == "in_hole"
    descriptors = exp.parcel_descriptors(holed)
    assert descriptors["hole_count"] == 1 and descriptors["centroid_location"] == "in_hole"
    assert descriptors["solidity"] == pytest.approx(96 / 100)


def test_overlapping_zones_do_not_double_count_omitted_area() -> None:
    parcel = box(0, 0, 10, 10)
    case = exp.CaseSpec("overlap", "control", parcel, [exp.Zone("A", "A", box(0, 0, 7, 10)), exp.Zone("B", "B", box(4, 0, 10, 10))])
    result = exp.compare_case(case)
    assert result["centroid_status"] == "tie"  # (5,5) is covered by both
    assert result["omitted_area_pct"] == pytest.approx(0.0)


# --- guaranteed bounds from the frozen corpus shares --------------------------------


def test_bounds_from_frozen_corpus_shares(manifest: dict[str, Any]) -> None:
    rows = {row["case_id"]: row for row in exp.bounds_from_corpus(manifest)}
    assert len(rows) == 11  # 7 POG + 4 Kraków MPZP cases with numeric shares
    multi = rows["real-028-246101-1-0004-737-26"]
    assert multi["zone_count"] == 2 and multi["centroid_loses_zone_guaranteed"] is True
    assert multi["min_missing_zones"] == 1
    assert multi["dominant_share_pct"] == pytest.approx(80.34066)
    assert multi["min_omitted_area_pct"] == pytest.approx(19.65934)
    assert multi["max_omitted_area_pct"] == pytest.approx(80.34066)
    even = rows["real-029-246101-1-0004-741-132"]
    assert even["min_omitted_area_pct"] == pytest.approx(49.929208)
    three = rows["real-007-126105-9-0001-540-15"]
    assert three["zone_count"] == 3 and three["significant_zone_count"] == 2  # 0.0417% is below 0.1%
    assert three["centroid_loses_significant_zone_guaranteed"] is True
    assert three["min_missing_zones"] == 2
    single = rows["real-005-126105-9-0001-580-4"]
    assert single["zone_count"] == 1 and single["centroid_loses_zone_guaranteed"] is False
    cases_without_numeric_share = {"real-017-281603-4-0001-496-5", "real-013-026201-1-0009-1319-4"}
    assert not cases_without_numeric_share & set(rows)


def test_bounds_summary_counts_with_denominators(experiment: dict[str, Any]) -> None:
    summary = experiment["bounds_summary"]
    assert summary["cases"] == 11
    assert summary["multi_zone_guaranteed_loss"]["numerator"] == 3
    assert summary["multi_zone_guaranteed_loss"]["denominator"] == 11
    assert summary["min_missing_zones_total"] == 1 + 1 + 2


# --- simulation over real parcel geometries -------------------------------------------


def test_partition_zones_tile_the_bounding_box_and_hand_check() -> None:
    parcel = box(0, 0, 10, 4)
    zones = exp.partition_zones(parcel, "vertical", 0.3)
    assert sum(zone.geometry.intersection(parcel).area for zone in zones) == pytest.approx(parcel.area)
    case = exp.CaseSpec("rect", "simulation", parcel, zones)
    result = exp.compare_case(case)
    assert [(i["zone_id"], round(i["pct"], 6)) for i in result["intersections"]] == [("Z2", 70.0), ("Z1", 30.0)]
    assert result["centroid_zone_ids"] == ["Z2"] and result["centroid_is_dominant"] is True
    assert result["omitted_area_pct"] == pytest.approx(30.0)
    horizontal = exp.compare_case(exp.CaseSpec("rect-h", "simulation", parcel, exp.partition_zones(parcel, "horizontal", 0.25)))
    assert horizontal["omitted_area_pct"] == pytest.approx(25.0)  # centroid y=2 lies in the larger upper zone


def test_simulation_covers_every_parcel_and_scenario(experiment: dict[str, Any], manifest: dict[str, Any]) -> None:
    simulation = experiment["simulation"]
    assert len(simulation) == len(manifest["cases"]) * len(exp.SIMULATION_ORIENTATIONS) * len(exp.SIMULATION_FRACTIONS) == 540
    assert len({item["case_id"] for item in simulation}) == 540
    assert {item["source"]["parcel_case_id"] for item in simulation} == {c["case_id"] for c in manifest["cases"]}
    summary = experiment["simulation_summary"]["all_scenarios"]
    assert summary["cases"] == 540 and summary["cases_with_intersecting_zone"] == 540
    thresholds = summary["omitted_area_at_least"]
    rates = [thresholds[key]["value"] for key in ("1", "5", "10", "25", "50")]
    assert rates == sorted(rates, reverse=True)  # monotone by construction
    assert sum(summary["centroid_status_counts"].values()) == 540
    by_solidity = experiment["simulation_summary"]["by_solidity"]
    assert sum(item["cases"] for item in by_solidity.values()) == experiment["simulation_summary"]["two_zone_scenarios"]["cases"]


def test_parcel_descriptors_cover_all_real_parcels(experiment: dict[str, Any]) -> None:
    summary = experiment["parcel_descriptor_summary"]
    assert summary["parcels"] == 30
    assert sum(summary["centroid_location_counts"].values()) == 30
    assert summary["centroid_location_counts"].get("outside_parcel", 0) >= 1
    assert summary["parcels_with_holes"] >= 1


def test_summary_rates_have_explicit_denominators() -> None:
    def result(missing: list[str], dominant: bool | None, status: str, zones: int, omitted: float) -> dict[str, Any]:
        return {
            "zone_count": zones, "missing_ids": missing, "missing_significant_ids": missing,
            "centroid_is_dominant": dominant, "centroid_status": status, "phantom_ids": [],
            "centroid": {"location": "inside_parcel"}, "omitted_area_pct": omitted, "parameter_differences": [],
        }

    summary = exp.summarize([
        result(["B"], True, "unique", 2, 40.0),
        result(["B"], False, "unique", 2, 60.0),
        result([], None, "tie", 2, 0.0),
        result([], True, "unique", 1, 0.0),
    ])
    assert summary["cases"] == 4 and summary["multi_zone_cases"] == 3
    assert (summary["centroid_omits_at_least_one_zone"]["numerator"], summary["centroid_omits_at_least_one_zone"]["denominator"]) == (2, 4)
    assert summary["multi_zone_centroid_omits_zone"]["denominator"] == 3
    decided = summary["centroid_not_dominant_among_decided"]
    assert (decided["numerator"], decided["denominator"]) == (1, 3)
    assert summary["centroid_not_dominant_unresolved_counted_as_not"]["numerator"] == 2
    assert summary["centroid_unresolved"]["numerator"] == 1
    assert summary["omitted_area_at_least"]["50"]["numerator"] == 1
    assert exp.summarize([])["centroid_omits_at_least_one_zone"]["value"] is None


# --- frozen real zone layers ----------------------------------------------------------


def _parcel_for(manifest: dict[str, Any], case_id: str) -> Any:
    return next(p["geometry"] for p in exp.load_parcels(manifest, CORPUS.parent) if p["case_id"] == case_id)


def _cut_with_share(parcel: Any, share: float) -> float:
    minx, _miny, maxx, _maxy = parcel.bounds
    low, high = minx, maxx
    for _ in range(80):
        middle = (low + high) / 2
        left = parcel.intersection(box(minx - 1, parcel.bounds[1] - 1, middle, parcel.bounds[3] + 1)).area / parcel.area
        if left < share:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def _write_layers(directory: Path, entries: dict[str, list[exp.Zone]], *, status: str = "verified") -> None:
    layers = {}
    for key, zones in entries.items():
        case_id = key.split(":")[0]
        relative, digest = freeze.write_layer(directory, case_id, zones)
        layers[key] = {"path": relative, "sha256": digest, "status": status, "source_release_id": "test@2026-09-30"}
    (directory / "manifest.json").write_text(json.dumps({"layers": layers}), encoding="utf-8")


def test_real_layers_are_used_when_frozen_and_missing_cases_are_listed(tmp_path: Path, manifest: dict[str, Any]) -> None:
    case_id = "real-028-246101-1-0004-737-26"
    parcel = _parcel_for(manifest, case_id)
    cut = _cut_with_share(parcel, 0.1965934)
    zones = [
        exp.Zone("1POG-121SO", "SO", box(parcel.bounds[0] - 1, parcel.bounds[1] - 1, cut, parcel.bounds[3] + 1)),
        exp.Zone("1POG-78SJ", "SJ", box(cut, parcel.bounds[1] - 1, parcel.bounds[2] + 1, parcel.bounds[3] + 1)),
    ]
    _write_layers(tmp_path, {f"{case_id}:pog": zones})
    cases, missing = exp.load_zone_layers(tmp_path, manifest, CORPUS.parent)
    assert [case.case_id for case in cases] == [case_id]
    assert len(missing) == 10 and case_id not in {item["case_id"] for item in missing}
    assert {item["layer"] for item in missing} == {"pog", "mpzp"}
    result = exp.compare_case(cases[0])
    shares = {i["zone_id"]: i["pct"] for i in result["intersections"]}
    assert shares["1POG-121SO"] == pytest.approx(19.65934, abs=1e-4)
    assert shares["1POG-78SJ"] == pytest.approx(80.34066, abs=1e-4)
    assert result["dominant_id"] == "1POG-78SJ" and len(result["missing_ids"]) in {0, 1}
    verification = freeze.verify_against_corpus(
        parcel, zones, [{"symbol": "SO", "share_pct": 19.65934}, {"symbol": "SJ", "share_pct": 80.34066}]
    )
    assert verification["status"] == "verified" and verification["max_abs_difference_pp"] < 0.001


def test_layer_loading_rejects_tampering_and_skips_unverified(tmp_path: Path, manifest: dict[str, Any]) -> None:
    case_id = "real-028-246101-1-0004-737-26"
    zones = [exp.Zone("Z", "SO", box(0, 0, 1, 1))]
    _write_layers(tmp_path, {f"{case_id}:pog": zones}, status="release_mismatch")
    cases, missing = exp.load_zone_layers(tmp_path, manifest, CORPUS.parent)
    assert cases == [] and any("not verified" in item["reason"] for item in missing)
    _write_layers(tmp_path, {f"{case_id}:pog": zones})
    layer = tmp_path / "layers" / f"{case_id}.zones.geojson"
    layer.write_text(layer.read_text() + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        exp.load_zone_layers(tmp_path, manifest, CORPUS.parent)
    cases, missing = exp.load_zone_layers(None, manifest, CORPUS.parent)
    assert cases == [] and len(missing) == 11


# --- the manual freezing tool (network mocked with the real RU fixture) -----------------


def _mini_corpus(tmp_path: Path, expected_share: float = 100.0) -> tuple[dict[str, Any], Path, Any]:
    zones = freeze.zones_from_gml(RU_ZONE_FIXTURE.read_bytes())
    polygon = zones[0].geometry
    parcel = polygon.representative_point().buffer(3.0).intersection(polygon)
    (tmp_path / "parcel.geojson").write_text(json.dumps({"type": "Feature", "properties": {}, "geometry": json.loads(json.dumps(parcel.__geo_interface__))}), encoding="utf-8")
    artifact = {"path": "parcel.geojson", "sha256": "0" * 64, "crs": "EPSG:2180"}
    empty = {"status": "unknown", "values": {"zones": []}}
    case = {
        "case_id": "mini-1", "municipality": "Sopot", "artifact_ids": ["geometry:mini-1"],
        "expected": {
            "pog": {"status": "available", "values": {"zones": [
                {"symbol": {"value": zones[0].symbol, "unit": "code"}, "share": {"value": expected_share, "unit": "percent"}}]}},
            "mpzp": empty,
        },
    }
    return {"corpus_id": "mini", "artifacts": {"geometry:mini-1": artifact}, "cases": [case]}, tmp_path, parcel


def test_freezing_tool_writes_verified_layer_and_manifest(tmp_path: Path) -> None:
    manifest, corpus_dir, _parcel = _mini_corpus(tmp_path)
    output = tmp_path / "frozen"
    content = RU_ZONE_FIXTURE.read_bytes()
    with respx.mock(assert_all_called=True) as router:
        route = router.get(freeze.WFS_URL).mock(return_value=httpx.Response(200, content=content))
        with httpx.Client() as client:
            result = freeze.freeze_layers(manifest, corpus_dir, output, client, now=datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert route.called and result["failures"] == []
    entry = result["layers"]["mini-1:pog"]
    assert entry["status"] == "verified" and entry["verification"]["max_abs_difference_pp"] <= 0.1
    assert entry["response_sha256"] == hashlib.sha256(content).hexdigest()
    assert entry["request"]["params"]["typeNames"] == "app-pog:StrefaPlanistyczna"
    layer = json.loads((output / entry["path"]).read_text())
    assert layer["features"][0]["properties"]["symbol"] and shape(layer["features"][0]["geometry"]).area > 0
    assert hashlib.sha256((output / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
    saved = json.loads((output / "manifest.json").read_text())
    assert saved["attribution"] and saved["layers"].keys() == {"mini-1:pog"}
    # the experiment accepts exactly this directory
    cases, _missing = exp.load_zone_layers(output, manifest | {"cases": manifest["cases"]}, corpus_dir)
    assert [c.case_id for c in cases] == ["mini-1"]


def test_freezing_tool_records_mismatch_and_network_errors(tmp_path: Path) -> None:
    manifest, corpus_dir, _parcel = _mini_corpus(tmp_path, expected_share=60.0)
    content = RU_ZONE_FIXTURE.read_bytes()
    with respx.mock() as router:
        router.get(freeze.WFS_URL).mock(return_value=httpx.Response(200, content=content))
        with httpx.Client() as client:
            mismatch = freeze.freeze_layers(manifest, corpus_dir, tmp_path / "a", client)
    assert mismatch["layers"] == {}
    attempts = mismatch["failures"][0]["attempts"]
    assert [a["status"] for a in attempts] == ["release_mismatch", "release_mismatch"]
    assert [a["swap_axes"] for a in attempts] == [False, True]

    manifest, corpus_dir, _parcel = _mini_corpus(tmp_path)
    with respx.mock() as router:
        router.get(freeze.WFS_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
        with httpx.Client() as client:
            offline = freeze.freeze_layers(manifest, corpus_dir, tmp_path / "b", client)
    assert offline["layers"] == {} and "ConnectTimeout" in offline["failures"][0]["attempts"][0]["error"]


def test_request_parameters_and_legacy_srs_rewrite() -> None:
    straight = freeze.request_params((1.0, 2.0, 3.0, 4.0), swap_axes=False)
    swapped = freeze.request_params((1.0, 2.0, 3.0, 4.0), swap_axes=True)
    assert straight["bbox"] == "1.000,2.000,3.000,4.000,EPSG:2180"
    assert swapped["bbox"] == "2.000,1.000,4.000,3.000,EPSG:2180"
    assert straight["count"] == "100" and straight["version"] == "2.0.0"
    rewritten = freeze.normalize_legacy_srs(b'srsName="http://www.opengis.net/gml/srs/epsg.xml#2180"')
    assert rewritten == b'srsName="EPSG:2180"'
    assert exp.canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


# --- CLI, artifacts, determinism -------------------------------------------------------


def test_cli_writes_all_artifacts_with_hashes(tmp_path: Path, manifest: dict[str, Any]) -> None:
    output = tmp_path / "centroid"
    assert exp.main(["--output-dir", str(output), "--repeat", "2"]) == 0
    names = {path.name for path in output.iterdir()}
    assert {"report.md", "summary.json", "cases.json", "cases.csv", "bounds.csv", "run_manifest.json",
            "determinism.json", "examples"} <= names
    examples = {path.name for path in (output / "examples").iterdir()}
    assert {"control-rectangle-60-40.svg", "control-concave-l-shape.svg", "control-hole.svg",
            "control-boundary-tie.svg", "simulation-lowest-solidity.svg"} == examples
    svg = (output / "examples" / "simulation-lowest-solidity.svg").read_text()
    assert "input_geometry_sha256=" in svg and "artifact_sha256" in svg and "EPSG:2180" in svg
    run_manifest = json.loads((output / "run_manifest.json").read_text())
    summary = json.loads((output / "summary.json").read_text())
    assert run_manifest["manifest_sha256"] == summary["manifest_sha256"]
    assert run_manifest["corpus_sha256"] == hashlib.sha256(CORPUS.read_bytes()).hexdigest()
    assert run_manifest["parameters"]["randomness"].startswith("none")
    assert run_manifest["code_fingerprint"] and "missing" not in run_manifest["code_fingerprint"].values()
    assert json.loads((output / "determinism.json").read_text())["identical"] is True
    with (output / "cases.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 4 + 540
    with (output / "bounds.csv").open(newline="", encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 11
    report = (output / "report.md").read_text()
    for heading in ("Kontrole z ręcznie ustalonymi wynikami", "Dolne granice", "Symulacja", "Ograniczenia"):
        assert heading in report
    assert "Brak zamrożonych geometrii stref rzeczywistych" in report


def test_cli_fails_on_bad_repeat_and_broken_controls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert exp.main(["--output-dir", str(tmp_path / "x"), "--repeat", "0"]) == 1
    assert exp.main(["--corpus", str(tmp_path / "missing.json"), "--output-dir", str(tmp_path / "y")]) == 1
    original = exp.control_cases

    def broken() -> list[exp.CaseSpec]:
        specs = original()
        specs[0].expected = {**specs[0].expected, "omitted_area_pct": 0.0}
        return specs

    monkeypatch.setattr(exp, "control_cases", broken)
    assert exp.main(["--output-dir", str(tmp_path / "z")]) == 1


def test_experiment_is_deterministic_offline(monkeypatch: pytest.MonkeyPatch, manifest: dict[str, Any], experiment: dict[str, Any]) -> None:
    import socket

    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access is not allowed in the offline experiment")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    again = exp.run_experiment(manifest, CORPUS.parent)
    assert exp.canonical_json(again) == exp.canonical_json(experiment)
