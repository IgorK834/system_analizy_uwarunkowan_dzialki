"""Adapter PostGIS odczytu stanu krajowego POG: inspektor (BK-404) i agregaty (BK-405).

Zapytania czytają wyłącznie kolumny i gotowe agregaty — nie wykonują
``ST_Intersection``, ``ST_Area`` ani innych obliczeń przestrzennych (dowód:
test przechwytujący SQL w ``tests/test_pog_aggregates.py``). Zbiór wersji aktów
wyznacza rejestr w stanie ``:state_id`` (ADR-026), więc odpowiedzi są stałe dla
numeru stanu mimo późniejszych importów innych gmin.
"""

from __future__ import annotations

from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.planning.application.pog_state_queries import PogStateQueryRepository
from app.modules.planning.domain.pog_area_summary import (
    PogAreaSummaryQuery,
    PogAreaSummaryView,
    PogAreaSummaryZoneView,
)
from app.modules.planning.domain.pog_features import RAW_ATTRIBUTE_KEYS
from app.modules.planning.domain.pog_inspector import (
    PogActDetails,
    PogFeatureDetailsRow,
    PogFeatureRef,
    PogStateHeader,
)
from app.modules.planning.domain.pog_tiles import FEATURE_TYPE_LAYERS, PogTileCandidate
from app.shared.pog_registry import active_versions_join

_STATE_HEADER_SQL: Final[str] = """
    SELECT s.id, s.kind, s.created_at,
           s.id = (SELECT max(id) FROM pog_registry_states) AS is_current
    FROM pog_registry_states s
    WHERE s.id = :state_id
"""

_FEATURE_SQL: Final[str] = f"""
    SELECT pf.id AS pk, pf.feature_type, pf.feature_identifier, pf.feature_version,
           pf.symbol, pf.label, pf.parameters, pf.primary_profiles,
           pf.additional_profiles, pf.source_reference,
           (
               SELECT jsonb_object_agg(attr.key, attr.value)
               FROM jsonb_each(
                   CASE WHEN jsonb_typeof(pf.raw_attributes) = 'object'
                        THEN pf.raw_attributes ELSE '{{}}'::jsonb END
               ) AS attr
               WHERE lower(attr.key) = ANY(CAST(:raw_keys AS text[]))
           ) AS raw_attributes,
           pa.act_identifier, pa.teryt, pav.id AS act_version_id, pav.legal_status,
           pav.raw_legal_status, pav.object_version_id, pav.name,
           COALESCE(pav.resolution_number, doc.identification_number) AS resolution_number,
           COALESCE(pav.resolution_date, doc.document_date) AS resolution_date,
           pav.legal_valid_from, pav.legal_valid_to, pav.publication_id, pav.manual_review_required,
           pav.content_hash, pav.data_release_id, dr.version_label AS release_label,
           sa.content_hash AS artifact_sha256, sa.fetched_at AS artifact_fetched_at,
           CAST(pav.version_started_at AS date) AS version_started_on,
           doc.effective_date, left(doc.journal, 200) AS official_journal, doc.link AS document_link,
           e.ru_published_act_id
    FROM planning_features pf
    JOIN planning_act_versions pav ON pav.id = pf.planning_act_version_id
    JOIN planning_acts pa ON pa.id = pav.planning_act_id
    {active_versions_join()}
    JOIN data_releases dr ON dr.id = pav.data_release_id
    JOIN source_artifacts sa ON sa.id = pav.source_artifact_id
    LEFT JOIN LATERAL (
        SELECT d.identification_number, d.document_date, d.effective_date, d.link,
               d.raw_attributes -> 'dziennikUrzedowy' ->> 'label' AS journal
        FROM pog_formal_documents d
        WHERE d.planning_act_version_id = pav.id AND d.relation = 'uchwala'
          AND d.resolution_status = 'resolved'
        ORDER BY d.effective_date DESC NULLS LAST, d.id
        LIMIT 1
    ) doc ON true
    WHERE pa.kind = 'pog'
      AND pf.feature_type = ANY(CAST(:feature_types AS text[]))
      AND (
          (CAST(:feature_id AS text) IS NOT NULL AND pf.feature_identifier = CAST(:feature_id AS text))
          OR (CAST(:pk AS bigint) IS NOT NULL AND pf.id = CAST(:pk AS bigint))
      )
    ORDER BY pf.id
    LIMIT 2
"""

# Agregat aktu: wiersz wersji aktywnej w stanie. Agregat gminy: najnowszy wiersz z numerem stanu ≤ przypiętemu;
# wiersz ``act_count = 0`` („brak aktywnych aktów”) zasłania starsze agregaty i daje 404.
_SUMMARY_SQL: Final[str] = """
    SELECT s.*, dr.version_label,
           (SELECT sa.content_hash FROM planning_act_versions pav
              JOIN source_artifacts sa ON sa.id = pav.source_artifact_id
             WHERE pav.data_release_id = dr.id ORDER BY pav.id LIMIT 1) AS artifact_sha256
    FROM pog_area_summaries s
    JOIN data_releases dr ON dr.id = s.data_release_id
    WHERE s.act_count > 0 AND (
        (:scope = 'act' AND s.scope = 'act' AND s.act_identifier = CAST(:act_id AS text)
         AND EXISTS (
             SELECT 1 FROM pog_registry_entries e
             WHERE e.planning_act_version_id = s.planning_act_version_id
               AND e.state_from_id <= :state_id AND (e.state_to_id IS NULL OR e.state_to_id > :state_id)))
        OR (:scope = 'municipality' AND s.scope = 'municipality'
            AND s.teryt = CAST(:teryt AS text) AND s.edition = CAST(:edition AS text)
            AND s.id = (
                SELECT s3.id FROM pog_area_summaries s3
                WHERE s3.scope = 'municipality' AND s3.teryt = s.teryt AND s3.edition = s.edition
                  AND s3.registry_state_id <= :state_id
                ORDER BY s3.registry_state_id DESC LIMIT 1))
    )
    ORDER BY s.id
    LIMIT 1
"""

_SUMMARY_ZONES_SQL: Final[str] = """
    SELECT zone_code, area_sqm, area_sqkm, share_pct, zone_count
    FROM pog_area_summary_zones
    WHERE summary_id = :summary_id
    ORDER BY order_index
"""

_HAS_SUMMARIES_SQL: Final[str] = f"""
    SELECT EXISTS (
        SELECT 1 FROM pog_area_summaries s
        JOIN planning_act_versions pav ON pav.id = s.planning_act_version_id
        {active_versions_join()}
        WHERE s.scope = 'act'
    )
"""


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


class SqlAlchemyPogStateQueryRepository(PogStateQueryRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def state_header(self, state_id: int) -> PogStateHeader | None:
        row = self._session.execute(text(_STATE_HEADER_SQL), {"state_id": state_id}).mappings().one_or_none()
        if row is None:
            return None
        return PogStateHeader(
            state_id=int(row["id"]),
            kind=str(row["kind"]),
            created_at=row["created_at"],
            is_current=bool(row["is_current"]),
        )

    def feature_rows(self, state_id: int, ref: PogFeatureRef) -> list[PogFeatureDetailsRow]:
        rows = self._session.execute(
            text(_FEATURE_SQL),
            {
                "state_id": state_id,
                "feature_id": ref.feature_id,
                "pk": ref.pk,
                "raw_keys": sorted(RAW_ATTRIBUTE_KEYS),
                "feature_types": list(FEATURE_TYPE_LAYERS),
            },
        ).mappings()
        return [
            PogFeatureDetailsRow(
                candidate=PogTileCandidate(
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
                ),
                act=PogActDetails(
                    act_id=str(row["act_identifier"]),
                    act_version=row["object_version_id"],
                    name=row["name"],
                    teryt=row["teryt"],
                    legal_status=str(row["legal_status"] or "unknown"),
                    legal_status_code=row["raw_legal_status"],
                    resolution_number=row["resolution_number"],
                    resolution_date=row["resolution_date"],
                    legal_valid_from=row["legal_valid_from"],
                    legal_valid_to=row["legal_valid_to"],
                    publication_id=row["publication_id"],
                    manual_review_required=bool(row["manual_review_required"]),
                    act_version_id=int(row["act_version_id"]),
                    data_release_id=int(row["data_release_id"]),
                    release_label=row["release_label"],
                    artifact_sha256=row["artifact_sha256"],
                    artifact_fetched_at=row["artifact_fetched_at"],
                    content_hash=row["content_hash"],
                    effective_date=row["effective_date"],
                    official_journal=row["official_journal"],
                    document_link=row["document_link"],
                    version_started_on=row["version_started_on"],
                    ru_published_act_id=(
                        int(row["ru_published_act_id"]) if row["ru_published_act_id"] is not None else None
                    ),
                ),
                source_reference=row["source_reference"],
            )
            for row in rows
        ]

    def area_summary(self, query: PogAreaSummaryQuery) -> PogAreaSummaryView | None:
        row = self._session.execute(
            text(_SUMMARY_SQL),
            {
                "state_id": query.state_id,
                "scope": query.scope,
                "act_id": query.act_id,
                "teryt": query.teryt,
                "edition": query.edition,
            },
        ).mappings().one_or_none()
        if row is None:
            return None
        zones = tuple(
            PogAreaSummaryZoneView(
                zone_code=str(zone["zone_code"]),
                area_sqm=float(zone["area_sqm"]),
                area_sqkm=float(zone["area_sqkm"]),
                share_pct=_optional_float(zone["share_pct"]),
                zone_count=int(zone["zone_count"]),
            )
            for zone in self._session.execute(
                text(_SUMMARY_ZONES_SQL), {"summary_id": row["id"]}
            ).mappings()
        )
        current = self._session.execute(text("SELECT max(id) FROM pog_registry_states")).scalar_one()
        return PogAreaSummaryView(
            state_id=query.state_id,
            computed_in_state_id=row["registry_state_id"],
            release_id=int(row["data_release_id"]),
            release_label=str(row["version_label"]),
            state_is_current=current is not None and int(current) == query.state_id,
            artifact_sha256=row["artifact_sha256"],
            scope=str(row["scope"]),
            act_id=row["act_identifier"],
            act_version=row["act_version"],
            teryt=row["teryt"],
            edition=row["edition"],
            legal_status=row["legal_status"],
            act_ids=tuple(str(item) for item in (row["act_identifiers"] or [])),
            act_count=int(row["act_count"]),
            denominator_area_sqm=_optional_float(row["denominator_area_sqm"]),
            denominator_source=row["denominator_source"],
            zones_area_sqm=float(row["zones_area_sqm"]),
            missing_area_sqm=_optional_float(row["missing_area_sqm"]),
            overlap_area_sqm=float(row["overlap_area_sqm"]),
            outside_area_sqm=float(row["outside_area_sqm"]),
            deduplicated_area_sqm=_optional_float(row["deduplicated_area_sqm"]),
            share_sum_pct=_optional_float(row["share_sum_pct"]),
            share_tolerance_pct=float(row["share_tolerance_pct"]),
            area_tolerance_sqm=float(row["area_tolerance_sqm"]),
            zone_count=int(row["zone_count"]),
            is_complete=bool(row["is_complete"]),
            incomplete_reasons=tuple(str(item) for item in (row["incomplete_reasons"] or [])),
            zones=zones,
            method_version=str(row["method_version"]),
            computed_at=row["computed_at"],
        )

    def state_has_area_summaries(self, state_id: int) -> bool:
        return bool(self._session.execute(text(_HAS_SUMMARIES_SQL), {"state_id": state_id}).scalar_one())
