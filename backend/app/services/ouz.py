"""Powierzchniowa klasyfikacja położenia działki względem OUZ."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal, Sequence

from shapely import make_valid
from shapely.geometry import MultiPolygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.schemas.source import WarningMessage
from app.services.pog_fetch import PogVectorFeature

# Jeden metr kwadratowy jest świadomym progiem produktowym dla OUZ. Jest dużo
# większy niż epsilon 1e-6 m² w ISOK, ponieważ decyzja o ``in_ouz`` ma odsiać
# nie tylko błędy zmiennoprzecinkowe Shapely, lecz również drobne przesunięcia
# granic danych ULDK i APP. Nie jest to minimalna powierzchnia wynikająca z prawa.
DEFAULT_OUZ_INTERSECTION_AREA_EPSILON_SQM: Final[float] = 1.0

# Udział 0,1% jest niezależnym zabezpieczeniem dla małych działek: przecięcie
# mniejsze niż 1 m² może być proporcjonalnie istotne. Próg ma charakter
# techniczny i informacyjny, analogicznie do granicznego progu w mpzp_zones.py.
DEFAULT_OUZ_MIN_AREA_RATIO_PERCENT: Final[float] = 0.1

OUZ_LEGAL_DISCLAIMER: Final[str] = (
    "Wynik ma charakter informacyjny, zależy od aktualnego stanu prawnego "
    "i nie stanowi decyzji administracyjnej ani porady prawnej dotyczącej "
    "możliwości uzyskania decyzji o warunkach zabudowy."
)


@dataclass(frozen=True)
class OuzStatusResult:
    """Wynik powierzchniowej analizy OUZ w konwencji procentowej 0–100."""

    status: Literal["available", "unknown"]
    in_ouz: bool
    intersection_area_sqm: float
    area_ratio: float
    touches_ouz_boundary: bool
    manual_review_required: bool
    legal_disclaimer: str
    warnings: list[WarningMessage] = field(default_factory=list)


def calculate_ouz_status(
    parcel_geometry: BaseGeometry,
    ouz_geometries: Sequence[BaseGeometry | PogVectorFeature] | None,
    *,
    intersection_area_epsilon_sqm: float = DEFAULT_OUZ_INTERSECTION_AREA_EPSILON_SQM,
    minimum_area_ratio_percent: float = DEFAULT_OUZ_MIN_AREA_RATIO_PERCENT,
) -> OuzStatusResult:
    """Określa powierzchniowe położenie działki w OUZ w EPSG:2180.

    Samo ``intersects()`` nie wystarcza: działka stykająca się rogiem albo
    krawędzią z granicą OUZ nie uzyskuje przez ten styk realnej powierzchni
    położonej w obszarze uzupełnienia zabudowy. Dlatego ``in_ouz`` zależy od
    pola przecięcia w m² lub udziału procentowego 0–100, a styk jest zwracany
    osobno jako ``touches_ouz_boundary``.

    Progi są technicznym filtrem jakości geometrii, nie regułą prawną. Wynik
    opisuje stan danych przestrzennych i zawsze zawiera klauzulę, że wpływ OUZ
    na możliwość uzyskania WZ wymaga sprawdzenia aktualnego stanu prawnego.
    Brak geometrii OUZ daje ``unknown`` i wymaga ręcznej weryfikacji.
    """
    if intersection_area_epsilon_sqm < 0 or minimum_area_ratio_percent < 0:
        return _unknown_result(
            "OUZ_INVALID_THRESHOLD",
            "Progi analizy OUZ nie mogą mieć wartości ujemnych.",
        )

    parcel = _polygonal_geometry(parcel_geometry)
    if parcel is None or parcel.area <= 0:
        return _unknown_result(
            "OUZ_INVALID_PARCEL_GEOMETRY",
            "Geometria działki nie pozwala na powierzchniową analizę OUZ.",
        )

    if not ouz_geometries:
        return _unknown_result(
            "OUZ_DATA_UNAVAILABLE",
            "Brak danych geometrycznych OUZ nie potwierdza położenia działki poza OUZ.",
        )

    normalized_ouz = [
        geometry
        for item in ouz_geometries
        if (geometry := _polygonal_geometry(_geometry_from_input(item))) is not None
        and not geometry.is_empty
    ]
    if not normalized_ouz:
        return _unknown_result(
            "OUZ_GEOMETRY_UNAVAILABLE",
            "Dane OUZ nie zawierają użytecznej geometrii poligonowej.",
        )

    # Unia zapobiega podwójnemu liczeniu powierzchni, gdy gmina opublikowała
    # nakładające się części OUZ albo MultiPolygon jako kilka obiektów.
    combined_ouz = unary_union(normalized_ouz)
    intersection_area_sqm = parcel.intersection(combined_ouz).area
    area_ratio = intersection_area_sqm / parcel.area * 100.0
    in_ouz = (
        intersection_area_sqm > intersection_area_epsilon_sqm
        or area_ratio > minimum_area_ratio_percent
    )
    touches_boundary = not in_ouz and (
        parcel.touches(combined_ouz)
        or 0 < intersection_area_sqm <= intersection_area_epsilon_sqm
        or 0 < area_ratio <= minimum_area_ratio_percent
    )

    warnings: list[WarningMessage] = []
    manual_review_required = False
    if touches_boundary:
        warnings.append(
            _warning(
                "OUZ_BOUNDARY_TOUCH",
                "Działka tylko styka się z granicą OUZ albo ma przecięcie poniżej progów jakości geometrii.",
            )
        )
        manual_review_required = True

    return OuzStatusResult(
        status="available",
        in_ouz=in_ouz,
        intersection_area_sqm=intersection_area_sqm,
        area_ratio=area_ratio,
        touches_ouz_boundary=touches_boundary,
        manual_review_required=manual_review_required,
        legal_disclaimer=OUZ_LEGAL_DISCLAIMER,
        warnings=warnings,
    )


def _geometry_from_input(item: BaseGeometry | PogVectorFeature) -> BaseGeometry:
    return item.geometry if isinstance(item, PogVectorFeature) else item


def _polygonal_geometry(geometry: BaseGeometry) -> BaseGeometry | None:
    candidate = geometry if geometry.is_valid else make_valid(geometry)
    if candidate.geom_type in {"Polygon", "MultiPolygon"}:
        return candidate
    if candidate.geom_type == "GeometryCollection":
        polygons = [part for part in candidate.geoms if part.geom_type == "Polygon"]
        if polygons:
            return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
    return None


def _unknown_result(code: str, message: str) -> OuzStatusResult:
    return OuzStatusResult(
        status="unknown",
        in_ouz=False,
        intersection_area_sqm=0.0,
        area_ratio=0.0,
        touches_ouz_boundary=False,
        manual_review_required=True,
        legal_disclaimer=OUZ_LEGAL_DISCLAIMER,
        warnings=[_warning(code, message, severity="error")],
    )


def _warning(
    code: str,
    message: str,
    *,
    severity: Literal["info", "warning", "error"] = "warning",
) -> WarningMessage:
    return WarningMessage(
        code=code,
        message=message,
        severity=severity,
        source_name="ouz",
    )
