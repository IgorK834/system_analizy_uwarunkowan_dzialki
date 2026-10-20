"""Definicje pokrycia i sąsiedztwa lasu (EPSG:2180), bez interpretacji prawnej."""

from dataclasses import dataclass
from shapely import from_wkb
from shapely.ops import unary_union

LAND_COVER_NOTE = (
    "Udziały to pola przecięć klas BDOT10k z działką i pierścieniem buffer(działka, 200 m) − działka "
    "w EPSG:2180. Nakładające się obiekty jednej klasy są scalane. Brak pokrycia nie jest zerowym udziałem. "
    "Las oznacza OT_PTLZ_A z rodzajem las; zagajniki i zadrzewienia nie są lasem w tym obliczeniu. "
    "Bufory 8/12/16 m są orientacyjnym przybliżeniem geometrycznym, bez interpretacji przepisów."
)
CLASS_LABELS = {
    "OT_PTWP_A": "Wody powierzchniowe",
    "OT_PTZB_A": "Zabudowa",
    "OT_PTLZ_A": "Tereny leśne i zadrzewione",
    "OT_PTRK_A": "Roślinność krzewiasta",
    "OT_PTUT_A": "Uprawy trwałe",
    "OT_PTTR_A": "Roślinność trawiasta i uprawy rolne",
    "OT_PTKM_A": "Tereny komunikacyjne",
    "OT_PTGN_A": "Grunty nieużytkowane",
    "OT_PTPL_A": "Place",
    "OT_PTSO_A": "Składowiska odpadów",
    "OT_PTWZ_A": "Wyrobiska i zwałowiska",
    "OT_PTNZ_A": "Pozostałe tereny niezabudowane",
}


@dataclass(frozen=True)
class CoverMeasurement:
    zone: str
    class_code: str
    area_sqm: float
    zone_area_sqm: float
    geometry_wkb: bytes


@dataclass(frozen=True)
class ForestMeasurement:
    local_id: str
    distance_m: float
    geometry_wkb: bytes
    buffers: tuple[tuple[float, float, bytes], ...]


def coverage_is_complete(rows: list[CoverMeasurement]) -> bool:
    """Nie normalizujemy udziałów do 100%: luka i nadmiar są błędem jakości, nie inną klasą."""
    for zone in ("parcel", "surroundings"):
        group = [r for r in rows if r.zone == zone]
        if not group or group[0].zone_area_sqm <= 0:
            return False
        total = sum(r.area_sqm for r in group) / group[0].zone_area_sqm * 100
        union_pct = unary_union([from_wkb(r.geometry_wkb) for r in group]).area / group[0].zone_area_sqm * 100
        if abs(total - 100) > 0.01 or abs(union_pct - 100) > 0.01:
            return False
    return True


CLASS_COLORS = {
    "OT_PTWP_A": "#61a5d8",
    "OT_PTZB_A": "#b78274",
    "OT_PTLZ_A": "#387c45",
    "OT_PTRK_A": "#91ad69",
    "OT_PTUT_A": "#b7a34c",
    "OT_PTTR_A": "#c5cf81",
    "OT_PTKM_A": "#99999b",
    "OT_PTGN_A": "#d7bf98",
    "OT_PTPL_A": "#c9beb3",
    "OT_PTSO_A": "#ad829e",
    "OT_PTWZ_A": "#bc915f",
    "OT_PTNZ_A": "#b5a8cb",
}
