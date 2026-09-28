"""Realne działki (ULDK + WCS NMT) porównane z referencją GIS ``gdaldem`` (BK-302).

``gdaldem slope/aspect -alg Horn`` to implementacja używana przez algorytmy
GDAL „Slope”/„Aspect” w QGIS Processing. Fixtures są zamrożone z SHA-256 w
manifeście (``tests/fixtures/terrain/real``); porównanie jest offline.
Tolerancje: spadek piksela 0,01°, ekspozycja 0,1°, statystyki 0,01°, udział
klasy 0,1 pp (referencja zapisuje wynik w Float32).
"""

from __future__ import annotations

import shutil

import pytest

from scripts.compare_terrain_reference import _manifest, compare_fixture
from tests.terrain_fixtures import requires_gdal

pytestmark = [
    requires_gdal,
    pytest.mark.skipif(shutil.which("gdaldem") is None, reason="Wymaga gdaldem."),
]


@pytest.mark.parametrize("slug", [item["slug"] for item in _manifest()])
def test_real_parcel_matches_gdaldem_reference(slug: str) -> None:
    report = compare_fixture(slug)

    assert report["compared_pixels"] == report["parcel_pixels"] > 0
    assert report["max_abs_slope_diff_deg"] < 0.01
    assert report["compared_aspect_pixels"] > 0
    assert report["max_abs_aspect_diff_deg"] < 0.1
    ours, reference = report["ours"], report["reference_gdaldem"]
    for key in ("mean_deg", "median_deg", "p90_deg", "max_deg"):
        assert ours[key] == pytest.approx(reference[key], abs=0.01), key
    for class_id, share in ours["classes_pct"].items():
        assert share == pytest.approx(reference["classes_pct"][class_id], abs=0.1), class_id
    assert report["resolution_m"] == 1.0
    assert report["profile_samples"] > 1


def test_manifest_covers_steep_undulating_and_flat_terrain() -> None:
    slugs = {item["slug"]: item for item in _manifest()}
    assert set(slugs) == {"zakopane_stok", "krakow_zakrzowek", "warszawa_plasko"}
    for item in slugs.values():
        assert len(item["geotiff_sha256"]) == 64
        assert item["wcs_request_url"].startswith("https://mapy.geoportal.gov.pl/")
