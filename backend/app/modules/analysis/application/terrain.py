"""Przypadek użycia: pochodne rastra NMT dla działki (BK-302).

Warstwa aplikacyjna zna wyłącznie porty — źródło rastra (WCS + dekoder GDAL)
i obrys działki (operacje geometryczne) są dostarczane przez infrastrukturę.
Każda ścieżka zwraca ``ReliefOutcome`` z provenance, także gdy pomiaru nie
wykonano: brak danych, zły CRS, za duży raster i timeout nigdy nie wytwarzają
statystyk ze sztucznych zer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Protocol

from app.modules.analysis.domain.terrain import (
    ElevationGrid,
    TerrainDerivatives,
    TerrainProfile,
    aligned_window,
    compute_derivatives,
    sample_profile,
    window_pixel_count,
)
from app.shared.geometry import BoundingBox
from app.shared.provenance import Provenance

# Kody przyczyn — trafiają do ``TerrainReliefResult.reason_code``.
REASON_SOURCE_NOT_RUNNABLE: Final[str] = "SOURCE_NOT_RUNNABLE"
REASON_SERVICE_TIMEOUT: Final[str] = "SERVICE_TIMEOUT"
REASON_SERVICE_ERROR: Final[str] = "SERVICE_ERROR"
REASON_CONTRACT_MISMATCH: Final[str] = "CONTRACT_MISMATCH"
REASON_RASTER_TOO_LARGE: Final[str] = "RASTER_TOO_LARGE"
REASON_RASTER_CRS_MISMATCH: Final[str] = "RASTER_CRS_MISMATCH"
REASON_RASTER_GRID_INVALID: Final[str] = "RASTER_GRID_INVALID"
REASON_RASTER_DECODE_ERROR: Final[str] = "RASTER_DECODE_ERROR"
REASON_OUTSIDE_COVERAGE: Final[str] = "OUTSIDE_COVERAGE"
REASON_NO_DATA_IN_PARCEL: Final[str] = "NO_DATA_IN_PARCEL"
REASON_PARCEL_BELOW_RESOLUTION: Final[str] = "PARCEL_BELOW_RESOLUTION"
REASON_INSUFFICIENT_WINDOW: Final[str] = "INSUFFICIENT_VALID_WINDOW"
REASON_UNEXPECTED_ERROR: Final[str] = "UNEXPECTED_ERROR"

# Brak pokrycia jest faktem o danych, a nie awarią — osobny status.
_NO_COVERAGE_REASONS: Final[frozenset[str]] = frozenset(
    {REASON_OUTSIDE_COVERAGE, REASON_NO_DATA_IN_PARCEL}
)


@dataclass(frozen=True)
class NativeGridSpec:
    """Natywna siatka pokrycia potwierdzona w DescribeCoverage."""

    coverage_id: str
    origin_x: float  # lewa krawędź pierwszej kolumny (easting)
    origin_y: float  # górna krawędź pierwszego wiersza (northing)
    resolution: float
    crs: str = "EPSG:2180"


@dataclass(frozen=True)
class RasterMetadata:
    coverage_id: str
    resolution_m: float
    width_px: int
    height_px: int
    bbox: BoundingBox
    buffer_m: float
    size_bytes: int
    nodata_value: float | None
    nodata_policy: str
    masked_pixel_count: int
    vertical_datum: str | None
    gdal_version: str | None


@dataclass(frozen=True)
class FetchedElevationRaster:
    grid: ElevationGrid
    metadata: RasterMetadata
    provenance: Provenance
    warnings: tuple[str, ...] = ()


class ElevationRasterError(Exception):
    """Kontrolowana porażka pobrania albo dekodowania rastra z provenance."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str,
        provenance: Provenance | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.provenance = provenance


class ElevationRasterSource(Protocol):
    """Port źródła rastra wysokości (np. WCS + dekoder GeoTIFF)."""

    source_id: str
    service_url: str

    def describe(self) -> NativeGridSpec: ...

    def fetch(
        self, window: BoundingBox, spec: NativeGridSpec, buffer_m: float
    ) -> FetchedElevationRaster: ...


class ParcelFootprint(Protocol):
    """Port operacji geometrycznych na obrysie działki w EPSG:2180."""

    @property
    def bounds(self) -> BoundingBox: ...

    def pixel_mask(self, grid: ElevationGrid) -> tuple[bool, ...]: ...

    def contains(self, x: float, y: float) -> bool: ...

    def profile_line(
        self,
    ) -> tuple[tuple[float, float], tuple[float, float]] | None: ...


@dataclass(frozen=True)
class ReliefLimits:
    max_pixels: int
    buffer_px: int = 2

    def __post_init__(self) -> None:
        if self.max_pixels <= 0 or self.buffer_px < 1:
            raise ValueError("Limit pikseli musi być dodatni, a bufor ≥ 1 piksel.")


@dataclass(frozen=True)
class ReliefOutcome:
    """Wynik pochodnych rastra: status, pomiar (albo None) i provenance."""

    status: str  # available | no_coverage | unavailable
    reason_code: str | None
    derivatives: TerrainDerivatives | None = None
    profile: TerrainProfile | None = None
    raster: RasterMetadata | None = None
    provenance: Provenance | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


def analyze_parcel_relief(
    footprint: ParcelFootprint,
    source: ElevationRasterSource,
    limits: ReliefLimits,
) -> ReliefOutcome:
    """Pobiera wyrównany raster działki z buforem i liczy pochodne.

    Kolejność gwarantuje, że limit pikseli jest sprawdzany przed pobraniem, a
    każda porażka kończy się jawnym statusem z provenance próby.
    """
    try:
        spec = source.describe()
    except ElevationRasterError as exc:
        return _failed(exc)

    window = aligned_window(
        footprint.bounds,
        grid_origin_x=spec.origin_x,
        grid_origin_y=spec.origin_y,
        resolution=spec.resolution,
        buffer_px=limits.buffer_px,
    )
    pixels = window_pixel_count(window, spec.resolution)
    if pixels > limits.max_pixels:
        return ReliefOutcome(
            status="unavailable",
            reason_code=REASON_RASTER_TOO_LARGE,
            provenance=Provenance(
                source_id=source.source_id,
                request_url=source.service_url,
                operation="WCS:GetCoverage",
                complete=False,
                error_code="limit",
            ),
            warnings=(
                f"Raster działki ma {pixels} pikseli przy limicie "
                f"{limits.max_pixels} — spadku i ekspozycji nie policzono. "
                "Brak statystyk nie oznacza płaskiego terenu.",
            ),
        )

    buffer_m = limits.buffer_px * spec.resolution
    try:
        raster = source.fetch(window, spec, buffer_m)
    except ElevationRasterError as exc:
        return _failed(exc)

    derivatives = compute_derivatives(raster.grid, footprint.pixel_mask(raster.grid))
    warnings = list(raster.warnings)
    if derivatives.parcel_pixel_count == 0:
        return _empty(
            raster,
            "unavailable",
            REASON_PARCEL_BELOW_RESOLUTION,
            "Działka jest mniejsza niż piksel rastra NMT — spadku nie policzono.",
        )
    if derivatives.nodata_pixel_count == derivatives.parcel_pixel_count:
        return _empty(
            raster,
            "no_coverage",
            REASON_NO_DATA_IN_PARCEL,
            "Raster NMT nie ma danych w obrysie działki — spadek i ekspozycja są "
            "nieznane; brak danych nie oznacza płaskiego terenu.",
        )
    if derivatives.valid_pixel_count == 0:
        return _empty(
            raster,
            "unavailable",
            REASON_INSUFFICIENT_WINDOW,
            "Żaden piksel działki nie ma pełnego okna 3×3 z danymi — spadku nie "
            "policzono.",
        )
    if derivatives.nodata_pixel_count > 0 or derivatives.valid_area_share_pct < 100.0:
        warnings.append(
            "Pochodne policzono dla "
            f"{derivatives.valid_area_share_pct:.2f}% pikseli działki; pozostała "
            "część nie ma danych NMT albo pełnego okna 3×3 i nie jest wliczana "
            "jako teren płaski."
        )

    line = footprint.profile_line()
    profile = (
        sample_profile(raster.grid, line[0], line[1], footprint.contains)
        if line is not None
        else None
    )
    return ReliefOutcome(
        status="available",
        reason_code=None,
        derivatives=derivatives,
        profile=profile,
        raster=raster.metadata,
        provenance=raster.provenance,
        warnings=tuple(warnings),
    )


def _failed(exc: ElevationRasterError) -> ReliefOutcome:
    status = "no_coverage" if exc.reason_code in _NO_COVERAGE_REASONS else "unavailable"
    suffix = (
        " Brak danych nie oznacza płaskiego terenu."
        if status == "no_coverage"
        else " Spadku i ekspozycji nie policzono."
    )
    return ReliefOutcome(
        status=status,
        reason_code=exc.reason_code,
        provenance=exc.provenance,
        warnings=(f"{exc}{suffix}",),
    )


def _empty(
    raster: FetchedElevationRaster, status: str, reason_code: str, message: str
) -> ReliefOutcome:
    return ReliefOutcome(
        status=status,
        reason_code=reason_code,
        raster=raster.metadata,
        provenance=raster.provenance,
        warnings=(*raster.warnings, message),
    )
