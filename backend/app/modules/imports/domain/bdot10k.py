"""Czyste typy i reguły normalizacji paczek powiatowych BDOT10k (Task 23.1, BK-304/BK-305).

Paczka ma jeden plik GML na klasę obiektów. Importujemy trzy:

* ``OT_SKJZ_L`` — osie jezdni (linie),
* ``OT_BUBD_A`` — obrysy budynków (poligony),
* ``OT_ADJA_A`` — granice jednostek administracyjnych; obiekt ``powiat`` o danym TERYT jest zasięgiem wydania.

Moduł nie zna GDAL, SQL ani systemu plików. Geometria jest przekazywana jako WKB w EPSG:2180 (kolejność osi
``easting northing``, jak w paczce) i naprawiana przez infrastrukturę. Reguły są jawne: brak identyfikatora, zła klasa
geometrii albo duplikat identyfikatora to odrzucenie obiektu z powodem, a nie ciche pominięcie. Pola tekstu wolnego
(``nazwa``, ``informacjaDodatkowa``, ``uwagi``) i ``identyfikatorEGiB`` nie są czytane — dane osobowe nie wchodzą do
bazy, wyniku ani eksportu.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Final, Mapping

BDOT10K_SOURCE_ID: Final[str] = "bdot10k"
ROAD_LAYER: Final[str] = "OT_SKJZ_L"
BUILDING_LAYER: Final[str] = "OT_BUBD_A"
BOUNDARY_LAYER: Final[str] = "OT_ADJA_A"
REQUIRED_LAYERS: Final[tuple[str, ...]] = (ROAD_LAYER, BUILDING_LAYER, BOUNDARY_LAYER)
LAND_COVER_LAYERS: Final[tuple[str, ...]] = tuple(
    f"OT_{code}_A" for code in ("PTWP", "PTZB", "PTLZ", "PTRK", "PTUT", "PTTR", "PTKM", "PTGN", "PTPL", "PTSO", "PTWZ", "PTNZ")
)
POWIAT_UNIT_KIND: Final[str] = "powiat"
SCOPE_PREFIX: Final[str] = "powiat:"

# Klucze mapowania pól katalogu (``field_mapping``) czytane z każdej klasy — jedyne źródło prawdy o kolumnach GML.
ROAD_FIELD_KEYS: Final[tuple[str, ...]] = (
    "local_id", "existence_category", "feature_version", "management_category", "road_class", "surface_material",
    "road_position", "road_number", "street_name", "surface_width_m",
)
BUILDING_FIELD_KEYS: Final[tuple[str, ...]] = (
    "local_id", "existence_category", "feature_version", "building_general_function", "building_detailed_function",
    "building_predominant_function", "building_floors", "geometry_source",
)
BOUNDARY_FIELD_KEYS: Final[tuple[str, ...]] = ("local_id", "unit_name", "unit_kind", "unit_teryt")
ALL_FIELD_KEYS: Final[frozenset[str]] = frozenset(
    (*ROAD_FIELD_KEYS, *BUILDING_FIELD_KEYS, *BOUNDARY_FIELD_KEYS)
)

# Powody odrzucenia obiektu (stałe kody, widoczne w statystykach importu).
REJECT_MISSING_ID: Final[str] = "missing_local_id"
REJECT_ID_TOO_LONG: Final[str] = "local_id_too_long"
REJECT_NO_GEOMETRY: Final[str] = "no_geometry"
REJECT_WRONG_GEOMETRY: Final[str] = "wrong_geometry_type"
REJECT_EMPTY_AFTER_REPAIR: Final[str] = "empty_after_repair"
REJECT_DUPLICATE_ID: Final[str] = "duplicate_local_id"

_TERYT_POWIAT = re.compile(r"^\d{4}$")
_MAX_LOCAL_ID = 64
_WHITESPACE = re.compile(r"\s+")
# Kategoria istnienia, którą zapytania o kontekst traktują jako stan faktyczny (reszta: w budowie, nieczynny…).
IN_OPERATION: Final[str] = "eksploatowany"


class Bdot10kValidationError(ValueError):
    """Paczka nie spełnia minimalnego kontraktu (brak klasy, zasięgu, niezgodny powiat)."""


def validate_powiat_teryt(value: str) -> str:
    """TERYT powiatu: dokładnie cztery cyfry (``1261``, ``0262``); inaczej błąd użycia."""
    cleaned = (value or "").strip()
    if not _TERYT_POWIAT.fullmatch(cleaned):
        raise Bdot10kValidationError(
            f"Kod powiatu {value!r} nie jest czterocyfrowym TERYT (np. 1261)."
        )
    return cleaned


def validate_mapping(mapping: Mapping[str, str]) -> Mapping[str, str]:
    """Katalog musi mapować wszystkie pola importu; brak klucza to błąd konfiguracji, nie pusty atrybut."""
    missing = sorted(ALL_FIELD_KEYS - set(mapping))
    if missing:
        raise Bdot10kValidationError(f"Mapowanie pól bdot10k w katalogu nie ma kluczy: {', '.join(missing)}.")
    return mapping


def scope_key(teryt_powiat: str) -> str:
    """Klucz zakresu wydania: jedno aktywne wydanie na powiat."""
    return f"{SCOPE_PREFIX}{validate_powiat_teryt(teryt_powiat)}"


@dataclass(frozen=True)
class RawFeature:
    """Obiekt odczytany z GML: atrybuty źródłowe i geometria jako WKB (EPSG:2180)."""

    attributes: Mapping[str, Any]
    geometry_wkb: bytes | None


@dataclass(frozen=True)
class RoadRow:
    """Oś jezdni gotowa do zapisu."""

    local_id: str
    geometry_wkb: bytes
    existence_category: str | None
    management_category: str | None
    road_class: str | None
    surface_material: str | None
    road_position: str | None
    road_number: str | None
    street_name: str | None
    surface_width_m: float | None
    feature_version_at: datetime | None


@dataclass(frozen=True)
class BuildingRow:
    """Obrys budynku gotowy do zapisu (bez nazw, uwag i identyfikatora EGiB)."""

    local_id: str
    geometry_wkb: bytes
    area_sqm: float
    existence_category: str | None
    general_function: str | None
    detailed_function: str | None
    predominant_function: str | None
    floors: int | None
    geometry_source: str | None
    feature_version_at: datetime | None


@dataclass(frozen=True)
class LandCoverRow:
    local_id: str
    class_code: str
    kind: str | None
    geometry_wkb: bytes
    feature_version_at: datetime | None


@dataclass(frozen=True)
class ExtentRow:
    """Zasięg wydania: granica powiatu (``OT_ADJA_A``, rodzaj ``powiat``)."""

    teryt_powiat: str
    name: str | None
    geometry_wkb: bytes


@dataclass
class LayerStats:
    """Liczniki jednej klasy obiektów; ``imported`` to liczba wierszy do zapisu."""

    read: int = 0
    imported: int = 0
    repaired: int = 0
    rejected: Counter[str] = field(default_factory=Counter)
    existence: Counter[str] = field(default_factory=Counter)
    latest_version: datetime | None = None

    def reject(self, reason: str) -> None:
        self.rejected[reason] += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "read": self.read,
            "imported": self.imported,
            "repaired": self.repaired,
            "rejected": dict(sorted(self.rejected.items())),
            "rejected_total": sum(self.rejected.values()),
            "existence": dict(sorted(self.existence.items())),
            "latest_version": self.latest_version.isoformat() if self.latest_version else None,
        }


def clean_text(value: Any) -> str | None:
    """Tekst bez nadmiarowych białych znaków; pusty łańcuch i ``None`` to brak wartości (nie zero)."""
    if value is None:
        return None
    text = _WHITESPACE.sub(" ", str(value).strip())
    return text or None


def parse_version(value: Any) -> datetime | None:
    """Wersja obiektu (``wersja``): czas bez strefy w źródle jest interpretowany jako UTC; błędny zapis to ``None``."""
    text = clean_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_positive_int(value: Any) -> int | None:
    """Liczba kondygnacji: liczba całkowita >= 0; brak albo nieliczbowa wartość to ``None`` (nie 0)."""
    text = clean_text(value)
    if text is None:
        return None
    try:
        number = int(float(text.replace(",", ".")))
    except ValueError:
        return None
    return number if number >= 0 else None


def parse_width(value: Any) -> float | None:
    """Szerokość nawierzchni w metrach; brak, zero i wartości nieliczbowe to ``None``."""
    text = clean_text(value)
    if text is None:
        return None
    try:
        number = float(text.replace(",", "."))
    except ValueError:
        return None
    return number if number > 0 else None


def _local_id(attributes: Mapping[str, Any], key: str, stats: LayerStats) -> str | None:
    local_id = clean_text(attributes.get(key))
    if local_id is None:
        stats.reject(REJECT_MISSING_ID)
        return None
    if len(local_id) > _MAX_LOCAL_ID:
        stats.reject(REJECT_ID_TOO_LONG)
        return None
    return local_id


def _track(stats: LayerStats, existence: str | None, version: datetime | None) -> None:
    stats.existence[existence or "(brak)"] += 1
    if version is not None and (stats.latest_version is None or version > stats.latest_version):
        stats.latest_version = version


def normalize_road(
    feature: RawFeature, mapping: Mapping[str, str], stats: LayerStats
) -> RoadRow | None:
    """Mapuje atrybuty źródłowe osi jezdni według ``field_mapping`` katalogu; ``None`` = odrzucony (z powodem)."""
    stats.read += 1
    attributes = feature.attributes
    local_id = _local_id(attributes, mapping["local_id"], stats)
    if local_id is None:
        return None
    if feature.geometry_wkb is None:
        stats.reject(REJECT_NO_GEOMETRY)
        return None
    existence = clean_text(attributes.get(mapping["existence_category"]))
    version = parse_version(attributes.get(mapping["feature_version"]))
    _track(stats, existence, version)
    street = clean_text(attributes.get(mapping["street_name"]))
    return RoadRow(
        local_id=local_id,
        geometry_wkb=feature.geometry_wkb,
        existence_category=existence,
        management_category=clean_text(attributes.get(mapping["management_category"])),
        road_class=clean_text(attributes.get(mapping["road_class"])),
        surface_material=clean_text(attributes.get(mapping["surface_material"])),
        road_position=clean_text(attributes.get(mapping["road_position"])),
        road_number=clean_text(attributes.get(mapping["road_number"])),
        street_name=street,
        surface_width_m=parse_width(attributes.get(mapping["surface_width_m"])),
        feature_version_at=version,
    )


def normalize_building(
    feature: RawFeature, mapping: Mapping[str, str], stats: LayerStats
) -> BuildingRow | None:
    """Mapuje atrybuty budynku; ``area_sqm`` jest uzupełniane po naprawie geometrii (``with_area``)."""
    stats.read += 1
    attributes = feature.attributes
    local_id = _local_id(attributes, mapping["local_id"], stats)
    if local_id is None:
        return None
    if feature.geometry_wkb is None:
        stats.reject(REJECT_NO_GEOMETRY)
        return None
    existence = clean_text(attributes.get(mapping["existence_category"]))
    version = parse_version(attributes.get(mapping["feature_version"]))
    _track(stats, existence, version)
    return BuildingRow(
        local_id=local_id,
        geometry_wkb=feature.geometry_wkb,
        area_sqm=0.0,
        existence_category=existence,
        general_function=clean_text(attributes.get(mapping["building_general_function"])),
        detailed_function=clean_text(attributes.get(mapping["building_detailed_function"])),
        predominant_function=clean_text(attributes.get(mapping["building_predominant_function"])),
        floors=parse_positive_int(attributes.get(mapping["building_floors"])),
        geometry_source=clean_text(attributes.get(mapping["geometry_source"])),
        feature_version_at=version,
    )


def select_extent(
    features: list[RawFeature], mapping: Mapping[str, str], teryt_powiat: str
) -> ExtentRow:
    """Zasięg wydania: dokładnie jeden obiekt ``powiat`` o podanym TERYT; inaczej paczka jest odrzucana."""
    kind_key, teryt_key = mapping["unit_kind"], mapping["unit_teryt"]
    matching = [
        feature
        for feature in features
        if clean_text(feature.attributes.get(kind_key)) == POWIAT_UNIT_KIND
        and clean_text(feature.attributes.get(teryt_key)) == teryt_powiat
        and feature.geometry_wkb is not None
    ]
    if len(matching) != 1:
        raise Bdot10kValidationError(
            f"Paczka nie zawiera jednoznacznego obiektu powiatu {teryt_powiat} w {BOUNDARY_LAYER} "
            f"(znaleziono {len(matching)}); zasięg wydania jest nieustalony."
        )
    chosen = matching[0]
    assert chosen.geometry_wkb is not None
    return ExtentRow(
        teryt_powiat=teryt_powiat,
        name=clean_text(chosen.attributes.get(mapping["unit_name"])),
        geometry_wkb=chosen.geometry_wkb,
    )


def keep_latest_per_id(rows: list[Any], stats: LayerStats) -> list[Any]:
    """Duplikat ``lokalnyId`` w jednej klasie: zostaje wersja najnowsza (potem pierwsza w pliku); reszta jest policzona."""
    best: dict[str, Any] = {}
    order: dict[str, int] = {}
    for index, row in enumerate(rows):
        current = best.get(row.local_id)
        if current is None:
            best[row.local_id] = row
            order[row.local_id] = index
            continue
        stats.reject(REJECT_DUPLICATE_ID)
        floor = datetime.min.replace(tzinfo=timezone.utc)
        if (row.feature_version_at or floor) > (current.feature_version_at or floor):
            best[row.local_id] = row
    return [best[key] for key in sorted(best, key=lambda item: order[item])]


def data_as_of(*layers: LayerStats) -> date | None:
    """Data stanu danych: najnowsza wersja obiektu spośród zaimportowanych klas (nie data pobrania paczki)."""
    versions = [layer.latest_version for layer in layers if layer.latest_version is not None]
    return max(versions).date() if versions else None
