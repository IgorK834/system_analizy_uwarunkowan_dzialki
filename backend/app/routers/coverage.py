"""Pokrycie lokalnych wydań BDOT10k (Task 23.1): które powiaty są zaimportowane i czy punkt leży w ich zasięgu.

Odpowiedź służy operatorowi i UI do odróżnienia „powiat bez zaimportowanego wydania” (brak pokrycia — analiza drogi i
zabudowy da ``no_coverage``) od „zaimportowano, ale w ograniczonym promieniu nic nie znaleziono”. Zwraca wyłącznie
metadane wydań (liczności, stan danych, SHA-256 paczki, obwiednia w WGS84) — nie geometrię obiektów.
"""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.rate_limit import rate_limit
from app.core.settings import settings
from app.db.session import get_db
from app.modules.analysis.domain.bdot import BDOT10K_SOURCE_ID

router = APIRouter(prefix="/api/v1/coverage", tags=["coverage"])
_coverage_limit = rate_limit(settings.rate_limit_coverage_per_minute)

_COUNTIES = text(
    """
    SELECT br.teryt_powiat, br.powiat_name, dr.id AS release_id, dr.version_label, br.data_as_of, br.imported_at,
           br.road_count, br.building_count, br.package_sha256,
           ST_XMin(b.box) AS min_lon, ST_YMin(b.box) AS min_lat, ST_XMax(b.box) AS max_lon, ST_YMax(b.box) AS max_lat
    FROM bdot_releases br
    JOIN data_releases dr ON dr.id = br.data_release_id
    JOIN data_sources ds ON ds.id = dr.data_source_id,
    LATERAL (SELECT ST_Envelope(ST_Transform(br.extent, 4326)) AS box) b
    WHERE ds.source_id = :source_id AND dr.is_active
    ORDER BY br.teryt_powiat
    """
)
_POINT = text(
    """
    SELECT br.teryt_powiat
    FROM bdot_releases br
    JOIN data_releases dr ON dr.id = br.data_release_id
    JOIN data_sources ds ON ds.id = dr.data_source_id
    WHERE ds.source_id = :source_id AND dr.is_active
      AND ST_Intersects(br.extent, ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 2180))
    ORDER BY br.teryt_powiat
    LIMIT 1
    """
)


class Bdot10kCounty(BaseModel):
    teryt_powiat: str = Field(description="Czterocyfrowy TERYT powiatu paczki.")
    name: str | None = None
    release_id: int
    version_label: str
    data_as_of: date | None = Field(
        default=None, description="Najnowsza wersja obiektu w paczce (stan danych), nie data pobrania."
    )
    imported_at: datetime
    road_count: int
    building_count: int
    package_sha256: str
    bounds_wgs84: list[float] = Field(description="[min_lon, min_lat, max_lon, max_lat] obwiednia granicy powiatu.")


class Bdot10kPointCoverage(BaseModel):
    lon: float
    lat: float
    covered: bool = Field(description="Czy punkt leży w granicy zaimportowanego powiatu.")
    teryt_powiat: str | None = None


class Bdot10kCoverageResponse(BaseModel):
    source_id: str = BDOT10K_SOURCE_ID
    counties: list[Bdot10kCounty]
    point: Bdot10kPointCoverage | None = Field(
        default=None,
        description="Obecne, gdy podano lon i lat. Brak pokrycia nie oznacza braku drogi ani budynków.",
    )
    note: str = (
        "Pokrycie dotyczy zaimportowanych wydań BDOT10k (paczki powiatowe). Punkt poza pokryciem daje w analizie status "
        "no_coverage, a nie brak drogi lub budynków."
    )


@router.get(
    "/bdot10k",
    response_model=Bdot10kCoverageResponse,
    dependencies=[Depends(_coverage_limit)],
    summary="Zaimportowane powiaty BDOT10k i pokrycie punktu",
)
def get_bdot10k_coverage(
    db: Annotated[Session, Depends(get_db)],
    lon: Annotated[float | None, Query(ge=-180.0, le=180.0)] = None,
    lat: Annotated[float | None, Query(ge=-90.0, le=90.0)] = None,
) -> Bdot10kCoverageResponse:
    rows = db.execute(_COUNTIES, {"source_id": BDOT10K_SOURCE_ID}).mappings().all()
    counties = [
        Bdot10kCounty(
            teryt_powiat=str(row["teryt_powiat"]),
            name=row["powiat_name"],
            release_id=int(row["release_id"]),
            version_label=str(row["version_label"]),
            data_as_of=row["data_as_of"],
            imported_at=row["imported_at"],
            road_count=int(row["road_count"]),
            building_count=int(row["building_count"]),
            package_sha256=str(row["package_sha256"]),
            bounds_wgs84=[
                round(float(row[key]), 6) for key in ("min_lon", "min_lat", "max_lon", "max_lat")
            ],
        )
        for row in rows
    ]
    point: Bdot10kPointCoverage | None = None
    if lon is not None and lat is not None:
        found = db.execute(_POINT, {"source_id": BDOT10K_SOURCE_ID, "lon": lon, "lat": lat}).scalar_one_or_none()
        point = Bdot10kPointCoverage(lon=lon, lat=lat, covered=found is not None, teryt_powiat=found)
    return Bdot10kCoverageResponse(counties=counties, point=point)
