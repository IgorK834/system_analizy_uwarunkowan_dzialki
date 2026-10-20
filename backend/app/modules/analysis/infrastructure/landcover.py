"""PostGIS: rzeczywiste przecięcia klas PT, pierścień 200 m i najbliższy las."""

from sqlalchemy import text

from app.modules.analysis.application.bdot_ports import BdotIndexError
from app.modules.analysis.domain.landcover import CoverMeasurement, ForestMeasurement
from app.modules.analysis.infrastructure.bdot_repository import PostgisBdotIndex


class PostgisLandCoverIndex(PostgisBdotIndex):
    def land_cover_ready(self, release):
        with self._guarded("land_cover_ready"):
            return (
                self._session.execute(
                    text("""SELECT class_stats->'land_cover'->>'complete' = 'true'
                FROM bdot_releases WHERE data_release_id=:release"""),
                    {"release": release.data_release_id},
                ).scalar_one()
                is True
            )

    def cover(self, release, parcel_wkt, radius_m, limit):
        params = {"release": release.data_release_id, "wkt": parcel_wkt, "radius": radius_m}
        with self._guarded("land_cover"):
            count = self._session.execute(
                text("""SELECT count(*) FROM bdot_landcover
                WHERE data_release_id=:release AND ST_DWithin(geometry, ST_GeomFromText(:wkt,2180), :radius)"""),
                params,
            ).scalar_one()
        if count > limit:
            raise BdotIndexError("Przekroczono limit obiektów pokrycia.", "TOO_MANY_CANDIDATES")
        with self._guarded("land_cover_intersections"):
            rows = (
                self._session.execute(
                    text("""
                WITH p AS (SELECT ST_GeomFromText(:wkt,2180) geom),
                zones AS (SELECT 'parcel' zone, geom FROM p UNION ALL
                    SELECT 'surroundings', ST_Difference(ST_Buffer(geom,:radius,'quad_segs=16'),geom) FROM p),
                clipped AS (SELECT z.zone, z.geom zone_geom, c.class_code,
                    ST_Intersection(c.geometry,z.geom) geom
                    FROM zones z JOIN bdot_landcover c ON c.data_release_id=:release AND ST_Intersects(c.geometry,z.geom)),
                merged AS (SELECT zone,class_code,ST_Area(zone_geom) zone_area,
                    ST_UnaryUnion(ST_Collect(geom)) geom FROM clipped GROUP BY zone,class_code,zone_geom)
                SELECT zone,class_code,zone_area,ST_Area(geom) area,ST_AsBinary(geom) wkb
                FROM merged WHERE ST_Area(geom)>0 ORDER BY zone,class_code
                """),
                    params,
                )
                .mappings()
                .all()
            )
        return [
            CoverMeasurement(r["zone"], r["class_code"], float(r["area"]), float(r["zone_area"]), bytes(r["wkb"]))
            for r in rows
        ]

    def forest(self, release, parcel_wkt, radius_m):
        params = {"release": release.data_release_id, "wkt": parcel_wkt, "radius": radius_m}
        with self._guarded("forest"):
            row = (
                self._session.execute(
                    text("""
                SELECT local_id,ST_Distance(geometry,ST_GeomFromText(:wkt,2180)) distance,
                    ST_AsBinary(ST_Intersection(geometry,ST_Buffer(ST_GeomFromText(:wkt,2180),:radius,'quad_segs=16'))) wkb
                FROM bdot_landcover WHERE data_release_id=:release AND class_code='OT_PTLZ_A' AND kind='las'
                    AND ST_DWithin(geometry,ST_GeomFromText(:wkt,2180),:radius)
                ORDER BY geometry <-> ST_GeomFromText(:wkt,2180),local_id LIMIT 1
            """),
                    params,
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            buffers = (
                self._session.execute(
                    text("""
                WITH p AS (SELECT ST_GeomFromText(:wkt,2180) geom), forests AS (
                    SELECT COALESCE(ST_UnaryUnion(ST_Collect(geometry)),
                        ST_GeomFromText('MULTIPOLYGON EMPTY',2180)) geom FROM bdot_landcover,p
                    WHERE data_release_id=:release AND class_code='OT_PTLZ_A' AND kind='las'
                        AND ST_DWithin(geometry,p.geom,16)), clipped AS (
                    SELECT r,ST_Intersection(p.geom,ST_Buffer(f.geom,r,'quad_segs=16')) geom
                    FROM p,forests f,unnest(ARRAY[8,12,16]) r)
                SELECT r,ST_Area(geom) area,ST_AsBinary(geom) wkb FROM clipped ORDER BY r
            """),
                    params,
                )
                .mappings()
                .all()
            )
        return ForestMeasurement(
            row["local_id"],
            float(row["distance"]),
            bytes(row["wkb"]),
            tuple((r["r"], float(r["area"]), bytes(r["wkb"]) if r["wkb"] else b"") for r in buffers),
        )
