"""Narzędzia pomocnicze Task 23.2–23.3: niezależny pomiar GDAL oraz generator fixtures UI (zgodność z backendem)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import compare_live_smoke_bdot as live_check
from scripts import verify_bdot10k_with_gdal as gdal_check
from tests.repo_structure import find_repo_root

BACKEND_DIR = Path(__file__).resolve().parents[1]
FIXTURES = BACKEND_DIR / "tests" / "fixtures" / "bdot10k"
requires_gdal = pytest.mark.skipif(shutil.which("ogr2ogr") is None, reason="brak ogr2ogr (GDAL) w PATH")


def _expected(teryt: str) -> dict:
    return json.loads((FIXTURES / teryt / "expected.json").read_text(encoding="utf-8"))


def test_the_parcel_wkt_comes_straight_from_the_corpus_geometry_in_easting_northing_order() -> None:
    case_id = next(iter(_expected("1261")["cases"]))

    wkt = gdal_check.parcel_wkt(case_id)

    assert wkt.startswith("MULTIPOLYGON(((") and wkt.endswith(")))")
    first_x, first_y = (float(value) for value in wkt.removeprefix("MULTIPOLYGON(((").split(",")[0].split())
    assert 500_000 < first_x < 800_000 and 100_000 < first_y < 800_000  # EPSG:2180: easting przed northing


def test_every_corpus_parcel_has_a_matching_geometry_file_and_expected_values() -> None:
    cases = {teryt: _expected(teryt)["cases"] for teryt in ("0221", "0262", "1261", "1465", "2461", "2816")}

    assert sum(len(item) for item in cases.values()) == 30
    for powiat_cases in cases.values():
        for case_id in powiat_cases:
            assert gdal_check.parcel_wkt(case_id).startswith("MULTIPOLYGON")


def _matching_pair() -> tuple[dict, dict, dict]:
    want = next(iter(_expected("1261")["cases"].values()))
    road = {"status": "available", **{key: want["roads"][key] for key in ("search_radius_m", "distance_m", "relation", "road_id", "management_category", "road_class")}}
    buildings = {
        "on_parcel": {"count": want["buildings"]["on_parcel"]["count"], "area_sqm": want["buildings"]["on_parcel"]["union_area_in_parcel_sqm"]},
        "buffers": {
            radius: {"count": item["count"], "clipped_sqm": item["footprint_area_in_ring_sqm"], "min_distance_m": item["min_distance_m"]}
            for radius, item in want["buildings"]["buffers"].items()
        },
    }
    return want, road, buildings


def test_compare_case_accepts_equal_measurements_and_values_within_tolerance() -> None:
    want, road, buildings = _matching_pair()
    assert gdal_check.compare_case("c", want, road, buildings) == []

    road["distance_m"] += gdal_check.DISTANCE_TOLERANCE_M / 2
    buildings["buffers"]["50"]["clipped_sqm"] += gdal_check.AREA_TOLERANCE_SQM / 2
    assert gdal_check.compare_case("c", want, road, buildings) == []


def test_compare_case_reports_every_kind_of_discrepancy() -> None:
    want, road, buildings = _matching_pair()
    road.update(distance_m=road["distance_m"] + 1.0, relation="disjoint", road_id="OTHER")
    buildings["on_parcel"]["count"] += 1
    buildings["buffers"]["50"]["count"] += 1
    buildings["buffers"]["100"]["clipped_sqm"] += 5.0
    buildings["buffers"]["200"]["min_distance_m"] = None if want["buildings"]["buffers"]["200"]["min_distance_m"] is not None else 1.0

    text = "\n".join(gdal_check.compare_case("c", want, road, buildings))

    for fragment in ("odległość", "relation", "road_id", "budynki na działce", "bufor 50 m: liczba", "bufor 100 m: pole", "bufor 200 m: odległość minimalna null"):
        assert fragment in text, fragment

    road_missing = {"status": "not_found_within_radius"}
    assert "status" in "\n".join(gdal_check.compare_case("c", want, road_missing, buildings))


def test_the_report_marks_agreement_and_names_the_tool_that_was_used() -> None:
    want, road, buildings = _matching_pair()
    good = {"case_id": "c-1", "powiat": "1261", "road": road, "buildings": buildings, "problems": []}
    bad = {**good, "case_id": "c-2", "problems": ["droga: odległość 1 ≠ 2"]}

    text = gdal_check.render_markdown([good, bad], "GDAL 3.10.3")

    assert "Nie był to pomiar w QGIS" in text and "GDAL 3.10.3" in text
    assert "ROZBIEŻNOŚCI" in text and "1 z 2 działek zgodnych" in text and "droga: odległość 1 ≠ 2" in text
    assert "ZGODNE" in gdal_check.render_markdown([good], "GDAL 3.10.3")


def test_without_the_tool_the_check_exits_with_a_dedicated_code(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(gdal_check.shutil, "which", lambda _name: None)

    assert gdal_check.main(["--powiat", "1261"]) == 2
    assert "ogr2ogr" in capsys.readouterr().err


@requires_gdal
@pytest.mark.parametrize("teryt", ["1261", "0262"])
def test_the_independent_gdal_measurement_agrees_with_the_frozen_expected_values(teryt: str) -> None:
    results = gdal_check.verify_powiat(teryt, None)

    assert len(results) == len(_expected(teryt)["cases"])
    assert [item for item in results if item["problems"]] == []


@requires_gdal
def test_main_writes_the_json_and_markdown_reports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case_id = next(iter(_expected("2816")["cases"]))

    code = gdal_check.main(["--powiat", "2816", "--case", case_id, "--output", str(tmp_path / "out" / "check")])

    assert code == 0 and "ZGODNE" in capsys.readouterr().out
    saved = json.loads((tmp_path / "out" / "check.json").read_text(encoding="utf-8"))
    assert [item["case_id"] for item in saved["results"]] == [case_id] and saved["gdal"].startswith("GDAL")
    assert (tmp_path / "out" / "check.md").read_text(encoding="utf-8").startswith("# Niezależny pomiar GDAL")


@requires_gdal
def test_a_wrong_expected_value_is_detected_by_the_gdal_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pomiar GDAL nie jest atrapą: zmieniona wartość oczekiwana musi dać kod wyjścia 1."""
    fixtures = tmp_path / "fixtures"
    shutil.copytree(FIXTURES / "1261", fixtures / "1261")
    expected = _expected("1261")
    case_id, case = next(iter(expected["cases"].items()))
    case["buildings"]["buffers"]["50"]["count"] += 7
    (fixtures / "1261" / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
    monkeypatch.setattr(gdal_check, "FIXTURES", fixtures)

    assert gdal_check.main(["--powiat", "1261", "--case", case_id]) == 1


def test_ui_fixtures_are_generated_from_the_backend_and_match_the_committed_file() -> None:
    """``frontend/test/bdotFixtures.ts`` nie jest pisany ręcznie: zmiana kontraktu musi go regenerować."""
    committed = find_repo_root() / "frontend" / "test" / "bdotFixtures.ts"
    if not committed.is_file():
        pytest.skip("brak katalogu frontendu w tym środowisku")

    generated = subprocess.run(
        [sys.executable, "-I", str(BACKEND_DIR / "scripts" / "export_bdot_fixtures.py")],
        capture_output=True, text=True, check=True, cwd=BACKEND_DIR, env={**os.environ, "PYTHONPATH": str(BACKEND_DIR)},
    ).stdout

    assert generated == committed.read_text(encoding="utf-8"), (
        "Odtwórz: python scripts/export_bdot_fixtures.py > ../frontend/test/bdotFixtures.ts"
    )


def _live_case(case_id: str, want: dict) -> dict:
    return {
        "case_id": case_id, "road_status": "available", "buildings_status": "available",
        "road_distance_m": want["roads"]["distance_m"], "buildings_on_parcel": want["buildings"]["on_parcel"]["count"],
    }


def test_live_smoke_matches_the_independent_values_for_all_thirty_corpus_parcels(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    expected = live_check.load_expected()
    assert len(expected) == 30
    report = tmp_path / "smoke.json"
    report.write_text(json.dumps({"cases": [_live_case(case_id, want) for case_id, want in expected.items()]}), encoding="utf-8")

    code = live_check.main([str(report), "--output", str(tmp_path / "out" / "cmp")])

    assert code == 0 and "30 z 30 działek zgodnych" in capsys.readouterr().out
    saved = json.loads((tmp_path / "out" / "cmp.json").read_text(encoding="utf-8"))
    assert len(saved["results"]) == 30 and not [row for row in saved["results"] if row["problems"]]
    assert (tmp_path / "out" / "cmp.md").read_text(encoding="utf-8").startswith("# Żywe API a pomiar niezależny")


def test_live_smoke_comparison_reports_a_wrong_distance_a_wrong_count_a_missing_case_and_a_bad_status(tmp_path: Path) -> None:
    expected = live_check.load_expected()
    ids = sorted(expected)
    cases = [_live_case(case_id, expected[case_id]) for case_id in ids[:-1]]  # ostatni przypadek pominięty
    cases[0]["road_distance_m"] += 1.0
    cases[1]["buildings_on_parcel"] += 1
    cases[2]["road_status"] = "no_coverage"
    cases[3]["road_distance_m"] += live_check.DISTANCE_TOLERANCE_M / 2  # w tolerancji

    rows = {row["case_id"]: row["problems"] for row in live_check.compare(cases, expected)}

    assert any("odległość" in problem for problem in rows[ids[0]])
    assert any("budynki na działce" in problem for problem in rows[ids[1]])
    assert any("statusy" in problem for problem in rows[ids[2]])
    assert rows[ids[3]] == []
    assert rows[ids[-1]] == ["brak przypadku w raporcie live smoke"]
    assert "ROZBIEŻNOŚCI" in live_check.render_markdown(live_check.compare(cases, expected), "x.json")


def test_live_smoke_comparison_rejects_an_unreadable_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{}", encoding="utf-8")

    assert live_check.main([str(broken)]) == 2
    assert "Nieczytelny raport" in capsys.readouterr().err
