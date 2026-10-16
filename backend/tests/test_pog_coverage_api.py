"""Pokrycie krajowe POG (issue 301): stany gmin, daty i wersje po imporcie realnych plików GML."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from app.main import app
from app.modules.imports.infrastructure.job_queue import enqueue_commune_import
from tests.national_support import (
    BARCZEWO,
    FIXTURES,
    JEJKOWICE,
    SEJNY,
    SLUPSK,
    FixtureGateway,
    build_stack,
    fixture_listing,
    frozen_listing,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_coverage_lists_imported_communes_with_dates_versions_and_marks_the_rest_as_waiting(
    session: Session, client: TestClient, tmp_path: Path
) -> None:
    stack = build_stack(tmp_path)
    # Wykaz RU zawiera 465 aktów, importujemy trzy gminy: pozostałe mają plan w RU, ale nie są zaimportowane.
    stack.run(FixtureGateway(frozen_listing()), terc=("241203", "200901", "281401"))
    with SessionLocal() as db:
        enqueue_commune_import(db, "226301", requested_by="analysis")
        db.commit()
    session.expire_all()

    body = client.get("/api/v1/coverage/pog").json()

    assert body["catalog_fresh"] is True and body["catalog_synced_at"]
    summary = body["summary"]
    assert (summary["ru_communes"], summary["imported_communes"], summary["in_ru_not_imported"]) == (462, 3, 459)
    assert summary["imported_without_ru_entry"] == 0 and summary["outdated_versions"] == 0
    communes = {item["terc"]: item for item in body["communes"]}
    jejkowice = communes["241203"]
    assert jejkowice["status"] == "imported" and jejkowice["name"] == "Jejkowice"
    (act,) = jejkowice["imported_acts"]
    assert act["legal_status"] == "binding" and act["up_to_date"] is True
    assert len(act["content_hash"]) == 64 and act["zone_count"] > 0 and act["imported_at"] and act["fetched_at"]
    assert act["ru_published_act_id"] == JEJKOWICE and act["state_id"] == summary["state_id"] or act["state_id"] > 0
    waiting = communes["226301"]
    assert waiting["status"] == "in_ru_not_imported" and waiting["imported_acts"] == []
    assert waiting["import_job"]["status"] == "queued"
    assert communes["246101"]["status"] == "in_ru_not_imported"  # Bielsko-Biała: plan w RU, jeszcze bez importu

    only_waiting = client.get("/api/v1/coverage/pog?status=in_ru_not_imported&q=bielsko").json()["communes"]
    assert [item["terc"] for item in only_waiting] == ["246101"]
    only_imported = client.get("/api/v1/coverage/pog?status=imported").json()["communes"]
    assert {item["terc"] for item in only_imported} == {"241203", "200901", "281401"}


def test_commune_lookup_separates_the_three_states_and_unknown(
    session: Session, client: TestClient, tmp_path: Path
) -> None:
    unknown = client.get("/api/v1/coverage/pog/commune/246101").json()
    assert unknown["status"] == "unknown", "bez wykazu RU nie wolno twierdzić „brak planu”"

    stack = build_stack(tmp_path)
    stack.run(FixtureGateway(frozen_listing()), terc=("241203",))
    session.expire_all()

    assert client.get("/api/v1/coverage/pog/commune/2412031").json()["status"] == "imported"
    assert client.get("/api/v1/coverage/pog/commune/246101").json()["status"] == "in_ru_not_imported"
    absent = client.get("/api/v1/coverage/pog/commune/146501").json()  # Warszawa: brak aktu POG w wykazie RU
    assert absent["status"] == "no_act_in_ru" and "nie dowodzi" in absent["detail"]
    assert client.get("/api/v1/coverage/pog/commune/12").json()["status"] == "unknown"


def test_point_coverage_uses_imported_act_boundaries(session: Session, client: TestClient, tmp_path: Path) -> None:
    from pyproj import Transformer
    from shapely import from_wkt

    from app.modules.imports.infrastructure.pog.ru_gml_reader import RuGmlPogReader, RuGmlSource
    from tests.national_support import FIXTURES, WFS_URL

    stack = build_stack(tmp_path)
    stack.run(FixtureGateway(fixture_listing(JEJKOWICE, SEJNY, SLUPSK, BARCZEWO)))
    session.expire_all()
    batch = RuGmlPogReader(RuGmlSource((FIXTURES / f"act_{JEJKOWICE}.gml").read_bytes(), "x.gml", JEJKOWICE, "241203", WFS_URL)).read()
    inside = from_wkt(batch.acts[0].boundary.wkt).representative_point()  # type: ignore[union-attr]
    lon, lat = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True).transform(inside.x, inside.y)

    covered = client.get(f"/api/v1/coverage/pog/point?lon={lon}&lat={lat}").json()
    assert covered["covered"] is True and covered["acts"][0]["teryt"] == "241203"
    outside = client.get("/api/v1/coverage/pog/point?lon=0.5&lat=40.0").json()
    assert outside["covered"] is False and "nie oznacza braku planu" in outside["note"]


def test_coverage_names_the_last_rejected_import_attempt_of_a_waiting_commune(
    session: Session, client: TestClient, tmp_path: Path
) -> None:
    """Gmina czekająca na import, której akt odrzuciła kontrola jakości (wada danych RU), ma to wskazane w pokryciu."""
    stack = build_stack(tmp_path)
    original = (FIXTURES / f"act_{SEJNY}.gml").read_text(encoding="utf-8")
    broken = original.replace(
        "<app:minUdzialPowierzchniBiologicznieCzynnej>30.0<", "<app:minUdzialPowierzchniBiologicznieCzynnej>150.0<", 1
    )
    result = stack.run(FixtureGateway(fixture_listing(JEJKOWICE, SEJNY), overrides={SEJNY: broken.encode()}))
    assert result.status == "partial"
    session.expire_all()

    communes = {item["terc"]: item for item in client.get("/api/v1/coverage/pog").json()["communes"]}

    sejny = communes["200901"]
    assert sejny["status"] == "in_ru_not_imported"  # niezaimportowana, a nie „brak planu”
    failure = sejny["last_import_failure"]
    assert failure["code"] == "qa_rejected" and failure["published_act_id"] == SEJNY
    assert "wartość: 150" in failure["message"] and failure["run_id"]
    assert communes["241203"]["status"] == "imported" and communes["241203"]["last_import_failure"] is None
    assert set(communes) == {"241203", "200901"}  # wykaz tego przebiegu ma dwa akty
