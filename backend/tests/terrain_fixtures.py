"""Pomocnicze dane testów rastra NMT (BK-302).

Syntetyczne GeoTIFF-y są budowane tymi samymi narzędziami ``gdal-bin``, których
używa adapter produkcyjny — z tekstowej siatki AAIGrid, więc wartości są
dokładnie znane. Testy wymagające GDAL są pomijane poza obrazem backendu.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

import pytest

from app.modules.imports.composition import OgcError, OgcResult
from app.shared.provenance import Provenance

FIXTURES = Path(__file__).parent / "fixtures" / "terrain"
SERVICE_URL = (
    "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/"
    "DigitalTerrainModelFormatTIFF"
)
COVERAGE_ID = "DTM_PL-KRON86-NH_TIFF"
NOW = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)

requires_gdal = pytest.mark.skipif(
    shutil.which("gdal_translate") is None or shutil.which("gdalinfo") is None,
    reason="Wymaga gdal-bin (obraz backendu, CI).",
)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def provenance(content: bytes, url: str, operation: str) -> Provenance:
    return Provenance(
        source_id="nmt_wcs",
        fetched_at=NOW,
        content_hash=hashlib.sha256(content).hexdigest(),
        request_url=url,
        operation=operation,
        complete=True,
    )


def ogc_result(content: bytes, operation: str = "WCS:GetCoverage") -> OgcResult:
    url = f"{SERVICE_URL}?request={operation.split(':')[1]}"
    return OgcResult(
        artifact=content,
        features=(),
        source=provenance(content, url, operation),
        complete=True,
    )


class FakeWcsClient:
    """Zamrożony klient WCS: odpowiedzi albo wyjątki OGC podane w teście."""

    def __init__(
        self,
        *,
        coverage: bytes | OgcError | Callable[[Sequence[tuple[str, float, float]]], bytes] | None = None,
        describe: bytes | OgcError | None = None,
    ) -> None:
        self.describe = fixture_bytes("wcs_describecoverage.xml") if describe is None else describe
        self.coverage = coverage
        self.describe_calls = 0
        self.coverage_calls: list[Sequence[tuple[str, float, float]]] = []

    def fetch_wcs_description(self, url: str, **_: object) -> OgcResult:
        self.describe_calls += 1
        if isinstance(self.describe, OgcError):
            raise self.describe
        return ogc_result(self.describe, "WCS:DescribeCoverage")

    def fetch_wcs_coverage(
        self, url: str, *, subsets: Sequence[tuple[str, float, float]], **_: object
    ) -> OgcResult:
        self.coverage_calls.append(subsets)
        if isinstance(self.coverage, OgcError):
            raise self.coverage
        if callable(self.coverage):
            return ogc_result(self.coverage(subsets))
        assert self.coverage is not None, "Test nie przewidział pobrania rastra."
        return ogc_result(self.coverage)

    def close(self) -> None:
        return None


def make_geotiff(
    values: Sequence[Sequence[float]],
    *,
    origin_x: float,
    origin_y: float,
    resolution: float = 1.0,
    epsg: int = 2180,
    nodata: float | None = None,
    output_type: str = "Float32",
    bands: int = 1,
) -> bytes:
    """GeoTIFF z dokładnie znanych wartości (wiersz 0 = północ)."""
    height = len(values)
    width = len(values[0])
    lines = [
        f"ncols {width}",
        f"nrows {height}",
        f"xllcorner {origin_x!r}",
        f"yllcorner {origin_y - height * resolution!r}",
        f"cellsize {resolution!r}",
    ]
    if nodata is not None:
        lines.append(f"NODATA_value {nodata!r}")
    lines.extend(" ".join(repr(float(value)) for value in row) for row in values)
    with tempfile.TemporaryDirectory(prefix="test-dem-") as workdir:
        work = Path(workdir)
        source = work / "dem.asc"
        target = work / "dem.tif"
        source.write_text("\n".join(lines) + "\n", encoding="ascii")
        command = [
            "gdal_translate",
            "-q",
            "-of",
            "GTiff",
            "-ot",
            output_type,
            "-a_srs",
            f"EPSG:{epsg}",
            *(["-b", "1"] * bands),
            str(source),
            str(target),
        ]
        subprocess.run(command, check=True, capture_output=True, timeout=60)  # noqa: S603
        return target.read_bytes()


def plane_values(
    width: int,
    height: int,
    *,
    origin_x: float,
    origin_y: float,
    dz_east: float,
    dz_north: float,
    base: float = 100.0,
    resolution: float = 1.0,
) -> list[list[float]]:
    return [
        [
            base
            + dz_east * ((col + 0.5) * resolution)
            - dz_north * ((row + 0.5) * resolution)
            for col in range(width)
        ]
        for row in range(height)
    ]
