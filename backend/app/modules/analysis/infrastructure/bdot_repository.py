"""Adapter PostGIS indeksów BDOT10k: wybór wydania po położeniu działki, osie jezdni i obrysy budynków.

Wszystkie zapytania filtrują po ``data_release_id`` aktywnego wydania powiatu, w którego granicy leży punkt
reprezentatywny działki, i po ``kategoriaIstnienia = eksploatowany``. Odległości liczy PostGIS od poligonu działki
(``ST_Distance``) w EPSG:2180; zakres kandydatów wyznacza ``ST_DWithin`` (indeks GiST). Każde zapytanie ma limit czasu
(``statement_timeout``), a jego przekroczenie to kontrolowany ``BdotIndexTimeout`` — nigdy „brak drogi”.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from shapely import wkb as shapely_wkb
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.analysis.application.bdot_ports import BdotIndexError, BdotIndexTimeout
from app.modules.analysis.domain.bdot import (
    BDOT10K_SOURCE_ID,
    IN_OPERATION,
    BdotReleaseRef,
    CoverageCheck,
)
from app.modules.analysis.domain.buildings import BuildingFootprint
from app.modules.analysis.domain.roads import RoadCandidate

logger = logging.getLogger(__name__)

_QUERY_CANCELED = "57014"
_QUAD_SEGS = 16

_RELEASE_FOR = text(
    """
    SELECT dr.id, dr.version_label, dr.published_at, br.teryt_powiat, br.package_sha256, br.data_as_of
    FROM bdot_releases br
    JOIN data_releases dr ON dr.id = br.data_release_id
    JOIN data_sources ds ON ds.id = dr.data_source_id
    WHERE ds.source_id = :source_id
      AND dr.is_active
      AND ST_Intersects(br.extent, ST_PointOnSurface(ST_GeomFromText(:wkt, 2180)))
    ORDER BY ST_Area(ST_Intersection(br.extent, ST_GeomFromText(:wkt, 2180))) DESC, dr.id
    LIMIT 1
    """
)
_HAS_RELEASES = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM bdot_releases br
        JOIN data_releases dr ON dr.id = br.data_release_id
        JOIN data_sources ds ON ds.id = dr.data_source_id
        WHERE ds.source_id = :source_id AND dr.is_active
    )
    """
)
_COVERAGE = text(
    f"""
    SELECT ST_Covers(br.extent, p.geom) AS parcel_covered,
           CASE WHEN :radius <= 0 THEN ST_Covers(br.extent, p.geom)
                ELSE ST_Covers(br.extent, ST_Buffer(p.geom, :radius, 'quad_segs={_QUAD_SEGS}')) END AS area_covered
    FROM bdot_releases br, (SELECT ST_GeomFromText(:wkt, 2180) AS geom) p
    WHERE br.data_release_id = :release_id
    """
)
_NEAREST = text(
    """
    SELECT r.local_id,
           ST_Distance(r.geometry, p.geom) AS distance_m,
           ST_Intersects(r.geometry, p.geom) AS intersects,
           ST_Touches(r.geometry, p.geom) AS touches,
           r.management_category, r.road_class, r.road_number, r.street_name, r.surface_material,
           r.surface_width_m, r.road_position, r.feature_version_at,
           ST_AsText(r.geometry) AS road_wkt,
           CASE WHEN ST_Distance(r.geometry, p.geom) > 0
                THEN ST_AsText(ST_ShortestLine(p.geom, r.geometry)) END AS link_wkt
    FROM bdot_roads r, (SELECT ST_GeomFromText(:wkt, 2180) AS geom) p
    WHERE r.data_release_id = :release_id
      AND r.existence_category = :in_operation
      AND ST_DWithin(r.geometry, p.geom, :radius)
    ORDER BY distance_m, r.local_id
    LIMIT :limit
    """
)
_ADJACENCY = text(
    """
    SELECT count(*) FILTER (WHERE NOT ST_Touches(r.geometry, p.geom)) AS intersecting,
           count(*) FILTER (WHERE ST_Touches(r.geometry, p.geom)) AS touching
    FROM bdot_roads r, (SELECT ST_GeomFromText(:wkt, 2180) AS geom) p
    WHERE r.data_release_id = :release_id
      AND r.existence_category = :in_operation
      AND ST_Intersects(r.geometry, p.geom)
    """
)
_BUILDINGS = text(
    """
    SELECT b.local_id, b.general_function, b.detailed_function, b.predominant_function, b.floors,
           b.geometry_source, b.feature_version_at, b.area_sqm, ST_AsBinary(b.geometry) AS geometry_wkb
    FROM bdot_buildings b, (SELECT ST_GeomFromText(:wkt, 2180) AS geom) p
    WHERE b.data_release_id = :release_id
      AND b.existence_category = :in_operation
      AND ST_DWithin(b.geometry, p.geom, :radius)
    ORDER BY ST_Distance(b.geometry, p.geom), b.local_id
    LIMIT :limit
    """
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class PostgisBdotIndex:
    """Port ``RoadNetworkIndex`` i ``BuildingIndex`` na jednej sesji SQLAlchemy (używanej sekwencyjnie)."""

    def __init__(self, session: Session, *, statement_timeout_ms: int) -> None:
        self._session = session
        self._timeout_ms = int(statement_timeout_ms)

    @contextmanager
    def _guarded(self, operation: str) -> Iterator[None]:
        """Limit czasu zapytania; po błędzie sesja jest wycofana, a przyczyna zamieniona na błąd indeksu."""
        try:
            self._session.execute(text(f"SET LOCAL statement_timeout = {self._timeout_ms}"))
            yield
        except DBAPIError as exc:
            self._session.rollback()
            code = getattr(getattr(exc, "orig", None), "pgcode", None)
            if code == _QUERY_CANCELED:
                logger.warning("bdot_query_timeout operation=%s timeout_ms=%d", operation, self._timeout_ms)
                raise BdotIndexTimeout(f"Zapytanie {operation} przekroczyło limit czasu.") from exc
            logger.warning("bdot_query_failed operation=%s error=%s", operation, type(exc.orig).__name__)
            raise BdotIndexError(f"Zapytanie {operation} nie powiodło się.") from exc
        except SQLAlchemyError as exc:
            self._session.rollback()
            logger.warning("bdot_query_failed operation=%s error=%s", operation, type(exc).__name__)
            raise BdotIndexError(f"Zapytanie {operation} nie powiodło się.") from exc
        else:
            self._session.execute(text("SET LOCAL statement_timeout = DEFAULT"))

    def release_for(self, parcel_wkt: str) -> BdotReleaseRef | None:
        with self._guarded("release_for"):
            row = self._session.execute(
                _RELEASE_FOR, {"source_id": BDOT10K_SOURCE_ID, "wkt": parcel_wkt}
            ).mappings().first()
        if row is None:
            return None
        return BdotReleaseRef(
            data_release_id=int(row["id"]),
            version_label=str(row["version_label"]),
            teryt_powiat=str(row["teryt_powiat"]),
            published_at=row["published_at"],
            package_sha256=row["package_sha256"],
            data_as_of=row["data_as_of"],
        )

    def has_active_releases(self) -> bool:
        with self._guarded("has_active_releases"):
            return bool(self._session.execute(_HAS_RELEASES, {"source_id": BDOT10K_SOURCE_ID}).scalar_one())

    def coverage(self, release: BdotReleaseRef, parcel_wkt: str, radius_m: float) -> CoverageCheck:
        with self._guarded("coverage"):
            row = self._session.execute(
                _COVERAGE,
                {"wkt": parcel_wkt, "release_id": release.data_release_id, "radius": float(radius_m)},
            ).mappings().one()
        return CoverageCheck(parcel_covered=bool(row["parcel_covered"]), search_area_covered=bool(row["area_covered"]))

    def nearest(
        self, release: BdotReleaseRef, parcel_wkt: str, radius_m: float, limit: int
    ) -> list[RoadCandidate]:
        with self._guarded("roads_nearest"):
            rows = self._session.execute(
                _NEAREST,
                {
                    "wkt": parcel_wkt,
                    "release_id": release.data_release_id,
                    "radius": float(radius_m),
                    "limit": int(limit),
                    "in_operation": IN_OPERATION,
                },
            ).mappings().all()
        return [
            RoadCandidate(
                road_id=str(row["local_id"]),
                distance_m=float(row["distance_m"]),
                intersects=bool(row["intersects"]),
                touches=bool(row["touches"]),
                category=row["management_category"],
                road_class=row["road_class"],
                road_number=row["road_number"],
                street_name=row["street_name"],
                surface_material=row["surface_material"],
                surface_width_m=row["surface_width_m"],
                position=row["road_position"],
                road_wkt=row["road_wkt"],
                link_wkt=row["link_wkt"],
                feature_version=_iso(row["feature_version_at"]),
            )
            for row in rows
        ]

    def adjacency_counts(self, release: BdotReleaseRef, parcel_wkt: str) -> tuple[int, int]:
        with self._guarded("roads_adjacency"):
            row = self._session.execute(
                _ADJACENCY,
                {"wkt": parcel_wkt, "release_id": release.data_release_id, "in_operation": IN_OPERATION},
            ).mappings().one()
        return int(row["intersecting"]), int(row["touching"])

    def candidates(
        self, release: BdotReleaseRef, parcel_wkt: str, radius_m: float, limit: int
    ) -> list[BuildingFootprint]:
        with self._guarded("buildings_candidates"):
            rows = self._session.execute(
                _BUILDINGS,
                {
                    "wkt": parcel_wkt,
                    "release_id": release.data_release_id,
                    "radius": float(radius_m),
                    "limit": int(limit),
                    "in_operation": IN_OPERATION,
                },
            ).mappings().all()
        return [
            BuildingFootprint(
                building_id=str(row["local_id"]),
                geometry=shapely_wkb.loads(bytes(row["geometry_wkb"])),
                area_sqm=float(row["area_sqm"]),
                general_function=row["general_function"],
                detailed_function=row["detailed_function"],
                predominant_function=row["predominant_function"],
                floors=row["floors"],
                geometry_source=row["geometry_source"],
                feature_version=row["feature_version_at"],
            )
            for row in rows
        ]
