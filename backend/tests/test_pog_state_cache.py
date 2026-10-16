"""Metadane stanu krajowego (``GET /api/v1/map/pog/state``) są liczone raz na stan i raz na wersję aktu (ADR-026).

Pomiar na stanie krajowym (464 akty, 518 tys. obiektów): sprawdzenie „czy każda wersja ma wiersze mapy” kosztowało ponad
sekundę przy każdym żądaniu. Wiersze mapy wersji powstają raz i się nie zmieniają, więc kompletność sprawdza się tylko dla
wersji jeszcze niezweryfikowanych, a stan z brakami nigdy nie trafia do pamięci (po ``pog-map-rebuild`` odpowiedź się zmienia).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from app.main import app
from app.modules.imports.infrastructure.pog_map_tables import rebuild_missing_map_tables
from app.modules.planning.infrastructure.mvt import ready_map_versions, state_meta_cache
from tests.test_pog_aggregates import SIXTY_FORTY, _act, _import, _rect

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


def _delete_map_rows(session: Session, version_id: int) -> None:
    for table in ("pog_map_acts", "pog_map_zones", "pog_map_overlays"):
        session.execute(text(f"DELETE FROM {table} WHERE planning_act_version_id = :id"), {"id": version_id})  # noqa: S608
    session.flush()


def test_incomplete_map_is_reported_never_cached_and_corrected_after_the_rebuild(
    session: Session, client: TestClient, tmp_path: Path
) -> None:
    outcome = _import(session, tmp_path, _act("1POG", SIXTY_FORTY, boundary=_rect(0, 1000)), label="A")
    state_id = int(outcome.stats["registry_state_id"])
    version_id = int(session.execute(text("SELECT planning_act_version_id FROM pog_registry_entries WHERE state_to_id IS NULL")).scalar_one())
    _delete_map_rows(session, version_id)  # wersja sprzed migracji 037: dane są, wierszy mapy jeszcze nie ma

    incomplete = client.get("/api/v1/map/pog/state").json()
    assert incomplete["state_id"] == state_id
    assert incomplete["map_ready"] is False and incomplete["versions_without_map_rows"] == 1
    assert state_meta_cache.get(state_id) is None and version_id not in ready_map_versions

    rebuild_missing_map_tables(session)
    session.flush()
    ready = client.get("/api/v1/map/pog/state").json()
    assert ready["map_ready"] is True and ready["versions_without_map_rows"] == 0
    assert ready["act_count"] == 1 and len(ready["coverage_areas"]) == 1
    assert state_meta_cache.get(state_id) is not None and version_id in ready_map_versions


def test_a_ready_state_is_served_from_memory_and_only_the_flag_of_being_current_is_refreshed(
    session: Session, client: TestClient, tmp_path: Path
) -> None:
    first = _import(session, tmp_path, _act("1POG", SIXTY_FORTY, boundary=_rect(0, 1000)), label="A")
    state_a = int(first.stats["registry_state_id"])
    first_body = client.get(f"/api/v1/map/pog/states/{state_a}").json()
    assert first_body["is_current"] is True and first_body["map_ready"] is True

    # Brak ponownego liczenia: nawet po usunięciu wierszy mapy odpowiedź dla zatwierdzonego stanu pochodzi z pamięci.
    version_id = int(session.execute(text("SELECT planning_act_version_id FROM pog_registry_entries WHERE state_to_id IS NULL")).scalar_one())
    _delete_map_rows(session, version_id)
    cached = client.get(f"/api/v1/map/pog/states/{state_a}").json()
    assert cached["coverage_areas"] == first_body["coverage_areas"] and cached["map_ready"] is True

    # Nowszy stan zmienia tylko flagę „bieżący” starego stanu; zbiory i zasięgi obu stanów są różne.
    second = _import(
        session, tmp_path,
        _act("1POG", [("SW", _rect(0, 700)), ("SU", _rect(700, 1000))], boundary=_rect(0, 1000), version="20260915T000000"),
        label="B",
    )
    state_b = int(second.stats["registry_state_id"])
    assert state_b > state_a
    assert client.get(f"/api/v1/map/pog/states/{state_a}").json()["is_current"] is False
    assert client.get(f"/api/v1/map/pog/states/{state_b}").json()["is_current"] is True
