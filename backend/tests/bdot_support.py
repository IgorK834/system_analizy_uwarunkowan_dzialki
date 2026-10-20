"""Wspólne narzędzia testów BDOT10k (Task 23.1–23.3): zamrożone paczki powiatów, import do bazy i sprzątanie.

Fixtures w ``tests/fixtures/bdot10k/<powiat>/`` są dosłownymi fragmentami oryginalnych paczek powiatowych (patrz
``scripts/build_bdot10k_fixtures.py``) razem z wartościami oczekiwanymi policzonymi niezależnie od aplikacji
(``expected.json``). Import idzie prawdziwym przypadkiem użycia, czytnikiem GDAL i repozytorium PostGIS.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.data_sources import get_catalog
from app.db.session import SessionLocal
from app.models.bdot10k import BdotBuilding, BdotRelease, BdotRoad, BdotLandCover
from app.models.bdot_powerline import BdotPowerline
from app.models.versioned import DataRelease, DataSource, ImportRun, SourceArtifact
from app.modules.imports.application.bdot10k_import import run_bdot10k_import
from app.modules.imports.application.common import ImportOutcome, ImportRelease
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.bdot10k.geometry import ShapelyGeometryTools
from app.modules.imports.infrastructure.bdot10k.reader import ZipBdot10kReader
from app.modules.imports.infrastructure.bdot10k.repository import SqlAlchemyBdot10kRepository

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "bdot10k"
CORPUS_DIR = Path(__file__).resolve().parent / "fixtures" / "reference_corpus"
SOURCE_ID = "bdot10k"
POWIATS = ("0221", "0262", "1261", "1465", "2461", "2816")
IMPORTED_AT = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)


def package_path(teryt: str) -> Path:
    return FIXTURES_DIR / teryt / f"{teryt}_GML.zip"


@lru_cache(maxsize=None)
def expected(teryt: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / teryt / "expected.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def corpus_parcels() -> dict[str, BaseGeometry]:
    """Działki korpusu (EPSG:2180) po identyfikatorze przypadku."""
    manifest = json.loads((CORPUS_DIR / "manifest.json").read_text(encoding="utf-8"))
    parcels: dict[str, BaseGeometry] = {}
    for case in manifest["cases"]:
        path = CORPUS_DIR / "artifacts" / "parcels" / f"{case['case_id']}.geojson"
        parcels[case["case_id"]] = shape(json.loads(path.read_text(encoding="utf-8"))["geometry"])
    return parcels


def case_ids(teryt: str) -> list[str]:
    return sorted(expected(teryt)["cases"])


def import_package(
    session: Session, teryt: str, store_dir: Path, *, package: Path | None = None, dry_run: bool = False
) -> ImportOutcome:
    """Importuje zamrożoną paczkę (albo wskazany plik) prawdziwym przypadkiem użycia; zatwierdza transakcję."""
    source = get_catalog().get(SOURCE_ID)
    release = ImportRelease(
        source_id=SOURCE_ID,
        version_label="test",
        published_at=IMPORTED_AT,
        publication_allowed=True,
        dry_run=dry_run,
    )
    outcome = run_bdot10k_import(
        ZipBdot10kReader(package or package_path(teryt), teryt_powiat=teryt, mapping=source.field_mapping),
        release,
        ShapelyGeometryTools(),
        None if dry_run else SqlAlchemyBdot10kRepository(session, source, LocalArtifactStore(store_dir)),
        source.field_mapping,
        teryt_powiat=teryt,
    )
    if outcome.status == "succeeded":
        session.commit()
    else:
        session.rollback()
    return outcome


def purge_bdot(session: Session) -> None:
    """Usuwa wszystkie dane źródła ``bdot10k`` (obiekty, opisy wydań, przebiegi, artefakty, wydania)."""
    source_row = session.execute(select(DataSource.id).where(DataSource.source_id == SOURCE_ID)).scalar_one_or_none()
    if source_row is None:
        return
    release_ids = select(DataRelease.id).where(DataRelease.data_source_id == source_row)
    session.execute(delete(BdotPowerline).where(BdotPowerline.data_release_id.in_(release_ids)))
    session.execute(delete(BdotLandCover).where(BdotLandCover.data_release_id.in_(release_ids)))
    session.execute(delete(BdotBuilding).where(BdotBuilding.data_release_id.in_(release_ids)))
    session.execute(delete(BdotRoad).where(BdotRoad.data_release_id.in_(release_ids)))
    session.execute(delete(BdotRelease).where(BdotRelease.data_release_id.in_(release_ids)))
    session.execute(delete(ImportRun).where(ImportRun.data_source_id == source_row))
    session.execute(delete(DataRelease).where(DataRelease.data_source_id == source_row))
    session.execute(delete(SourceArtifact).where(SourceArtifact.data_source_id == source_row))
    session.commit()


@contextmanager
def installed_releases(store_dir: Path, *powiats: str) -> Iterator[None]:
    """Czyste środowisko z aktywnymi wydaniami wskazanych powiatów; po teście dane są usuwane."""
    with SessionLocal() as session:
        purge_bdot(session)
        for teryt in powiats:
            import_package(session, teryt, store_dir)
    try:
        yield
    finally:
        with SessionLocal() as session:
            purge_bdot(session)
