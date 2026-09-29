"""Adapter PostGIS kafli wektorowych POG (BK-401).

Potok SQL: ``ST_TileEnvelope`` (EPSG:3857) → selekcja indeksem GiST na
kanonicznych geometriach EPSG:2180 → ``ST_Transform`` do 3857 →
``ST_AsMVTGeom(extent=4096, buffer=64)`` → ``ST_AsMVT`` osobno dla każdej z pięciu
warstw logicznych, sklejonych w jeden kafel (konkatenacja warstw protobuf).

Selekcja ma dwa kroki: najpierw pobierane są kandydaci z wąskim podzbiorem
atrybutów, a atrybuty kafla wylicza ta sama czysta funkcja domenowa, której
używa analiza działki. Drugie zapytanie koduje geometrie z identyfikatorami
cech i gotowymi atrybutami. Surowe ``raw_attributes`` nie trafiają do kafla.
"""

from __future__ import annotations

import json
import math
import threading
from collections import OrderedDict
from collections.abc import Sequence
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.planning.application.pog_tiles import PogTileCache, PogTileRepository
from app.modules.planning.domain.pog_features import RAW_ATTRIBUTE_KEYS
from app.shared.planning_status import LEGAL_STATUS_ALIASES, LEGAL_STATUS_VALUES
from app.modules.planning.domain.pog_tiles import (
    FEATURE_TYPE_LAYERS,
    MVT_BUFFER,
    MVT_EXTENT,
    POG_TILE_LAYERS,
    PogReleaseInfo,
    PogTile,
    PogTileCandidate,
    PogTileRequest,
)

WEB_MERCATOR_WORLD_SIZE_M: Final[float] = 2 * math.pi * 6378137.0
# Krawędź koperty 3857 jest dzielona przed transformacją do 2180, aby łuk
# odwzorowania nie „ścinał” rogów kafla przy selekcji.
ENVELOPE_SEGMENTS_PER_EDGE: Final[int] = 16

_TILE_CTE: Final[str] = """
    tile AS (
        SELECT
            ST_TileEnvelope(:z, :x, :y) AS env3857,
            ST_Transform(
                ST_Segmentize(
                    ST_TileEnvelope(:z, :x, :y, margin => :margin), :segment
                ),
                2180
            ) AS env2180
    )
"""

_STATUS_FILTER: Final[str] = (
    "(CAST(:statuses AS text[]) IS NULL OR pav.legal_status = ANY(CAST(:statuses AS text[])))"
)

_CANDIDATES_SQL: Final[str] = f"""
    WITH {_TILE_CTE}
    SELECT pf.id AS pk, pf.feature_type, pf.feature_identifier, pf.feature_version,
           pf.symbol, pf.label, pf.parameters, pf.primary_profiles,
           pf.additional_profiles,
           (
               SELECT jsonb_object_agg(attr.key, attr.value)
               FROM jsonb_each(
                   CASE WHEN jsonb_typeof(pf.raw_attributes) = 'object'
                        THEN pf.raw_attributes ELSE '{{}}'::jsonb END
               ) AS attr
               WHERE lower(attr.key) = ANY(CAST(:raw_keys AS text[]))
           ) AS raw_attributes,
           pa.act_identifier, pa.teryt, pav.legal_status
    FROM planning_features pf
    JOIN planning_act_versions pav ON pav.id = pf.planning_act_version_id
    JOIN planning_acts pa ON pa.id = pav.planning_act_id
    CROSS JOIN tile
    WHERE pa.kind = 'pog'
      AND pav.data_release_id = :release_id
      AND pf.feature_type = ANY(CAST(:feature_types AS text[]))
      AND {_STATUS_FILTER}
      AND pf.geometry && tile.env2180
      AND ST_Intersects(pf.geometry, tile.env2180)
    ORDER BY pf.id
    LIMIT :limit
"""

# Kolumny (atrybuty) każdej warstwy. ``NULL`` nie jest kodowany w MVT, więc
# brak wartości parametru pozostaje brakiem, a nie zerem.
_COMMON_COLUMNS: Final[tuple[str, ...]] = (
    "feature_id", "feature_version", "symbol", "label", "legal_status", "teryt",
    "act_id", "data_release_id",
)
_ZONE_COLUMNS: Final[tuple[str, ...]] = (
    *_COMMON_COLUMNS,
    "zone_code",
    "max_overground_floor_area_ratio",
    "max_building_height_m",
    "max_building_coverage_pct",
    "min_biologically_active_pct",
    "parameters_informational",
    "primary_profiles",
    "additional_profiles",
)
_PROPS_RECORD: Final[str] = """
    pk bigint, layer text, feature_id text, feature_version text, symbol text,
    label text, legal_status text, teryt text, act_id text, data_release_id bigint,
    zone_code text, max_overground_floor_area_ratio double precision,
    max_building_height_m double precision, max_building_coverage_pct double precision,
    min_biologically_active_pct double precision, parameters_informational boolean,
    primary_profiles text, additional_profiles text
"""


def _canonical_legal_status_sql(column: str) -> str:
    """SQL-owe lustro ``canonical_legal_status`` generowane z tych samych stałych."""
    values = ", ".join(f"'{value}'" for value in LEGAL_STATUS_VALUES)
    aliases = " ".join(
        f"WHEN {column} = '{alias}' THEN '{target}'"
        for alias, target in LEGAL_STATUS_ALIASES.items()
    )
    return f"CASE WHEN {column} IN ({values}) THEN {column} {aliases} ELSE 'unknown' END"


def _layer_sql(layer: str) -> str:
    if layer == "act_boundary":
        columns = "pk, geom, act_id, label, legal_status, teryt, data_release_id, act_version"
        source = "boundaries"
        where = "geom IS NOT NULL"
    else:
        selected = _ZONE_COLUMNS if layer == "zones" else _COMMON_COLUMNS
        columns = ", ".join(("pk", "geom", *selected))
        source = "feats"
        where = f"layer = '{layer}' AND geom IS NOT NULL"
    return (
        f"COALESCE((SELECT ST_AsMVT(q, '{layer}', {MVT_EXTENT}, 'geom', 'pk') "
        f"FROM (SELECT {columns} FROM {source} WHERE {where} ORDER BY pk) AS q), "
        "'\\x'::bytea)"
    )


_ENCODE_SQL: Final[str] = f"""
    WITH {_TILE_CTE},
    props AS (
        SELECT * FROM jsonb_to_recordset(CAST(:features AS jsonb)) AS p({_PROPS_RECORD})
    ),
    feats AS (
        SELECT props.*,
               ST_AsMVTGeom(
                   ST_Transform(ST_ClipByBox2D(pf.geometry, ST_Envelope(tile.env2180)), 3857),
                   tile.env3857, {MVT_EXTENT}, {MVT_BUFFER}, true
               ) AS geom
        FROM props
        JOIN planning_features pf ON pf.id = props.pk
        CROSS JOIN tile
    ),
    boundaries AS (
        SELECT pb.id AS pk, pa.act_identifier AS act_id,
               left(pav.name, 200) AS label,
               {_canonical_legal_status_sql("pav.legal_status")} AS legal_status,
               pa.teryt, CAST(:release_id AS bigint) AS data_release_id,
               pav.object_version_id AS act_version,
               ST_AsMVTGeom(
                   ST_Transform(ST_ClipByBox2D(pb.geometry, ST_Envelope(tile.env2180)), 3857),
                   tile.env3857, {MVT_EXTENT}, {MVT_BUFFER}, true
               ) AS geom
        FROM plan_boundaries pb
        JOIN planning_act_versions pav ON pav.id = pb.planning_act_version_id
        JOIN planning_acts pa ON pa.id = pav.planning_act_id
        CROSS JOIN tile
        WHERE pa.kind = 'pog'
          AND pav.data_release_id = :release_id
          AND {_STATUS_FILTER}
          AND pb.geometry && tile.env2180
          AND ST_Intersects(pb.geometry, tile.env2180)
        ORDER BY pb.id
        LIMIT :boundary_limit
    )
    SELECT {" || ".join(_layer_sql(layer) for layer in POG_TILE_LAYERS)} AS tile
"""

_RELEASE_SQL: Final[str] = """
    SELECT dr.id, dr.version_label, dr.published_at, dr.is_active, ds.source_id,
           (SELECT sa.content_hash
              FROM planning_act_versions pav
              JOIN source_artifacts sa ON sa.id = pav.source_artifact_id
             WHERE pav.data_release_id = dr.id
             ORDER BY pav.id LIMIT 1) AS artifact_sha256
    FROM data_releases dr
    JOIN data_sources ds ON ds.id = dr.data_source_id
    WHERE dr.id = :release_id
      AND EXISTS (
          SELECT 1 FROM planning_act_versions pav
          JOIN planning_acts pa ON pa.id = pav.planning_act_id
          WHERE pav.data_release_id = dr.id AND pa.kind = 'pog'
      )
"""

_RELEASE_STATUS_SQL: Final[str] = """
    SELECT COALESCE(pav.legal_status, 'unknown') AS legal_status, count(*) AS acts
    FROM planning_act_versions pav
    JOIN planning_acts pa ON pa.id = pav.planning_act_id
    WHERE pav.data_release_id = :release_id AND pa.kind = 'pog'
    GROUP BY 1
"""

_RELEASE_BOUNDS_SQL: Final[str] = """
    WITH geoms AS (
        SELECT pf.geometry AS geom
        FROM planning_features pf
        JOIN planning_act_versions pav ON pav.id = pf.planning_act_version_id
        WHERE pav.data_release_id = :release_id
        UNION ALL
        SELECT pb.geometry
        FROM plan_boundaries pb
        JOIN planning_act_versions pav ON pav.id = pb.planning_act_version_id
        WHERE pav.data_release_id = :release_id
    ), extent AS (
        SELECT ST_Transform(ST_SetSRID(ST_Extent(geom)::geometry, 2180), 4326) AS g
        FROM geoms
    )
    SELECT ST_XMin(g) AS min_lon, ST_YMin(g) AS min_lat,
           ST_XMax(g) AS max_lon, ST_YMax(g) AS max_lat
    FROM extent WHERE g IS NOT NULL
"""

_ACTIVE_RELEASE_SQL: Final[str] = """
    SELECT dr.id
    FROM data_releases dr
    JOIN data_sources ds ON ds.id = dr.data_source_id
    WHERE ds.source_id = :source_id AND dr.is_active
    LIMIT 1
"""


def _tile_params(request: PogTileRequest) -> dict[str, Any]:
    tile_size = WEB_MERCATOR_WORLD_SIZE_M / (1 << request.z)
    return {
        "z": request.z,
        "x": request.x,
        "y": request.y,
        "margin": MVT_BUFFER / MVT_EXTENT,
        "segment": tile_size / ENVELOPE_SEGMENTS_PER_EDGE,
        "release_id": request.release_id,
        "statuses": list(request.legal_statuses) if request.legal_statuses else None,
    }


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

    def release_info(self, release_id: int) -> PogReleaseInfo | None:
        row = self._session.execute(
            text(_RELEASE_SQL), {"release_id": release_id}
        ).mappings().one_or_none()
        if row is None:
            return None
        statuses = {
            str(item["legal_status"]): int(item["acts"])
            for item in self._session.execute(
                text(_RELEASE_STATUS_SQL), {"release_id": release_id}
            ).mappings()
        }
        bounds_row = self._session.execute(
            text(_RELEASE_BOUNDS_SQL), {"release_id": release_id}
        ).mappings().one_or_none()
        bounds = (
            (
                float(bounds_row["min_lon"]),
                float(bounds_row["min_lat"]),
                float(bounds_row["max_lon"]),
                float(bounds_row["max_lat"]),
            )
            if bounds_row is not None
            else None
        )
        return PogReleaseInfo(
            release_id=int(row["id"]),
            source_id=str(row["source_id"]),
            version_label=str(row["version_label"]),
            published_at=row["published_at"],
            is_active=bool(row["is_active"]),
            artifact_sha256=row["artifact_sha256"],
            bounds=bounds,
            acts_by_legal_status=statuses,
        )

    def active_release_id(self, source_id: str) -> int | None:
        value = self._session.execute(
            text(_ACTIVE_RELEASE_SQL), {"source_id": source_id}
        ).scalar_one_or_none()
        return int(value) if value is not None else None

    def tile_candidates(
        self, request: PogTileRequest, *, limit: int
    ) -> list[PogTileCandidate]:
        self._limit_statement_time()
        rows = self._session.execute(
            text(_CANDIDATES_SQL),
            {
                **_tile_params(request),
                "raw_keys": sorted(RAW_ATTRIBUTE_KEYS),
                "feature_types": list(FEATURE_TYPE_LAYERS),
                "limit": limit,
            },
        ).mappings()
        return [
            PogTileCandidate(
                pk=int(row["pk"]),
                feature_type=str(row["feature_type"]),
                feature_identifier=row["feature_identifier"],
                feature_version=row["feature_version"],
                symbol=row["symbol"],
                label=row["label"],
                parameters=row["parameters"],
                primary_profiles=row["primary_profiles"],
                additional_profiles=row["additional_profiles"],
                raw_attributes=row["raw_attributes"],
                act_identifier=str(row["act_identifier"]),
                teryt=row["teryt"],
                legal_status=row["legal_status"],
            )
            for row in rows
        ]

    def encode_tile(
        self,
        request: PogTileRequest,
        features: Sequence[dict[str, object]],
        *,
        boundary_limit: int,
    ) -> bytes:
        self._limit_statement_time()
        content = self._session.execute(
            text(_ENCODE_SQL),
            {
                **_tile_params(request),
                "features": json.dumps(list(features)),
                "boundary_limit": boundary_limit,
            },
        ).scalar_one()
        return bytes(content or b"")


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
