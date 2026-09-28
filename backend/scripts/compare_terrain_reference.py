"""Porównanie pochodnych rastra NMT (BK-302) z referencją GIS dla realnych działek.

Referencją jest ``gdaldem slope``/``gdaldem aspect`` z obrazu backendu — to ta
sama implementacja metody Horna, której używają algorytmy „Slope”/„Aspect”
dostawcy GDAL w QGIS Processing (``gdal:slope``, ``gdal:aspect``). Porównanie
odbywa się piksel po pikselu dla pikseli, których środek leży w działce, oraz
na statystykach liczonych tymi samymi wzorami.

Tryby:

* ``fetch`` (wymaga internetu, jednorazowo): pobiera geometrię działki z ULDK i
  wyrównane okno GeoTIFF z WCS produkcyjnym adapterem, zapisuje fixtures i
  manifest z SHA-256 do ``tests/fixtures/terrain/real``;
* ``compare`` (offline): czyta fixtures, liczy wynik produkcyjnym adapterem i
  domeną oraz porównuje z ``gdaldem``; wypisuje raport JSON.

Użycie w kontenerze backendu::

    python -m scripts.compare_terrain_reference fetch
    python -m scripts.compare_terrain_reference compare --output report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shapely import from_wkt

from app.modules.analysis.application.terrain import (
    FetchedElevationRaster,
    ReliefLimits,
    analyze_parcel_relief,
)
from app.modules.analysis.domain.terrain import (
    FLAT_THRESHOLD_PCT,
    SLOPE_CLASSES,
    aligned_window,
    aspect_from_gradient,
    horn_gradient,
    percentile_linear,
    slope_from_gradient,
)
from app.modules.analysis.infrastructure.terrain_raster import (
    ShapelyParcelFootprint,
    WcsCoverageContract,
    WcsElevationRasterSource,
    parse_describe_coverage,
)
from app.modules.imports.composition import GdalRasterProcessor, OgcResult
from app.shared.provenance import Provenance

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "terrain"
REAL_DIR = FIXTURES / "real"
SERVICE_URL = (
    "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/"
    "DigitalTerrainModelFormatTIFF"
)
COVERAGE_ID = "DTM_PL-KRON86-NH_TIFF"
BUFFER_PX = 2

# Realne działki korpusu porównawczego (identyfikatory i punkty ULDK).
PARCELS: tuple[dict[str, str], ...] = (
    {"slug": "zakopane_stok", "xy": "19.9335,49.3050", "note": "stok — teren górski"},
    {"slug": "krakow_zakrzowek", "xy": "19.9120,50.0390", "note": "teren falisty"},
    {"slug": "warszawa_plasko", "xy": "20.9800,52.2300", "note": "teren płaski"},
)


@dataclass(frozen=True)
class _FrozenClient:
    """Klient OGC zwracający zamrożone odpowiedzi — bez sieci."""

    describe: bytes
    coverage: bytes
    request_url: str

    def fetch_wcs_description(self, url: str, **_: object) -> OgcResult:
        return _result(self.describe, url, "WCS:DescribeCoverage")

    def fetch_wcs_coverage(self, url: str, **_: object) -> OgcResult:
        return _result(self.coverage, self.request_url, "WCS:GetCoverage")


def _result(content: bytes, url: str, operation: str) -> OgcResult:
    return OgcResult(
        artifact=content,
        features=(),
        source=Provenance(
            source_id="nmt_wcs",
            fetched_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
            content_hash=hashlib.sha256(content).hexdigest(),
            request_url=url,
            operation=operation,
            complete=True,
        ),
        complete=True,
    )


def fetch_fixtures() -> None:  # pragma: no cover - wymaga internetu
    import httpx

    from app.modules.imports.composition import build_ogc_client

    REAL_DIR.mkdir(parents=True, exist_ok=True)
    describe = (FIXTURES / "wcs_describecoverage.xml").read_bytes()
    spec = parse_describe_coverage(describe, COVERAGE_ID)
    manifest: list[dict[str, Any]] = []
    with httpx.Client(timeout=30) as http, build_ogc_client(
        source_id="nmt_wcs",
        urls=[SERVICE_URL],
        total_timeout_seconds=60,
        max_response_bytes=16 * 1024 * 1024,
    ) as client:
        for parcel in PARCELS:
            response = http.get(
                "https://uldk.gugik.gov.pl/",
                params={
                    "request": "GetParcelByXY",
                    "xy": f"{parcel['xy']},4326",
                    "result": "teryt,geom_wkt",
                    "srid": "2180",
                },
            )
            response.raise_for_status()
            status, payload = response.text.strip().split("\n", 1)
            if status.strip() != "0":
                raise RuntimeError(f"ULDK nie zwrócił działki dla {parcel['slug']}")
            parcel_id, ewkt = payload.split("|", 1)
            wkt = ewkt.split(";", 1)[1].strip()
            geometry = from_wkt(wkt)
            window = aligned_window(
                ShapelyParcelFootprint(geometry).bounds,
                grid_origin_x=spec.origin_x,
                grid_origin_y=spec.origin_y,
                resolution=spec.resolution,
                buffer_px=BUFFER_PX,
            )
            result = client.fetch_wcs_coverage(
                SERVICE_URL,
                coverage_id=COVERAGE_ID,
                subsets=(("x", window.min_x, window.max_x), ("y", window.min_y, window.max_y)),
            )
            (REAL_DIR / f"{parcel['slug']}.wkt").write_text(wkt + "\n", encoding="utf-8")
            (REAL_DIR / f"{parcel['slug']}.tif").write_bytes(result.artifact)
            manifest.append(
                {
                    "slug": parcel["slug"],
                    "parcel_identifier": parcel_id.strip(),
                    "note": parcel["note"],
                    "uldk_point_wgs84": parcel["xy"],
                    "wcs_request_url": result.source.request_url,
                    "geotiff_sha256": result.source.content_hash,
                    "fetched_at": result.source.fetched_at.isoformat()
                    if result.source.fetched_at
                    else None,
                }
            )
    (REAL_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def compare_fixture(
    slug: str,
    decoder: GdalRasterProcessor | None = None,
) -> dict[str, Any]:
    """Porównuje wynik produkcyjny z gdaldem dla jednej zamrożonej działki."""
    decoder = decoder or GdalRasterProcessor(command_timeout_s=60)
    manifest = {item["slug"]: item for item in _manifest()}[slug]
    geometry = from_wkt((REAL_DIR / f"{slug}.wkt").read_text(encoding="utf-8"))
    tiff = (REAL_DIR / f"{slug}.tif").read_bytes()
    if hashlib.sha256(tiff).hexdigest() != manifest["geotiff_sha256"]:
        raise RuntimeError(f"SHA-256 fixture {slug} nie zgadza się z manifestem.")

    client = _FrozenClient(
        describe=(FIXTURES / "wcs_describecoverage.xml").read_bytes(),
        coverage=tiff,
        request_url=manifest["wcs_request_url"],
    )
    source = WcsElevationRasterSource(
        WcsCoverageContract(service_url=SERVICE_URL, coverage_id=COVERAGE_ID),
        client,  # type: ignore[arg-type]
        decoder,
        max_bytes=16 * 1024 * 1024,
    )
    footprint = ShapelyParcelFootprint(geometry)
    outcome = analyze_parcel_relief(
        footprint, source, ReliefLimits(max_pixels=4_000_000, buffer_px=BUFFER_PX)
    )
    if outcome.status != "available" or outcome.derivatives is None:
        raise RuntimeError(f"Wynik produkcyjny dla {slug}: {outcome.status}")
    raster: FetchedElevationRaster = source.fetch(
        outcome.raster.bbox,  # type: ignore[union-attr]
        source.describe(),
        BUFFER_PX * 1.0,
    )
    grid = raster.grid
    mask = footprint.pixel_mask(grid)
    reference_slope, reference_aspect = _gdaldem(tiff, decoder)

    slope_diffs: list[float] = []
    aspect_diffs: list[float] = []
    reference_pct: list[float] = []
    reference_deg: list[float] = []
    for row in range(grid.height):
        for col in range(grid.width):
            index = row * grid.width + col
            if not mask[index]:
                continue
            gradient = horn_gradient(grid, col, row)
            ref_deg = reference_slope[index]
            if gradient is None or ref_deg is None:
                continue
            ours_deg, ours_pct = slope_from_gradient(*gradient)
            slope_diffs.append(abs(ours_deg - ref_deg))
            reference_deg.append(ref_deg)
            reference_pct.append(100.0 * math.tan(math.radians(ref_deg)))
            ours_aspect = aspect_from_gradient(*gradient)
            ref_aspect = reference_aspect[index]
            if ours_pct >= FLAT_THRESHOLD_PCT and ours_aspect is not None and ref_aspect is not None:
                delta = abs(ours_aspect - ref_aspect) % 360.0
                aspect_diffs.append(min(delta, 360.0 - delta))

    derivatives = outcome.derivatives
    reference = _reference_statistics(reference_deg, reference_pct)
    ours = {
        "mean_deg": derivatives.slope.mean_deg,  # type: ignore[union-attr]
        "median_deg": derivatives.slope.median_deg,  # type: ignore[union-attr]
        "p90_deg": derivatives.slope.p90_deg,  # type: ignore[union-attr]
        "max_deg": derivatives.slope.max_deg,  # type: ignore[union-attr]
        "classes_pct": {item.class_id: item.share_pct for item in derivatives.slope_classes},
    }
    return {
        "slug": slug,
        "parcel_identifier": manifest["parcel_identifier"],
        "note": manifest["note"],
        "geotiff_sha256": manifest["geotiff_sha256"],
        "resolution_m": derivatives.resolution_m,
        "compared_pixels": len(slope_diffs),
        "parcel_pixels": derivatives.parcel_pixel_count,
        "max_abs_slope_diff_deg": round(max(slope_diffs), 6) if slope_diffs else None,
        "compared_aspect_pixels": len(aspect_diffs),
        "max_abs_aspect_diff_deg": round(max(aspect_diffs), 6) if aspect_diffs else None,
        "ours": ours,
        "reference_gdaldem": reference,
        "aspect": {
            "status": derivatives.aspect.status,  # type: ignore[union-attr]
            "mean_azimuth_deg": derivatives.aspect.mean_azimuth_deg,  # type: ignore[union-attr]
            "dominant_direction": derivatives.aspect.dominant_direction,  # type: ignore[union-attr]
        },
        "profile_samples": len(outcome.profile.samples) if outcome.profile else 0,
    }


def _reference_statistics(degrees: list[float], percents: list[float]) -> dict[str, Any]:
    ordered = sorted(degrees)
    counts = {definition.class_id: 0 for definition in SLOPE_CLASSES}
    for value in percents:
        for definition in SLOPE_CLASSES:
            if definition.contains(value):
                counts[definition.class_id] += 1
                break
    total = len(percents)
    return {
        "mean_deg": round(math.fsum(ordered) / len(ordered), 4),
        "median_deg": round(percentile_linear(ordered, 0.5), 4),
        "p90_deg": round(percentile_linear(ordered, 0.9), 4),
        "max_deg": round(ordered[-1], 4),
        "classes_pct": {key: round(100.0 * value / total, 2) for key, value in counts.items()},
    }


def _gdaldem(
    tiff: bytes, decoder: GdalRasterProcessor
) -> tuple[list[float | None], list[float | None]]:
    with tempfile.TemporaryDirectory(prefix="gdaldem-") as workdir:
        work = Path(workdir)
        source = work / "dem.tif"
        source.write_bytes(tiff)
        outputs: list[list[float | None]] = []
        for mode in ("slope", "aspect"):
            target = work / f"{mode}.tif"
            subprocess.run(  # noqa: S603 - stała lista argumentów GDAL
                ["gdaldem", mode, "-alg", "Horn", "-of", "GTiff", "-q", str(source), str(target)],
                check=True,
                capture_output=True,
                timeout=120,
            )
            band = decoder.read_float_band(target.read_bytes(), max_bytes=64 * 1024 * 1024)
            outputs.append(
                [
                    None if (band.nodata is not None and value == band.nodata) else value
                    for value in band.values
                ]
            )
    return outputs[0], outputs[1]


def _manifest() -> list[dict[str, Any]]:
    return json.loads((REAL_DIR / "manifest.json").read_text(encoding="utf-8"))


def main() -> None:  # pragma: no cover - CLI
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("fetch", "compare"))
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if args.mode == "fetch":
        fetch_fixtures()
        return
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reference": "gdaldem slope/aspect -alg Horn (GDAL; odpowiednik QGIS gdal:slope/gdal:aspect)",
        "gdal_version": GdalRasterProcessor().gdal_version(),
        "parcels": [compare_fixture(item["slug"]) for item in _manifest()],
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":  # pragma: no cover
    main()
