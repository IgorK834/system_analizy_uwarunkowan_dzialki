"""Pokrycie lokalnych wydań BDOT10k (Task 23.1): które powiaty są zaimportowane i czy punkt leży w ich zasięgu.

Odpowiedź służy operatorowi i UI do odróżnienia „powiat bez zaimportowanego wydania” (brak pokrycia — analiza drogi i
zabudowy da ``no_coverage``) od „zaimportowano, ale w ograniczonym promieniu nic nie znaleziono”. Zwraca wyłącznie
metadane wydań (liczności, stan danych, SHA-256 paczki, obwiednia w WGS84) — nie geometrię obiektów.
"""

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.rate_limit import rate_limit
from app.core.settings import settings
from app.db.session import get_db
from app.modules.analysis.domain.bdot import BDOT10K_SOURCE_ID
from app.modules.planning.application.pog_release_queries import coverage_features
from app.modules.planning.infrastructure.commune_boundaries import PrgCommuneBoundaries
from app.routers.pog_coverage import PogCoverageResponse, get_pog_coverage
from app.services.pog_on_demand import request_commune_import
from app.modules.imports.composition import enqueue_county_import

router = APIRouter(prefix="/api/v1/coverage", tags=["coverage"])
_coverage_limit = rate_limit(settings.rate_limit_coverage_per_minute)

_COUNTIES = text(
    """
    SELECT br.teryt_powiat, br.powiat_name, dr.id AS release_id, dr.version_label, br.data_as_of, br.imported_at,
           br.road_count, br.building_count, br.package_sha256, br.class_stats,
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
    landcover_count: int | None = Field(default=None, description="None: klasa niezaimportowana lub niekompletna; 0: potwierdzona pusta klasa.")
    powerline_count: int | None = Field(default=None, description="Linie elektroenergetyczne OT_SULN_L; None przy braku importu klasy.")
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
    try:
        return _load_bdot10k_coverage(db, lon, lat)
    except (SQLAlchemyError, ValueError, KeyError, TypeError) as exc:
        db.rollback()
        raise HTTPException(503, "Pokrycie BDOT10k jest niedostępne — stan nieustalony.") from exc


def _load_bdot10k_coverage(db: Session, lon: float | None, lat: float | None) -> Bdot10kCoverageResponse:
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
            landcover_count=_class_count(row["class_stats"], "land_cover"),
            powerline_count=_class_count(row["class_stats"], "powerlines"),
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


def _class_count(stats: dict[str, Any], name: str) -> int | None:
    if not isinstance(stats, dict):
        raise ValueError("Invalid BDOT class statistics")
    entry = stats.get(name) or {}
    if not isinstance(entry, dict):
        raise ValueError("Invalid BDOT class statistics")
    if entry.get("complete") is not True:
        return None
    count = entry.get("imported")
    if type(count) is not int or count < 0:
        raise ValueError("Invalid BDOT class count")
    return count


class CountyImportResponse(BaseModel):
    powiat: str
    job_id: int
    status: str
    detail: str


@router.post("/bdot10k/county/{powiat}/import", response_model=CountyImportResponse,
             dependencies=[Depends(rate_limit(settings.rate_limit_refresh_per_minute))])
def request_county_import(powiat: str, db: Annotated[Session, Depends(get_db)]) -> CountyImportResponse:
    if len(powiat) != 4 or not powiat.isascii() or not powiat.isdigit():
        raise HTTPException(422, "TERYT powiatu musi mieć 4 cyfry.")
    try:
        job = enqueue_county_import(db, powiat)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(503, "Kolejka importów jest niedostępna; nie potwierdzono zlecenia.") from exc
    return CountyImportResponse(powiat=powiat, job_id=job.job_id, status=job.status,
        detail=f"Import BDOT10k powiatu {powiat} jest w kolejce (zlecenie #{job.job_id}). Dane pojawią się po zakończeniu; brak lokalnego wydania nie oznacza braku obiektów.")


# Wspólny widok POG + BDOT10k. Zachowujemy istniejące endpointy szczegółowe.
class CoverageResponse(BaseModel):
    pog: PogCoverageResponse
    bdot10k: Bdot10kCoverageResponse
    communes: dict[str, Any]
    boundaries_note: str = "Granice PRG pobrano 16.10.2026; geometria uproszczona wyłącznie do podglądu pokrycia. Źródło: GUGiK."


@router.get("", response_model=CoverageResponse, dependencies=[Depends(_coverage_limit)])
def get_coverage(db: Annotated[Session, Depends(get_db)]) -> CoverageResponse:
    try:
        pog = get_pog_coverage(db, status=None, q=None, voivodeship=None)
        return CoverageResponse(
            pog=pog, bdot10k=get_bdot10k_coverage(db, lon=None, lat=None),
            communes=coverage_features(PrgCommuneBoundaries(), [item.model_dump() for item in pog.communes],
                                       catalog_fresh=pog.catalog_fresh),
        )
    except (SQLAlchemyError, OSError, ValueError, KeyError, TypeError) as exc:
        db.rollback()
        raise HTTPException(503, "Pokrycie danych jest chwilowo niedostępne — stan nieustalony.") from exc


class CoverageImportResponse(BaseModel):
    terc: str
    job_id: int | None
    status: str
    detail: str


@router.post("/pog/commune/{terc}/import", response_model=CoverageImportResponse,
             dependencies=[Depends(rate_limit(settings.rate_limit_refresh_per_minute))])
def request_coverage_import(
    terc: str, db: Annotated[Session, Depends(get_db)],
) -> CoverageImportResponse:
    if len(terc) != 6 or not terc.isdigit():
        raise HTTPException(422, "Kod TERC musi mieć 6 cyfr.")
    request = request_commune_import(db, terc, discovery_found_act=False)
    if request is None:
        raise HTTPException(409, "Nie zlecono importu: gmina ma dane lokalne, RU nie potwierdził aktu lub kolejka jest niedostępna.")
    return CoverageImportResponse(terc=terc, job_id=request.job_id, status=request.job_status,
                                  detail=request.warning().message)
