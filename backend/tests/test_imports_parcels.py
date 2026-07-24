"""Potok importu działek: domena, adapter OGR i publikacja PostGIS."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from shapely import from_wkt
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

import app.modules.imports.composition as import_composition
from app.core.data_sources import DataSourceEntry
from app.db.session import SessionLocal
from app.models.parcel import Parcel
from app.models.versioned import ImportRun, ParcelVersion
from app.modules.imports.api.cli import parse_args
from app.modules.imports.application.common import (
    ImportOutcome,
    ImportRelease,
    ImportSourceNotRunnable,
)
from app.modules.imports.application.parcels_import import (
    ParcelSourceBatch,
    PublicationResult,
    RepairedGeometry,
    run_parcel_import,
)
from app.modules.imports.domain.parcels import (
    ParcelNormalizationError,
    ParcelRecord,
    area_is_within_tolerance,
    duplicate_identifiers,
    normalize_parcel,
    teryt_is_in_scope,
)
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.parcels.reader import PyogrioParcelReader
from app.modules.imports.infrastructure.repository import SqlAlchemyImportRepository
from app.modules.imports.infrastructure.vector import _normalize_crs
from app.shared.geometry import GeometryPayload


FIXTURES = Path(__file__).parent / "fixtures" / "imports" / "parcels"
NOW = datetime(2026, 7, 24, tzinfo=timezone.utc)


class StaticReader:
    def __init__(self, *records: ParcelRecord, content: bytes = b"fixture") -> None:
        self.records = records
        self.content = content

    def read(self) -> ParcelSourceBatch:
        return ParcelSourceBatch(
            content=self.content,
            filename="fixture.gml",
            media_type="application/gml+xml",
            records=tuple(self.records),
        )


class FakeRepository:
    area_tolerance_ratio = 0.02
    overlap_tolerance_sqm = 0.01

    def __init__(self, overlaps: set[str] | None = None) -> None:
        self.overlaps = overlaps or set()
        self.published = ()

    def repair_geometry(self, geometry: GeometryPayload):
        shape = from_wkt(geometry.wkt)
        if shape.is_empty:
            return None
        digest = hashlib.sha256(shape.wkb).hexdigest()
        return RepairedGeometry(
            GeometryPayload(shape.wkt), shape.area, digest, not shape.is_valid
        )

    def overlapping_identifiers(self, parcels):
        return self.overlaps

    def publish_parcels(
        self, *, release, batch, artifact_hash, parcels, stats, warnings
    ):
        self.published = parcels
        return PublicationResult(
            new=len(parcels),
            changed=0,
            unchanged=0,
            import_run_id=10,
            data_release_id=20,
        )


def parcel(
    identifier: str = "146501_8.0001.1",
    *,
    geometry: str = "POLYGON((0 0,10 0,10 10,0 10,0 0))",
    area: float | None = 100.0,
) -> ParcelRecord:
    return ParcelRecord(
        parcel_identifier=identifier,
        number="1",
        sheet=None,
        precinct=" Test ",
        cadastral_unit=" Warszawa ",
        teryt="1465011",
        geometry=GeometryPayload(geometry),
        reported_area_sqm=area,
    )


def release(
    source_id: str = "fixture",
    *,
    allowed: bool = True,
    dry_run: bool = False,
    label: str = "v1",
) -> ImportRelease:
    return ImportRelease(
        source_id,
        label,
        NOW,
        publication_allowed=allowed,
        dry_run=dry_run,
        teryt_scope=("1465011",),
    )


def test_normalization_cleans_values_and_validates_number() -> None:
    normalized = normalize_parcel(parcel())
    assert normalized.precinct == "Test"
    assert normalized.cadastral_unit == "Warszawa"
    bad = parcel()
    bad = ParcelRecord(**{**bad.__dict__, "number": "abc"})
    with pytest.raises(ParcelNormalizationError):
        normalize_parcel(bad)


def test_normalization_rejects_identifier_area_and_teryt() -> None:
    with pytest.raises(ParcelNormalizationError):
        normalize_parcel(ParcelRecord(**{**parcel().__dict__, "parcel_identifier": "x y"}))
    with pytest.raises(ParcelNormalizationError):
        normalize_parcel(ParcelRecord(**{**parcel().__dict__, "reported_area_sqm": 0}))
    with pytest.raises(ParcelNormalizationError):
        normalize_parcel(ParcelRecord(**{**parcel().__dict__, "teryt": "abc"}))


def test_domain_qa_helpers() -> None:
    first = normalize_parcel(parcel())
    assert duplicate_identifiers([first, first]) == {first.parcel_identifier}
    assert area_is_within_tolerance(100, 101, 0.02)
    assert not area_is_within_tolerance(100, 103, 0.02)
    assert area_is_within_tolerance(None, 999, 0)
    with pytest.raises(ValueError):
        area_is_within_tolerance(100, 100, -1)
    assert teryt_is_in_scope("1465011", ("1465011",)) is True
    assert teryt_is_in_scope("1261011", ("1465011",)) is False
    assert teryt_is_in_scope(None, ("1465011",)) is None


def test_non_runnable_requires_explicit_dry_run() -> None:
    with pytest.raises(ImportSourceNotRunnable, match="DRY-RUN ONLY"):
        run_parcel_import(StaticReader(parcel()), release(allowed=False), FakeRepository())
    result = run_parcel_import(
        StaticReader(parcel()),
        release(allowed=False, dry_run=True),
        FakeRepository(),
    )
    assert result.status == "dry_run_only"
    assert result.stats["publishable"] == 1


def test_qa_rejects_duplicates_area_empty_and_overlap() -> None:
    duplicate = parcel()
    wrong_area = parcel("146501_8.0001.2", area=1)
    empty = parcel("146501_8.0001.3", geometry="POLYGON EMPTY", area=None)
    overlap = parcel("146501_8.0001.4")
    repository = FakeRepository(overlaps={overlap.parcel_identifier})
    result = run_parcel_import(
        StaticReader(duplicate, duplicate, wrong_area, empty, overlap),
        release(dry_run=True),
        repository,
    )
    assert result.stats["input"] == 5
    assert result.stats["rejected"] == 5
    assert {"duplicate_parcel_identifier", "parcel_overlaps"} <= set(result.warnings)


def test_success_maps_publication_counts() -> None:
    repository = FakeRepository()
    result = run_parcel_import(StaticReader(parcel()), release(), repository)
    assert result.status == "succeeded"
    assert result.stats["new"] == 1
    assert result.import_run_id == 10
    assert len(repository.published) == 1


def test_cli_parser_supports_required_contract() -> None:
    parsed = parse_args(
        ["parcels", "--source", "egib_geometry_warsaw", "--dry-run", "--input", "x.gml"]
    )
    assert parsed.name == "parcels"
    assert parsed.dry_run is True
    assert parsed.input_path == "x.gml"


@pytest.mark.parametrize(
    ("status", "expected_commits", "expected_rollbacks"),
    (("succeeded", 1, 0), ("dry_run_only", 0, 1)),
)
def test_composition_closes_command_transaction(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected_commits: int,
    expected_rollbacks: int,
) -> None:
    source = _source("fixture_composition")

    class FakeSession:
        commits = 0
        rollbacks = 0

        def commit(self) -> None:
            self.commits += 1

        def rollback(self) -> None:
            self.rollbacks += 1

    session = FakeSession()
    monkeypatch.setattr(
        import_composition,
        "get_catalog",
        lambda: SimpleNamespace(get=lambda _source_id: source),
    )
    monkeypatch.setattr(
        import_composition, "ensure_source_runnable", lambda _source_id: source
    )
    monkeypatch.setattr(
        import_composition, "PyogrioParcelReader", lambda *a, **k: object()
    )
    monkeypatch.setattr(import_composition, "_repository", lambda *a: object())
    monkeypatch.setattr(
        import_composition,
        "run_parcel_import",
        lambda *a: ImportOutcome(status=status, stats={}),
    )

    outcome = import_composition.run_parcels_command(
        session,
        source_id=source.source_id,
        dry_run=status != "succeeded",
        input_path="fixture.geojson",
    )
    assert outcome.status == status
    assert session.commits == expected_commits
    assert session.rollbacks == expected_rollbacks


def test_composition_rolls_back_failed_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source("fixture_composition_error")

    class FakeSession:
        rollbacks = 0

        def rollback(self) -> None:
            self.rollbacks += 1

    session = FakeSession()
    monkeypatch.setattr(
        import_composition,
        "get_catalog",
        lambda: SimpleNamespace(get=lambda _source_id: source),
    )
    monkeypatch.setattr(
        import_composition, "ensure_source_runnable", lambda _source_id: source
    )
    monkeypatch.setattr(
        import_composition, "PyogrioParcelReader", lambda *a, **k: object()
    )
    monkeypatch.setattr(import_composition, "_repository", lambda *a: object())

    def fail(*_args):
        raise RuntimeError("publication failed")

    monkeypatch.setattr(import_composition, "run_parcel_import", fail)
    with pytest.raises(RuntimeError, match="publication failed"):
        import_composition.run_parcels_command(
            session,
            source_id=source.source_id,
            dry_run=False,
            input_path="fixture.geojson",
        )
    assert session.rollbacks == 1


def test_local_artifact_store_deduplicates(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    first = store.save(
        source_id="fixture",
        content_hash="abc",
        filename="../data.gml",
        content=b"one",
    )
    second = store.save(
        source_id="fixture",
        content_hash="abc",
        filename="data.gml",
        content=b"two",
    )
    assert first == second
    assert Path(first).read_bytes() == b"one"


def test_pyogrio_reader_maps_fixture() -> None:
    pytest.importorskip("pyogrio")
    reader = PyogrioParcelReader(
        FIXTURES / "parcels_2180.geojson",
        field_mapping={
            "parcel_identifier": "ID_DZIALKI",
            "number": "NUMER_DZIALKI",
            "precinct": "NAZWA_OBREBU",
            "cadastral_unit": "NAZWA_GMINY",
            "reported_area_sqm": "POLE_EWIDENCYJNE",
        },
        teryt="1465011",
        declared_crs="EPSG:2180",
    )
    batch = reader.read()
    assert len(batch.records) == 2
    assert batch.records[0].parcel_identifier == "146501_8.0001.1"
    assert batch.records[0].reported_area_sqm == 100


def test_crs_detection_handles_urn_and_rejects_unknown_value() -> None:
    assert _normalize_crs("urn:ogc:def:crs:EPSG::2178") == "EPSG:2178"
    assert _normalize_crs("lokalny-uklad-bez-kontraktu") is None


def _source(source_id: str) -> DataSourceEntry:
    return DataSourceEntry.model_validate(
        {
            "source_id": source_id,
            "name": "Fixture działek",
            "owner": "Test",
            "status": "production",
            "production_ready": True,
            "contract_confirmed": True,
            "access_type": "file",
            "capabilities_url": None,
            "file_url": "file:///fixture.gml",
            "type_names": None,
            "layers": None,
            "protocol_version": "fixture/1",
            "source_crs": "EPSG:2180",
            "target_crs": "EPSG:2180",
            "teryt_scope": ["1465011"],
            "license": "Fixture syntetyczny.",
            "attribution": "Test.",
            "expected_update_interval": "never",
            "sla": "test",
            "last_manual_verification": "2026-07-24",
        }
    )


@pytest.fixture
def session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.mark.integration
def test_postgis_pipeline_is_idempotent_and_versions_changes(
    session: Session, tmp_path: Path
) -> None:
    source_id = f"parcel_fixture_{uuid4().hex[:10]}"
    repository = SqlAlchemyImportRepository(
        session, _source(source_id), LocalArtifactStore(tmp_path)
    )
    identifier = f"parcel-{uuid4().hex[:10]}"
    first_reader = StaticReader(parcel(identifier), content=b"first")

    first = run_parcel_import(first_reader, release(source_id), repository)
    second = run_parcel_import(first_reader, release(source_id), repository)
    assert first.stats["new"] == 1
    assert second.stats["unchanged"] == 1
    assert session.scalar(
        select(func.count()).select_from(ParcelVersion).where(
            ParcelVersion.parcel_id
            == session.execute(
                text("SELECT id FROM parcels WHERE parcel_identifier=:identifier"),
                {"identifier": identifier},
            ).scalar_one()
        )
    ) == 1

    changed_reader = StaticReader(
        parcel(
            identifier,
            geometry="POLYGON((0 0,20 0,20 10,0 10,0 0))",
            area=200,
        ),
        content=b"changed",
    )
    changed = run_parcel_import(
        changed_reader, release(source_id, label="v2"), repository
    )
    assert changed.stats["changed"] == 1
    versions = session.execute(
        select(ParcelVersion)
        .join_from(ParcelVersion, Parcel)
        .where(Parcel.parcel_identifier == identifier)
        .order_by(ParcelVersion.valid_from)
    ).scalars().all()
    assert len(versions) == 2
    assert versions[0].valid_to is not None
    assert versions[1].valid_to is None

    srid, empty = session.execute(
        text(
            """
            SELECT ST_SRID(geometry), ST_IsEmpty(geometry)
            FROM parcel_versions WHERE id=:id
            """
        ),
        {"id": versions[1].id},
    ).one()
    assert srid == 2180
    assert empty is False
    run = session.get(ImportRun, changed.import_run_id)
    assert run is not None and run.stats["changed"] == 1
