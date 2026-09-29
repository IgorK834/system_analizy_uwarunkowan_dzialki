"""Adapter PostGIS odczytu wydania POG: inspektor (BK-404) i agregaty (BK-405).

Zapytania czytają wyłącznie kolumny i gotowe agregaty — nie wykonują
``ST_Intersection``, ``ST_Area`` ani innych obliczeń przestrzennych (dowód:
test przechwytujący SQL w ``tests/test_pog_aggregates.py``).
"""

from __future__ import annotations

from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.planning.application.pog_release_queries import (
    PogReleaseQueryRepository,
)
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
    PogReleaseHeader,
)
from app.modules.planning.domain.pog_tiles import FEATURE_TYPE_LAYERS, PogTileCandidate

_RELEASE_HEADER_SQL: Final[str] = """
    SELECT dr.id, dr.version_label, dr.published_at, dr.is_active,
           (SELECT sa.content_hash
              FROM planning_act_versions pav
              JOIN source_artifacts sa ON sa.id = pav.source_artifact_id
             WHERE pav.data_release_id = dr.id
             ORDER BY pav.id LIMIT 1) AS artifact_sha256
    FROM data_releases dr
    WHERE dr.id = :release_id
      AND EXISTS (
          SELECT 1 FROM planning_act_versions pav
          JOIN planning_acts pa ON pa.id = pav.planning_act_id
          WHERE pav.data_release_id = dr.id AND pa.kind = 'pog'
      )
"""

_FEATURE_SQL: Final[str] = """
    SELECT pf.id AS pk, pf.feature_type, pf.feature_identifier, pf.feature_version,
           pf.symbol, pf.label, pf.parameters, pf.primary_profiles,
           pf.additional_profiles, pf.source_reference,
           (
               SELECT jsonb_object_agg(attr.key, attr.value)
               FROM jsonb_each(
                   CASE WHEN jsonb_typeof(pf.raw_attributes) = 'object'
                        THEN pf.raw_attributes ELSE '{}'::jsonb END
               ) AS attr
               WHERE lower(attr.key) = ANY(CAST(:raw_keys AS text[]))
           ) AS raw_attributes,
           pa.act_identifier, pa.teryt, pav.legal_status, pav.raw_legal_status,
           pav.object_version_id, pav.name, pav.resolution_number,
           pav.resolution_date, pav.legal_valid_from, pav.legal_valid_to,
           pav.publication_id, pav.manual_review_required
    FROM planning_features pf
    JOIN planning_act_versions pav ON pav.id = pf.planning_act_version_id
    JOIN planning_acts pa ON pa.id = pav.planning_act_id
    WHERE pa.kind = 'pog'
      AND pav.data_release_id = :release_id
      AND pf.feature_type = ANY(CAST(:feature_types AS text[]))
      AND (
          (CAST(:feature_id AS text) IS NOT NULL AND pf.feature_identifier = CAST(:feature_id AS text))
          OR (CAST(:pk AS bigint) IS NOT NULL AND pf.id = CAST(:pk AS bigint))
      )
    ORDER BY pf.id
    LIMIT 2
"""

_SUMMARY_SQL: Final[str] = """
    SELECT s.*, dr.version_label, dr.is_active,
           (SELECT sa.content_hash
              FROM planning_act_versions pav
              JOIN source_artifacts sa ON sa.id = pav.source_artifact_id
             WHERE pav.data_release_id = dr.id
             ORDER BY pav.id LIMIT 1) AS artifact_sha256
    FROM pog_area_summaries s
    JOIN data_releases dr ON dr.id = s.data_release_id
    WHERE s.data_release_id = :release_id
      AND (
          (:scope = 'act' AND s.scope = 'act' AND s.act_identifier = CAST(:act_id AS text))
          OR (:scope = 'municipality' AND s.scope = 'municipality'
              AND s.teryt = CAST(:teryt AS text) AND s.edition = CAST(:edition AS text))
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

_HAS_SUMMARIES_SQL: Final[str] = """
    SELECT EXISTS (SELECT 1 FROM pog_area_summaries WHERE data_release_id = :release_id)
"""


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


class SqlAlchemyPogReleaseQueryRepository(PogReleaseQueryRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def release_header(self, release_id: int) -> PogReleaseHeader | None:
        row = self._session.execute(
            text(_RELEASE_HEADER_SQL), {"release_id": release_id}
        ).mappings().one_or_none()
        if row is None:
            return None
        return PogReleaseHeader(
            release_id=int(row["id"]),
            version_label=str(row["version_label"]),
            published_at=row["published_at"],
            is_active=bool(row["is_active"]),
            artifact_sha256=row["artifact_sha256"],
        )

    def feature_rows(self, release_id: int, ref: PogFeatureRef) -> list[PogFeatureDetailsRow]:
        rows = self._session.execute(
            text(_FEATURE_SQL),
            {
                "release_id": release_id,
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
                ),
                source_reference=row["source_reference"],
            )
            for row in rows
        ]

    def area_summary(self, query: PogAreaSummaryQuery) -> PogAreaSummaryView | None:
        row = self._session.execute(
            text(_SUMMARY_SQL),
            {
                "release_id": query.release_id,
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
        return PogAreaSummaryView(
            release_id=int(row["data_release_id"]),
            release_label=str(row["version_label"]),
            release_is_active=bool(row["is_active"]),
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

    def release_has_area_summaries(self, release_id: int) -> bool:
        return bool(
            self._session.execute(
                text(_HAS_SUMMARIES_SQL), {"release_id": release_id}
            ).scalar_one()
        )
