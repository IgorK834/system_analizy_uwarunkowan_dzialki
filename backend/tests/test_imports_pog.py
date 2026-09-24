"""Import wersjonowany POG: domena, adapter APP/GML, bezpieczeństwo i PostGIS."""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Iterator
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from shapely import from_wkt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.data_sources import DataSourceEntry
from app.db.session import SessionLocal
from app.models.versioned import (
    PlanBoundary,
    PlanningAct,
    PlanningActVersion,
    PlanningFeature,
)
from app.modules.imports.api.cli import parse_args
from app.modules.imports.application.common import (
    ImportOutcome,
    ImportRelease,
    ImportSourceNotRunnable,
)
from app.modules.imports import composition
from app.modules.imports.application.parcels_import import RepairedGeometry
from app.modules.imports.application.pog_import import (
    PogPublicationResult,
    PogSourceBatch,
    run_pog_import,
)
from app.modules.imports.domain.pog import (
    POG_FEATURE_TYPES,
    PogActRecord,
    PogFeatureRecord,
    PogValidationError,
    normalize_feature_type,
    normalize_legal_status,
)
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.pog.reader import (
    PogActMetadata,
    PogLayerResource,
    PyogrioPogReader,
    WfsPogReader,
    read_pog_archive,
)
from app.modules.imports.infrastructure.repository import (
    SqlAlchemyImportRepository,
    find_pog_intersections,
)
from app.modules.imports.infrastructure.vector import VectorReadError
from app.shared.geometry import GeometryPayload
from app.shared.safe_archive import UnsafeArchiveError


FIXTURES = Path(__file__).parent / "fixtures" / "imports" / "pog"
NOW = datetime(2026, 7, 25, tzinfo=timezone.utc)


def geom(wkt: str) -> GeometryPayload:
    return GeometryPayload(wkt)


def feature(feature_type: str, wkt: str, **attrs: object) -> PogFeatureRecord:
    return PogFeatureRecord(feature_type, geom(wkt), attrs)


def act(
    identifier: str = "pog-a",
    *,
    legal_status: str = "adopted",
    boundary: str | None = "POLYGON((0 0,100 0,100 100,0 100,0 0))",
    features: tuple[PogFeatureRecord, ...] | None = None,
) -> PogActRecord:
    return PogActRecord(
        act_identifier=identifier,
        resolution_number="I/1/2026",
        resolution_date=date(2026, 1, 10),
        teryt="1261011",
        name="POG testowy",
        legal_status=legal_status,
        boundary=geom(boundary) if boundary is not None else None,
        features=features
        if features is not None
        else (
            feature(
                "planning_zone", "POLYGON((0 0,50 0,50 100,0 100,0 0))", SYMBOL="SJ-01"
            ),
            feature("ouz", "POLYGON((0 0,40 0,40 40,0 40,0 0))"),
            feature("downtown_area", "POLYGON((0 0,20 0,20 20,0 20,0 0))"),
            feature(
                "social_infrastructure_standard",
                "POLYGON((0 0,100 0,100 100,0 100,0 0))",
            ),
        ),
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
        teryt_scope=("1261011",),
    )


class StaticPogReader:
    def __init__(self, *acts: PogActRecord, content: bytes = b"pog") -> None:
        self.acts = acts
        self.content = content

    def read(self) -> PogSourceBatch:
        return PogSourceBatch(
            self.content,
            "pog.gml",
            "application/gml+xml",
            tuple(self.acts),
        )


class FakePogRepository:
    def __init__(self) -> None:
        self.published: tuple[tuple[PogActRecord, str], ...] = ()

    def repair_geometry(self, geometry: GeometryPayload):
        shape = from_wkt(geometry.wkt)
        if shape.is_empty:
            return None
        return RepairedGeometry(
            GeometryPayload(shape.wkt),
            shape.area,
            hashlib.sha256(shape.wkb).hexdigest(),
            False,
        )

    def publish_pog(self, *, release, batch, artifact_hash, acts, stats, warnings):
        self.published = acts
        return PogPublicationResult(len(acts), 0, 0, 1, 2)


# --- Domena ------------------------------------------------------------------


def test_normalize_feature_type_maps_known_layers_and_rejects_unknown() -> None:
    assert normalize_feature_type("strefaPlanistyczna") == "planning_zone"
    assert normalize_feature_type("obszarUzupelnieniaZabudowy") == "ouz"
    assert normalize_feature_type("obszarZabSrodmiejskiej") == "downtown_area"
    assert (
        normalize_feature_type("standardyDostepnosciInfrastrukturySpolecznej")
        == "social_infrastructure_standard"
    )
    assert normalize_feature_type("cos_nieznanego") is None
    assert normalize_feature_type(None, {"typ": "OUZ"}) == "ouz"


def test_normalize_legal_status_never_maps_unknown_to_adopted() -> None:
    assert normalize_legal_status("uchwalony") == "adopted"
    assert normalize_legal_status("projekt") == "project"
    assert normalize_legal_status("w trakcie sporządzania") == "in_progress"
    assert normalize_legal_status(None) == "not_available"
    assert normalize_legal_status("cokolwiek dziwnego") == "not_available"


def test_is_binding_only_for_adopted() -> None:
    assert act(legal_status="adopted").is_binding is True
    assert act(legal_status="project").is_binding is False
    assert act(legal_status="in_progress").is_binding is False
    assert act(legal_status="not_available").is_binding is False


def test_adopted_act_requires_resolution_metadata() -> None:
    invalid = PogActRecord("pog-x", None, None, "1261011", None, "adopted", None, ())
    with pytest.raises(PogValidationError):
        invalid.validate()
    # Projekt bez uchwały jest dopuszczalny — nie jest prezentowany jako wiążący.
    PogActRecord("pog-x", None, None, "1261011", None, "project", None, ()).validate()


def test_feature_record_rejects_unknown_type() -> None:
    with pytest.raises(PogValidationError):
        PogFeatureRecord("nieznany_typ", geom("POLYGON EMPTY")).validate()


def test_feature_counts_covers_all_four_layers() -> None:
    counts = act().feature_counts()
    assert set(counts) == set(POG_FEATURE_TYPES)
    assert counts == {
        "planning_zone": 1,
        "ouz": 1,
        "downtown_area": 1,
        "social_infrastructure_standard": 1,
    }


# --- Aplikacja ---------------------------------------------------------------


def test_research_source_is_dry_run_only() -> None:
    repository = FakePogRepository()
    with pytest.raises(ImportSourceNotRunnable):
        run_pog_import(
            StaticPogReader(act()),
            "pog_pilot_krakow",
            repository,
            release=release("pog_pilot_krakow", allowed=False),
        )
    outcome = run_pog_import(
        StaticPogReader(act()),
        "pog_pilot_krakow",
        repository,
        release=release("pog_pilot_krakow", allowed=False, dry_run=True),
    )
    assert outcome.status == "dry_run_only"
    assert outcome.stats["publishable"] == 1
    assert repository.published == ()


def test_pog_command_builds_wfs_reader_from_catalog_and_filters_teryt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(reader, source_id, repository, *, release):
        captured.update(reader=reader, source_id=source_id, release=release)
        return ImportOutcome(status="succeeded", stats={})

    monkeypatch.setattr(composition, "run_pog_import", fake_run)
    monkeypatch.setattr(composition, "_repository", lambda *_args: object())
    session = MagicMock()

    outcome = composition.run_pog_command(
        session,
        source_id="pog_app",
        dry_run=True,
        act_identifier="246101-POG",
        teryt="246101",
        legal_status="adopted",
    )

    assert outcome.status == "succeeded"
    reader = captured["reader"]
    assert isinstance(reader, WfsPogReader)
    resources = [resource for _feature_type, resource in reader._resources]
    catalog_wfs = next(
        item
        for item in composition.get_catalog().get("pog_app").resources
        if item.role == "ru_wfs"
    )
    assert {resource.url for resource in resources} == {catalog_wfs.url}
    assert {feature_type for feature_type, _resource in reader._resources} == {
        "planning_zone",
        "ouz",
        "downtown_area",
        "social_infrastructure_standard",
    }
    assert all("%246101%" in resource.extra_params["FILTER"] for resource in resources)
    assert all(
        "/app/3.0" in resource.extra_params["namespaces"] for resource in resources
    )
    session.commit.assert_called_once()


def test_publish_separates_four_layers_and_reports_status() -> None:
    repository = FakePogRepository()
    outcome = run_pog_import(
        StaticPogReader(act()), "fixture", repository, release=release()
    )
    assert outcome.status == "succeeded"
    assert outcome.stats["new"] == 1
    # Cztery warstwy rozdzielone i policzone.
    assert outcome.stats["feature:planning_zone"] == 1
    assert outcome.stats["feature:ouz"] == 1
    assert outcome.stats["feature:downtown_area"] == 1
    assert outcome.stats["feature:social_infrastructure_standard"] == 1
    assert outcome.stats["legal_status:adopted"] == 1


def test_project_status_is_flagged_and_never_binding() -> None:
    repository = FakePogRepository()
    project = act("pog-project", legal_status="project")
    outcome = run_pog_import(
        StaticPogReader(project), "fixture", repository, release=release()
    )
    # Assert na polu statusu w wyniku, nie tylko w komentarzu.
    published_act, _hash = repository.published[0]
    assert published_act.legal_status == "project"
    assert published_act.is_binding is False
    assert outcome.stats["legal_status:project"] == 1
    assert any(
        warning.startswith("pog_not_binding:pog-project:project")
        for warning in outcome.warnings
    )


def test_snapshot_hash_is_order_independent_and_covers_raw_attributes() -> None:
    base = act()
    first_repo = FakePogRepository()
    run_pog_import(StaticPogReader(base), "fixture", first_repo, release=release())
    first_hash = first_repo.published[0][1]

    reordered = PogActRecord(
        **{**base.__dict__, "features": tuple(reversed(base.features))}
    )
    reordered_repo = FakePogRepository()
    run_pog_import(
        StaticPogReader(reordered), "fixture", reordered_repo, release=release()
    )
    assert reordered_repo.published[0][1] == first_hash

    changed_feature = PogFeatureRecord(
        base.features[0].feature_type,
        base.features[0].geometry,
        {"changed": True},
    )
    changed = PogActRecord(
        **{**base.__dict__, "features": (changed_feature, *base.features[1:])}
    )
    changed_repo = FakePogRepository()
    run_pog_import(StaticPogReader(changed), "fixture", changed_repo, release=release())
    assert changed_repo.published[0][1] != first_hash


def test_empty_feature_is_rejected_without_aborting_act() -> None:
    repository = FakePogRepository()
    features = (
        feature("planning_zone", "POLYGON((0 0,50 0,50 100,0 100,0 0))"),
        feature("ouz", "POLYGON EMPTY"),
    )
    outcome = run_pog_import(
        StaticPogReader(act(features=features)),
        "fixture",
        repository,
        release=release(),
    )
    assert outcome.stats["rejected"] == 1
    assert any(warning.startswith("empty_feature:") for warning in outcome.warnings)


# --- Adapter APP/GML (pyogrio) -----------------------------------------------


def _layers(gmina: str, crs: str) -> tuple[PogLayerResource, ...]:
    return tuple(
        PogLayerResource(
            feature_type, FIXTURES / gmina / f"{feature_type}.geojson", crs, {}
        )
        for feature_type in POG_FEATURE_TYPES
    )


def _metadata(
    identifier: str = "pog-krakow", status: str = "adopted"
) -> PogActMetadata:
    return PogActMetadata(
        act_identifier=identifier,
        teryt="1261011",
        legal_status=status,
        resolution_number="I/1/2026",
        resolution_date=date(2026, 1, 10),
        name="POG",
    )


def test_pyogrio_reader_schema_a_krakow() -> None:
    pytest.importorskip("pyogrio")
    reader = PyogrioPogReader(_layers("krakow", "EPSG:2178"), metadata=_metadata())
    batch = reader.read()
    assert len(batch.acts) == 1
    act_record = batch.acts[0]
    assert act_record.feature_counts() == {
        "planning_zone": 2,
        "ouz": 1,
        "downtown_area": 1,
        "social_infrastructure_standard": 1,
    }
    zone = next(f for f in act_record.features if f.feature_type == "planning_zone")
    assert zone.raw_attributes["SYMBOL"] == "SJ-01"
    assert zone.geometry.crs == "EPSG:2178"


def test_pyogrio_reader_schema_b_wroclaw() -> None:
    pytest.importorskip("pyogrio")
    reader = PyogrioPogReader(
        _layers("wroclaw", "EPSG:2180"),
        metadata=_metadata("pog-wroclaw"),
    )
    act_record = reader.read().acts[0]
    zone = next(f for f in act_record.features if f.feature_type == "planning_zone")
    # Inny schemat atrybutów jest zachowany bez zmian.
    assert zone.raw_attributes["zone_code"] == "SW/1"
    assert zone.geometry.crs == "EPSG:2180"


def test_reader_rejects_unknown_or_mismatched_crs() -> None:
    pytest.importorskip("pyogrio")
    reader = PyogrioPogReader(
        (
            PogLayerResource(
                "planning_zone", FIXTURES / "unknown_crs.geojson", "EPSG:2178", {}
            ),
        ),
        metadata=_metadata(),
    )
    with pytest.raises(VectorReadError):
        reader.read()


# --- Bezpieczeństwo archiwum (Zip Slip, zip bomb) ----------------------------


def test_read_pog_archive_rejects_zip_slip() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escape.gml", b"<gml/>")
    with pytest.raises(UnsafeArchiveError, match="Zip Slip"):
        read_pog_archive(
            buffer.getvalue(),
            (PogLayerResource("planning_zone", Path("x.gml"), "EPSG:2180", {}),),
            metadata=_metadata(),
        )


def test_read_pog_archive_rejects_zip_bomb() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("strefy.gml", b"\x00" * (2 * 1024 * 1024))
    with pytest.raises(UnsafeArchiveError, match="współczynnik kompresji"):
        read_pog_archive(
            buffer.getvalue(),
            (PogLayerResource("planning_zone", Path("x.gml"), "EPSG:2180", {}),),
            metadata=_metadata(),
        )


# --- CLI ---------------------------------------------------------------------


def test_cli_parser_supports_pog_subcommand() -> None:
    parsed = parse_args(
        [
            "pog",
            "--source",
            "pog_pilot_krakow",
            "--dry-run",
            "--resource",
            "planning_zone=strefy.gml",
            "--resource",
            "ouz=ouz.gml",
            "--act-id",
            "pog-krakow",
            "--resolution-number",
            "I/1/2026",
            "--resolution-date",
            "2026-01-10",
            "--legal-status",
            "adopted",
            "--teryt",
            "1261011",
        ]
    )
    assert parsed.name == "pog"
    assert parsed.resources == (
        ("planning_zone", "strefy.gml"),
        ("ouz", "ouz.gml"),
    )
    assert parsed.act_identifier == "pog-krakow"
    assert parsed.legal_status == "adopted"
    assert parsed.resolution_date == date(2026, 1, 10)


# --- Guard CRS repozytorium (bez DB) -----------------------------------------


def test_repository_repair_geometry_rejects_disallowed_crs() -> None:
    repository = SqlAlchemyImportRepository(
        session=None, source=None, artifact_store=None
    )
    with pytest.raises(ValueError, match="Nieznany CRS"):
        repository.repair_geometry(
            GeometryPayload("POLYGON((0 0,1 0,1 1,0 1,0 0))", crs="EPSG:31337")
        )


# --- Integracja PostGIS ------------------------------------------------------


def _source(source_id: str) -> DataSourceEntry:
    return DataSourceEntry.model_validate(
        {
            "source_id": source_id,
            "name": "Fixture POG",
            "owner": "Test",
            "status": "production",
            "production_ready": True,
            "contract_confirmed": True,
            "access_type": "app_gml",
            "capabilities_url": None,
            "file_url": "file:///fixture.gml",
            "type_names": ["strefaPlanistyczna"],
            "layers": None,
            "protocol_version": "fixture/1",
            "source_crs": "EPSG:2180",
            "target_crs": "EPSG:2180",
            "teryt_scope": ["1261011"],
            "license": "Fixture syntetyczny.",
            "attribution": "Test.",
            "expected_update_interval": "never",
            "sla": "test",
            "last_manual_verification": "2026-07-25",
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
def test_postgis_pog_publication_stores_four_layers_and_status(
    session: Session, tmp_path: Path
) -> None:
    source_id = f"pog_fixture_{uuid4().hex[:10]}"
    repository = SqlAlchemyImportRepository(
        session, _source(source_id), LocalArtifactStore(tmp_path)
    )
    suffix = uuid4().hex[:8]
    adopted = PogActRecord(
        act_identifier=f"pog-adopted-{suffix}",
        resolution_number="I/1/2026",
        resolution_date=date(2026, 1, 10),
        teryt="1261011",
        name="POG uchwalony",
        legal_status="adopted",
        boundary=geom(
            "POLYGON((565000 244000,565100 244000,565100 244100,"
            "565000 244100,565000 244000))"
        ),
        features=(
            feature(
                "planning_zone",
                "POLYGON((565000 244000,565050 244000,565050 244100,"
                "565000 244100,565000 244000))",
                SYMBOL="SJ-01",
            ),
            feature(
                "ouz",
                "POLYGON((565000 244000,565040 244000,565040 244040,"
                "565000 244040,565000 244000))",
            ),
            feature(
                "downtown_area",
                "POLYGON((565000 244000,565020 244000,565020 244020,"
                "565000 244020,565000 244000))",
            ),
            feature(
                "social_infrastructure_standard",
                "POLYGON((565000 244000,565100 244000,565100 244100,"
                "565000 244100,565000 244000))",
            ),
        ),
    )
    outcome = run_pog_import(
        StaticPogReader(adopted), source_id, repository, release=release(source_id)
    )
    assert outcome.status == "succeeded"
    assert outcome.stats["new"] == 1

    version = session.execute(
        select(PlanningActVersion)
        .join(PlanningAct)
        .where(PlanningAct.act_identifier == adopted.act_identifier)
    ).scalar_one()
    planning_act = session.get(PlanningAct, version.planning_act_id)
    assert planning_act.kind == "pog"
    assert version.legal_status == "adopted"
    assert version.manual_review_required is False

    features = (
        session.execute(
            select(PlanningFeature.feature_type).where(
                PlanningFeature.planning_act_version_id == version.id
            )
        )
        .scalars()
        .all()
    )
    assert sorted(features) == sorted(POG_FEATURE_TYPES)
    assert (
        session.scalar(
            select(PlanBoundary).where(
                PlanBoundary.planning_act_version_id == version.id
            )
        )
        is not None
    )

    # Adopted POG jest widoczny w przecięciach.
    parcel = geom(
        "POLYGON((565005 244005,565015 244005,565015 244015,"
        "565005 244015,565005 244005))"
    )
    hits = find_pog_intersections(session, parcel)
    assert any(row["act_identifier"] == adopted.act_identifier for row in hits)


@pytest.mark.integration
def test_postgis_project_pog_never_binding(session: Session, tmp_path: Path) -> None:
    source_id = f"pog_proj_{uuid4().hex[:10]}"
    repository = SqlAlchemyImportRepository(
        session, _source(source_id), LocalArtifactStore(tmp_path)
    )
    suffix = uuid4().hex[:8]
    project = PogActRecord(
        act_identifier=f"pog-project-{suffix}",
        resolution_number=None,
        resolution_date=None,
        teryt="1261011",
        name="POG w opracowaniu",
        legal_status="project",
        boundary=None,
        features=(
            feature(
                "planning_zone",
                "POLYGON((565000 244000,565050 244000,565050 244100,"
                "565000 244100,565000 244000))",
            ),
        ),
    )
    outcome = run_pog_import(
        StaticPogReader(project), source_id, repository, release=release(source_id)
    )
    assert outcome.status == "succeeded"
    version = session.execute(
        select(PlanningActVersion)
        .join(PlanningAct)
        .where(PlanningAct.act_identifier == project.act_identifier)
    ).scalar_one()
    assert version.legal_status == "project"
    assert version.manual_review_required is True

    # Projekt POG nie jest prezentowany jako obowiązujące ograniczenie.
    parcel = geom(
        "POLYGON((565005 244005,565015 244005,565015 244015,"
        "565005 244015,565005 244005))"
    )
    hits = find_pog_intersections(session, parcel)
    assert all(row["act_identifier"] != project.act_identifier for row in hits)
