"""Import krajowy POG od końca do końca na PostGIS z rzeczywistymi plikami GML z RU (ADR-026).

Wykaz i pliki są zamrożone (``fixtures/ru_national``, nagrane skryptem ``capture_ru_pog_national.py`` 2026-10-13); brama RU
jest podmieniona na odczyt z dysku, reszta (czytnik GML, reprojekcja, QA, publikacja, rejestr, tabele mapy, kafle, analiza)
to produkcyjny kod. Dwie realne gminy (Jejkowice — PL-2000/2177, Sejny — PL-2000/2179) muszą pozostać widoczne razem.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pyproj import Transformer
from shapely import from_wkt
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from app.main import app
from app.modules.imports.infrastructure.pog.ru_gml_reader import RuGmlPogReader, RuGmlSource
from app.modules.planning.composition import pog_tile_cache
from app.services.analysis_orchestrator import _analyze_pog_local_release
from tests.mvt_decoder import decode_tile
from tests.national_support import (
    BARCZEWO,
    FIXTURES,
    JEJKOWICE,
    SEJNY,
    SLUPSK,
    FixtureGateway,
    WFS_URL,
    build_stack,
    fixture_listing,
    frozen_listing,
)

pytestmark = pytest.mark.integration
_TO_WGS84 = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)
TERC = {JEJKOWICE: "241203", SEJNY: "200901", SLUPSK: "226301", BARCZEWO: "281401"}


@pytest.fixture
def session() -> Iterator[Session]:
    db = SessionLocal()
    pog_tile_cache().clear()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
        pog_tile_cache().clear()


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    import math

    n = 1 << zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def _zone_point(act_id: int):  # noqa: ANN202
    """Punkt wewnątrz pierwszej strefy aktu (EPSG:2180) i kafel z16 z jego lon/lat."""
    batch = RuGmlPogReader(
        RuGmlSource((FIXTURES / f"act_{act_id}.gml").read_bytes(), "x.gml", act_id, TERC[act_id], WFS_URL)
    ).read()
    zone = next(f for f in batch.acts[0].features if f.feature_type == "planning_zone")
    shape = from_wkt(zone.geometry.wkt)
    point = shape.representative_point()
    lon, lat = _TO_WGS84.transform(point.x, point.y)
    return point, _tile(lon, lat, 16), zone.object_id.stable_id  # type: ignore[union-attr]


def _active(session: Session) -> dict[int, int]:
    rows = session.execute(
        text("SELECT ru_published_act_id, planning_act_version_id FROM pog_registry_entries WHERE state_to_id IS NULL")
    ).all()
    return {int(row[0]): int(row[1]) for row in rows}


def test_two_real_communes_are_imported_and_both_stay_visible(
    session: Session, client: TestClient, tmp_path: Path
) -> None:
    stack = build_stack(tmp_path)
    gateway = FixtureGateway(fixture_listing())

    result = stack.run(gateway)

    assert (result.status, result.exit_code) == ("succeeded", 0)
    assert {item.published_act_id: item.status for item in result.results} == {
        JEJKOWICE: "succeeded", SEJNY: "succeeded", SLUPSK: "succeeded", BARCZEWO: "succeeded"
    }
    assert sorted(_active(session)) == sorted([JEJKOWICE, SEJNY, SLUPSK, BARCZEWO])

    state = client.get("/api/v1/map/pog/state").json()
    assert state["map_ready"] is True and state["act_count"] == 4
    assert state["acts_by_legal_status"] == {"binding": 2, "in_progress": 2}

    for act_id in (JEJKOWICE, SEJNY):
        point, (x, y), zone_id = _zone_point(act_id)
        parcel = point.buffer(3, cap_style="square")
        analysis = _analyze_pog_local_release(session, parcel, TERC[act_id] + "1")
        assert analysis is not None, f"gmina {TERC[act_id]} niewidoczna w analizie"
        pog = analysis[0]
        assert (pog.legal_status, pog.coverage_status, pog.data_availability) == ("binding", "available", "current")
        assert pog.zones and pog.registry_state_id == state["state_id"]
        assert pog.pinned_act_versions and pog.pinned_act_versions[0].act_identifier == pog.act.act_identifier  # type: ignore[union-attr]

        tile = client.get(state["tile_url_template"].format(z=16, x=x, y=y)).content
        layers = decode_tile(tile)
        assert zone_id in {f.properties["feature_id"] for f in layers["zones"].features}
        details = client.get(
            f"/api/v1/map/pog/states/{state['state_id']}/features/{zone_id}"
        )
        assert details.status_code == 200 and details.json()["act"]["teryt"] == TERC[act_id]
        assert details.json()["act"]["ru_act_url"].endswith(f"/published/details/pog/{act_id}")

    # Przegląd statusów przy małym zoomie: granice obu gmin w jednym kafelku z5/6 (cały kraj), bez stref.
    jejkowice_point, _tile16, _zone = _zone_point(JEJKOWICE)
    ox, oy = _tile(*_TO_WGS84.transform(jejkowice_point.x, jejkowice_point.y), 6)
    overview = decode_tile(client.get(state["tile_url_template"].format(z=6, x=ox, y=oy)).content)
    assert set(overview) == {"act_boundary"}
    statuses = {f.properties["legal_status"] for f in overview["act_boundary"].features}
    assert "binding" in statuses
    assert {f.properties["act_id"] for f in overview["act_boundary"].features} >= {
        "PL.ZIPPZP.10169/241203-POG/1POG"
    }


def test_incremental_run_downloads_nothing_new_and_does_not_change_the_state(
    session: Session, tmp_path: Path
) -> None:
    stack = build_stack(tmp_path)
    first = stack.run(FixtureGateway(fixture_listing()))
    assert first.status == "succeeded" and first.registry_state_after is not None

    gateway = FixtureGateway(fixture_listing())
    second = stack.run(gateway)

    assert second.status == "succeeded" and second.plan["to_import"] == 0 and second.plan["unchanged"] == 4
    assert gateway.downloads == [], "tryb przyrostowy pobrał niezmieniony akt"
    assert second.registry_state_after == first.registry_state_after


def test_new_ru_version_with_the_same_content_updates_only_the_token_and_changed_content_makes_a_new_state(
    session: Session, tmp_path: Path
) -> None:
    stack = build_stack(tmp_path)
    first = stack.run(FixtureGateway(fixture_listing()))
    state_a = first.registry_state_after
    version_before = _active(session)[JEJKOWICE]

    # RU nadało aktowi nowy publishedActId, treść pliku ta sama → token zmieniony w miejscu, bez nowego stanu.
    listing = fixture_listing()
    renumbered = tuple(
        act if act.published_act_id != JEJKOWICE else type(act)(**{**act.__dict__, "published_act_id": 91152})
        for act in listing.acts
    )
    gateway = FixtureGateway(type(listing)(renumbered, listing.declared_total, 1, 1, True, None), overrides={
        91152: (FIXTURES / f"act_{JEJKOWICE}.gml").read_bytes()
    })
    same_content = stack.run(gateway)
    assert same_content.status == "succeeded" and same_content.registry_state_after == state_a
    session.expire_all()
    active = _active(session)
    assert 91152 in active and JEJKOWICE not in active and active[91152] == version_before
    assert same_content.results[0].status == "unchanged"

    # Zmieniona treść (inny tytuł aktu) → nowa wersja i nowy numer stanu; stara wersja zamknięta jako „replaced”.
    original = (FIXTURES / f"act_{JEJKOWICE}.gml").read_text(encoding="utf-8")
    edited = original.replace("Plan ogólny gminy Jejkowice", "Plan ogólny gminy Jejkowice (zmiana)", 1)
    assert edited != original
    renumbered_again = tuple(
        act if act.published_act_id != 91152 else type(act)(**{**act.__dict__, "published_act_id": 91153})
        for act in gateway.listing.acts
    )
    changed = stack.run(FixtureGateway(
        type(listing)(renumbered_again, listing.declared_total, 1, 1, True, None), overrides={91153: edited.encode()}
    ))
    assert changed.status == "succeeded" and changed.registry_state_after > state_a  # type: ignore[operator]
    session.expire_all()
    row = session.execute(
        text(
            "SELECT count(*) FILTER (WHERE state_to_id IS NOT NULL AND end_reason = 'replaced'), "
            "count(*) FILTER (WHERE state_to_id IS NULL) FROM pog_registry_entries e "
            "JOIN planning_acts pa ON pa.id = e.planning_act_id WHERE pa.teryt = '241203'"
        )
    ).one()
    assert tuple(row) == (1, 1)


def test_act_removed_from_ru_is_withdrawn_but_old_state_and_history_remain(
    session: Session, client: TestClient, tmp_path: Path
) -> None:
    stack = build_stack(tmp_path)
    first = stack.run(FixtureGateway(fixture_listing()))
    state_a = int(first.registry_state_after)  # type: ignore[arg-type]
    point, (x, y), zone_id = _zone_point(SEJNY)

    # Wykaz kompletny, ale bez Sejn: wycofanie jednego z czterech aktów mieści się w limicie (≤ 5 naraz).
    gateway = FixtureGateway(fixture_listing(JEJKOWICE, SLUPSK, BARCZEWO))
    result = stack.run(gateway)

    assert result.status == "succeeded" and result.withdrawal["withdrawn"] == 1
    assert gateway.downloads == []
    session.expire_all()
    assert sorted(_active(session)) == sorted([JEJKOWICE, SLUPSK, BARCZEWO])
    state_b = int(result.registry_state_after)  # type: ignore[arg-type]
    assert state_b > state_a
    reason = session.execute(
        text("SELECT end_reason FROM pog_registry_entries WHERE ru_published_act_id = :id"), {"id": SEJNY}
    ).scalar_one()
    assert reason == "not_current"
    # Analiza bieżącego stanu nie widzi Sejn, a stan przypięty A nadal je odtwarza (raporty zapisane wcześniej).
    parcel = point.buffer(3, cap_style="square")
    assert _analyze_pog_local_release(session, parcel, "2009011") is None
    template = "/api/v1/map/pog/states/{state}/{z}/{x}/{y}.mvt"
    old = decode_tile(client.get(template.format(state=state_a, z=16, x=x, y=y)).content)
    new = decode_tile(client.get(template.format(state=state_b, z=16, x=x, y=y)).content)
    assert zone_id in {f.properties["feature_id"] for f in old["zones"].features}
    assert "zones" not in new or zone_id not in {f.properties["feature_id"] for f in new["zones"].features}
    assert client.get(f"/api/v1/map/pog/states/{state_b}/features/{zone_id}").status_code == 404
    assert client.get(f"/api/v1/map/pog/states/{state_a}/features/{zone_id}").status_code == 200


def test_one_failing_download_does_not_block_the_others_and_is_retried_by_the_next_run(
    session: Session, tmp_path: Path
) -> None:
    stack = build_stack(tmp_path)
    partial = stack.run(FixtureGateway(fixture_listing(), failing={SLUPSK: "transport"}), act_retries=0)

    assert (partial.status, partial.exit_code) == ("partial", 3)
    assert {item.published_act_id for item in partial.results if item.status == "failed"} == {SLUPSK}
    payload = partial.as_payload()
    assert payload["failure_codes"] == {"transport": 1} and payload["counts"]["succeeded"] == 3
    assert sorted(_active(session)) == sorted([JEJKOWICE, SEJNY, BARCZEWO])

    rerun = stack.run(FixtureGateway(fixture_listing()))
    assert rerun.status == "succeeded" and rerun.plan["to_import"] == 1
    session.expire_all()
    assert sorted(_active(session)) == sorted([JEJKOWICE, SEJNY, SLUPSK, BARCZEWO])


def test_transient_download_error_is_retried_inside_the_run(tmp_path: Path) -> None:
    stack = build_stack(tmp_path)

    class Flaky(FixtureGateway):
        def download(self, act):  # noqa: ANN001, ANN201
            if act.published_act_id == SEJNY and self.downloads.count(SEJNY) == 0:
                self.downloads.append(SEJNY)
                raise __import__("app.modules.imports.infrastructure.ru_published", fromlist=["x"]).RuDownloadError(
                    "rate_limited", "429"
                )
            return super().download(act)

    result = stack.run(Flaky(fixture_listing(JEJKOWICE, SEJNY)))
    sejny = next(item for item in result.results if item.published_act_id == SEJNY)
    assert result.status == "succeeded" and sejny.status == "succeeded" and sejny.attempts == 2


def test_corrupt_gml_is_rejected_per_act_with_a_stable_code(session: Session, tmp_path: Path) -> None:
    stack = build_stack(tmp_path)
    broken = (FIXTURES / f"act_{SEJNY}.gml").read_bytes().replace(b"200901-POG", b"999999-POG")
    result = stack.run(FixtureGateway(fixture_listing(JEJKOWICE, SEJNY), overrides={SEJNY: broken}))

    failures = {item.published_act_id: item for item in result.results if item.status == "failed"}
    assert set(failures) == {SEJNY} and failures[SEJNY].code == "gml_terc_mismatch"
    assert sorted(_active(session)) == [JEJKOWICE]


def test_percent_parameter_above_100_in_the_source_rejects_only_that_act_and_names_the_feature(
    session: Session, tmp_path: Path
) -> None:
    # Realny przypadek z RU (Bolków, akt 3438, 2026-10-13): minUdzialPowierzchniBiologicznieCzynnej = 150.0 w jednej strefie.
    stack = build_stack(tmp_path)
    original = (FIXTURES / f"act_{SEJNY}.gml").read_text(encoding="utf-8")
    broken = original.replace(
        "<app:minUdzialPowierzchniBiologicznieCzynnej>30.0<", "<app:minUdzialPowierzchniBiologicznieCzynnej>150.0<", 1
    )
    assert broken != original
    result = stack.run(FixtureGateway(fixture_listing(JEJKOWICE, SEJNY), overrides={SEJNY: broken.encode()}))

    failures = {item.published_act_id: item for item in result.results if item.status == "failed"}
    assert set(failures) == {SEJNY} and result.status == "partial" and result.exit_code == 3
    assert failures[SEJNY].code == "qa_rejected"  # stały kod wady danych źródła, nie nazwa klasy wyjątku
    message = failures[SEJNY].message or ""
    # Obiekt (identyfikator idIIP) i wartość są wskazane operatorowi, który zgłasza wadę wydawcy aktu.
    assert "wartość: 150" in message and message.startswith("PL.ZIPPZP.11530/200901-POG/")
    assert sorted(_active(session)) == [JEJKOWICE]  # druga gmina bez wpływu, brak częściowego aktu w rejestrze
    assert result.as_payload()["failure_codes"] == {"qa_rejected": 1}


def test_dry_run_and_plan_only_publish_nothing(session: Session, tmp_path: Path) -> None:
    stack = build_stack(tmp_path)
    dry = stack.run(FixtureGateway(fixture_listing()), dry_run=True)
    assert dry.status == "succeeded" and {item.status for item in dry.results} == {"dry_run"}
    assert _active(session) == {} and dry.registry_state_after is None

    gateway = FixtureGateway(fixture_listing())
    planned = stack.run(gateway, plan_only=True)
    assert planned.status == "plan_only" and gateway.downloads == [] and planned.plan["to_import"] == 4


def test_cancel_stops_scheduling_and_reports_an_incomplete_run(session: Session, tmp_path: Path) -> None:
    stack = build_stack(tmp_path)
    cancel = threading.Event()
    cancel.set()
    result = stack.run(FixtureGateway(fixture_listing()), cancel=cancel)
    assert (result.status, result.exit_code) == ("interrupted", 3)
    assert result.resume is not None and _active(session) == {}


def test_catalog_is_stored_and_marks_missing_acts_only_for_a_complete_listing(
    session: Session, tmp_path: Path
) -> None:
    stack = build_stack(tmp_path)
    stack.run(FixtureGateway(frozen_listing()), plan_only=False, limit=0)
    session.expire_all()
    current = session.execute(text("SELECT count(*) FROM pog_ru_catalog WHERE is_current")).scalar_one()
    assert current == 465

    stack.run(FixtureGateway(fixture_listing()), limit=0)
    session.expire_all()
    # Wykaz kompletny (z własnego punktu widzenia) usuwa pozostałe z „bieżących” — to dane katalogu, nie rejestr.
    assert session.execute(text("SELECT count(*) FROM pog_ru_catalog WHERE is_current")).scalar_one() == 4
