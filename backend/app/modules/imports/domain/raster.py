"""Czysta domena georeferencji rastra planistycznego.

Zawiera dopasowanie transformacji afinicznej (piksel → EPSG:2180) metodą
najmniejszych kwadratów oraz miary jakości: RMSE w metrach, rozmiar piksela i
zasięg. Logika jest wolna od zależności GIS/ORM (tylko biblioteka standardowa),
aby była łatwo testowalna i zgodna z warstwą ``domain`` (ADR-001).

Raster jest materiałem prezentacyjnym i pomocniczym — nie zastępuje wektorowej
granicy strefy. Miara RMSE służy do oceny jakości georeferencji i jest częścią
raportu QA, ale ostateczna akceptacja rastra jest ZAWSZE ręczna.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from app.shared.crs import CANONICAL_CRS
from app.shared.geometry import GeometryPayload


# Minimalna liczba punktów kontrolnych dla transformacji afinicznej (order 1).
MIN_CONTROL_POINTS: int = 3

# Domyślny próg akceptowalnego RMSE (m). Ostateczna akceptacja i tak jest ręczna,
# ale wynik powyżej progu jest jawnie oznaczany w raporcie QA.
DEFAULT_MAX_RMSE_M: float = 5.0


class GeoreferenceError(ValueError):
    """Georeferencji nie da się wiarygodnie wyznaczyć z podanych punktów."""


@dataclass(frozen=True)
class ControlPoint:
    """Punkt kontrolny: piksel (kolumna, wiersz) → współrzędna EPSG:2180."""

    pixel_col: float
    pixel_row: float
    map_x: float
    map_y: float


@dataclass(frozen=True)
class AffineTransform:
    """Transformacja afiniczna piksel → mapa.

    x' = a*col + b*row + c
    y' = d*col + e*row + f
    """

    a: float
    b: float
    c: float
    d: float
    e: float
    f: float

    def apply(self, col: float, row: float) -> tuple[float, float]:
        return (
            self.a * col + self.b * row + self.c,
            self.d * col + self.e * row + self.f,
        )

    @property
    def determinant(self) -> float:
        return self.a * self.e - self.b * self.d


@dataclass(frozen=True)
class GeoreferenceResult:
    """Wynik dopasowania georeferencji wraz z miarami jakości."""

    transform: AffineTransform
    rmse_m: float
    pixel_size_m: float
    point_count: int
    residuals_m: tuple[float, ...]

    def is_within_tolerance(self, max_rmse_m: float = DEFAULT_MAX_RMSE_M) -> bool:
        return self.rmse_m <= max_rmse_m


def _solve_3x3(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Rozwiązuje układ 3x3 metodą eliminacji Gaussa z częściowym pivotem."""
    # Kopia rozszerzonej macierzy [A | b].
    augmented = [row[:] + [rhs[index]] for index, row in enumerate(matrix)]
    size = 3
    for column in range(size):
        pivot_row = max(
            range(column, size), key=lambda r: abs(augmented[r][column])
        )
        if abs(augmented[pivot_row][column]) < 1e-12:
            raise GeoreferenceError(
                "Punkty kontrolne są współliniowe lub zdegenerowane — nie można "
                "wyznaczyć transformacji afinicznej."
            )
        augmented[column], augmented[pivot_row] = (
            augmented[pivot_row],
            augmented[column],
        )
        pivot = augmented[column][column]
        for row_index in range(size):
            if row_index == column:
                continue
            factor = augmented[row_index][column] / pivot
            for col_index in range(column, size + 1):
                augmented[row_index][col_index] -= factor * augmented[column][col_index]
    return [augmented[index][size] / augmented[index][index] for index in range(size)]


def _fit_linear(points: Sequence[ControlPoint], target: str) -> list[float]:
    """Dopasowuje 3 współczynniki (col, row, 1) metodą najmniejszych kwadratów."""
    # Normalne równania: (A^T A) x = A^T b, gdzie wiersz A to [col, row, 1].
    ata = [[0.0, 0.0, 0.0] for _ in range(3)]
    atb = [0.0, 0.0, 0.0]
    for point in points:
        design = [point.pixel_col, point.pixel_row, 1.0]
        value = point.map_x if target == "x" else point.map_y
        for i in range(3):
            atb[i] += design[i] * value
            for j in range(3):
                ata[i][j] += design[i] * design[j]
    return _solve_3x3(ata, atb)


def fit_georeference(points: Sequence[ControlPoint]) -> GeoreferenceResult:
    """Dopasowuje transformację afiniczną i liczy RMSE oraz rozmiar piksela.

    Wymaga co najmniej ``MIN_CONTROL_POINTS`` punktów. RMSE jest liczone jako
    pierwiastek średniego kwadratu odległości (w metrach EPSG:2180) między
    współrzędną punktu kontrolnego a jej predykcją z dopasowanej transformacji.
    """
    if len(points) < MIN_CONTROL_POINTS:
        raise GeoreferenceError(
            f"Potrzeba co najmniej {MIN_CONTROL_POINTS} punktów kontrolnych, "
            f"podano {len(points)}."
        )
    coeffs_x = _fit_linear(points, "x")
    coeffs_y = _fit_linear(points, "y")
    transform = AffineTransform(
        a=coeffs_x[0],
        b=coeffs_x[1],
        c=coeffs_x[2],
        d=coeffs_y[0],
        e=coeffs_y[1],
        f=coeffs_y[2],
    )
    if abs(transform.determinant) < 1e-9:
        raise GeoreferenceError(
            "Wyznaczona transformacja jest zdegenerowana (zerowy wyznacznik)."
        )

    residuals: list[float] = []
    for point in points:
        predicted_x, predicted_y = transform.apply(point.pixel_col, point.pixel_row)
        residuals.append(
            math.hypot(predicted_x - point.map_x, predicted_y - point.map_y)
        )
    rmse = math.sqrt(sum(value * value for value in residuals) / len(residuals))
    # Rozmiar piksela to geometryczny bok jednostkowego piksela w metrach.
    pixel_size = math.sqrt(abs(transform.determinant))
    return GeoreferenceResult(
        transform=transform,
        rmse_m=rmse,
        pixel_size_m=pixel_size,
        point_count=len(points),
        residuals_m=tuple(residuals),
    )


def bounds_polygon(
    transform: AffineTransform, width_px: int, height_px: int
) -> GeometryPayload:
    """Zwraca zasięg rastra jako prostokąt (POLYGON) w EPSG:2180.

    Cztery narożniki obrazu są mapowane transformacją afiniczną; pierścień jest
    domykany i orientowany zgodnie z ruchem wskazówek/przeciwnie tak, aby był
    poprawnym wielokątem po naprawie w PostGIS.
    """
    if width_px <= 0 or height_px <= 0:
        raise GeoreferenceError("Wymiary rastra muszą być dodatnie.")
    corners_px = [
        (0.0, 0.0),
        (float(width_px), 0.0),
        (float(width_px), float(height_px)),
        (0.0, float(height_px)),
    ]
    mapped = [transform.apply(col, row) for col, row in corners_px]
    mapped.append(mapped[0])  # domknięcie pierścienia
    ring = ", ".join(f"{x} {y}" for x, y in mapped)
    return GeometryPayload(f"POLYGON(({ring}))", crs=CANONICAL_CRS)


def quality_report(
    result: GeoreferenceResult,
    *,
    width_px: int,
    height_px: int,
    max_rmse_m: float = DEFAULT_MAX_RMSE_M,
) -> dict[str, object]:
    """Buduje raport QA georeferencji: RMSE, liczba punktów, rozdzielczość, zasięg."""
    bounds = bounds_polygon(result.transform, width_px, height_px)
    return {
        "rmse_m": round(result.rmse_m, 4),
        "control_point_count": result.point_count,
        "pixel_size_m": round(result.pixel_size_m, 4),
        "width_px": width_px,
        "height_px": height_px,
        "max_residual_m": round(max(result.residuals_m), 4),
        "within_tolerance": result.is_within_tolerance(max_rmse_m),
        "max_rmse_threshold_m": max_rmse_m,
        "bounds_wkt": bounds.wkt,
    }
