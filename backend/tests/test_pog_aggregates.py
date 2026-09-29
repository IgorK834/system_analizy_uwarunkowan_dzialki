"""BK-405: agregaty powierzchniowe stref POG liczone przy imporcie wydania.

Testy jednostkowe sprawdzają czystą logikę udziałów i kompletności, a testy
PostGIS/HTTP importują syntetyczne akty (EPSG:2180) przez prawdziwy
``run_pog_import`` do transakcji wycofywanej po teście i czytają gotowe
agregaty przez endpoint ``/summary`` — z przechwyceniem SQL, żeby potwierdzić,
że HTTP nie wykonuje obliczeń przestrzennych.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.core.data_sources import DataSourceEntry
from app.db.session import SessionLocal, get_db
from app.main import app
from app.models.versioned import DataRelease, PogAreaSummary
from app.modules.imports.application.common import ImportRelease
from app.modules.imports.application.pog_import import PogSourceBatch, run_pog_import
from app.modules.imports.domain.pog import (
    PogActRecord,
    PogFeatureRecord,
    PogObjectId,
)
from app.modules.imports.domain.pog_aggregates import (
    AGGREGATE_METHOD_VERSION,
    MIN_AREA_TOLERANCE_SQM,
    SHARE_SUM_TOLERANCE_PCT,
    ActForAggregation,
    AreaMeasurement,
    ZoneAreaMeasurement,
    aggregate_areas,
    aggregate_warning,
    area_tolerance_sqm,
    edition_for_status,
    priority_ordered,
    sqm_to_sqkm,
)
from app.modules.imports.infrastructure import repository as repository_module
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.repository import SqlAlchemyImportRepository
from app.modules.planning.application.pog_release_queries import (
    PogAreaSummaryNotFoundError,
    PogReleaseQueryService,
)
from app.modules.planning.application.pog_tiles import PogReleaseNotFoundError
from app.modules.planning.composition import pog_tile_cache
from app.modules.planning.domain.pog_area_summary import (
    InvalidPogSummaryQueryError,
    PogAreaSummaryQuery,
)
from app.shared.geometry import GeometryPayload
from app.shared.planning_status import inspire_status_uri

SOURCE_ID = "pog_app"
TERYT = "999901"
ORIGIN_X, ORIGIN_Y = 500_000.0, 500_000.0
KM = 1_000.0
# Operacje przestrzenne, których odczyt agregatu przez HTTP nie może wykonywać.
SPATIAL_FUNCTIONS = ("st_intersection", "st_area", "st_union", "st_difference", "st_intersects")


# --- Logika domenowa -----------------------------------------------------------


def _measurement(
    zones: dict[str, float],
    *,
    denominator: float | None,
    union: float | None = None,
    total: float | None = None,
    outside: float = 0.0,
) -> AreaMeasurement:
    zone_sum = sum(zones.values())
    return AreaMeasurement(
        denominator_area_sqm=denominator,
        zones=tuple(ZoneAreaMeasurement(code, area, 1) for code, area in zones.items()),
        zones_union_area_sqm=zone_sum if union is None else union,
        zones_sum_area_sqm=zone_sum if total is None else total,
        outside_area_sqm=outside,
    )


def test_one_square_kilometre_split_sixty_forty_is_complete() -> None:
    aggregate = aggregate_areas(
        _measurement({"SU": 400_000.0, "SW": 600_000.0}, denominator=1_000_000.0)
    )

    assert [(zone.zone_code, zone.share_pct) for zone in aggregate.zones] == [
        ("SW", pytest.approx(60.0)),
        ("SU", pytest.approx(40.0)),
    ]
    assert [zone.area_sqkm for zone in aggregate.zones] == [
        pytest.approx(0.6),
        pytest.approx(0.4),
    ]
    assert aggregate.denominator_area_sqkm == pytest.approx(1.0)
    assert aggregate.share_sum_pct == pytest.approx(100.0)
    assert aggregate.missing_area_sqm == pytest.approx(0.0)
    assert aggregate.is_complete and aggregate.incomplete_reasons == ()
    assert aggregate.zone_count == 2


def test_gap_of_point_one_square_kilometre_is_incomplete_with_explicit_denominator() -> None:
    aggregate = aggregate_areas(
        _measurement({"SW": 600_000.0, "SU": 300_000.0}, denominator=1_000_000.0)
    )

    assert aggregate.is_complete is False
    assert aggregate.denominator_area_sqm == pytest.approx(1_000_000.0)
    assert aggregate.missing_area_sqm == pytest.approx(100_000.0)
    assert aggregate.share_sum_pct == pytest.approx(90.0)
    assert aggregate.incomplete_reasons == ("missing_area", "share_sum_out_of_tolerance")
    assert aggregate_warning("akt-1", aggregate) == (
        "pog_aggregate_incomplete:akt-1:missing_area,share_sum_out_of_tolerance"
    )


def test_missing_denominator_gives_null_shares_not_hundred_percent() -> None:
    aggregate = aggregate_areas(_measurement({"SW": 600_000.0}, denominator=None))

    assert aggregate.denominator_area_sqm is None
    assert aggregate.denominator_area_sqkm is None
    assert aggregate.zones[0].share_pct is None
    assert aggregate.zones[0].area_sqkm == pytest.approx(0.6)
    assert aggregate.share_sum_pct is None and aggregate.missing_area_sqm is None
    assert aggregate.is_complete is False
    assert aggregate.incomplete_reasons == ("no_boundary",)
    # Mianownik zerowy traktujemy jak brak mianownika, nie dzielimy przez zero.
    assert aggregate_areas(_measurement({"SW": 1.0}, denominator=0.0)).share_sum_pct is None


def test_overlap_and_zones_outside_boundary_are_reported() -> None:
    aggregate = aggregate_areas(
        _measurement(
            {"SW": 600_000.0, "SU": 500_000.0},
            denominator=1_000_000.0,
            union=1_000_000.0,
            total=1_100_000.0,
            outside=5_000.0,
        )
    )

    assert aggregate.overlap_area_sqm == pytest.approx(100_000.0)
    assert aggregate.outside_area_sqm == pytest.approx(5_000.0)
    assert aggregate.share_sum_pct == pytest.approx(110.0)
    assert aggregate.incomplete_reasons == (
        "overlapping_zones",
        "zones_outside_boundary",
        "share_sum_out_of_tolerance",
    )


def test_share_sum_is_controlled_by_tolerance() -> None:
    denominator = 1_000_000.0
    within = denominator * (1 - SHARE_SUM_TOLERANCE_PCT / 200)
    aggregate = aggregate_areas(_measurement({"SW": within}, denominator=denominator))
    assert aggregate.is_complete, aggregate.incomplete_reasons
    assert aggregate_warning("akt", aggregate) is None

    # Drzazgi poniżej tolerancji nie są luką; tolerancja rośnie z mianownikiem.
    assert area_tolerance_sqm(None) == MIN_AREA_TOLERANCE_SQM
    assert area_tolerance_sqm(100.0) == MIN_AREA_TOLERANCE_SQM
    assert area_tolerance_sqm(1_000_000.0) == pytest.approx(500.0)
    assert sqm_to_sqkm(2_500_000.0) == pytest.approx(2.5)


def test_empty_act_has_no_zones_reason() -> None:
    aggregate = aggregate_areas(_measurement({}, denominator=1_000_000.0))
    assert aggregate.incomplete_reasons == (
        "no_zones",
        "missing_area",
        "share_sum_out_of_tolerance",
    )
    assert aggregate.zone_count == 0


def test_editions_and_priority_follow_tile_editions() -> None:
    assert edition_for_status("binding") == "binding"
    assert edition_for_status("project") == "project"
    assert edition_for_status("in_progress") == "project"
    assert edition_for_status("unknown") is None
    assert edition_for_status("superseded") is None
    older = ActForAggregation(1, "A", None, TERYT, "binding", ("2024-01-01", "", 1))
    newer = ActForAggregation(2, "B", None, TERYT, "binding", ("2026-01-01", "", 2))
    assert [act.act_identifier for act in priority_ordered([older, newer])] == ["B", "A"]


# --- Kontrakt zapytania i serwis odczytu ---------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "dokładnie jeden"),
        ({"act_id": "A", "teryt": "226401"}, "dokładnie jeden"),
        ({"act_id": "A", "edition": "binding"}, "wyłącznie agregatu gminy"),
        ({"act_id": "x" * 201}, "za długi"),
        ({"teryt": "22640"}, "6 lub 7 cyfr"),
        ({"teryt": "226401", "edition": "all"}, "Nieznana edycja"),
    ],
)
def test_summary_query_requires_exactly_one_scope(kwargs: dict[str, str], message: str) -> None:
    with pytest.raises(InvalidPogSummaryQueryError, match=message):
        PogAreaSummaryQuery(1, **kwargs).normalized()


def test_summary_query_normalizes_scope() -> None:
    assert PogAreaSummaryQuery(1, teryt="2264011").normalized() == PogAreaSummaryQuery(
        1, teryt="226401", edition="binding"
    )
    assert PogAreaSummaryQuery(1, act_id=" A ").normalized().scope == "act"
    with pytest.raises(InvalidPogSummaryQueryError, match="dodatni"):
        PogAreaSummaryQuery(0, act_id="A").normalized()


class _FakeRepository:
    def __init__(self, *, release: bool = True, has_summaries: bool = True) -> None:
        self.release = release
        self.has_summaries = has_summaries

    def release_header(self, release_id: int):  # noqa: ANN201
        return object() if self.release else None

    def feature_rows(self, release_id, ref):  # noqa: ANN001, ANN201
        return []

    def area_summary(self, query):  # noqa: ANN001, ANN201
        return None

    def release_has_area_summaries(self, release_id: int) -> bool:
        return self.has_summaries


def test_service_distinguishes_missing_release_legacy_release_and_missing_scope() -> None:
    query = PogAreaSummaryQuery(5, act_id="A")
    with pytest.raises(PogReleaseNotFoundError):
        PogReleaseQueryService(_FakeRepository(release=False)).area_summary(query)  # type: ignore[arg-type]
    with pytest.raises(PogAreaSummaryNotFoundError, match="przed BK-405"):
        PogReleaseQueryService(_FakeRepository(has_summaries=False)).area_summary(query)  # type: ignore[arg-type]
    with pytest.raises(PogAreaSummaryNotFoundError, match="aktu 'A'"):
        PogReleaseQueryService(_FakeRepository()).area_summary(query)  # type: ignore[arg-type]
    with pytest.raises(PogAreaSummaryNotFoundError, match="gminy 226401 w edycji project"):
        PogReleaseQueryService(_FakeRepository()).area_summary(  # type: ignore[arg-type]
            PogAreaSummaryQuery(5, teryt="226401", edition="project")
        )


# --- PostGIS + HTTP --------------------------------------------------------------


def _source_entry() -> DataSourceEntry:
    return DataSourceEntry.model_validate(
        {
            "source_id": SOURCE_ID,
            "name": "POG APP (syntetyczne akty BK-405)",
            "owner": "Test",
            "status": "production",
            "production_ready": True,
            "contract_confirmed": True,
            "access_type": "app_gml",
            "capabilities_url": None,
            "file_url": "file:///fixture.gml",
            "type_names": ["StrefaPlanistyczna"],
            "layers": None,
            "protocol_version": "fixture/1",
            "source_crs": "EPSG:2180",
            "target_crs": "EPSG:2180",
            "teryt_scope": [TERYT],
            "license": "Fixture.",
            "attribution": "Test.",
            "expected_update_interval": "never",
            "sla": "test",
            "last_manual_verification": "2026-09-29",
        }
    )


def _rect(x0: float, x1: float, y0: float = 0.0, y1: float = KM) -> GeometryPayload:
    return GeometryPayload(box(ORIGIN_X + x0, ORIGIN_Y + y0, ORIGIN_X + x1, ORIGIN_Y + y1).wkt)


def _act(
    local_id: str,
    zones: list[tuple[str, GeometryPayload]],
    *,
    boundary: GeometryPayload | None,
    legal_status: str = "binding",
    version: str = "20260901T000000",
) -> PogActRecord:
    namespace = f"PL.ZIPPZP.9999/{TERYT}-POG"
    return PogActRecord(
        act_identifier=f"{namespace}/{local_id}",
        resolution_number=f"XV/{local_id}/2026",
        resolution_date=None,
        teryt=TERYT,
        name=f"Syntetyczny plan ogólny {local_id}",
        legal_status=legal_status,
        raw_legal_status=inspire_status_uri(legal_status),  # type: ignore[arg-type]
        boundary=boundary,
        object_id=PogObjectId(namespace, local_id, version),
        version_started_at=datetime.strptime(version, "%Y%m%dT%H%M%S").replace(
            tzinfo=timezone.utc
        ),
        features=tuple(
            PogFeatureRecord(
                feature_type="planning_zone",
                geometry=geometry,
                object_id=PogObjectId(namespace, f"{local_id}-{index}{code}", version),
                symbol=code,
            )
            for index, (code, geometry) in enumerate(zones, start=1)
        ),
    )


class _Reader:
    def __init__(self, *acts: PogActRecord) -> None:
        self.acts = acts

    def read(self) -> PogSourceBatch:
        return PogSourceBatch(
            f"bk405-{uuid4().hex}".encode(), "pog.gml", "application/gml+xml", self.acts
        )


def _import(session: Session, tmp_path: Path, *acts: PogActRecord, label: str):  # noqa: ANN202
    return run_pog_import(
        _Reader(*acts),
        SOURCE_ID,
        SqlAlchemyImportRepository(session, _source_entry(), LocalArtifactStore(tmp_path)),
        release=ImportRelease(
            SOURCE_ID,
            label,
            datetime(2026, 9, 29, tzinfo=timezone.utc),
            publication_allowed=True,
            dry_run=False,
            teryt_scope=(TERYT,),
        ),
    )


@pytest.fixture
def db_session() -> Iterator[Session]:
    session = SessionLocal()
    pog_tile_cache().clear()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        pog_tile_cache().clear()


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _summary_url(release_id: int, **params: str) -> str:
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return f"/api/v1/map/pog/releases/{release_id}/summary?{query}"


SIXTY_FORTY = [("SW", _rect(0, 600)), ("SU", _rect(600, 1000))]


@pytest.mark.integration
def test_one_km2_act_split_sixty_forty_via_import_and_http(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    act = _act("1POG", SIXTY_FORTY, boundary=_rect(0, 1000))
    outcome = _import(db_session, tmp_path, act, label="A")
    assert outcome.status == "succeeded"
    assert outcome.stats["area_summaries"] == 2  # akt + gmina (edycja binding)
    assert outcome.stats["area_summaries_incomplete"] == 0
    release_id = int(outcome.data_release_id)  # type: ignore[arg-type]

    response = client.get(_summary_url(release_id, act_id=act.act_identifier))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema"] == "pog-area-summary/1"
    assert body["scope"] == "act" and body["legal_status"] == "binding"
    assert body["act_version"] == "20260901T000000"
    assert body["denominator_area_sqkm"] == pytest.approx(1.0)
    assert body["denominator_source"] == "act_boundary"
    assert [(zone["zone_code"], zone["share_pct"], zone["area_sqkm"]) for zone in body["zones"]] == [
        ("SW", pytest.approx(60.0), pytest.approx(0.6)),
        ("SU", pytest.approx(40.0), pytest.approx(0.4)),
    ]
    assert body["share_sum_pct"] == pytest.approx(100.0)
    assert body["is_complete"] is True and body["incomplete_reasons"] == []
    assert body["zone_count"] == 2 and body["act_count"] == 1
    assert body["method_version"] == AGGREGATE_METHOD_VERSION
    assert body["release_is_active"] is True and body["artifact_sha256"]

    etag = response.headers["etag"]
    assert response.headers["cache-control"].startswith("public, max-age=")
    cached = client.get(
        _summary_url(release_id, act_id=act.act_identifier), headers={"If-None-Match": etag}
    )
    assert cached.status_code == 304 and cached.content == b""

    municipality = client.get(_summary_url(release_id, teryt=TERYT)).json()
    assert municipality["scope"] == "municipality" and municipality["edition"] == "binding"
    assert [zone["share_pct"] for zone in municipality["zones"]] == [
        pytest.approx(60.0),
        pytest.approx(40.0),
    ]


@pytest.mark.integration
def test_gap_of_point_one_km2_is_persisted_as_incomplete_with_denominator(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    act = _act(
        "1POG", [("SW", _rect(0, 600)), ("SU", _rect(700, 1000))], boundary=_rect(0, 1000)
    )
    outcome = _import(db_session, tmp_path, act, label="A")
    release_id = int(outcome.data_release_id)  # type: ignore[arg-type]
    assert outcome.stats["area_summaries_incomplete"] == 2
    assert (
        f"pog_aggregate_incomplete:{act.act_identifier}:missing_area,share_sum_out_of_tolerance"
        in outcome.warnings
    )

    body = client.get(_summary_url(release_id, act_id=act.act_identifier)).json()
    assert body["is_complete"] is False
    assert body["denominator_area_sqkm"] == pytest.approx(1.0)
    assert body["missing_area_sqkm"] == pytest.approx(0.1)
    assert [zone["share_pct"] for zone in body["zones"]] == [
        pytest.approx(60.0),
        pytest.approx(30.0),
    ]
    assert body["incomplete_reasons"] == ["missing_area", "share_sum_out_of_tolerance"]
    # Provenance zapisane także dla niepełnego wyniku.
    assert body["method_version"] == AGGREGATE_METHOD_VERSION and body["computed_at"]


@pytest.mark.integration
def test_act_without_source_boundary_has_null_denominator_not_hundred_percent(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    act = _act("1POG", SIXTY_FORTY, boundary=None)
    release_id = int(_import(db_session, tmp_path, act, label="A").data_release_id)  # type: ignore[arg-type]

    body = client.get(_summary_url(release_id, act_id=act.act_identifier)).json()
    assert body["denominator_area_sqm"] is None and body["denominator_source"] is None
    assert body["share_sum_pct"] is None and body["missing_area_sqm"] is None
    assert [zone["share_pct"] for zone in body["zones"]] == [None, None]
    assert [zone["area_sqkm"] for zone in body["zones"]] == [
        pytest.approx(0.6),
        pytest.approx(0.4),
    ]
    assert body["is_complete"] is False and body["incomplete_reasons"] == ["no_boundary"]

    # Metadane wydania (BK-406) przenoszą tę samą kompletność do stanu mapy.
    release = client.get(f"/api/v1/map/pog/releases/{release_id}").json()
    assert release["coverage_areas"] == [
        {
            "act_id": act.act_identifier,
            "teryt": TERYT,
            "legal_status": "binding",
            "bounds": pytest.approx(release["coverage_areas"][0]["bounds"]),
            "has_boundary": False,
            "is_complete": False,
            "incomplete_reasons": ["no_boundary"],
        }
    ]
    min_lon, min_lat, max_lon, max_lat = release["coverage_areas"][0]["bounds"]
    assert 18.0 < min_lon < max_lon < 20.0 and 51.0 < min_lat < max_lat < 53.0


@pytest.mark.integration
def test_municipality_summary_does_not_double_count_overlapping_acts(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    older = _act(
        "1POG", [("SW", _rect(0, 1000))], boundary=_rect(0, 1000), version="20250101T000000"
    )
    newer = _act(
        "2POG", [("SU", _rect(500, 1500))], boundary=_rect(500, 1500), version="20260101T000000"
    )
    project = _act(
        "3POG", [("SJ", _rect(0, 1000))], boundary=_rect(0, 1000), legal_status="project"
    )
    outcome = _import(db_session, tmp_path, older, newer, project, label="A")
    release_id = int(outcome.data_release_id)  # type: ignore[arg-type]
    assert outcome.stats["area_summaries"] == 5  # 3 akty + gmina binding + gmina project

    body = client.get(_summary_url(release_id, teryt=TERYT, edition="binding")).json()
    assert body["act_ids"] == [newer.act_identifier, older.act_identifier]
    assert body["act_count"] == 2
    assert body["denominator_area_sqkm"] == pytest.approx(1.5)
    assert body["denominator_source"] == "act_boundaries_union"
    assert body["deduplicated_area_sqm"] == pytest.approx(500_000.0)
    assert [(zone["zone_code"], zone["area_sqkm"]) for zone in body["zones"]] == [
        ("SU", pytest.approx(1.0)),
        ("SW", pytest.approx(0.5)),
    ]
    assert body["share_sum_pct"] == pytest.approx(100.0)
    assert body["overlap_area_sqm"] == pytest.approx(0.0, abs=1.0)
    assert body["is_complete"] is True

    # Projekt nie trafia do edycji wiążącej — ma własny agregat gminy.
    project_body = client.get(_summary_url(release_id, teryt=TERYT, edition="project")).json()
    assert project_body["act_ids"] == [project.act_identifier]
    assert [zone["zone_code"] for zone in project_body["zones"]] == ["SJ"]

    missing = client.get(_summary_url(release_id, act_id="nie-istnieje"))
    assert missing.status_code == 404 and "nie zawiera agregatu" in missing.json()["detail"]
    assert client.get(_summary_url(release_id)).status_code == 422
    assert client.get(_summary_url(release_id, teryt="12")).status_code == 422
    assert client.get(_summary_url(987_654_321, act_id="x")).status_code == 404


@pytest.mark.integration
def test_http_reads_ready_aggregates_without_spatial_functions(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    act = _act("1POG", SIXTY_FORTY, boundary=_rect(0, 1000))
    release_id = int(_import(db_session, tmp_path, act, label="A").data_release_id)  # type: ignore[arg-type]

    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ANN202
        statements.append(statement.lower())

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        for params in ({"act_id": act.act_identifier}, {"teryt": TERYT}):
            assert client.get(_summary_url(release_id, **params)).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert any("pog_area_summaries" in statement for statement in statements)
    offending = [
        statement
        for statement in statements
        if any(function in statement for function in SPATIAL_FUNCTIONS)
    ]
    assert offending == []


@pytest.mark.integration
def test_release_switch_atomically_switches_aggregates(
    db_session: Session, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    act_a = _act("1POG", SIXTY_FORTY, boundary=_rect(0, 1000))
    release_a = int(_import(db_session, tmp_path, act_a, label="A").data_release_id)  # type: ignore[arg-type]
    act_b = _act(
        "1POG",
        [("SW", _rect(0, 700)), ("SU", _rect(700, 1000))],
        boundary=_rect(0, 1000),
        version="20260915T000000",
    )
    release_b = int(_import(db_session, tmp_path, act_b, label="B").data_release_id)  # type: ignore[arg-type]
    assert release_b != release_a

    active = client.get("/api/v1/map/pog/releases/active").json()
    assert active["release_id"] == release_b
    assert active["coverage_areas"][0]["is_complete"] is True
    shares_a = [
        zone["share_pct"]
        for zone in client.get(_summary_url(release_a, act_id=act_a.act_identifier)).json()["zones"]
    ]
    shares_b = [
        zone["share_pct"]
        for zone in client.get(_summary_url(release_b, act_id=act_b.act_identifier)).json()["zones"]
    ]
    # Przypięte wydanie A nadal odtwarza własne agregaty; B ma nowe.
    assert shares_a == [pytest.approx(60.0), pytest.approx(40.0)]
    assert shares_b == [pytest.approx(70.0), pytest.approx(30.0)]
    assert client.get(_summary_url(release_a, act_id=act_a.act_identifier)).json()[
        "release_is_active"
    ] is False

    # Awaria obliczenia agregatów wycofuje publikację: aktywne pozostaje B, a
    # nowe wydanie nie zostaje z danymi bez agregatów.
    def broken(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("agregaty niedostępne")

    monkeypatch.setattr(repository_module, "compute_pog_area_summaries", broken)
    act_c = _act(
        "1POG", [("SW", _rect(0, 1000))], boundary=_rect(0, 1000), version="20260920T000000"
    )
    with pytest.raises(RuntimeError, match="agregaty niedostępne"):
        _import(db_session, tmp_path, act_c, label="C")
    active_ids = db_session.execute(
        select(DataRelease.id).where(DataRelease.is_active.is_(True))
    ).scalars().all()
    assert release_b in active_ids
    assert client.get("/api/v1/map/pog/releases/active").json()["release_id"] == release_b
    orphan = db_session.execute(
        text(
            "SELECT count(*) FROM data_releases dr WHERE dr.id > :b AND NOT EXISTS "
            "(SELECT 1 FROM pog_area_summaries s WHERE s.data_release_id = dr.id) "
            "AND EXISTS (SELECT 1 FROM planning_act_versions pav WHERE pav.data_release_id = dr.id)"
        ),
        {"b": release_b},
    ).scalar_one()
    assert orphan == 0


@pytest.mark.integration
def test_reimport_of_identical_artifact_recomputes_without_duplicates(
    db_session: Session, tmp_path: Path
) -> None:
    act = _act("1POG", SIXTY_FORTY, boundary=_rect(0, 1000))
    content = f"bk405-{uuid4().hex}".encode()

    class _SameContent(_Reader):
        def read(self) -> PogSourceBatch:
            return PogSourceBatch(content, "pog.gml", "application/gml+xml", self.acts)

    repository = SqlAlchemyImportRepository(db_session, _source_entry(), LocalArtifactStore(tmp_path))
    release = ImportRelease(
        SOURCE_ID, "A", datetime(2026, 9, 29, tzinfo=timezone.utc), publication_allowed=True
    )
    first = run_pog_import(_SameContent(act), SOURCE_ID, repository, release=release)
    second = run_pog_import(_SameContent(act), SOURCE_ID, repository, release=release)
    assert first.data_release_id == second.data_release_id
    rows = db_session.execute(
        select(PogAreaSummary).where(PogAreaSummary.data_release_id == first.data_release_id)
    ).scalars().all()
    assert sorted(row.scope for row in rows) == ["act", "municipality"]


@pytest.mark.integration
def test_null_denominator_cannot_be_stored_as_complete(db_session: Session, tmp_path: Path) -> None:
    act = _act("1POG", SIXTY_FORTY, boundary=None)
    release_id = int(_import(db_session, tmp_path, act, label="A").data_release_id)  # type: ignore[arg-type]
    with pytest.raises(Exception, match="ck_pog_area_summaries_null_denominator"):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "UPDATE pog_area_summaries SET is_complete = true "
                    "WHERE data_release_id = :release AND scope = 'act'"
                ),
                {"release": release_id},
            )
