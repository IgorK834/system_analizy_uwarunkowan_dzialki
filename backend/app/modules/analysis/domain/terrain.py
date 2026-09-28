"""Pochodne rastra wysokościowego działki: spadek, klasy, ekspozycja i profil.

Czysta logika domenowa BK-302 — wyłącznie biblioteka standardowa i
``app.shared``. Raster przychodzi jako ``ElevationGrid`` w metrycznym EPSG:2180
z kwadratowym pikselem; brak danych (NoData) jest reprezentowany przez ``None``
i nigdy nie jest zamieniany na 0.

Algorytm (wersjonowany przez ``ALGORITHM_VERSION``):

* gradient liczony metodą Horna (1981) na oknie 3×3 — ta sama metoda, której
  domyślnie używają ``gdaldem slope/aspect`` i narzędzia terenu QGIS;
* piksel ma pochodną wyłącznie wtedy, gdy całe okno 3×3 ma dane — brzegi rastra
  i sąsiedztwo NoData są maskowane, a nie wypełniane sztucznymi wartościami;
* statystyki liczone są wyłącznie z pikseli, których środek leży w działce
  (maska przekazywana z warstwy infrastruktury);
* percentyl P90 — interpolacja liniowa między statystykami pozycyjnymi (R-7,
  domyślna metoda ``numpy.percentile``);
* ekspozycja — średnia kołowa azymutów kierunku spadku (0° = północ, zgodnie z
  ruchem wskazówek zegara) pikseli nachylonych; dla terenu płaskiego null.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Final, Sequence

from app.shared.geometry import BoundingBox

ALGORITHM_VERSION: Final[str] = "horn1981-3x3-v1"
SLOPE_CLASSES_VERSION: Final[str] = "slope-classes-pl-v1"

# Piksel o spadku poniżej progu jest płaski — nie ma sensownej ekspozycji.
FLAT_THRESHOLD_PCT: Final[float] = 2.0
# Ekspozycja ma sens, gdy co najmniej tyle zmierzonej powierzchni jest nachylone.
ASPECT_MIN_NON_FLAT_SHARE: Final[float] = 0.25
# Poniżej tej długości wypadkowej kierunki są rozproszone (brak dominanty).
ASPECT_MIN_RESULTANT_LENGTH: Final[float] = 0.3
# Górny limit liczby próbek profilu — krok rośnie dla bardzo długich działek.
MAX_PROFILE_SAMPLES: Final[int] = 401
PROFILE_METHOD: Final[str] = "parcel_long_axis_through_rectangle_center"

_ROUND_DIGITS: Final[int] = 4
_SECTORS: Final[tuple[str, ...]] = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


@dataclass(frozen=True)
class SlopeClassDefinition:
    """Jawna klasa nachylenia; granica dolna włącznie, górna rozłącznie."""

    class_id: str
    label: str
    min_pct: float
    max_pct: float | None

    def contains(self, slope_pct: float) -> bool:
        return slope_pct >= self.min_pct and (
            self.max_pct is None or slope_pct < self.max_pct
        )


# Progi są konwencją inżynierską systemu (odwodnienie powierzchniowe ~2%,
# pochylnie i dojazdy ~5%, drogi i skarpy 10–15%, tereny wymagające
# zabezpieczeń ≥30%), nie normą prawną. Zmiana progów wymaga nowej wersji.
SLOPE_CLASSES: Final[tuple[SlopeClassDefinition, ...]] = (
    SlopeClassDefinition("flat", "płaski (< 2%)", 0.0, 2.0),
    SlopeClassDefinition("gentle", "łagodny (2–5%)", 2.0, 5.0),
    SlopeClassDefinition("moderate", "umiarkowany (5–10%)", 5.0, 10.0),
    SlopeClassDefinition("strong", "znaczny (10–15%)", 10.0, 15.0),
    SlopeClassDefinition("steep", "stromy (15–30%)", 15.0, 30.0),
    SlopeClassDefinition("very_steep", "bardzo stromy (≥ 30%)", 30.0, None),
)


@dataclass(frozen=True)
class ElevationGrid:
    """Raster wysokości w metrycznym CRS; wiersz 0 to północny skraj rastra.

    ``origin_x``/``origin_y`` to lewy górny narożnik (krawędź, nie środek
    piksela). ``values`` ma układ wierszowy; ``None`` oznacza NoData.
    """

    width: int
    height: int
    origin_x: float
    origin_y: float
    resolution: float
    values: tuple[float | None, ...]

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError("Raster musi mieć co najmniej jeden piksel.")
        if not self.resolution > 0:
            raise ValueError("Rozdzielczość rastra musi być dodatnia.")
        if len(self.values) != self.width * self.height:
            raise ValueError("Liczba wartości nie odpowiada wymiarom rastra.")
        for value in self.values:
            if value is not None and not math.isfinite(value):
                raise ValueError("Raster zawiera wartość nieskończoną — zamaskuj ją jako NoData.")

    def value(self, col: int, row: int) -> float | None:
        return self.values[row * self.width + col]

    def pixel_center(self, col: int, row: int) -> tuple[float, float]:
        return (
            self.origin_x + (col + 0.5) * self.resolution,
            self.origin_y - (row + 0.5) * self.resolution,
        )

    @property
    def pixel_area(self) -> float:
        return self.resolution * self.resolution

    @property
    def bounds(self) -> BoundingBox:
        return BoundingBox(
            min_x=self.origin_x,
            min_y=self.origin_y - self.height * self.resolution,
            max_x=self.origin_x + self.width * self.resolution,
            max_y=self.origin_y,
        )


@dataclass(frozen=True)
class SlopeStatistics:
    mean_deg: float
    median_deg: float
    p90_deg: float
    max_deg: float
    mean_pct: float
    median_pct: float
    p90_pct: float
    max_pct: float


@dataclass(frozen=True)
class SlopeClassShare:
    class_id: str
    label: str
    min_pct: float
    max_pct: float | None
    pixel_count: int
    area_sqm: float
    share_pct: float


@dataclass(frozen=True)
class AspectSummary:
    status: str  # defined | dispersed | flat
    mean_azimuth_deg: float | None
    resultant_length: float | None
    dominant_direction: str | None
    sector_shares_pct: dict[str, float]
    non_flat_share_pct: float
    flat_threshold_pct: float


@dataclass(frozen=True)
class TerrainDerivatives:
    """Statystyki pochodnych z pikseli działki (bez sztucznych zer)."""

    resolution_m: float
    parcel_pixel_count: int
    valid_pixel_count: int
    nodata_pixel_count: int
    valid_area_share_pct: float
    min_height_m: float | None
    max_height_m: float | None
    mean_height_m: float | None
    slope: SlopeStatistics | None
    slope_classes: tuple[SlopeClassShare, ...]
    aspect: AspectSummary | None


@dataclass(frozen=True)
class ProfileSample:
    distance_m: float
    x: float
    y: float
    height_m: float | None
    inside_parcel: bool


@dataclass(frozen=True)
class TerrainProfile:
    method: str
    start: tuple[float, float]
    end: tuple[float, float]
    length_m: float
    step_m: float
    samples: tuple[ProfileSample, ...]


# --- Wyrównanie okna do natywnej siatki -------------------------------------


def aligned_window(
    bounds: BoundingBox,
    *,
    grid_origin_x: float,
    grid_origin_y: float,
    resolution: float,
    buffer_px: int,
) -> BoundingBox:
    """Rozszerza bbox o bufor i przyciąga go na zewnątrz do krawędzi pikseli.

    Żądanie wyrównane do natywnej siatki pokrycia zwraca oryginalne piksele —
    bez przepróbkowania po stronie serwera, więc wynik jest powtarzalny.
    """
    if resolution <= 0 or buffer_px < 0:
        raise ValueError("Niepoprawna rozdzielczość albo bufor okna.")
    pad = buffer_px * resolution
    min_col = math.floor((bounds.min_x - pad - grid_origin_x) / resolution)
    max_col = math.ceil((bounds.max_x + pad - grid_origin_x) / resolution)
    min_row = math.floor((grid_origin_y - (bounds.max_y + pad)) / resolution)
    max_row = math.ceil((grid_origin_y - (bounds.min_y - pad)) / resolution)
    return BoundingBox(
        min_x=round(grid_origin_x + min_col * resolution, 6),
        min_y=round(grid_origin_y - max_row * resolution, 6),
        max_x=round(grid_origin_x + max_col * resolution, 6),
        max_y=round(grid_origin_y - min_row * resolution, 6),
        crs=bounds.crs,
    )


def window_pixel_count(window: BoundingBox, resolution: float) -> int:
    width = round((window.max_x - window.min_x) / resolution)
    height = round((window.max_y - window.min_y) / resolution)
    return width * height


# --- Gradient i pochodne -----------------------------------------------------


def horn_gradient(grid: ElevationGrid, col: int, row: int) -> tuple[float, float] | None:
    """Zwraca (dz/d_wschód, dz/d_północ) metodą Horna albo ``None``.

    ``None`` dla piksela brzegowego albo gdy którykolwiek z 9 pikseli okna nie
    ma danych — pochodnej nie da się wtedy policzyć bez zmyślania wartości.
    """
    if col < 1 or row < 1 or col > grid.width - 2 or row > grid.height - 2:
        return None
    window: list[float] = []
    for d_row in (-1, 0, 1):
        for d_col in (-1, 0, 1):
            value = grid.value(col + d_col, row + d_row)
            if value is None:
                return None
            window.append(value)
    a, b, c, d, _e, f, g, h, i = window
    scale = 8.0 * grid.resolution
    dz_east = ((c + 2.0 * f + i) - (a + 2.0 * d + g)) / scale
    # Wiersz -1 leży na północy, więc różnica (góra − dół) to pochodna ku północy.
    dz_north = ((a + 2.0 * b + c) - (g + 2.0 * h + i)) / scale
    return dz_east, dz_north


def slope_from_gradient(dz_east: float, dz_north: float) -> tuple[float, float]:
    """Spadek w stopniach i procentach z wektora gradientu."""
    magnitude = math.hypot(dz_east, dz_north)
    return math.degrees(math.atan(magnitude)), 100.0 * magnitude


def aspect_from_gradient(dz_east: float, dz_north: float) -> float | None:
    """Azymut kierunku spadku (0° = N, 90° = E); ``None`` dla zerowego gradientu."""
    if dz_east == 0.0 and dz_north == 0.0:
        return None
    azimuth = math.degrees(math.atan2(-dz_east, -dz_north)) % 360.0
    return 0.0 if azimuth >= 360.0 else azimuth


def percentile_linear(sorted_values: Sequence[float], fraction: float) -> float:
    """Percentyl metodą R-7 (interpolacja liniowa) z posortowanej sekwencji."""
    if not sorted_values:
        raise ValueError("Percentyl pustego zbioru jest niezdefiniowany.")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("Frakcja percentyla musi leżeć w [0, 1].")
    position = fraction * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    weight = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * weight


def compute_derivatives(
    grid: ElevationGrid,
    inside_mask: Sequence[bool],
) -> TerrainDerivatives:
    """Liczy statystyki spadku, klasy i ekspozycję dla pikseli działki."""
    if len(inside_mask) != grid.width * grid.height:
        raise ValueError("Maska działki nie odpowiada wymiarom rastra.")

    parcel_pixels = 0
    nodata_pixels = 0
    heights: list[float] = []
    slopes_deg: list[float] = []
    slopes_pct: list[float] = []
    azimuths: list[float] = []
    for row in range(grid.height):
        base = row * grid.width
        for col in range(grid.width):
            if not inside_mask[base + col]:
                continue
            parcel_pixels += 1
            value = grid.values[base + col]
            if value is None:
                nodata_pixels += 1
                continue
            heights.append(value)
            gradient = horn_gradient(grid, col, row)
            if gradient is None:
                continue
            slope_deg, slope_pct = slope_from_gradient(*gradient)
            slopes_deg.append(slope_deg)
            slopes_pct.append(slope_pct)
            if slope_pct >= FLAT_THRESHOLD_PCT:
                azimuth = aspect_from_gradient(*gradient)
                if azimuth is not None:
                    azimuths.append(azimuth)

    valid = len(slopes_deg)
    share = 100.0 * valid / parcel_pixels if parcel_pixels else 0.0
    return TerrainDerivatives(
        resolution_m=grid.resolution,
        parcel_pixel_count=parcel_pixels,
        valid_pixel_count=valid,
        nodata_pixel_count=nodata_pixels,
        valid_area_share_pct=_round(share, 2),
        min_height_m=_round(min(heights), 3) if heights else None,
        max_height_m=_round(max(heights), 3) if heights else None,
        mean_height_m=_round(math.fsum(heights) / len(heights), 3) if heights else None,
        slope=_slope_statistics(slopes_deg, slopes_pct) if valid else None,
        slope_classes=_slope_classes(slopes_pct, grid.pixel_area) if valid else (),
        aspect=_aspect_summary(azimuths, valid) if valid else None,
    )


def _slope_statistics(slopes_deg: list[float], slopes_pct: list[float]) -> SlopeStatistics:
    ordered_deg = sorted(slopes_deg)
    ordered_pct = sorted(slopes_pct)
    return SlopeStatistics(
        mean_deg=_round(math.fsum(ordered_deg) / len(ordered_deg)),
        median_deg=_round(percentile_linear(ordered_deg, 0.5)),
        p90_deg=_round(percentile_linear(ordered_deg, 0.9)),
        max_deg=_round(ordered_deg[-1]),
        mean_pct=_round(math.fsum(ordered_pct) / len(ordered_pct)),
        median_pct=_round(percentile_linear(ordered_pct, 0.5)),
        p90_pct=_round(percentile_linear(ordered_pct, 0.9)),
        max_pct=_round(ordered_pct[-1]),
    )


def _slope_classes(
    slopes_pct: list[float], pixel_area: float
) -> tuple[SlopeClassShare, ...]:
    counts = [0] * len(SLOPE_CLASSES)
    for slope in slopes_pct:
        for index, definition in enumerate(SLOPE_CLASSES):
            if definition.contains(slope):
                counts[index] += 1
                break
    total = len(slopes_pct)
    return tuple(
        SlopeClassShare(
            class_id=definition.class_id,
            label=definition.label,
            min_pct=definition.min_pct,
            max_pct=definition.max_pct,
            pixel_count=count,
            area_sqm=_round(count * pixel_area, 2),
            share_pct=_round(100.0 * count / total, 2),
        )
        for definition, count in zip(SLOPE_CLASSES, counts, strict=True)
    )


def _aspect_summary(azimuths: list[float], valid_pixels: int) -> AspectSummary:
    non_flat_share = len(azimuths) / valid_pixels
    sector_counts = [0] * len(_SECTORS)
    for azimuth in azimuths:
        sector_counts[_sector_index(azimuth)] += 1
    sector_shares = (
        {
            name: _round(100.0 * count / len(azimuths), 2)
            for name, count in zip(_SECTORS, sector_counts, strict=True)
        }
        if azimuths
        else {}
    )
    non_flat_pct = _round(100.0 * non_flat_share, 2)
    if not azimuths or non_flat_share < ASPECT_MIN_NON_FLAT_SHARE:
        return AspectSummary(
            status="flat",
            mean_azimuth_deg=None,
            resultant_length=None,
            dominant_direction=None,
            sector_shares_pct=sector_shares,
            non_flat_share_pct=non_flat_pct,
            flat_threshold_pct=FLAT_THRESHOLD_PCT,
        )
    sum_sin = math.fsum(math.sin(math.radians(value)) for value in azimuths)
    sum_cos = math.fsum(math.cos(math.radians(value)) for value in azimuths)
    resultant = math.hypot(sum_sin, sum_cos) / len(azimuths)
    mean_azimuth = math.degrees(math.atan2(sum_sin, sum_cos)) % 360.0
    mean_azimuth = _round(mean_azimuth) % 360.0
    defined = resultant >= ASPECT_MIN_RESULTANT_LENGTH
    return AspectSummary(
        status="defined" if defined else "dispersed",
        mean_azimuth_deg=mean_azimuth,
        resultant_length=min(1.0, _round(resultant)),
        dominant_direction=_SECTORS[_sector_index(mean_azimuth)] if defined else None,
        sector_shares_pct=sector_shares,
        non_flat_share_pct=non_flat_pct,
        flat_threshold_pct=FLAT_THRESHOLD_PCT,
    )


def _sector_index(azimuth: float) -> int:
    return int(((azimuth + 22.5) % 360.0) // 45.0)


# --- Profil ------------------------------------------------------------------


def bilinear_height(grid: ElevationGrid, x: float, y: float) -> float | None:
    """Wysokość w punkcie z interpolacji dwuliniowej 4 środków pikseli.

    ``None``, gdy punkt wychodzi poza środki skrajnych pikseli albo którykolwiek
    z czterech sąsiadów nie ma danych.
    """
    fx = (x - grid.origin_x) / grid.resolution - 0.5
    fy = (grid.origin_y - y) / grid.resolution - 0.5
    if not (0.0 <= fx <= grid.width - 1 and 0.0 <= fy <= grid.height - 1):
        return None
    # Punkt na środku skrajnego piksela jest poprawny — okno cofamy o jeden.
    col0 = min(math.floor(fx), max(grid.width - 2, 0))
    row0 = min(math.floor(fy), max(grid.height - 2, 0))
    col1 = min(col0 + 1, grid.width - 1)
    row1 = min(row0 + 1, grid.height - 1)
    corners = (
        grid.value(col0, row0),
        grid.value(col1, row0),
        grid.value(col0, row1),
        grid.value(col1, row1),
    )
    if any(value is None for value in corners):
        return None
    z00, z10, z01, z11 = (float(value) for value in corners)  # type: ignore[arg-type]
    tx = fx - col0
    ty = fy - row0
    top = z00 + (z10 - z00) * tx
    bottom = z01 + (z11 - z01) * tx
    return top + (bottom - top) * ty


def profile_step(length_m: float, resolution: float) -> float:
    """Krok próbkowania: rozdzielczość rastra, zwiększana dla długich linii."""
    if length_m <= 0:
        return resolution
    return max(resolution, length_m / (MAX_PROFILE_SAMPLES - 1))


def sample_profile(
    grid: ElevationGrid,
    start: tuple[float, float],
    end: tuple[float, float],
    inside: Callable[[float, float], bool],
) -> TerrainProfile:
    """Próbkuje wysokości wzdłuż odcinka ze stałym krokiem (deterministycznie)."""
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    step = profile_step(length, grid.resolution)
    distances: list[float] = []
    index = 0
    while index * step <= length + 1e-9:
        distances.append(index * step)
        index += 1
    if length > 0 and length - distances[-1] > 1e-6:
        distances.append(length)
    samples: list[ProfileSample] = []
    for distance in distances:
        ratio = distance / length if length > 0 else 0.0
        x = start[0] + (end[0] - start[0]) * ratio
        y = start[1] + (end[1] - start[1]) * ratio
        height = bilinear_height(grid, x, y)
        samples.append(
            ProfileSample(
                distance_m=_round(distance, 3),
                x=_round(x, 3),
                y=_round(y, 3),
                height_m=_round(height, 3) if height is not None else None,
                inside_parcel=inside(x, y),
            )
        )
    return TerrainProfile(
        method=PROFILE_METHOD,
        start=(_round(start[0], 3), _round(start[1], 3)),
        end=(_round(end[0], 3), _round(end[1], 3)),
        length_m=_round(length, 3),
        step_m=_round(step, 4),
        samples=tuple(samples),
    )


def order_profile_endpoints(
    first: tuple[float, float], second: tuple[float, float]
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Porządkuje końce linii: najpierw zachodni (przy remisie — południowy)."""
    return (first, second) if (first[0], first[1]) <= (second[0], second[1]) else (second, first)


def _round(value: float, digits: int = _ROUND_DIGITS) -> float:
    rounded = round(value, digits)
    return 0.0 if rounded == 0 else rounded
