"""Obliczanie i zapis agregatów powierzchniowych stref POG w PostGIS (BK-405, ADR-026).

Wywoływane przez ``SqlAlchemyImportRepository.publish_pog`` w tej samej
transakcji co zmiana rejestru: agregaty aktu (zależą tylko od wersji) powstają
PRZED aktywacją wersji, agregaty gminy — po niej, dla stanu krajowego, który ta
zmiana tworzy. Endpoint HTTP czyta wyłącznie gotowe liczby (bez
``ST_Intersection``/``ST_Area``).

Wszystkie pola liczone są na kanonicznych geometriach EPSG:2180. Typ strefy
wyznacza ta sama czysta funkcja co w analizie i kaflach MVT
(``planning.domain.pog_features``), więc agregat, mapa i wynik analizy nie
mogą się rozjechać co do kodu strefy.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.models.versioned import PogAreaSummary, PogAreaSummaryZone
from app.modules.imports.domain.pog_aggregates import (
    AGGREGATE_METHOD_VERSION,
    ActForAggregation,
    AreaAggregate,
    AreaMeasurement,
    ZoneAreaMeasurement,
    aggregate_areas,
    aggregate_warning,
    edition_for_status,
    priority_ordered,
)
from app.shared.pog_registry import active_versions_join
from app.modules.planning.domain.pog_features import (
    RAW_ATTRIBUTE_KEYS,
    pog_feature_presentation,
    release_feature_attributes,
)

_ZONE_FEATURES_SQL: Final[str] = """
    SELECT pf.id AS pk, pf.planning_act_version_id AS version_id,
           pf.feature_identifier, pf.feature_version, pf.symbol, pf.label,
           pf.parameters, pf.primary_profiles, pf.additional_profiles,
           (
               SELECT jsonb_object_agg(attr.key, attr.value)
               FROM jsonb_each(
                   CASE WHEN jsonb_typeof(pf.raw_attributes) = 'object'
                        THEN pf.raw_attributes ELSE '{}'::jsonb END
               ) AS attr
               WHERE lower(attr.key) = ANY(CAST(:raw_keys AS text[]))
           ) AS raw_attributes
    FROM planning_features pf
    JOIN planning_act_versions pav ON pav.id = pf.planning_act_version_id
    JOIN planning_acts pa ON pa.id = pav.planning_act_id
    WHERE pav.id = ANY(CAST(:ids AS int[]))
      AND pa.kind = 'pog'
      AND pf.feature_type = 'planning_zone'
    ORDER BY pf.id
"""

_ACT_VERSIONS_SQL: Final[str] = """
    SELECT pav.id AS version_id, pa.act_identifier, pa.teryt, pav.legal_status,
           pav.object_version_id, pav.version_started_at, pav.legal_valid_from
    FROM planning_act_versions pav
    JOIN planning_acts pa ON pa.id = pav.planning_act_id
    WHERE pav.id = ANY(CAST(:ids AS int[])) AND pa.kind = 'pog'
    ORDER BY pav.id
"""

_ACTIVE_ACT_VERSIONS_SQL: Final[str] = f"""
    SELECT pav.id AS version_id, pa.act_identifier, pa.teryt, pav.legal_status,
           pav.object_version_id, pav.version_started_at, pav.legal_valid_from
    FROM planning_act_versions pav
    JOIN planning_acts pa ON pa.id = pav.planning_act_id
    {active_versions_join()}
    WHERE pa.kind = 'pog' AND pa.teryt = ANY(CAST(:teryts AS text[]))
    ORDER BY pav.id
"""

# Wspólne CTE pomiaru grupy aktów. ``rank`` = 1 to najwyższy priorytet; strefy
# aktu o niższym priorytecie tracą obszar pokryty granicami aktów wyższych,
# dzięki czemu nakładające się akty gminy nie są liczone podwójnie. Dla
# pojedynczego aktu (rank 1) maska jest pusta.
_GROUP_CTE: Final[str] = """
    codes AS (
        SELECT * FROM jsonb_to_recordset(CAST(:codes AS jsonb))
            AS c(pk bigint, zone_code text)
    ),
    acts AS (
        SELECT * FROM jsonb_to_recordset(CAST(:acts AS jsonb))
            AS a(version_id bigint, rank integer)
    ),
    boundaries AS (
        SELECT a.version_id, a.rank, ST_Union(pb.geometry) AS geom
        FROM acts a
        JOIN plan_boundaries pb ON pb.planning_act_version_id = a.version_id
        GROUP BY a.version_id, a.rank
    ),
    zones AS (
        SELECT c.zone_code, pf.geometry AS raw_geom,
               CASE WHEN b.geom IS NULL THEN pf.geometry
                    ELSE ST_CollectionExtract(ST_Intersection(pf.geometry, b.geom), 3)
               END AS clipped,
               (SELECT ST_Union(b2.geom) FROM boundaries b2 WHERE b2.rank < a.rank) AS mask
        FROM acts a
        JOIN planning_features pf ON pf.planning_act_version_id = a.version_id
        JOIN codes c ON c.pk = pf.id
        LEFT JOIN boundaries b ON b.version_id = a.version_id
    ),
    effective AS (
        SELECT zone_code, raw_geom, clipped,
               CASE WHEN mask IS NULL THEN clipped
                    ELSE ST_CollectionExtract(ST_Difference(clipped, mask), 3)
               END AS geom
        FROM zones
    )
"""

_ZONE_MEASURE_SQL: Final[str] = f"""
    WITH {_GROUP_CTE}
    SELECT zone_code, GROUPING(zone_code) = 1 AS is_total,
           COALESCE(ST_Area(ST_Union(geom)), 0) AS area_sqm,
           count(*) FILTER (WHERE ST_Area(geom) > 0) AS zone_count,
           COALESCE(SUM(ST_Area(geom)), 0) AS sum_sqm,
           COALESCE(SUM(ST_Area(raw_geom)), 0) AS raw_sqm,
           COALESCE(SUM(ST_Area(clipped)), 0) AS clipped_sqm
    FROM effective
    GROUP BY GROUPING SETS ((zone_code), ())
"""

_BOUNDARY_MEASURE_SQL: Final[str] = f"""
    WITH {_GROUP_CTE}
    SELECT count(*) AS acts_with_boundary,
           ST_Area(ST_Union(geom)) AS union_sqm,
           SUM(ST_Area(geom)) AS sum_sqm
    FROM boundaries
"""


@dataclass(frozen=True)
class StoredAggregate:
    """Zapisany agregat (dla statystyk i ostrzeżeń importu)."""

    scope: str
    scope_key: str
    aggregate: AreaAggregate


def _iso(value: object) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else ""


def _zone_codes(session: Session, version_ids: Sequence[int]) -> dict[int, list[dict[str, Any]]]:
    """Kody stref wg wersji aktu: ta sama funkcja co analiza i MVT."""
    rows = session.execute(
        text(_ZONE_FEATURES_SQL),
        {"ids": list(version_ids), "raw_keys": sorted(RAW_ATTRIBUTE_KEYS)},
    ).mappings()
    by_version: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        attributes = release_feature_attributes(
            row["raw_attributes"],
            feature_identifier=row["feature_identifier"],
            feature_version=row["feature_version"],
            symbol=row["symbol"],
            label=row["label"],
            primary_profiles=row["primary_profiles"],
            additional_profiles=row["additional_profiles"],
            parameters=row["parameters"],
        )
        by_version[int(row["version_id"])].append(
            {"pk": int(row["pk"]), "zone_code": pog_feature_presentation(attributes).zone_code}
        )
    return by_version


def _act_from_row(row: Any) -> ActForAggregation:
    return ActForAggregation(
        version_id=int(row["version_id"]),
        act_identifier=str(row["act_identifier"]),
        act_version=row["object_version_id"],
        teryt=row["teryt"],
        legal_status=row["legal_status"],
        priority_key=(
            _iso(row["version_started_at"]),
            _iso(row["legal_valid_from"]),
            int(row["version_id"]),
        ),
    )


def _acts(session: Session, version_ids: Sequence[int]) -> list[ActForAggregation]:
    return [
        _act_from_row(row)
        for row in session.execute(text(_ACT_VERSIONS_SQL), {"ids": list(version_ids)}).mappings()
    ]


def _active_acts(session: Session, state_id: int, teryts: Sequence[str]) -> list[ActForAggregation]:
    return [
        _act_from_row(row)
        for row in session.execute(
            text(_ACTIVE_ACT_VERSIONS_SQL), {"state_id": state_id, "teryts": list(teryts)}
        ).mappings()
    ]


def measure_group(
    session: Session,
    acts: Sequence[ActForAggregation],
    codes: Sequence[dict[str, Any]],
) -> AreaMeasurement:
    """Mierzy grupę aktów (w kolejności priorytetu) w EPSG:2180."""
    params = {
        "codes": json.dumps(list(codes)),
        "acts": json.dumps(
            [{"version_id": act.version_id, "rank": rank} for rank, act in enumerate(acts, 1)]
        ),
    }
    zones: list[ZoneAreaMeasurement] = []
    union_sqm = sum_sqm = outside_sqm = 0.0
    for row in session.execute(text(_ZONE_MEASURE_SQL), params).mappings():
        if row["is_total"]:
            union_sqm = float(row["area_sqm"])
            sum_sqm = float(row["sum_sqm"])
            outside_sqm = float(row["raw_sqm"]) - float(row["clipped_sqm"])
            continue
        if int(row["zone_count"]) == 0:
            continue
        zones.append(
            ZoneAreaMeasurement(
                zone_code=str(row["zone_code"]),
                area_sqm=float(row["area_sqm"]),
                zone_count=int(row["zone_count"]),
            )
        )
    boundary = session.execute(text(_BOUNDARY_MEASURE_SQL), params).mappings().one()
    with_boundary = int(boundary["acts_with_boundary"] or 0)
    # Mianownik tylko wtedy, gdy KAŻDY akt grupy ma granicę ze źródła — inaczej
    # udział byłby liczony do niepełnej powierzchni i zawyżony.
    denominator = (
        float(boundary["union_sqm"])
        if acts and with_boundary == len(acts) and boundary["union_sqm"]
        else None
    )
    deduplicated = (
        float(boundary["sum_sqm"] or 0.0) - float(boundary["union_sqm"] or 0.0)
        if len(acts) > 1
        else None
    )
    return AreaMeasurement(
        denominator_area_sqm=denominator,
        zones=tuple(zones),
        zones_union_area_sqm=union_sqm,
        zones_sum_area_sqm=sum_sqm,
        outside_area_sqm=outside_sqm,
        deduplicated_area_sqm=deduplicated,
    )


def _summary_row(
    release_id: int,
    aggregate: AreaAggregate,
    *,
    scope: str,
    acts: Sequence[ActForAggregation],
    computed_at: datetime,
    act: ActForAggregation | None = None,
    teryt: str | None = None,
    edition: str | None = None,
    registry_state_id: int | None = None,
) -> PogAreaSummary:
    return PogAreaSummary(
        data_release_id=release_id,
        registry_state_id=registry_state_id,
        scope=scope,
        planning_act_version_id=act.version_id if act else None,
        act_identifier=act.act_identifier if act else None,
        act_version=act.act_version if act else None,
        teryt=act.teryt if act else teryt,
        edition=edition,
        legal_status=act.legal_status if act else None,
        denominator_area_sqm=aggregate.denominator_area_sqm,
        denominator_source=(
            None
            if aggregate.denominator_area_sqm is None
            else ("act_boundary" if scope == "act" else "act_boundaries_union")
        ),
        zones_area_sqm=aggregate.zones_area_sqm,
        missing_area_sqm=aggregate.missing_area_sqm,
        overlap_area_sqm=aggregate.overlap_area_sqm,
        outside_area_sqm=aggregate.outside_area_sqm,
        deduplicated_area_sqm=aggregate.deduplicated_area_sqm,
        share_sum_pct=aggregate.share_sum_pct,
        zone_count=aggregate.zone_count,
        act_count=len(acts),
        is_complete=aggregate.is_complete,
        incomplete_reasons=list(aggregate.incomplete_reasons),
        act_identifiers=[item.act_identifier for item in acts],
        area_tolerance_sqm=aggregate.area_tolerance_sqm,
        share_tolerance_pct=aggregate.share_tolerance_pct,
        method_version=AGGREGATE_METHOD_VERSION,
        computed_at=computed_at,
    )


def _store(session: Session, summary: PogAreaSummary, aggregate: AreaAggregate) -> None:
    session.add(summary)
    session.flush()
    for index, zone in enumerate(aggregate.zones):
        session.add(
            PogAreaSummaryZone(
                summary_id=summary.id,
                zone_code=zone.zone_code,
                area_sqm=zone.area_sqm,
                area_sqkm=zone.area_sqkm,
                share_pct=zone.share_pct,
                zone_count=zone.zone_count,
                order_index=index,
            )
        )


def compute_act_summaries(
    session: Session,
    release_id: int,
    version_ids: Sequence[int],
    *,
    computed_at: datetime | None = None,
) -> tuple[StoredAggregate, ...]:
    """Agregaty aktu dla podanych wersji (deterministycznie; ponowne wywołanie zastępuje wiersze wersji).

    Agregat aktu zależy wyłącznie od zapisanej wersji aktu (granica i strefy), nie od stanu rejestru.
    """
    ids = sorted({int(item) for item in version_ids})
    if not ids:
        return ()
    now = computed_at or datetime.now(timezone.utc)
    session.execute(
        delete(PogAreaSummary).where(
            PogAreaSummary.scope == "act", PogAreaSummary.planning_act_version_id.in_(ids)
        )
    )
    codes = _zone_codes(session, ids)
    stored: list[StoredAggregate] = []
    for act in _acts(session, ids):
        aggregate = aggregate_areas(measure_group(session, [act], codes.get(act.version_id, [])))
        _store(
            session,
            _summary_row(release_id, aggregate, scope="act", acts=[act], computed_at=now, act=act),
            aggregate,
        )
        stored.append(StoredAggregate("act", act.act_identifier, aggregate))
    session.flush()
    return tuple(stored)


def _tombstone(
    release_id: int, state_id: int, teryt: str, edition: str, now: datetime
) -> PogAreaSummary:
    """Wiersz „brak aktywnych aktów”: zasłania starszy agregat gminy w stanach po wycofaniu aktu."""
    return PogAreaSummary(
        data_release_id=release_id,
        registry_state_id=state_id,
        scope="municipality",
        teryt=teryt,
        edition=edition,
        zones_area_sqm=0.0,
        overlap_area_sqm=0.0,
        outside_area_sqm=0.0,
        zone_count=0,
        act_count=0,
        is_complete=False,
        incomplete_reasons=["no_active_acts"],
        act_identifiers=[],
        area_tolerance_sqm=0.0,
        share_tolerance_pct=0.0,
        method_version=AGGREGATE_METHOD_VERSION,
        computed_at=now,
    )


def _copy_act_summary(
    session: Session,
    act: ActForAggregation,
    release_id: int,
    state_id: int,
    teryt: str,
    edition: str,
    now: datetime,
) -> AreaAggregate | None:
    """Agregat gminy z jednego aktu = agregat tego aktu (bez ponownych obliczeń przestrzennych)."""
    source = session.execute(
        select(PogAreaSummary).where(
            PogAreaSummary.scope == "act", PogAreaSummary.planning_act_version_id == act.version_id
        )
    ).scalar_one_or_none()
    if source is None:
        return None
    zones = session.execute(
        select(PogAreaSummaryZone)
        .where(PogAreaSummaryZone.summary_id == source.id)
        .order_by(PogAreaSummaryZone.order_index)
    ).scalars().all()
    summary = PogAreaSummary(
        data_release_id=release_id,
        registry_state_id=state_id,
        scope="municipality",
        planning_act_version_id=None,
        act_identifier=None,
        act_version=None,
        teryt=teryt,
        edition=edition,
        legal_status=None,
        denominator_area_sqm=source.denominator_area_sqm,
        denominator_source=None if source.denominator_area_sqm is None else "act_boundaries_union",
        zones_area_sqm=source.zones_area_sqm,
        missing_area_sqm=source.missing_area_sqm,
        overlap_area_sqm=source.overlap_area_sqm,
        outside_area_sqm=source.outside_area_sqm,
        deduplicated_area_sqm=source.deduplicated_area_sqm,
        share_sum_pct=source.share_sum_pct,
        zone_count=source.zone_count,
        act_count=1,
        is_complete=source.is_complete,
        incomplete_reasons=list(source.incomplete_reasons),
        act_identifiers=[act.act_identifier],
        area_tolerance_sqm=source.area_tolerance_sqm,
        share_tolerance_pct=source.share_tolerance_pct,
        method_version=source.method_version,
        computed_at=now,
    )
    session.add(summary)
    session.flush()
    for zone in zones:
        session.add(
            PogAreaSummaryZone(
                summary_id=summary.id,
                zone_code=zone.zone_code,
                area_sqm=zone.area_sqm,
                area_sqkm=zone.area_sqkm,
                share_pct=zone.share_pct,
                zone_count=zone.zone_count,
                order_index=zone.order_index,
            )
        )
    return AreaAggregate(
        denominator_area_sqm=source.denominator_area_sqm,
        zones_area_sqm=source.zones_area_sqm,
        missing_area_sqm=source.missing_area_sqm,
        overlap_area_sqm=source.overlap_area_sqm,
        outside_area_sqm=source.outside_area_sqm,
        deduplicated_area_sqm=source.deduplicated_area_sqm,
        share_sum_pct=source.share_sum_pct,
        zone_count=source.zone_count,
        is_complete=source.is_complete,
        incomplete_reasons=tuple(source.incomplete_reasons),
        zones=(),
        area_tolerance_sqm=source.area_tolerance_sqm,
        share_tolerance_pct=source.share_tolerance_pct,
    )


def compute_municipality_summaries(
    session: Session,
    release_id: int,
    state_id: int,
    teryts: Sequence[str] | set[str],
    *,
    computed_at: datetime | None = None,
) -> tuple[StoredAggregate, ...]:
    """Agregaty gmin i edycji dla stanu ``state_id`` (po zmianie rejestru), tylko dla podanych TERYT.

    Grupa z jednym aktem kopiuje pomiar z agregatu aktu (bez ponownych obliczeń przestrzennych); grupa z kilkoma
    aktami jest mierzona z kolejnością priorytetu, żeby nakładające się akty nie były liczone podwójnie. Gmina i
    edycja, które po zmianie nie mają aktywnych aktów, dostają wiersz ``no_active_acts`` (zasłania starszy agregat).
    """
    scope = sorted({str(item) for item in teryts if item})
    if not scope:
        return ()
    now = computed_at or datetime.now(timezone.utc)
    acts = _active_acts(session, state_id, scope)
    groups: dict[tuple[str, str], list[ActForAggregation]] = defaultdict(list)
    for act in acts:
        edition = edition_for_status(act.legal_status)
        if act.teryt and edition:
            groups[(act.teryt, edition)].append(act)
    version_ids = sorted({act.version_id for members in groups.values() for act in members})
    codes = _zone_codes(session, version_ids) if version_ids else {}
    stored: list[StoredAggregate] = []
    editions = ("binding", "project")
    for teryt in scope:
        for edition in editions:
            members = groups.get((teryt, edition))
            if not members:
                session.add(_tombstone(release_id, state_id, teryt, edition, now))
                continue
            ordered = priority_ordered(members)
            if len(ordered) == 1:
                copied = _copy_act_summary(session, ordered[0], release_id, state_id, teryt, edition, now)
                if copied is not None:
                    stored.append(StoredAggregate("municipality", f"{teryt}:{edition}", copied))
                    continue
            group_codes = [code for act in ordered for code in codes.get(act.version_id, [])]
            aggregate = aggregate_areas(measure_group(session, ordered, group_codes))
            _store(
                session,
                _summary_row(
                    release_id,
                    aggregate,
                    scope="municipality",
                    acts=ordered,
                    computed_at=now,
                    teryt=teryt,
                    edition=edition,
                    registry_state_id=state_id,
                ),
                aggregate,
            )
            stored.append(StoredAggregate("municipality", f"{teryt}:{edition}", aggregate))
    session.flush()
    return tuple(stored)


def aggregate_warnings(stored: Sequence[StoredAggregate]) -> tuple[str, ...]:
    return tuple(
        warning
        for item in stored
        if (warning := aggregate_warning(item.scope_key, item.aggregate)) is not None
    )
