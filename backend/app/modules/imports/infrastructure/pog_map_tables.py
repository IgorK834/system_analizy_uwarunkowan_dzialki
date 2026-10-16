"""Tabele mapy POG (``pog_map_*``): kopie do wyświetlania, wypełniane przy publikacji wersji aktu (ADR-026).

Tabele zawierają wyłącznie to, co potrzebne do narysowania kafla: geometrię w EPSG:3857 w stopniach uproszczenia
zależnych od zoomu i gotowe atrybuty kafla. Atrybuty wylicza ta sama czysta funkcja, której używa analiza działki
(``planning.domain.pog_tiles.tile_feature_properties`` → ``release_feature_attributes``), więc kliknięta cecha mapy ma
z konstrukcji te same wartości co wynik analizy. Wiersze powstają raz dla wersji aktu i nie są zmieniane — o tym,
które wersje są widoczne, decyduje rejestr (``pog_registry_entries``).

Uproszczenie (``ST_SimplifyPreserveTopology``, tolerancja w metrach EPSG:3857; piksel kafla 512 px: z11 ≈ 38 m,
z13 ≈ 9,6 m, z15 ≈ 2,4 m):

* strefy i OUZ/OZS/OSDIS: ``geom_low`` (z ≤ 12) tolerancja 12 m, ``geom_mid`` (z 13–14) 2 m, ``geom_full`` (z ≥ 15);
* granica aktu: ``geom_overview`` (z ≤ 8) 400 m, ``geom_low`` (z 9–12) 40 m, ``geom_full`` (z ≥ 13).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.modules.planning.domain.pog_features import RAW_ATTRIBUTE_KEYS
from app.modules.planning.domain.pog_tiles import (
    FEATURE_TYPE_LAYERS,
    PogTileCandidate,
    tile_feature_properties,
)
from app.shared.planning_status import (
    LEGAL_STATUS_ALIASES,
    LEGAL_STATUS_VALUES,
)

MAP_TABLES_VERSION: Final[str] = "pog-map-tables/1"
ZONE_TOLERANCES_M: Final[dict[str, float]] = {"geom_low": 12.0, "geom_mid": 2.0}
ACT_TOLERANCES_M: Final[dict[str, float]] = {"geom_overview": 400.0, "geom_low": 40.0}
INSERT_CHUNK: Final[int] = 1500


@dataclass(frozen=True)
class MapTablesResult:
    acts: int = 0
    zones: int = 0
    overlays: int = 0
    acts_without_boundary: int = 0


def canonical_status_sql(column: str) -> str:
    """SQL-owe lustro ``canonical_legal_status`` generowane z tych samych stałych."""
    values = ", ".join(f"'{value}'" for value in LEGAL_STATUS_VALUES)
    aliases = " ".join(
        f"WHEN {column} = '{alias}' THEN '{target}'" for alias, target in LEGAL_STATUS_ALIASES.items()
    )
    return f"CASE WHEN {column} IN ({values}) THEN {column} {aliases} ELSE 'unknown' END"


_DELETE_SQL: Final[tuple[str, ...]] = (
    "DELETE FROM pog_map_acts WHERE planning_act_version_id = ANY(CAST(:ids AS int[]))",
    "DELETE FROM pog_map_zones WHERE planning_act_version_id = ANY(CAST(:ids AS int[]))",
    "DELETE FROM pog_map_overlays WHERE planning_act_version_id = ANY(CAST(:ids AS int[]))",
)

_ACTS_SQL: Final[str] = f"""
    INSERT INTO pog_map_acts (
        planning_act_version_id, act_id, teryt, legal_status, name, act_version, resolution_number,
        resolution_date, legal_valid_from, legal_valid_to, version_started_on, effective_date,
        official_journal, document_link, geom_overview, geom_low, geom_full)
    SELECT pav.id, pa.act_identifier, pa.teryt, {canonical_status_sql("pav.legal_status")},
           left(pav.name, 300), pav.object_version_id, doc.identification_number, doc.document_date,
           pav.legal_valid_from, pav.legal_valid_to, CAST(pav.version_started_at AS date), doc.effective_date,
           left(doc.journal, 200), doc.link,
           ST_Multi(ST_SimplifyPreserveTopology(g.geom, :tol_overview)),
           ST_Multi(ST_SimplifyPreserveTopology(g.geom, :tol_low)),
           g.geom
    FROM planning_act_versions pav
    JOIN planning_acts pa ON pa.id = pav.planning_act_id
    CROSS JOIN LATERAL (
        SELECT ST_Multi(ST_CollectionExtract(ST_Union(ST_Transform(pb.geometry, 3857)), 3)) AS geom
        FROM plan_boundaries pb WHERE pb.planning_act_version_id = pav.id
    ) g
    LEFT JOIN LATERAL (
        SELECT d.identification_number, d.document_date, d.effective_date, d.link,
               d.raw_attributes -> 'dziennikUrzedowy' ->> 'label' AS journal
        FROM pog_formal_documents d
        WHERE d.planning_act_version_id = pav.id AND d.relation = 'uchwala'
          AND d.resolution_status = 'resolved'
        ORDER BY d.effective_date DESC NULLS LAST, d.id
        LIMIT 1
    ) doc ON true
    WHERE pav.id = ANY(CAST(:ids AS int[])) AND g.geom IS NOT NULL AND NOT ST_IsEmpty(g.geom)
"""

_ACTS_WITHOUT_BOUNDARY_SQL: Final[str] = """
    SELECT count(*) FROM planning_act_versions pav
    WHERE pav.id = ANY(CAST(:ids AS int[]))
      AND NOT EXISTS (SELECT 1 FROM plan_boundaries pb WHERE pb.planning_act_version_id = pav.id)
"""

_FEATURES_SQL: Final[str] = """
    SELECT pf.id AS pk, pf.feature_type, pf.feature_identifier, pf.feature_version,
           pf.symbol, pf.label, pf.parameters, pf.primary_profiles, pf.additional_profiles,
           (
               SELECT jsonb_object_agg(attr.key, attr.value)
               FROM jsonb_each(
                   CASE WHEN jsonb_typeof(pf.raw_attributes) = 'object'
                        THEN pf.raw_attributes ELSE '{}'::jsonb END
               ) AS attr
               WHERE lower(attr.key) = ANY(CAST(:raw_keys AS text[]))
           ) AS raw_attributes,
           pa.act_identifier, pa.teryt, pav.legal_status
    FROM planning_features pf
    JOIN planning_act_versions pav ON pav.id = pf.planning_act_version_id
    JOIN planning_acts pa ON pa.id = pav.planning_act_id
    WHERE pav.id = ANY(CAST(:ids AS int[]))
      AND pf.feature_type = ANY(CAST(:feature_types AS text[]))
    ORDER BY pf.id
"""

_ZONES_INSERT_SQL: Final[str] = """
    INSERT INTO pog_map_zones (
        feature_id, planning_act_version_id, legal_status, feature_ref, feature_version, symbol, designation,
        label, zone_code, max_overground_floor_area_ratio, max_building_height_m, max_building_coverage_pct,
        min_biologically_active_pct, parameters_informational, primary_profiles, additional_profiles, teryt,
        act_id, geom_low, geom_mid, geom_full)
    SELECT r.pk, pf.planning_act_version_id, r.legal_status, r.feature_id, r.feature_version, r.symbol,
           r.designation, r.label, r.zone_code, r.max_overground_floor_area_ratio, r.max_building_height_m,
           r.max_building_coverage_pct, r.min_biologically_active_pct, r.parameters_informational,
           r.primary_profiles, r.additional_profiles, r.teryt, r.act_id,
           ST_SimplifyPreserveTopology(g.geom, :tol_low), ST_SimplifyPreserveTopology(g.geom, :tol_mid), g.geom
    FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS r(
        pk bigint, legal_status text, feature_id text, feature_version text, symbol text, designation text,
        label text, zone_code text, max_overground_floor_area_ratio double precision,
        max_building_height_m double precision, max_building_coverage_pct double precision,
        min_biologically_active_pct double precision, parameters_informational boolean,
        primary_profiles text, additional_profiles text, teryt text, act_id text)
    JOIN planning_features pf ON pf.id = r.pk
    CROSS JOIN LATERAL (SELECT ST_Transform(pf.geometry, 3857) AS geom) g
"""

_OVERLAYS_INSERT_SQL: Final[str] = """
    INSERT INTO pog_map_overlays (
        feature_id, planning_act_version_id, layer, legal_status, feature_ref, feature_version, symbol, label,
        teryt, act_id, geom_low, geom_mid, geom_full)
    SELECT r.pk, pf.planning_act_version_id, r.layer, r.legal_status, r.feature_id, r.feature_version, r.symbol,
           r.label, r.teryt, r.act_id,
           ST_SimplifyPreserveTopology(g.geom, :tol_low), ST_SimplifyPreserveTopology(g.geom, :tol_mid), g.geom
    FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS r(
        pk bigint, layer text, legal_status text, feature_id text, feature_version text, symbol text,
        label text, teryt text, act_id text)
    JOIN planning_features pf ON pf.id = r.pk
    CROSS JOIN LATERAL (SELECT ST_Transform(pf.geometry, 3857) AS geom) g
"""

_ZONE_FIELDS: Final[tuple[str, ...]] = (
    "pk", "legal_status", "feature_id", "feature_version", "symbol", "designation", "label", "zone_code",
    "max_overground_floor_area_ratio", "max_building_height_m", "max_building_coverage_pct",
    "min_biologically_active_pct", "parameters_informational", "primary_profiles", "additional_profiles",
    "teryt", "act_id",
)
_OVERLAY_FIELDS: Final[tuple[str, ...]] = (
    "pk", "layer", "legal_status", "feature_id", "feature_version", "symbol", "label", "teryt", "act_id",
)


def _candidate(row: Any) -> PogTileCandidate:
    return PogTileCandidate(
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


def populate_pog_map_tables(session: Session, version_ids: Sequence[int]) -> MapTablesResult:
    """Zastępuje wiersze map dla podanych wersji aktów (idempotentnie, w transakcji wołającego)."""
    ids = sorted({int(item) for item in version_ids})
    if not ids:
        return MapTablesResult()
    params: dict[str, Any] = {"ids": ids}
    for statement in _DELETE_SQL:
        session.execute(text(statement), params)
    without_boundary = int(session.execute(text(_ACTS_WITHOUT_BOUNDARY_SQL), params).scalar_one())
    acts = cast(
        CursorResult[Any],
        session.execute(
            text(_ACTS_SQL),
            {**params, "tol_overview": ACT_TOLERANCES_M["geom_overview"], "tol_low": ACT_TOLERANCES_M["geom_low"]},
        ),
    ).rowcount
    rows = session.execute(
        text(_FEATURES_SQL),
        {**params, "raw_keys": sorted(RAW_ATTRIBUTE_KEYS), "feature_types": list(FEATURE_TYPE_LAYERS)},
    ).mappings().all()
    zone_rows: list[dict[str, Any]] = []
    overlay_rows: list[dict[str, Any]] = []
    for row in rows:
        properties = tile_feature_properties(_candidate(row))
        if properties["layer"] == "zones":
            zone_rows.append({name: properties.get(name) for name in _ZONE_FIELDS})
        else:
            overlay_rows.append({name: properties.get(name) for name in _OVERLAY_FIELDS})
    tolerances = {"tol_low": ZONE_TOLERANCES_M["geom_low"], "tol_mid": ZONE_TOLERANCES_M["geom_mid"]}
    for start in range(0, len(zone_rows), INSERT_CHUNK):
        chunk = zone_rows[start : start + INSERT_CHUNK]
        session.execute(text(_ZONES_INSERT_SQL), {"rows": json.dumps(chunk), **tolerances})
    for start in range(0, len(overlay_rows), INSERT_CHUNK):
        chunk = overlay_rows[start : start + INSERT_CHUNK]
        session.execute(text(_OVERLAYS_INSERT_SQL), {"rows": json.dumps(chunk), **tolerances})
    return MapTablesResult(
        acts=int(acts or 0),
        zones=len(zone_rows),
        overlays=len(overlay_rows),
        acts_without_boundary=without_boundary,
    )


_MISSING_SQL: Final[str] = """
    SELECT DISTINCT e.planning_act_version_id
    FROM pog_registry_entries e
    WHERE e.state_to_id IS NULL
      AND (
        NOT EXISTS (SELECT 1 FROM pog_map_acts a WHERE a.planning_act_version_id = e.planning_act_version_id)
        AND EXISTS (SELECT 1 FROM plan_boundaries pb WHERE pb.planning_act_version_id = e.planning_act_version_id)
        OR EXISTS (
            SELECT 1 FROM planning_features pf
            WHERE pf.planning_act_version_id = e.planning_act_version_id
              AND pf.feature_type = ANY(CAST(:feature_types AS text[]))
              AND NOT EXISTS (SELECT 1 FROM pog_map_zones z WHERE z.feature_id = pf.id)
              AND NOT EXISTS (SELECT 1 FROM pog_map_overlays o WHERE o.feature_id = pf.id))
      )
    ORDER BY 1
"""


def versions_missing_map_rows(session: Session) -> list[int]:
    """Aktywne wersje, którym brakuje wierszy mapy (np. po migracji 037, przed ``pog-map-rebuild``)."""
    return [
        int(row[0])
        for row in session.execute(text(_MISSING_SQL), {"feature_types": list(FEATURE_TYPE_LAYERS)})
    ]


def rebuild_missing_map_tables(session: Session, *, batch: int = 20) -> MapTablesResult:
    """Uzupełnia wiersze mapy dla aktywnych wersji, którym ich brakuje (porcjami po ``batch`` wersji)."""
    missing = versions_missing_map_rows(session)
    total = MapTablesResult()
    for start in range(0, len(missing), batch):
        part = populate_pog_map_tables(session, missing[start : start + batch])
        total = MapTablesResult(
            acts=total.acts + part.acts,
            zones=total.zones + part.zones,
            overlays=total.overlays + part.overlays,
            acts_without_boundary=total.acts_without_boundary + part.acts_without_boundary,
        )
        session.commit()
    return total
