"""Adapter rastra NMT: WCS 2.0.1 GUGiK, dekoder GeoTIFF (GDAL CLI) i obrys Shapely.

Kontrakt usługi potwierdzono realnymi zapytaniami 2026-09-28 (fixtures
``tests/fixtures/terrain/``):

1. Pokrycie ``DTM_PL-KRON86-NH_TIFF`` (NMT GRID1, układ wysokości
   PL-KRON86-NH) ma kwadratowy piksel 1 m w EPSG:2180. ``DescribeCoverage``
   podaje origin i offsetVectors w osiowej kolejności EPSG (northing, easting)
   — adapter weryfikuje tę interpretację z ``gml:Envelope``.
2. ``GetCoverage`` przyjmuje ``subset=x(...)`` jako easting i ``subset=y(...)``
   jako northing, a zwraca siatkę dokładnie w granicach żądania. Serwer
   przepróbkowuje dane, gdy bbox nie leży na krawędziach natywnych pikseli,
   dlatego okno jest zawsze przyciągane do siatki z DescribeCoverage.
3. Żądanie spoza obwiedni pokrycia daje HTTP 400 z
   ``ows:ExceptionReport exceptionCode="ExtentError"`` — to brak pokrycia, nie
   awaria. Nieznane pokrycie daje HTTP 404 ``NoSuchCoverage``.
4. Obszar w obwiedni, ale bez danych (np. za granicą państwa), wraca jako
   **dokładne 0.0 bez znacznika GDAL_NODATA**. Kontrakt maskuje więc 0.0 jako
   NoData. Realne wysokości bywają ujemne (Żuławy, wybrzeże), dlatego wartości
   ujemne nie są odrzucane.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from xml.etree import ElementTree

import httpx
import shapely
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from app.core.ru_contracts import RuContractError, parse_xml_root
from app.modules.analysis.application.terrain import (
    REASON_CONTRACT_MISMATCH,
    REASON_OUTSIDE_COVERAGE,
    REASON_RASTER_CRS_MISMATCH,
    REASON_RASTER_DECODE_ERROR,
    REASON_RASTER_GRID_INVALID,
    REASON_RASTER_TOO_LARGE,
    REASON_SERVICE_ERROR,
    REASON_SERVICE_TIMEOUT,
    ElevationRasterError,
    FetchedElevationRaster,
    NativeGridSpec,
    RasterMetadata,
)
from app.modules.analysis.domain.terrain import ElevationGrid, order_profile_endpoints
from app.modules.imports.composition import (
    DecodedFloatBand,
    GdalRasterProcessor,
    OgcClient,
    OgcContractError,
    OgcError,
    OgcExceptionReportError,
    OgcLimitError,
    OgcTransportError,
    RasterProcessingError,
)
from app.shared.geometry import BoundingBox
from app.shared.provenance import Provenance

WCS_SOURCE_ID = "nmt_wcs"
NODATA_POLICY = (
    "GDAL_NODATA (jeśli zadeklarowane), NaN oraz niezadeklarowane 0.0 poza "
    "zasięgiem danych (kontrakt WCS NMT potwierdzony 2026-09-28)"
)
_EPSG_2180_SUFFIX = "/EPSG/0/2180"
_GRID_TOLERANCE_M = 1e-3
_RESOLUTION_TOLERANCE_M = 1e-6


@dataclass(frozen=True)
class WcsCoverageContract:
    """Kontrakt pokrycia z katalogu źródeł (``docs/data_sources/catalog.yaml``)."""

    service_url: str
    coverage_id: str
    vertical_datum: str | None = None
    media_type: str = "image/tiff"
    version: str = "2.0.1"
    implicit_nodata_values: tuple[float, ...] = (0.0,)


class WcsElevationRasterSource:
    """Źródło rastra wysokości: WCS GetCoverage + ścisły dekoder GeoTIFF."""

    source_id = WCS_SOURCE_ID

    def __init__(
        self,
        contract: WcsCoverageContract,
        client: OgcClient,
        decoder: GdalRasterProcessor,
        *,
        max_bytes: int,
    ) -> None:
        self._contract = contract
        self._client = client
        self._decoder = decoder
        self._max_bytes = max_bytes
        self.service_url = contract.service_url

    def describe(self) -> NativeGridSpec:
        key = (self._contract.service_url, self._contract.coverage_id)
        cached = _NATIVE_GRID_CACHE.get(key)
        if cached is not None:
            return cached
        try:
            result = self._client.fetch_wcs_description(
                self._contract.service_url,
                coverage_id=self._contract.coverage_id,
                version=self._contract.version,
            )
        except OgcError as exc:
            raise _raster_error_from_ogc(exc, operation="DescribeCoverage") from exc
        spec = parse_describe_coverage(
            result.artifact, self._contract.coverage_id, provenance=result.source
        )
        _NATIVE_GRID_CACHE[key] = spec
        return spec

    def fetch(
        self, window: BoundingBox, spec: NativeGridSpec, buffer_m: float
    ) -> FetchedElevationRaster:
        try:
            result = self._client.fetch_wcs_coverage(
                self._contract.service_url,
                coverage_id=spec.coverage_id,
                subsets=(
                    ("x", window.min_x, window.max_x),
                    ("y", window.min_y, window.max_y),
                ),
                media_type=self._contract.media_type,
                version=self._contract.version,
            )
        except OgcError as exc:
            raise _raster_error_from_ogc(exc, operation="GetCoverage") from exc

        provenance = result.source
        try:
            band = self._decoder.read_float_band(result.artifact, max_bytes=self._max_bytes)
        except RasterProcessingError as exc:
            raise ElevationRasterError(
                f"Nie udało się zdekodować GeoTIFF z WCS: {exc}",
                reason_code=(
                    REASON_RASTER_TOO_LARGE
                    if "limit bajtów" in str(exc)
                    else REASON_RASTER_DECODE_ERROR
                ),
                provenance=_failed(provenance, "decode"),
            ) from exc

        grid, masked = self._validated_grid(band, window, spec, provenance)
        warnings: tuple[str, ...] = ()
        if masked:
            warnings = (
                f"{masked} pikseli rastra NMT nie ma danych (NoData) — zamaskowano "
                "je; nie są traktowane jako wysokość 0 m.",
            )
        metadata = RasterMetadata(
            coverage_id=spec.coverage_id,
            resolution_m=spec.resolution,
            width_px=grid.width,
            height_px=grid.height,
            bbox=window,
            buffer_m=buffer_m,
            size_bytes=len(result.artifact),
            nodata_value=band.nodata,
            nodata_policy=NODATA_POLICY,
            masked_pixel_count=masked,
            vertical_datum=self._contract.vertical_datum,
            gdal_version=_gdal_version(self._decoder),
        )
        return FetchedElevationRaster(
            grid=grid, metadata=metadata, provenance=provenance, warnings=warnings
        )

    def _validated_grid(
        self,
        band: DecodedFloatBand,
        window: BoundingBox,
        spec: NativeGridSpec,
        provenance: Provenance,
    ) -> tuple[ElevationGrid, int]:
        if band.epsg != 2180:
            raise ElevationRasterError(
                f"Raster WCS ma CRS EPSG:{band.epsg}, oczekiwano metrycznego EPSG:2180.",
                reason_code=REASON_RASTER_CRS_MISMATCH,
                provenance=_failed(provenance, "crs"),
            )
        origin_x, pixel_w, _rx, origin_y, _ry, pixel_h = band.geotransform
        expected_w = round((window.max_x - window.min_x) / spec.resolution)
        expected_h = round((window.max_y - window.min_y) / spec.resolution)
        grid_ok = (
            abs(pixel_w - spec.resolution) <= _RESOLUTION_TOLERANCE_M
            and abs(pixel_h + spec.resolution) <= _RESOLUTION_TOLERANCE_M
            and abs(origin_x - window.min_x) <= _GRID_TOLERANCE_M
            and abs(origin_y - window.max_y) <= _GRID_TOLERANCE_M
            and band.width == expected_w
            and band.height == expected_h
        )
        if not grid_ok:
            raise ElevationRasterError(
                "Siatka rastra WCS nie odpowiada żądanemu, wyrównanemu oknu "
                f"(piksel {pixel_w}×{pixel_h}, rozmiar {band.width}×{band.height}).",
                reason_code=REASON_RASTER_GRID_INVALID,
                provenance=_failed(provenance, "grid"),
            )
        implicit = set(self._contract.implicit_nodata_values)
        masked = 0
        values: list[float | None] = []
        for value in band.values:
            if (
                math.isnan(value)
                or math.isinf(value)
                or (band.nodata is not None and value == band.nodata)
                or value in implicit
            ):
                values.append(None)
                masked += 1
            else:
                values.append(float(value))
        grid = ElevationGrid(
            width=band.width,
            height=band.height,
            origin_x=window.min_x,
            origin_y=window.max_y,
            resolution=spec.resolution,
            values=tuple(values),
        )
        return grid, masked


_NATIVE_GRID_CACHE: dict[tuple[str, str], NativeGridSpec] = {}


def clear_native_grid_cache() -> None:
    """Czyści zapamiętane DescribeCoverage (testy, zmiana kontraktu)."""
    _NATIVE_GRID_CACHE.clear()
    _gdal_version.cache_clear()


def parse_describe_coverage(
    content: bytes,
    coverage_id: str,
    *,
    provenance: Provenance | None = None,
) -> NativeGridSpec:
    """Wyznacza natywną siatkę pokrycia z ``DescribeCoverage`` WCS 2.0.1.

    ``gml:pos`` i ``offsetVector`` są w osiowej kolejności EPSG:2180
    (northing, easting). Interpretacja jest potwierdzana obwiednią — gdy
    krawędzie siatki nie zgadzają się z ``gml:Envelope``, kontrakt jest
    odrzucany zamiast zgadywać kolejność osi.
    """
    try:
        root = parse_xml_root(content)
    except RuContractError as exc:
        raise _contract_error(f"DescribeCoverage nie jest poprawnym XML: {exc}", provenance) from exc
    description = next(
        (
            element
            for element in root.iter()
            if _local(element.tag) == "CoverageDescription"
            and _child_text(element, "CoverageId") == coverage_id
        ),
        None,
    )
    if description is None:
        raise _contract_error(f"DescribeCoverage nie opisuje pokrycia {coverage_id!r}.", provenance)
    grid = _first(description, "RectifiedGrid")
    envelope = _first(description, "Envelope")
    if grid is None or envelope is None:
        raise _contract_error("DescribeCoverage nie zawiera RectifiedGrid i Envelope.", provenance)
    origin_point = _first(grid, "Point")
    origin = _floats(_child_text(origin_point, "pos") if origin_point is not None else None)
    offsets = [
        (_floats(element.text), element.attrib.get("srsName", ""))
        for element in grid.iter()
        if _local(element.tag) == "offsetVector"
    ]
    srs_names = [
        envelope.attrib.get("srsName", ""),
        origin_point.attrib.get("srsName", "") if origin_point is not None else "",
        *(name for _vector, name in offsets),
    ]
    if not all(name.endswith(_EPSG_2180_SUFFIX) for name in srs_names):
        raise _contract_error("Siatka pokrycia nie jest zdefiniowana w EPSG:2180.", provenance)
    if origin is None or len(origin) != 2 or len(offsets) != 2:
        raise _contract_error("Niepełny opis siatki (origin/offsetVector).", provenance)
    column_step = next(
        (v for v, _ in offsets if v and len(v) == 2 and v[0] == 0.0 and v[1] > 0), None
    )
    row_step = next(
        (v for v, _ in offsets if v and len(v) == 2 and v[1] == 0.0 and v[0] < 0), None
    )
    if column_step is None or row_step is None:
        raise _contract_error("Siatka pokrycia jest rotowana albo ma nieznaną orientację.", provenance)
    resolution = column_step[1]
    if abs(resolution + row_step[0]) > _RESOLUTION_TOLERANCE_M:
        raise _contract_error("Piksel pokrycia nie jest kwadratowy.", provenance)
    north_center, east_center = origin
    origin_x = east_center - resolution / 2.0
    origin_y = north_center + resolution / 2.0
    lower = _floats(_child_text(envelope, "lowerCorner"))
    upper = _floats(_child_text(envelope, "upperCorner"))
    if (
        lower is None
        or upper is None
        or abs(upper[0] - origin_y) > _GRID_TOLERANCE_M
        or abs(lower[1] - origin_x) > _GRID_TOLERANCE_M
    ):
        raise _contract_error(
            "Obwiednia pokrycia nie potwierdza kolejności osi (northing, easting).",
            provenance,
        )
    return NativeGridSpec(
        coverage_id=coverage_id,
        origin_x=round(origin_x, 6),
        origin_y=round(origin_y, 6),
        resolution=resolution,
    )


class ShapelyParcelFootprint:
    """Operacje na obrysie działki (EPSG:2180) wymagane przez przypadek użycia."""

    def __init__(self, geometry: BaseGeometry) -> None:
        self._geometry = geometry
        shapely.prepare(self._geometry)

    @property
    def bounds(self) -> BoundingBox:
        min_x, min_y, max_x, max_y = self._geometry.bounds
        return BoundingBox(min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y)

    def pixel_mask(self, grid: ElevationGrid) -> tuple[bool, ...]:
        xs: list[float] = []
        ys: list[float] = []
        for row in range(grid.height):
            for col in range(grid.width):
                x, y = grid.pixel_center(col, row)
                xs.append(x)
                ys.append(y)
        return tuple(bool(item) for item in shapely.intersects_xy(self._geometry, xs, ys))

    def contains(self, x: float, y: float) -> bool:
        return bool(shapely.intersects_xy(self._geometry, x, y))

    def profile_line(self) -> tuple[tuple[float, float], tuple[float, float]] | None:
        """Linia wzdłuż dłuższej osi minimalnego prostokąta przez jego środek.

        Linia jest przycinana do działki, a jej końce to skrajne punkty
        przecięcia — zależy wyłącznie od geometrii, więc jest powtarzalna.
        """
        geometry = self._geometry
        if geometry.is_empty or geometry.area <= 0:
            return None
        rectangle = shapely.oriented_envelope(geometry)
        if rectangle.geom_type != "Polygon":
            return None
        corners = list(rectangle.exterior.coords)[:4]
        edge_a = (corners[1][0] - corners[0][0], corners[1][1] - corners[0][1])
        edge_b = (corners[2][0] - corners[1][0], corners[2][1] - corners[1][1])
        length_a = math.hypot(*edge_a)
        length_b = math.hypot(*edge_b)
        if abs(length_a - length_b) <= 1e-9:
            # Kwadrat: deterministycznie wybieramy oś bliższą kierunkowi W–E.
            axis = edge_a if abs(edge_a[0]) >= abs(edge_b[0]) else edge_b
        else:
            axis = edge_a if length_a > length_b else edge_b
        axis_length = math.hypot(*axis)
        if axis_length <= 0:
            return None
        ux, uy = axis[0] / axis_length, axis[1] / axis_length
        center = rectangle.centroid
        reach = axis_length
        line = LineString(
            [
                (center.x - ux * reach, center.y - uy * reach),
                (center.x + ux * reach, center.y + uy * reach),
            ]
        )
        points = shapely.get_coordinates(line.intersection(geometry)).tolist()
        if len(points) < 2:
            return None
        projected = sorted(
            points, key=lambda p: ((p[0] - center.x) * ux + (p[1] - center.y) * uy, p[0], p[1])
        )
        start = (round(projected[0][0], 3), round(projected[0][1], 3))
        end = (round(projected[-1][0], 3), round(projected[-1][1], 3))
        if math.hypot(end[0] - start[0], end[1] - start[1]) < 1e-6:
            return None
        return order_profile_endpoints(start, end)


@lru_cache(maxsize=1)
def _gdal_version(decoder: GdalRasterProcessor) -> str | None:
    try:
        return decoder.gdal_version()
    except RasterProcessingError:
        return None


def _raster_error_from_ogc(exc: OgcError, *, operation: str) -> ElevationRasterError:
    provenance = exc.source
    if isinstance(exc, OgcExceptionReportError):
        if exc.exception_code == "ExtentError":
            return ElevationRasterError(
                "Obszar działki leży poza zasięgiem pokrycia NMT (ExtentError).",
                reason_code=REASON_OUTSIDE_COVERAGE,
                provenance=provenance,
            )
        return ElevationRasterError(
            f"Usługa WCS NMT odrzuciła {operation}: {exc}",
            reason_code=REASON_SERVICE_ERROR,
            provenance=provenance,
        )
    if isinstance(exc, OgcLimitError):
        return ElevationRasterError(
            "Odpowiedź WCS NMT przekracza limit bajtów.",
            reason_code=REASON_RASTER_TOO_LARGE,
            provenance=provenance,
        )
    if isinstance(exc, OgcTransportError):
        timeout = isinstance(exc.__cause__, httpx.TimeoutException) or (
            "timeout" in str(exc).casefold()
        )
        return ElevationRasterError(
            (
                "Usługa WCS NMT nie odpowiedziała w wymaganym czasie."
                if timeout
                else f"Usługa WCS NMT jest niedostępna: {exc}"
            ),
            reason_code=REASON_SERVICE_TIMEOUT if timeout else REASON_SERVICE_ERROR,
            provenance=provenance,
        )
    if isinstance(exc, OgcContractError):
        return ElevationRasterError(
            f"Odpowiedź WCS NMT nie spełnia kontraktu: {exc}",
            reason_code=REASON_CONTRACT_MISMATCH,
            provenance=provenance,
        )
    return ElevationRasterError(
        f"Błąd WCS NMT: {exc}", reason_code=REASON_SERVICE_ERROR, provenance=provenance
    )


def _failed(provenance: Provenance, code: str) -> Provenance:
    return Provenance(**{**provenance.__dict__, "complete": False, "error_code": code})


def _contract_error(message: str, provenance: Provenance | None) -> ElevationRasterError:
    return ElevationRasterError(
        message,
        reason_code=REASON_CONTRACT_MISMATCH,
        provenance=_failed(provenance, "contract") if provenance else None,
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first(parent: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return next((item for item in parent.iter() if _local(item.tag) == name), None)


def _child_text(parent: ElementTree.Element | None, name: str) -> str | None:
    if parent is None:
        return None
    element = _first(parent, name)
    return element.text.strip() if element is not None and element.text else None


def _floats(raw: str | None) -> tuple[float, ...] | None:
    if not raw:
        return None
    try:
        return tuple(float(part) for part in raw.split())
    except ValueError:
        return None

