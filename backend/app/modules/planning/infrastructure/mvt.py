"""Adapter PostGIS kafli wektorowych POG z krajowego stanu rejestru (BK-401, ADR-026).

Kafel powstaje jednym zapytaniem: tabele ``pog_map_*`` (kopie do wyświetlania w EPSG:3857, gotowe atrybuty,
trzy stopnie uproszczenia zależne od zoomu) → selekcja indeksem GiST po obwiedni kafla z marginesem →
``ST_AsMVTGeom(extent=4096, buffer=64)`` → ``ST_AsMVT`` osobno dla każdej z pięciu warstw logicznych, sklejonych w jeden
protobuf. O widoczności wersji aktów decyduje rejestr: zbiór identyfikatorów wersji aktywnych w stanie ``N`` jest
wyliczany raz i zapamiętywany (stan jest niezmienny po zatwierdzeniu), a zapytanie dostaje go jako tablicę.

Atrybuty kafla liczy przy publikacji wersji ta sama funkcja co w analizie (``planning.domain.pog_tiles``), a ``NULL``
nie jest kodowany w MVT: brak wartości parametru = brak klucza, nigdy 0. Etykiety i kody profili trafiają do kafla
dopiero od z13 (``TEXT_ATTRIBUTES_MIN_ZOOM``) — przy małym zoomie nie są czytelne, a zwiększałyby kafel.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import RowMapping, text
from sqlalchemy.orm import Session

from app.modules.planning.application.pog_tiles import EncodedTile, PogTileCache, PogTileRepository
from app.modules.planning.domain.pog_tiles import (
    LAYER_MIN_ZOOM,
    MVT_BUFFER,
    MVT_EXTENT,
    TEXT_ATTRIBUTES_MIN_ZOOM,
    FEATURE_TYPE_LAYERS,
    PogCoverageArea,
    PogStateInfo,
    PogTile,
    PogTileRequest,
    geometry_column,
)
from app.shared.planning_status import canonical_legal_status
from app.shared.pog_registry import ACTIVE_AT_STATE_SQL

_ZONE_ATTRIBUTES: Final[str] = (
    "z.feature_ref AS feature_id, z.feature_version, z.symbol, z.designation, "
    "CASE WHEN CAST(:with_text AS boolean) THEN z.label END AS label, z.legal_status, z.teryt, z.act_id, "
    "z.zone_code, z.max_overground_floor_area_ratio, z.max_building_height_m, z.max_building_coverage_pct, "
    "z.min_biologically_active_pct, z.parameters_informational, "
    "CASE WHEN CAST(:with_text AS boolean) THEN z.primary_profiles END AS primary_profiles, "
    "CASE WHEN CAST(:with_text AS boolean) THEN z.additional_profiles END AS additional_profiles"
)
_OVERLAY_ATTRIBUTES: Final[str] = (
    "o.feature_ref AS feature_id, o.feature_version, o.symbol, "
    "CASE WHEN CAST(:with_text AS boolean) THEN o.label END AS label, o.legal_status, o.teryt, o.act_id"
)
_ACT_ATTRIBUTES: Final[str] = (
    "a.act_id, left(a.name, 200) AS label, a.legal_status, a.teryt, a.act_version, a.resolution_number, "
    "CAST(a.resolution_date AS text) AS resolution_date, CAST(a.legal_valid_from AS text) AS valid_from, "
    "CAST(a.effective_date AS text) AS effective_date, CAST(a.version_started_on AS text) AS version_started_on"
)
_STATUS_FILTER: Final[str] = (
    "(CAST(:statuses AS text[]) IS NULL OR {alias}.legal_status = ANY(CAST(:statuses AS text[])))"
)

_OVERLAY_LAYERS: Final[tuple[str, ...]] = ("ouz", "downtown", "social_infrastructure_standard")

_ACTIVE_VERSIONS_SQL: Final[str] = (
    "SELECT e.planning_act_version_id FROM pog_registry_entries e WHERE " + ACTIVE_AT_STATE_SQL
)


def _tile_ctes(z: int) -> tuple[str, list[str]]:
    """CTE-y warstw dostępnych przy zoomie ``z`` oraz ich nazwy (kolejność = kolejność warstw w kaflu)."""
    ctes: list[str] = []
    names: list[str] = []
    if z >= LAYER_MIN_ZOOM["zones"]:
        col = geometry_column("zones", z)
        ctes.append(
            f"""zones AS MATERIALIZED (
            SELECT z.feature_id AS pk, {_ZONE_ATTRIBUTES},
                   ST_AsMVTGeom(z.{col}, tile.env, {MVT_EXTENT}, {MVT_BUFFER}, true) AS geom
            FROM pog_map_zones z CROSS JOIN tile
            WHERE z.planning_act_version_id = ANY(CAST(:versions AS int[]))
              AND z.geom_full && tile.env_m AND {_STATUS_FILTER.format(alias="z")}
            ORDER BY z.feature_id LIMIT :limit)"""
        )
        names.append("zones")
    for layer in _OVERLAY_LAYERS:
        if z < LAYER_MIN_ZOOM[layer]:
            continue
        col = geometry_column(layer, z)
        ctes.append(
            f"""{layer} AS MATERIALIZED (
            SELECT o.feature_id AS pk, {_OVERLAY_ATTRIBUTES},
                   ST_AsMVTGeom(o.{col}, tile.env, {MVT_EXTENT}, {MVT_BUFFER}, true) AS geom
            FROM pog_map_overlays o CROSS JOIN tile
            WHERE o.planning_act_version_id = ANY(CAST(:versions AS int[]))
              AND o.layer = '{layer}' AND o.geom_full && tile.env_m AND {_STATUS_FILTER.format(alias="o")}
            ORDER BY o.feature_id LIMIT :limit)"""
        )
        names.append(layer)
    col = geometry_column("act_boundary", z)
    ctes.append(
        f"""act_boundary AS MATERIALIZED (
            SELECT a.planning_act_version_id AS pk, {_ACT_ATTRIBUTES},
                   ST_AsMVTGeom(a.{col}, tile.env, {MVT_EXTENT}, {MVT_BUFFER}, true) AS geom
            FROM pog_map_acts a CROSS JOIN tile
            WHERE a.planning_act_version_id = ANY(CAST(:versions AS int[]))
              AND a.geom_full && tile.env_m AND {_STATUS_FILTER.format(alias="a")}
            ORDER BY a.planning_act_version_id LIMIT :limit)"""
    )
    names.append("act_boundary")
    return ",\n    ".join(ctes), names


def _tile_sql(z: int) -> tuple[str, list[str]]:
    ctes, names = _tile_ctes(z)
    counts = ", ".join(f"(SELECT count(*) FROM {name}) AS n_{name}" for name in names)
    layers = " || ".join(
        f"COALESCE((SELECT ST_AsMVT(q, '{name}', {MVT_EXTENT}, 'geom', 'pk') "
        f"FROM (SELECT * FROM {name} WHERE geom IS NOT NULL ORDER BY pk) AS q), '\\x'::bytea)"
        for name in names
    )
    sql = f"""
    WITH tile AS (
        SELECT ST_TileEnvelope(:z, :x, :y) AS env,
               ST_TileEnvelope(:z, :x, :y, margin => :margin) AS env_m
    ),
    {ctes}
    SELECT {counts}, {layers} AS tile
    """
    return sql, names


_STATE_SQL: Final[str] = """
    SELECT s.id, s.kind, s.created_at, s.id = (SELECT max(id) FROM pog_registry_states) AS is_current
    FROM pog_registry_states s WHERE s.id = :state_id
"""

_STATE_STATUS_SQL: Final[str] = """
    SELECT COALESCE(pav.legal_status, 'unknown') AS legal_status, count(*) AS acts
    FROM planning_act_versions pav WHERE pav.id = ANY(CAST(:versions AS int[])) GROUP BY 1
"""

_STATE_BOUNDS_SQL: Final[str] = """
    SELECT ST_XMin(g) AS min_lon, ST_YMin(g) AS min_lat, ST_XMax(g) AS max_lon, ST_YMax(g) AS max_lat
    FROM (
        SELECT ST_Transform(ST_SetSRID(ST_Extent(a.geom_full)::geometry, 3857), 4326) AS g
        FROM pog_map_acts a WHERE a.planning_act_version_id = ANY(CAST(:versions AS int[]))
    ) e WHERE g IS NOT NULL
"""

# Zasięg danych każdej aktywnej wersji aktu w 4326 + kompletność agregatu aktu (BK-405). Akt bez granicy ma zasięg
# z obwiedni swoich stref. Koperta jest segmentyzowana przed transformacją, żeby łuk odwzorowania nie ścinał narożników.
_STATE_COVERAGE_SQL: Final[str] = """
    WITH versions AS (
        SELECT pav.id, pa.act_identifier, pa.teryt, pav.legal_status
        FROM planning_act_versions pav JOIN planning_acts pa ON pa.id = pav.planning_act_id
        WHERE pav.id = ANY(CAST(:versions AS int[]))
    ), extents AS (
        SELECT v.id AS version_id,
               ST_Transform(ST_Segmentize(COALESCE(
                   (SELECT ST_Envelope(a.geom_full) FROM pog_map_acts a WHERE a.planning_act_version_id = v.id),
                   (SELECT ST_SetSRID(ST_Extent(z.geom_full)::geometry, 3857) FROM pog_map_zones z
                     WHERE z.planning_act_version_id = v.id)), 50000), 4326) AS g
        FROM versions v
    )
    SELECT v.act_identifier, v.teryt, v.legal_status,
           ST_XMin(e.g) AS min_lon, ST_YMin(e.g) AS min_lat, ST_XMax(e.g) AS max_lon, ST_YMax(e.g) AS max_lat,
           EXISTS (SELECT 1 FROM plan_boundaries pb WHERE pb.planning_act_version_id = v.id) AS has_boundary,
           s.is_complete, s.incomplete_reasons
    FROM versions v
    LEFT JOIN extents e ON e.version_id = v.id
    LEFT JOIN pog_area_summaries s ON s.planning_act_version_id = v.id AND s.scope = 'act'
    ORDER BY v.act_identifier, v.id
"""

# Brakujące wiersze mapy liczone przez porównanie liczności per wersja (indeksy po ``planning_act_version_id``) —
# antyjoin po 500 tys. obiektów trwał ponad sekundę na stanie krajowym.
_MISSING_MAP_ROWS_SQL: Final[str] = """
    SELECT pav.id FROM planning_act_versions pav
    WHERE pav.id = ANY(CAST(:versions AS int[]))
      AND (
        (EXISTS (SELECT 1 FROM plan_boundaries pb WHERE pb.planning_act_version_id = pav.id)
         AND NOT EXISTS (SELECT 1 FROM pog_map_acts a WHERE a.planning_act_version_id = pav.id))
        OR (SELECT count(*) FROM planning_features pf
              WHERE pf.planning_act_version_id = pav.id AND pf.feature_type = ANY(CAST(:feature_types AS text[])))
           > (SELECT count(*) FROM pog_map_zones z WHERE z.planning_act_version_id = pav.id)
             + (SELECT count(*) FROM pog_map_overlays o WHERE o.planning_act_version_id = pav.id)
      )
"""

_STATE_CACHE_SIZE: Final[int] = 32


class _StateCache:
    """Pamięć procesu per numer stanu (LRU). Zbiór wersji aktywnych w stanie jest niezmienny po zatwierdzeniu."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._items: OrderedDict[int, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, state_id: int) -> Any | None:
        with self._lock:
            value = self._items.get(state_id)
            if value is not None:
                self._items.move_to_end(state_id)
            return value

    def put(self, state_id: int, value: Any) -> None:
        with self._lock:
            self._items[state_id] = value
            self._items.move_to_end(state_id)
            while len(self._items) > self._capacity:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


@dataclass(frozen=True)
class _StateMeta:
    """Niezmienna część metadanych stanu (nie zależy od tego, czy stan jest jeszcze bieżący)."""

    act_count: int
    bounds: tuple[float, float, float, float] | None
    acts_by_legal_status: Mapping[str, int]
    coverage_areas: tuple[PogCoverageArea, ...]


active_versions_cache = _StateCache(_STATE_CACHE_SIZE)
# Statystyki, zasięg i kompletność stanu liczone raz na stan — wyłącznie gdy mapa jest kompletna (po ``pog-map-rebuild``
# zasięgi brakujących wersji by się zmieniły, więc stanu z brakami nie zapamiętujemy).
state_meta_cache = _StateCache(_STATE_CACHE_SIZE)
# Wersje aktu, dla których sprawdzono kompletność wierszy mapy. Wiersze wersji powstają raz przy publikacji i się nie
# zmieniają, więc po stwierdzeniu kompletności sprawdzenie nie jest powtarzane — nowy stan sprawdza tylko nowe wersje.
ready_map_versions: set[int] = set()
_ready_lock = threading.Lock()


class SqlAlchemyPogTileRepository(PogTileRepository):
    def __init__(self, session: Session, *, statement_timeout_ms: int) -> None:
        self._session = session
        self._statement_timeout_ms = statement_timeout_ms

    def _limit_statement_time(self) -> None:
        # ``is_local=true``: limit obowiązuje tylko w bieżącej transakcji żądania.
        self._session.execute(
            text("SELECT set_config('statement_timeout', :value, true)"),
            {"value": str(self._statement_timeout_ms)},
        )

    def _versions(self, state_id: int) -> tuple[int, ...]:
        cached = active_versions_cache.get(state_id)
        if cached is not None:
            return cached
        versions = tuple(
            sorted(
                int(value)
                for value in self._session.execute(
                    text(_ACTIVE_VERSIONS_SQL), {"state_id": state_id}
                ).scalars()
            )
        )
        active_versions_cache.put(state_id, versions)
        return versions

    def current_state_id(self) -> int | None:
        value = self._session.execute(text("SELECT max(id) FROM pog_registry_states")).scalar_one()
        return int(value) if value is not None else None

    def state_info(self, state_id: int) -> PogStateInfo | None:
        row = self._session.execute(text(_STATE_SQL), {"state_id": state_id}).mappings().one_or_none()
        if row is None:
            return None
        cached: _StateMeta | None = state_meta_cache.get(state_id)
        if cached is not None:
            return self._info(row, cached, missing=0)
        versions = list(self._versions(state_id))
        statuses = {
            str(item["legal_status"]): int(item["acts"])
            for item in self._session.execute(text(_STATE_STATUS_SQL), {"versions": versions}).mappings()
        }
        bounds_row = self._session.execute(text(_STATE_BOUNDS_SQL), {"versions": versions}).mappings().one_or_none()
        bounds = (
            (
                float(bounds_row["min_lon"]),
                float(bounds_row["min_lat"]),
                float(bounds_row["max_lon"]),
                float(bounds_row["max_lat"]),
            )
            if bounds_row is not None and bounds_row["min_lon"] is not None
            else None
        )
        coverage = tuple(
            PogCoverageArea(
                act_id=str(item["act_identifier"]),
                teryt=item["teryt"],
                legal_status=canonical_legal_status(item["legal_status"]),
                bounds=(
                    (
                        float(item["min_lon"]),
                        float(item["min_lat"]),
                        float(item["max_lon"]),
                        float(item["max_lat"]),
                    )
                    if item["min_lon"] is not None
                    else None
                ),
                has_boundary=bool(item["has_boundary"]),
                is_complete=bool(item["is_complete"]) if item["is_complete"] is not None else None,
                incomplete_reasons=tuple(str(r) for r in (item["incomplete_reasons"] or [])),
            )
            for item in self._session.execute(text(_STATE_COVERAGE_SQL), {"versions": versions}).mappings()
        )
        with _ready_lock:
            unverified = [version for version in versions if version not in ready_map_versions]
        missing_ids: set[int] = set()
        if unverified:
            missing_ids = {
                int(value)
                for value in self._session.execute(
                    text(_MISSING_MAP_ROWS_SQL),
                    {"versions": unverified, "feature_types": list(FEATURE_TYPE_LAYERS)},
                ).scalars()
            }
            with _ready_lock:
                ready_map_versions.update(version for version in unverified if version not in missing_ids)
        missing = len(missing_ids)
        meta = _StateMeta(len(versions), bounds, statuses, coverage)
        if missing == 0:
            state_meta_cache.put(state_id, meta)
        return self._info(row, meta, missing=missing)

    @staticmethod
    def _info(row: RowMapping, meta: _StateMeta, *, missing: int) -> PogStateInfo:
        return PogStateInfo(
            state_id=int(row["id"]),
            source_id="pog_app",
            kind=str(row["kind"]),
            created_at=row["created_at"],
            is_current=bool(row["is_current"]),
            act_count=meta.act_count,
            bounds=meta.bounds,
            acts_by_legal_status=meta.acts_by_legal_status,
            coverage_areas=meta.coverage_areas,
            versions_without_map_rows=missing,
        )

    def encode_tile(self, request: PogTileRequest, *, max_features: int) -> EncodedTile:
        self._limit_statement_time()
        versions = list(self._versions(request.state_id))
        sql, names = _tile_sql(request.z)
        params: dict[str, Any] = {
            "z": request.z,
            "x": request.x,
            "y": request.y,
            "margin": MVT_BUFFER / MVT_EXTENT,
            "versions": versions,
            "statuses": list(request.legal_statuses) if request.legal_statuses else None,
            "with_text": request.z >= TEXT_ATTRIBUTES_MIN_ZOOM,
            "limit": max_features + 1,
        }
        row = self._session.execute(text(sql), params).mappings().one()
        counts = {name: int(row[f"n_{name}"]) for name in names}
        return EncodedTile(content=bytes(row["tile"] or b""), counts=counts)


class InMemoryPogTileCache(PogTileCache):
    """Wątkowo bezpieczny cache LRU ograniczony sumą bajtów kafli."""

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max(0, max_bytes)
        self._items: OrderedDict[str, PogTile] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def get(self, key: str) -> PogTile | None:
        with self._lock:
            tile = self._items.get(key)
            if tile is not None:
                self._items.move_to_end(key)
            return tile

    def put(self, key: str, tile: PogTile) -> None:
        size = len(tile.content)
        if size > self._max_bytes:
            return
        with self._lock:
            previous = self._items.pop(key, None)
            if previous is not None:
                self._size -= len(previous.content)
            self._items[key] = tile
            self._size += size
            while self._size > self._max_bytes and self._items:
                _, evicted = self._items.popitem(last=False)
                self._size -= len(evicted.content)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._size = 0

    def __len__(self) -> int:
        return len(self._items)
