"""Import pełnego snapshotu MPZP i kontrolowany fallback raster_only."""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Iterator
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import respx
from shapely import from_wkt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.data_sources import DataSourceEntry
from app.db.session import SessionLocal
from app.models.versioned import (
    LandUseArea,
    PlanBoundary,
    PlanningAct,
    PlanningActVersion,
)
from app.modules.imports.api.cli import parse_args
from app.modules.imports.application.common import (
    ImportRelease,
    ImportSourceNotRunnable,
)
from app.modules.imports.application.mpzp_import import (
    MpzpPublicationResult,
    MpzpSourceBatch,
    run_mpzp_import,
)
from app.modules.imports.application.parcels_import import RepairedGeometry
from app.modules.imports.domain.mpzp import (
    PlanningActRecord,
    PlanningActValidationError,
    ZoneRecord,
    normalize_zone_symbol,
    validate_topology,
)
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.mpzp.reader import (
    PyogrioMpzpReader,
    RasterOnlyMpzpReader,
    VectorLayerResource,
)
from app.modules.imports.infrastructure.repository import (
    SqlAlchemyImportRepository,
    find_plan_intersections,
)
from app.modules.imports.infrastructure.ogc_client import OgcClient, OgcClientConfig
from app.modules.imports.infrastructure.wfs import WfsFetcher, WfsResource
from app.shared.geometry import GeometryPayload


FIXTURES = Path(__file__).parent / "fixtures" / "imports" / "mpzp" / "krakow"
NOW = datetime(2026, 7, 24, tzinfo=timezone.utc)


def geom(wkt: str) -> GeometryPayload:
    return GeometryPayload(wkt)


def act(
    identifier: str = "plan-a",
    *,
    boundary: str = "POLYGON((0 0,100 0,100 100,0 100,0 0))",
    zones: tuple[ZoneRecord, ...] | None = None,
) -> PlanningActRecord:
    return PlanningActRecord(
        act_identifier=identifier,
        resolution_number="I/1/2026",
        resolution_date=date(2026, 1, 10),
        teryt="1261011",
        name="Plan testowy",
        boundary=geom(boundary),
        zones=zones
        if zones is not None
        else (
            ZoneRecord(
                "1MN",
                "single_family_housing",
                geom("POLYGON((0 0,50 0,50 100,0 100,0 0))"),
                {"source": "fixture"},
            ),
            ZoneRecord(
                "2U",
                "services",
                geom("POLYGON((50 0,100 0,100 100,50 100,50 0))"),
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


class StaticMpzpReader:
    def __init__(
        self,
        *acts: PlanningActRecord,
        content: bytes = b"mpzp",
        vector_available: bool = True,
        raster_act: PlanningActRecord | None = None,
    ) -> None:
        self.acts = acts
        self.content = content
        self.vector_available = vector_available
        self.raster_act = raster_act

    def read(self) -> MpzpSourceBatch:
        return MpzpSourceBatch(
            self.content,
            "mpzp.gml",
            "application/gml+xml",
            tuple(self.acts),
            self.vector_available,
            self.raster_act,
        )


class FakeRepository:
    topology_distance_tolerance_m = 0.01
    topology_area_tolerance_sqm = 0.01

    def __init__(self) -> None:
        self.published = ()

    def repair_geometry(self, geometry):
        shape = from_wkt(geometry.wkt)
        if shape.is_empty:
            return None
        return RepairedGeometry(
            GeometryPayload(shape.wkt),
            shape.area,
            hashlib.sha256(shape.wkb).hexdigest(),
            False,
        )

    def publish_mpzp(self, *, release, batch, artifact_hash, acts, stats, warnings):
        self.published = acts
        return MpzpPublicationResult(len(acts), 0, 0, 1, 2)


def test_topology_detects_overlap_gap_and_outside() -> None:
    zones = (
        ZoneRecord("A", None, geom("POLYGON((0 0,60 0,60 100,0 100,0 0))")),
        ZoneRecord("B", None, geom("POLYGON((50 0,90 0,90 100,50 100,50 0))")),
        ZoneRecord("C", None, geom("POLYGON((95 0,105 0,105 10,95 10,95 0))")),
    )
    result = validate_topology(
        act(zones=zones), distance_tolerance_m=0, area_tolerance_sqm=0.01
    )
    assert result.overlap_area_sqm > 0
    assert result.gap_area_sqm > 0
    assert result.outside_zone_count == 1
    assert {"zone_overlaps", "zone_gaps", "zones_outside_boundary"} <= set(
        result.warnings
    )


def test_act_requires_resolution_number_and_date() -> None:
    invalid = PlanningActRecord("x", "", None, "1261011", None, geom("POLYGON EMPTY"))
    with pytest.raises(PlanningActValidationError):
        invalid.validate()


def test_symbol_normalization_keeps_explicit_and_maps_common_codes() -> None:
    assert normalize_zone_symbol("1MN", None) == "single_family_housing"
    assert normalize_zone_symbol("2U", " Usługi lokalne ") == "usługi_lokalne"
    assert normalize_zone_symbol("X", None) is None


def test_contract_required_source_is_dry_run_only() -> None:
    repository = FakeRepository()
    with pytest.raises(ImportSourceNotRunnable):
        run_mpzp_import(
            StaticMpzpReader(act()),
            "mpzp_pilot_krakow",
            repository,
            release=release("mpzp_pilot_krakow", allowed=False),
        )
    outcome = run_mpzp_import(
        StaticMpzpReader(act()),
        "mpzp_pilot_krakow",
        repository,
        release=release("mpzp_pilot_krakow", allowed=False, dry_run=True),
    )
    assert outcome.status == "dry_run_only"
    assert outcome.stats["publishable"] == 1
    assert repository.published == ()


def test_raster_only_dry_run_has_no_fake_geometry() -> None:
    raster = PlanningActRecord(
        "raster", "I/2/2026", date(2026, 2, 1), "1261011", "Raster", None
    )
    outcome = run_mpzp_import(
        RasterOnlyMpzpReader(raster),
        "research",
        FakeRepository(),
        release=release("research", allowed=False, dry_run=True),
    )
    assert outcome.stats["raster_only"] == 1
    assert "manual_review_required" in outcome.warnings


def test_vector_application_publishes_whole_snapshot() -> None:
    repository = FakeRepository()
    outcome = run_mpzp_import(
        StaticMpzpReader(act()),
        "fixture",
        repository,
        release=release(),
    )
    assert outcome.stats["new"] == 1
    assert repository.published[0][0].zones[0].original_symbol == "1MN"


def test_whole_act_hash_is_order_independent_and_covers_raw_attributes() -> None:
    first_repository = FakeRepository()
    first_act = act()
    run_mpzp_import(
        StaticMpzpReader(first_act),
        "fixture",
        first_repository,
        release=release(),
    )
    first_hash = first_repository.published[0][1]

    reordered_repository = FakeRepository()
    reordered = PlanningActRecord(
        **{**first_act.__dict__, "zones": tuple(reversed(first_act.zones))}
    )
    run_mpzp_import(
        StaticMpzpReader(reordered),
        "fixture",
        reordered_repository,
        release=release(),
    )
    assert reordered_repository.published[0][1] == first_hash

    changed_repository = FakeRepository()
    changed_zone = ZoneRecord(
        first_act.zones[0].original_symbol,
        first_act.zones[0].normalized_symbol,
        first_act.zones[0].geometry,
        {"changed": True},
    )
    changed = PlanningActRecord(
        **{
            **first_act.__dict__,
            "zones": (changed_zone, *first_act.zones[1:]),
        }
    )
    run_mpzp_import(
        StaticMpzpReader(changed),
        "fixture",
        changed_repository,
        release=release(),
    )
    assert changed_repository.published[0][1] != first_hash


def test_cli_parser_accepts_ordered_resources() -> None:
    parsed = parse_args(
        [
            "mpzp",
            "--source",
            "mpzp_pilot_krakow",
            "--dry-run",
            "--resource",
            "boundaries=a.geojson",
            "--resource",
            "zones=b.geojson",
        ]
    )
    assert parsed.resources == (
        ("boundaries", "a.geojson"),
        ("zones", "b.geojson"),
    )


def test_cli_parser_requires_explicit_raster_act_metadata_from_operator() -> None:
    parsed = parse_args(
        [
            "mpzp",
            "--source",
            "kimpzp",
            "--dry-run",
            "--act-id",
            "act-123",
            "--resolution-number",
            "XII/123/2024",
            "--resolution-date",
            "2024-06-20",
            "--teryt",
            "1465011",
        ]
    )
    assert parsed.act_identifier == "act-123"
    assert parsed.resolution_number == "XII/123/2024"
    assert parsed.resolution_date == date(2024, 6, 20)
    assert parsed.teryt == "1465011"


def test_pyogrio_mpzp_reader_uses_confirmed_schema() -> None:
    pytest.importorskip("pyogrio")
    boundary_mapping = {
        "act_identifier": "id_pg",
        "resolution_number": "Uchwała",
        "resolution_date": "Data_uchwalenia",
        "name": "Nazwa_planu",
    }
    zone_mapping = {
        "act_identifier": "id_pg",
        "resolution_number": "Uchwalenie",
        "resolution_date": "Data_uchwalenia",
        "original_symbol": "Oznaczenie",
        "normalized_symbol": "Rodzaj_oznaczenia",
    }
    reader = PyogrioMpzpReader(
        (
            VectorLayerResource(
                "boundaries",
                FIXTURES / "boundaries_2180.geojson",
                "EPSG:2180",
                boundary_mapping,
            ),
            VectorLayerResource(
                "zones",
                FIXTURES / "zones_2180.geojson",
                "EPSG:2180",
                zone_mapping,
            ),
        ),
        teryt="1261011",
    )
    batch = reader.read()
    assert len(batch.acts) == 2
    assert [len(item.zones) for item in batch.acts] == [2, 2]
    assert batch.acts[0].resolution_date == date(2026, 1, 10)


@respx.mock
def test_wfs_fetcher_preserves_order_and_builds_stable_artifact() -> None:
    url = "https://example.gov.pl/wfs"
    route = respx.get(url).mock(
        return_value=httpx.Response(200, content=b"<?xml version='1.0'?><gml/>")
    )
    resources = (
        WfsResource("boundaries", url, "x:boundaries", "EPSG:2180", {}),
        WfsResource("zones", url, "x:zones", "EPSG:2180", {}),
    )
    client = OgcClient(
        source_id="fixture_wfs",
        config=OgcClientConfig(allowed_hosts=frozenset({"example.gov.pl"}), retries=0),
        resolver=lambda _host: ("93.184.216.34",),
    )
    with client:
        fetcher = WfsFetcher(ogc_client=client)
        first = fetcher.fetch(resources)
        second = fetcher.fetch(resources)
    assert first == second
    assert route.call_count == 4
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == ["00-boundaries.gml", "01-zones.gml"]


def _source(source_id: str) -> DataSourceEntry:
    return DataSourceEntry.model_validate(
        {
            "source_id": source_id,
            "name": "Fixture MPZP",
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
            "teryt_scope": ["1261011"],
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
def test_postgis_mpzp_publication_intersects_two_plans(
    session: Session, tmp_path: Path
) -> None:
    source_id = f"mpzp_fixture_{uuid4().hex[:10]}"
    repository = SqlAlchemyImportRepository(
        session, _source(source_id), LocalArtifactStore(tmp_path)
    )
    suffix = uuid4().hex[:8]
    first = act(
        f"pilot-a-{suffix}",
        boundary="POLYGON((565000 244000,565100 244000,565100 244100,565000 244100,565000 244000))",
        zones=(
            ZoneRecord(
                "1MN",
                "single_family_housing",
                geom(
                    "POLYGON((565000 244000,565100 244000,565100 244100,565000 244100,565000 244000))"
                ),
                {"official_field": "value"},
            ),
        ),
    )
    second = act(
        f"pilot-b-{suffix}",
        boundary="POLYGON((565100 244000,565200 244000,565200 244100,565100 244100,565100 244000))",
        zones=(
            ZoneRecord(
                "2U",
                "services",
                geom(
                    "POLYGON((565100 244000,565200 244000,565200 244100,565100 244100,565100 244000))"
                ),
            ),
        ),
    )
    outcome = run_mpzp_import(
        StaticMpzpReader(first, second),
        source_id,
        repository,
        release=release(source_id),
    )
    assert outcome.stats["new"] == 2
    version = session.execute(
        select(PlanningActVersion)
        .join(PlanningAct)
        .where(PlanningAct.act_identifier == first.act_identifier)
    ).scalar_one()
    assert version.resolution_number == first.resolution_number
    assert version.resolution_date == first.resolution_date
    assert version.name == first.name
    intersections = find_plan_intersections(
        session,
        geom(
            "POLYGON((565090 244020,565110 244020,565110 244080,"
            "565090 244080,565090 244020))"
        ),
    )
    assert [row["act_identifier"] for row in intersections] == [
        first.act_identifier,
        second.act_identifier,
    ]
    assert [row["intersection_area_sqm"] for row in intersections] == pytest.approx(
        [600.0, 600.0]
    )
    raw = session.execute(
        select(LandUseArea.raw_attributes)
        .join(
            PlanningActVersion,
            LandUseArea.planning_act_version_id == PlanningActVersion.id,
        )
        .join(PlanningAct, PlanningActVersion.planning_act_id == PlanningAct.id)
        .where(PlanningAct.act_identifier == first.act_identifier)
    ).scalar_one()
    assert raw["official_field"] == "value"


@pytest.mark.integration
def test_raster_only_publishes_review_flag_without_geometry(
    session: Session, tmp_path: Path
) -> None:
    source_id = f"raster_fixture_{uuid4().hex[:10]}"
    repository = SqlAlchemyImportRepository(
        session, _source(source_id), LocalArtifactStore(tmp_path)
    )
    raster = PlanningActRecord(
        f"raster-{uuid4().hex[:8]}",
        "II/2/2026",
        date(2026, 2, 2),
        "1261011",
        "Plan rastrowy",
        None,
    )
    outcome = run_mpzp_import(
        RasterOnlyMpzpReader(raster),
        source_id,
        repository,
        release=release(source_id),
    )
    version = session.execute(
        select(PlanningActVersion)
        .join(PlanningAct)
        .where(PlanningAct.act_identifier == raster.act_identifier)
    ).scalar_one()
    assert outcome.status == "succeeded"
    assert version.legal_status == "raster_only"
    assert version.manual_review_required is True
    assert version.resolution_number == raster.resolution_number
    assert version.resolution_date == raster.resolution_date
    assert (
        session.scalar(
            select(PlanBoundary).where(
                PlanBoundary.planning_act_version_id == version.id
            )
        )
        is None
    )


# --- QA importu i stabilne ID wydzieleń (BK-202) -----------------------------


class _EmptyingRepository(FakeRepository):
    """Zwraca None dla geometrii oznaczonych jako puste (symulacja repair)."""

    def repair_geometry(self, geometry):
        if "EMPTY" in geometry.wkt:
            return None
        return super().repair_geometry(geometry)


def test_import_rejects_invalid_acts_and_duplicate_zone_ids_without_merging() -> None:
    invalid = PlanningActRecord("bad", "", date(2026, 1, 1), "1261011", None, geom("POLYGON((0 0,1 0,1 1,0 1,0 0))"))
    no_boundary = PlanningActRecord("no-boundary", "I/1", date(2026, 1, 1), "1261011", None, None)
    empty_boundary = act("empty-boundary", boundary="POLYGON EMPTY")
    duplicate = act(
        "duplicate",
        zones=(
            ZoneRecord("1MN", None, geom("POLYGON((0 0,50 0,50 100,0 100,0 0))"), source_identifier="OBJ-1"),
            ZoneRecord("2U", None, geom("POLYGON((50 0,100 0,100 100,50 100,50 0))"), source_identifier="OBJ-1"),
        ),
    )
    empty_zone = act(
        "empty-zone",
        zones=(
            ZoneRecord("1MN", None, geom("POLYGON((0 0,100 0,100 100,0 100,0 0))"), source_identifier="A"),
            ZoneRecord("9X", None, geom("POLYGON EMPTY")),
        ),
    )
    repository = _EmptyingRepository()

    outcome = run_mpzp_import(
        StaticMpzpReader(invalid, no_boundary, empty_boundary, duplicate, empty_zone),
        "fixture",
        repository,
        release=release(),
    )

    assert outcome.stats["rejected"] == 5
    warnings = set(outcome.warnings)
    assert "missing_boundary:no-boundary" in warnings
    assert "empty_boundary:empty-boundary" in warnings
    assert "duplicate_zone_identifier:duplicate:OBJ-1" in warnings
    assert "empty_zone:empty-zone:9X" in warnings
    (published, _hash), = repository.published
    assert published.act_identifier == "empty-zone"
    assert [zone.zone_identifier for zone in published.zones] == ["empty-zone:A"]


def test_zone_identifier_is_deterministic_and_part_of_snapshot_hash() -> None:
    first, second = FakeRepository(), FakeRepository()
    run_mpzp_import(StaticMpzpReader(act()), "fixture", first, release=release())
    run_mpzp_import(StaticMpzpReader(act()), "fixture", second, release=release())
    (a, hash_a), = first.published
    (b, hash_b), = second.published
    assert [zone.zone_identifier for zone in a.zones] == [zone.zone_identifier for zone in b.zones]
    assert all(zone.zone_identifier.startswith("plan-a:") for zone in a.zones)
    assert hash_a == hash_b

    with_document = FakeRepository()
    documented = PlanningActRecord(**{**act().__dict__, "document_url": "https://bip.example.gov.pl/u.pdf"})
    run_mpzp_import(StaticMpzpReader(documented), "fixture", with_document, release=release())
    (_, hash_doc), = with_document.published
    assert hash_doc != hash_a  # URL dokumentu jest częścią snapshotu wersji


def test_import_guards_release_and_raster_metadata() -> None:
    with pytest.raises(ValueError, match="source_id"):
        run_mpzp_import(StaticMpzpReader(act()), "other", FakeRepository(), release=release())
    with pytest.raises(ImportSourceNotRunnable):
        run_mpzp_import(StaticMpzpReader(act()), "fixture", FakeRepository(), release=release(allowed=False))
    with pytest.raises(PlanningActValidationError, match="raster_only"):
        run_mpzp_import(
            StaticMpzpReader(vector_available=False, raster_act=None),
            "fixture",
            FakeRepository(),
            release=release(),
        )


def test_snapshot_hash_handles_numpy_like_and_unserializable_attributes() -> None:
    class NumpyLike:
        def __init__(self, value):
            self.value = value

        def item(self):
            return self.value

    class Broken:
        def item(self):
            raise ValueError("bad")

    zones = (
        ZoneRecord(
            "1MN",
            None,
            geom("POLYGON((0 0,100 0,100 100,0 100,0 0))"),
            {"n": NumpyLike(3), "when": date(2026, 1, 1), "obj": Broken(), "list": (1, 2)},
        ),
    )
    repository = FakeRepository()
    run_mpzp_import(StaticMpzpReader(act(zones=zones)), "fixture", repository, release=release())
    assert len(repository.published) == 1
