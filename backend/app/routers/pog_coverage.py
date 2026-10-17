"""Pokrycie krajowe POG (issue 301, ADR-026): które gminy są zaimportowane, kiedy, z jaką wersją — i które mają plan w RU.

Trzy rozłączne stany gminy (nigdy „brak planu”, jeśli nie sprawdzono):

* ``imported`` — w stanie krajowym jest aktywny akt tej gminy (data importu, wersja, ``content_hash``);
* ``in_ru_not_imported`` — wykaz RU zawiera bieżący akt, ale gmina nie jest jeszcze zaimportowana (bywa zlecenie w kolejce);
* ``no_act_in_ru`` — kompletny, świeży wykaz RU nie zawiera aktu tej gminy (to nie dowód braku planu w gminie);
* ``unknown`` — wykaz RU nie został zsynchronizowany albo jest przeterminowany (źródło niedostępne).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.rate_limit import rate_limit
from app.core.settings import settings
from app.db.session import get_db
from app.modules.imports.infrastructure.job_queue import job_status_for_terc, scope_key_for_terc  # noqa: F401

router = APIRouter(prefix="/api/v1/coverage", tags=["coverage"])
_coverage_limit = rate_limit(settings.rate_limit_coverage_per_minute)

CommuneStatus = Literal["imported", "in_ru_not_imported", "no_act_in_ru", "unknown"]

_CATALOG = text(
    """
    SELECT published_act_id, terc, commune, district, voivodeship, status_text, legal_status, publish_date, valid_from,
           last_seen_at
    FROM pog_ru_catalog WHERE is_current ORDER BY terc, published_act_id
    """
)
_IMPORTED = text(
    """
    SELECT left(pa.teryt, 6) AS terc6, pa.act_identifier, pa.teryt, pav.id AS act_version_id, pav.legal_status,
           pav.object_version_id, pav.content_hash, pav.name, pav.legal_valid_from, pav.resolution_date,
           e.ru_published_act_id, e.state_from_id, s.created_at AS state_created_at,
           dr.id AS release_id, dr.version_label AS release_label, sa.fetched_at AS fetched_at,
           (SELECT count(*) FROM pog_map_zones z WHERE z.planning_act_version_id = pav.id) AS zone_count,
           (SELECT count(*) FROM pog_map_overlays o WHERE o.planning_act_version_id = pav.id) AS overlay_count
    FROM pog_registry_entries e
    JOIN planning_acts pa ON pa.id = e.planning_act_id
    JOIN planning_act_versions pav ON pav.id = e.planning_act_version_id
    JOIN pog_registry_states s ON s.id = e.state_from_id
    JOIN data_releases dr ON dr.id = pav.data_release_id
    JOIN source_artifacts sa ON sa.id = pav.source_artifact_id
    WHERE e.state_to_id IS NULL AND pa.kind = 'pog' AND pa.teryt IS NOT NULL
    ORDER BY pa.teryt, pa.act_identifier
    """
)
_JOBS = text(
    """
    SELECT DISTINCT ON (scope_key) scope_key, id, status, attempts, requested_at, finished_at, last_error
    FROM import_jobs WHERE kind = 'pog_commune' ORDER BY scope_key, id DESC
    """
)
_LAST_FAILURES = text(
    """
    SELECT id, stats -> 'failures' AS failures FROM import_runs
    WHERE importer_version = 'pog-national-v1' AND stats ? 'failures' ORDER BY id DESC LIMIT 1
    """
)
_POINT = text(
    """
    SELECT pa.act_identifier, pa.teryt, pav.legal_status, e.state_from_id
    FROM pog_map_acts a
    JOIN planning_act_versions pav ON pav.id = a.planning_act_version_id
    JOIN planning_acts pa ON pa.id = pav.planning_act_id
    JOIN pog_registry_entries e ON e.planning_act_version_id = pav.id AND e.state_to_id IS NULL
    WHERE a.geom_full && ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 3857)
      AND ST_Intersects(a.geom_full, ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 3857))
    ORDER BY CASE pav.legal_status WHEN 'binding' THEN 0 ELSE 1 END, pa.act_identifier
    """
)


class ImportedActResponse(BaseModel):
    act_identifier: str
    act_version: str | None
    data_as_of: date | None = None
    legal_status: str
    name: str | None
    state_id: int = Field(description="Numer stanu krajowego, w którym wersja stała się aktywna.")
    imported_at: datetime = Field(description="Chwila zatwierdzenia stanu, w którym wersja stała się aktywna.")
    fetched_at: datetime | None = Field(description="Czas pobrania artefaktu źródłowego (GML).")
    release_id: int
    release_label: str
    content_hash: str
    ru_published_act_id: int | None
    up_to_date: bool | None = Field(
        description="true — wersja zgadza się z wykazem RU; false — RU ma nowszą wersję (czeka na import); null — brak tokenu RU."
    )
    zone_count: int
    overlay_count: int


class RuActResponse(BaseModel):
    published_act_id: int
    status_text: str | None
    legal_status: str
    publish_date: date | None
    valid_from: date | None


class ImportJobResponse(BaseModel):
    job_id: int
    status: str
    attempts: int
    requested_at: datetime | None
    finished_at: datetime | None
    last_error: str | None


class ImportFailureResponse(BaseModel):
    """Ostatnia nieudana próba importu aktu tej gminy w przebiegu krajowym (np. wada danych źródła)."""

    published_act_id: int
    code: str = Field(description="Stały kod przyczyny: `qa_rejected` (kontrola jakości odrzuciła dane źródła), `rate_limited`, ….")
    message: str | None = None
    run_id: int | None = None


class PogCommuneResponse(BaseModel):
    terc: str
    name: str | None
    district: str | None
    voivodeship: str | None
    status: CommuneStatus
    ru_acts: list[RuActResponse]
    imported_acts: list[ImportedActResponse]
    import_job: ImportJobResponse | None = None
    last_import_failure: ImportFailureResponse | None = None


class PogCoverageSummary(BaseModel):
    ru_communes: int
    imported_communes: int
    in_ru_not_imported: int
    imported_without_ru_entry: int
    outdated_versions: int
    state_id: int | None


class PogCoverageResponse(BaseModel):
    source_id: str = "pog_app"
    checked_at: datetime
    catalog_synced_at: datetime | None
    catalog_fresh: bool
    summary: PogCoverageSummary
    communes: list[PogCommuneResponse]
    note: str = (
        "Stan „brak aktu POG w RU” wynika z wykazu Rejestru Urbanistycznego z podanej daty i nie dowodzi braku planu w gminie. "
        "Brak lokalnych danych (≠ brak planu). Gmina „w RU, niezaimportowana” nie ma jeszcze lokalnych danych — analiza korzysta wtedy z podglądu RU, a import jest zlecany."
    )


class PogCommuneLookup(BaseModel):
    terc: str
    status: CommuneStatus
    catalog_synced_at: datetime | None
    detail: str


class PogPointCoverage(BaseModel):
    lon: float
    lat: float
    covered: bool
    acts: list[dict[str, Any]]
    note: str


def _catalog_state(db: Session) -> tuple[datetime | None, bool]:
    synced = db.execute(text("SELECT max(last_seen_at) FROM pog_ru_catalog")).scalar_one()
    if synced is None:
        return None, False
    age = datetime.now(timezone.utc) - synced
    return synced, age <= timedelta(seconds=max(3 * settings.pog_sync_interval_seconds, 3 * 24 * 3600))


@router.get(
    "/pog",
    response_model=PogCoverageResponse,
    dependencies=[Depends(_coverage_limit)],
    summary="Gminy z planem ogólnym w RU i stan ich importu lokalnego",
)
def get_pog_coverage(
    db: Annotated[Session, Depends(get_db)],
    status: Annotated[CommuneStatus | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=60, description="Fragment nazwy gminy lub powiatu.")] = None,
    voivodeship: Annotated[str | None, Query(max_length=40)] = None,
) -> PogCoverageResponse:
    catalog_rows = db.execute(_CATALOG).mappings().all()
    imported_rows = db.execute(_IMPORTED).mappings().all()
    jobs = {str(row["scope_key"]): row for row in db.execute(_JOBS).mappings()}
    failures_row = db.execute(_LAST_FAILURES).mappings().first()
    failures: dict[str, ImportFailureResponse] = {}
    for item in (failures_row["failures"] if failures_row and isinstance(failures_row["failures"], list) else []):
        if isinstance(item, dict) and item.get("terc") and item.get("published_act_id") is not None:
            failures[str(item["terc"])[:6]] = ImportFailureResponse(
                published_act_id=int(item["published_act_id"]),
                code=str(item.get("code") or "failed"),
                message=(str(item["message"])[:300] if item.get("message") else None),
                run_id=int(failures_row["id"]),
            )
    synced, fresh = _catalog_state(db)
    current_ids = {int(row["published_act_id"]) for row in catalog_rows}

    communes: dict[str, dict[str, Any]] = {}
    for row in catalog_rows:
        terc = str(row["terc"])[:6]
        entry = communes.setdefault(
            terc,
            {"terc": terc, "name": row["commune"], "district": row["district"], "voivodeship": row["voivodeship"],
             "ru_acts": [], "imported_acts": []},
        )
        entry["ru_acts"].append(
            RuActResponse(
                published_act_id=int(row["published_act_id"]), status_text=row["status_text"],
                legal_status=row["legal_status"], publish_date=row["publish_date"], valid_from=row["valid_from"],
            )
        )
    outdated = 0
    for row in imported_rows:
        terc = str(row["terc6"])
        entry = communes.setdefault(
            terc, {"terc": terc, "name": None, "district": None, "voivodeship": None, "ru_acts": [], "imported_acts": []}
        )
        token = row["ru_published_act_id"]
        up_to_date = None if token is None else (int(token) in current_ids if catalog_rows else None)
        if up_to_date is False:
            outdated += 1
        entry["imported_acts"].append(
            ImportedActResponse(
                act_identifier=row["act_identifier"], act_version=row["object_version_id"],
                data_as_of=row["resolution_date"],
                legal_status=row["legal_status"] or "unknown", name=row["name"], state_id=int(row["state_from_id"]),
                imported_at=row["state_created_at"], fetched_at=row["fetched_at"], release_id=int(row["release_id"]),
                release_label=row["release_label"], content_hash=row["content_hash"],
                ru_published_act_id=int(token) if token is not None else None, up_to_date=up_to_date,
                zone_count=int(row["zone_count"]), overlay_count=int(row["overlay_count"]),
            )
        )
    result: list[PogCommuneResponse] = []
    for terc in sorted(communes):
        entry = communes[terc]
        state: CommuneStatus = "imported" if entry["imported_acts"] else "in_ru_not_imported"
        job_row = jobs.get(f"terc:{terc}")
        job = (
            ImportJobResponse(
                job_id=int(job_row["id"]), status=job_row["status"], attempts=int(job_row["attempts"]),
                requested_at=job_row["requested_at"], finished_at=job_row["finished_at"], last_error=job_row["last_error"],
            )
            if job_row is not None and state != "imported"
            else None
        )
        result.append(
            PogCommuneResponse(
                status=state,
                import_job=job,
                last_import_failure=failures.get(terc) if state != "imported" else None,
                **entry,
            )
        )
    if status is not None:
        result = [item for item in result if item.status == status]
    if q:
        needle = q.casefold()
        result = [item for item in result if needle in (item.name or "").casefold() or needle in (item.district or "").casefold()]
    if voivodeship:
        result = [item for item in result if (item.voivodeship or "").casefold() == voivodeship.casefold()]
    imported_communes = sum(1 for item in communes.values() if item["imported_acts"])
    state_id = db.execute(text("SELECT max(id) FROM pog_registry_states")).scalar_one()
    return PogCoverageResponse(
        checked_at=datetime.now(timezone.utc),
        catalog_synced_at=synced,
        catalog_fresh=fresh,
        summary=PogCoverageSummary(
            ru_communes=sum(1 for item in communes.values() if item["ru_acts"]),
            imported_communes=imported_communes,
            in_ru_not_imported=sum(1 for item in communes.values() if item["ru_acts"] and not item["imported_acts"]),
            imported_without_ru_entry=sum(1 for item in communes.values() if item["imported_acts"] and not item["ru_acts"]),
            outdated_versions=outdated,
            state_id=int(state_id) if state_id is not None else None,
        ),
        communes=result,
    )


@router.get(
    "/pog/commune/{terc}",
    response_model=PogCommuneLookup,
    dependencies=[Depends(_coverage_limit)],
    summary="Stan jednej gminy (kod TERC): zaimportowana, plan w RU, brak aktu w RU albo nieustalone",
)
def get_pog_commune_state(terc: str, db: Annotated[Session, Depends(get_db)]) -> PogCommuneLookup:
    code = terc.strip()[:6]
    if not (code.isdigit() and len(code) == 6):
        return PogCommuneLookup(terc=terc, status="unknown", catalog_synced_at=None, detail="Kod TERC musi mieć 6 cyfr.")
    synced, fresh = _catalog_state(db)
    imported = db.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM pog_registry_entries e JOIN planning_acts pa ON pa.id = e.planning_act_id "
            "WHERE e.state_to_id IS NULL AND pa.kind = 'pog' AND pa.teryt LIKE :prefix)"
        ),
        {"prefix": f"{code}%"},
    ).scalar_one()
    if imported:
        return PogCommuneLookup(terc=code, status="imported", catalog_synced_at=synced, detail="Gmina ma lokalne dane POG.")
    in_catalog = db.execute(
        text("SELECT EXISTS (SELECT 1 FROM pog_ru_catalog WHERE is_current AND terc LIKE :prefix)"), {"prefix": f"{code}%"}
    ).scalar_one()
    if in_catalog:
        return PogCommuneLookup(
            terc=code, status="in_ru_not_imported", catalog_synced_at=synced,
            detail="Rejestr Urbanistyczny publikuje plan ogólny tej gminy; import lokalny jeszcze nie został wykonany.",
        )
    if not fresh:
        return PogCommuneLookup(
            terc=code, status="unknown", catalog_synced_at=synced,
            detail="Wykaz Rejestru Urbanistycznego nie jest zsynchronizowany albo jest przeterminowany — stanu nie ustalono.",
        )
    return PogCommuneLookup(
        terc=code, status="no_act_in_ru", catalog_synced_at=synced,
        detail="W zsynchronizowanym wykazie RU nie ma aktu POG tej gminy (to nie dowodzi braku planu).",
    )


@router.get(
    "/pog/point",
    response_model=PogPointCoverage,
    dependencies=[Depends(_coverage_limit)],
    summary="Czy punkt leży w zasięgu zaimportowanego aktu POG",
)
def get_pog_point_coverage(
    db: Annotated[Session, Depends(get_db)],
    lon: Annotated[float, Query(ge=-180.0, le=180.0)],
    lat: Annotated[float, Query(ge=-90.0, le=90.0)],
) -> PogPointCoverage:
    rows = db.execute(_POINT, {"lon": lon, "lat": lat}).mappings().all()
    return PogPointCoverage(
        lon=lon, lat=lat, covered=bool(rows),
        acts=[
            {"act_identifier": row["act_identifier"], "teryt": row["teryt"], "legal_status": row["legal_status"],
             "state_id": int(row["state_from_id"])}
            for row in rows
        ],
        note=(
            "Punkt poza zasięgiem zaimportowanych aktów POG: gmina może nie być zaimportowana albo nie mieć planu w RU — "
            "brak lokalnych danych nie oznacza braku planu ani braku ograniczeń."
        ),
    )
