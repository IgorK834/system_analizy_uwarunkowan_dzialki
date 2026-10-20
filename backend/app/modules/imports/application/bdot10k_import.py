"""Przypadek użycia: import paczki powiatowej BDOT10k do lokalnego wydania (Task 23.1).

Przepływ jest deterministyczny: ta sama paczka (SHA-256) daje to samo wydanie i te same liczności obiektów per klasa.
Use case nie zna GDAL, SQL ani systemu plików — czyta przez port ``Bdot10kReader``, naprawia geometrię przez
``Bdot10kGeometryTools`` i zapisuje przez ``Bdot10kRepository``. Odrzucone obiekty mają powód w statystykach; paczka bez
zasięgu powiatu albo bez żadnego użytecznego obiektu jest odrzucana w całości (wydanie bez zasięgu nie mogłoby odróżnić
„brak drogi” od „brak danych”).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Mapping, Protocol

from app.modules.imports.application.common import (
    ImportOutcome,
    ImportRelease,
    ImportSourceNotRunnable,
)
from app.modules.imports.application.powerlines import prepare_powerlines
from app.modules.imports.domain.powerlines import PowerlineRow
from app.modules.imports.domain.bdot10k import (
    REJECT_EMPTY_AFTER_REPAIR,
    BuildingRow,
    Bdot10kValidationError,
    ExtentRow,
    LayerStats,
    LandCoverRow,
    LAND_COVER_LAYERS,
    clean_text,
    parse_version,
    RawFeature,
    RoadRow,
    data_as_of,
    keep_latest_per_id,
    normalize_building,
    normalize_road,
    select_extent,
    validate_mapping,
    validate_powiat_teryt,
)

# Spadek liczności względem poprzedniego wydania tego powiatu, poniżej którego import niesie ostrzeżenie.
REGRESSION_WARNING_RATIO = 0.8


@dataclass(frozen=True)
class Bdot10kBatch:
    """Odczytana paczka: metadane artefaktu i surowe obiekty trzech klas."""

    package_path: Path
    package_name: str
    package_sha256: str
    size_bytes: int
    teryt_powiat: str
    roads: tuple[RawFeature, ...] = ()
    buildings: tuple[RawFeature, ...] = ()
    boundaries: tuple[RawFeature, ...] = ()
    land_cover: Mapping[str, tuple[RawFeature, ...]] = field(default_factory=dict)
    # None = klasa nieobecna (starszy wycinek); () = potwierdzona pusta klasa.
    powerlines: tuple[RawFeature, ...] | None = None


@dataclass(frozen=True)
class RepairOutcome:
    """Wynik naprawy geometrii: WKB w EPSG:2180 (Multi*) albo powód odrzucenia."""

    wkb: bytes | None
    repaired: bool = False
    area_sqm: float = 0.0
    reject_reason: str | None = None


class Bdot10kReader(Protocol):
    def read(self) -> Bdot10kBatch: ...


class Bdot10kGeometryTools(Protocol):
    def repair_line(self, wkb: bytes) -> RepairOutcome: ...

    def repair_polygon(self, wkb: bytes) -> RepairOutcome: ...

    def repair_extent(self, wkb: bytes) -> RepairOutcome: ...


@dataclass(frozen=True)
class Bdot10kPublication:
    """Dane wydania do zapisu w jednej transakcji."""

    release: ImportRelease
    batch: Bdot10kBatch
    extent: ExtentRow
    roads: tuple[RoadRow, ...]
    buildings: tuple[BuildingRow, ...]
    class_stats: Mapping[str, Mapping[str, object]]
    data_as_of: date | None
    land_cover: tuple[LandCoverRow, ...] = ()
    powerlines: tuple[PowerlineRow, ...] = ()


@dataclass(frozen=True)
class Bdot10kPublicationResult:
    data_release_id: int
    import_run_id: int
    created: bool
    activated: bool


class Bdot10kRepository(Protocol):
    def previous_counts(self, teryt_powiat: str) -> tuple[int, int] | None:
        """Liczność dróg i budynków aktywnego wydania tego powiatu (do ostrzeżenia o regresji)."""
        ...

    def publish_bdot10k(self, publication: Bdot10kPublication) -> Bdot10kPublicationResult: ...


@dataclass
class _Prepared:
    roads: list[RoadRow] = field(default_factory=list)
    buildings: list[BuildingRow] = field(default_factory=list)


def run_bdot10k_import(
    reader: Bdot10kReader,
    release: ImportRelease,
    geometry: Bdot10kGeometryTools,
    repository: Bdot10kRepository | None,
    mapping: Mapping[str, str],
    *,
    teryt_powiat: str,
) -> ImportOutcome:
    """Czyta, normalizuje, naprawia i (o ile wolno) publikuje wydanie powiatu."""
    teryt = validate_powiat_teryt(teryt_powiat)
    validate_mapping(mapping)
    if not release.publication_allowed and not release.dry_run:
        raise ImportSourceNotRunnable("DRY-RUN ONLY: źródło nie jest production_ready.")

    batch = reader.read()
    if batch.teryt_powiat != teryt:
        raise Bdot10kValidationError(
            f"Paczka dotyczy powiatu {batch.teryt_powiat}, a polecenie wskazuje {teryt}."
        )

    extent_raw = select_extent(list(batch.boundaries), mapping, teryt)
    extent_geometry = geometry.repair_extent(extent_raw.geometry_wkb)
    if extent_geometry.wkb is None:
        raise Bdot10kValidationError(
            f"Granica powiatu {teryt} jest pusta po naprawie geometrii; zasięg wydania jest nieustalony."
        )
    extent = ExtentRow(teryt_powiat=teryt, name=extent_raw.name, geometry_wkb=extent_geometry.wkb)

    road_stats, building_stats = LayerStats(), LayerStats()
    cover_stats = LayerStats()
    cover = _prepare_land_cover(batch, mapping, geometry, cover_stats)
    powerline_stats = LayerStats()
    powerlines = prepare_powerlines(batch.powerlines or (), mapping, geometry, powerline_stats)
    prepared = _Prepared()
    prepared.roads = _prepare_roads(batch, mapping, geometry, road_stats)
    prepared.buildings = _prepare_buildings(batch, mapping, geometry, building_stats)
    road_stats.imported = len(prepared.roads)
    building_stats.imported = len(prepared.buildings)
    if not prepared.roads and not prepared.buildings:
        raise Bdot10kValidationError(
            f"Paczka powiatu {teryt} nie zawiera użytecznych dróg ani budynków "
            f"(odrzucono {sum(road_stats.rejected.values()) + sum(building_stats.rejected.values())})."
        )

    class_stats = {
        "powerlines": {**powerline_stats.as_dict(), "complete": batch.powerlines is not None},
        "land_cover": {**cover_stats.as_dict(), "complete": set(batch.land_cover) == set(LAND_COVER_LAYERS)
                       and not cover_stats.rejected, "layers": sorted(batch.land_cover)},
        "roads": road_stats.as_dict(),
        "buildings": building_stats.as_dict(),
        "boundaries": {"read": len(batch.boundaries), "used": 1},
    }
    as_of = data_as_of(road_stats, building_stats, cover_stats, powerline_stats)
    stats: dict[str, int | float | str] = {
        "teryt_powiat": teryt,
        "package": batch.package_name,
        "package_sha256": batch.package_sha256,
        "package_bytes": batch.size_bytes,
        "land_cover_imported": len(cover),
        "powerlines_imported": len(powerlines),
        "powerlines_complete": int(batch.powerlines is not None),
        "roads_read": road_stats.read,
        "roads_imported": road_stats.imported,
        "roads_rejected": sum(road_stats.rejected.values()),
        "roads_repaired": road_stats.repaired,
        "buildings_read": building_stats.read,
        "buildings_imported": building_stats.imported,
        "buildings_rejected": sum(building_stats.rejected.values()),
        "buildings_repaired": building_stats.repaired,
        "data_as_of": as_of.isoformat() if as_of else "unknown",
    }
    warnings: list[str] = []
    for label, layer in (("roads", road_stats), ("buildings", building_stats), ("land_cover", cover_stats)):
        for reason, count in sorted(layer.rejected.items()):
            warnings.append(f"{label}:{reason}={count}")

    if release.dry_run or repository is None:
        return ImportOutcome(status="dry_run_only", stats=stats, warnings=tuple(warnings))

    previous = repository.previous_counts(teryt)
    if previous is not None:
        for label, before, now in (
            ("roads", previous[0], len(prepared.roads)),
            ("buildings", previous[1], len(prepared.buildings)),
        ):
            if before and now < before * REGRESSION_WARNING_RATIO:
                warnings.append(f"regression:{label}:{before}->{now}")
    result = repository.publish_bdot10k(
        Bdot10kPublication(
            release=release,
            batch=batch,
            extent=extent,
            roads=tuple(prepared.roads),
            buildings=tuple(prepared.buildings),
            class_stats=class_stats,
            data_as_of=as_of,
            land_cover=tuple(cover),
            powerlines=powerlines,
        )
    )
    stats["created"] = int(result.created)
    stats["activated"] = int(result.activated)
    return ImportOutcome(
        status="succeeded",
        stats=stats,
        warnings=tuple(warnings),
        import_run_id=result.import_run_id,
        data_release_id=result.data_release_id,
    )


def _prepare_roads(
    batch: Bdot10kBatch,
    mapping: Mapping[str, str],
    geometry: Bdot10kGeometryTools,
    stats: LayerStats,
) -> list[RoadRow]:
    rows: list[RoadRow] = []
    for feature in batch.roads:
        row = normalize_road(feature, mapping, stats)
        if row is None:
            continue
        repaired = geometry.repair_line(row.geometry_wkb)
        if repaired.wkb is None:
            stats.reject(repaired.reject_reason or REJECT_EMPTY_AFTER_REPAIR)
            continue
        if repaired.repaired:
            stats.repaired += 1
        rows.append(replace(row, geometry_wkb=repaired.wkb))
    return keep_latest_per_id(rows, stats)


def _prepare_buildings(
    batch: Bdot10kBatch,
    mapping: Mapping[str, str],
    geometry: Bdot10kGeometryTools,
    stats: LayerStats,
) -> list[BuildingRow]:
    rows: list[BuildingRow] = []
    for feature in batch.buildings:
        row = normalize_building(feature, mapping, stats)
        if row is None:
            continue
        repaired = geometry.repair_polygon(row.geometry_wkb)
        if repaired.wkb is None or repaired.area_sqm <= 0:
            stats.reject(repaired.reject_reason or REJECT_EMPTY_AFTER_REPAIR)
            continue
        if repaired.repaired:
            stats.repaired += 1
        rows.append(replace(row, geometry_wkb=repaired.wkb, area_sqm=repaired.area_sqm))
    return keep_latest_per_id(rows, stats)


def _prepare_land_cover(batch, mapping, geometry, stats) -> list[LandCoverRow]:
    rows = []
    for code, features in sorted(batch.land_cover.items()):
        if code not in LAND_COVER_LAYERS:
            raise Bdot10kValidationError(f"Nieznana klasa pokrycia: {code}")
        for feature in features:
            stats.read += 1
            local_id = clean_text(feature.attributes.get(mapping["local_id"]))
            if not local_id or len(local_id) > 64:
                stats.reject("invalid_local_id")
                continue
            if feature.geometry_wkb is None:
                stats.reject("no_geometry")
                continue
            repaired = geometry.repair_polygon(feature.geometry_wkb)
            if repaired.wkb is None:
                stats.reject(repaired.reject_reason or REJECT_EMPTY_AFTER_REPAIR)
                continue
            stats.repaired += int(repaired.repaired)
            version = parse_version(feature.attributes.get(mapping["feature_version"]))
            if version and (stats.latest_version is None or version > stats.latest_version):
                stats.latest_version = version
            kind = clean_text(feature.attributes.get(mapping.get("land_cover_kind", "rodzaj")))
            if code == "OT_PTLZ_A" and kind not in {"las", "zagajnik", "zadrzewienie"}:
                stats.reject("unknown_forest_kind")
                continue
            rows.append(LandCoverRow(local_id, code, kind, repaired.wkb, version))
    result = keep_latest_per_id(rows, stats)
    stats.imported = len(result)
    return result
