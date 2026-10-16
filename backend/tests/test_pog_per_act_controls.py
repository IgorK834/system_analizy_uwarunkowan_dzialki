"""Kontrole importu liczone per akt i zakres cache analiz po gminie (ADR-026).

* Kontrola regresji liczby obiektów porównuje akt wyłącznie z jego własną aktywną wersją: duży akt innej gminy nie
  maskuje ani nie wywołuje regresji, nowy akt (bez poprzedniej wersji) nie jest porównywany z niczym, a odrzucenie
  jednego aktu w paczce odrzuca całą paczkę bez zmiany rejestru.
* Wersja stanu POG w sygnaturze cache (``pog_scope_version``) zależy od aktów gminy działki: import innej gminy jej nie
  zmienia, nowa wersja aktu tej gminy — tak.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.modules.imports.application.common import ImportRelease
from app.modules.imports.application.pog_import import PogValidationError, run_pog_import
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.repository import SqlAlchemyImportRepository
from app.modules.planning.composition import pog_tile_cache
from app.services.cache import current_cache_signature, pog_scope_version
from tests.test_pog_aggregates import SOURCE_ID, TERYT, _act, _rect, _Reader, _source_entry

pytestmark = pytest.mark.integration

FOUR_ZONES = [("SW", _rect(0, 250)), ("SU", _rect(250, 500)), ("SJ", _rect(500, 750)), ("SH", _rect(750, 1000))]


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


def _import(session: Session, tmp_path: Path, *acts, allow_regression: bool = False):  # noqa: ANN002, ANN202
    return run_pog_import(
        _Reader(*acts),
        SOURCE_ID,
        SqlAlchemyImportRepository(session, _source_entry(), LocalArtifactStore(tmp_path)),
        release=ImportRelease(
            SOURCE_ID,
            "per-act",
            datetime(2026, 10, 13, tzinfo=timezone.utc),
            publication_allowed=True,
            dry_run=False,
            teryt_scope=("*",),
            allow_regression=allow_regression,
        ),
    )


def _other_commune(act, teryt: str):  # noqa: ANN001, ANN202
    """Ten sam syntetyczny akt w innej gminie (inne TERYT i identyfikator)."""
    namespace = f"PL.ZIPPZP.9999/{teryt}-POG"
    return replace(
        act,
        teryt=teryt,
        act_identifier=act.act_identifier.replace(f"{TERYT}-POG", f"{teryt}-POG"),
        object_id=replace(act.object_id, namespace=namespace),  # type: ignore[type-var]
        features=tuple(
            replace(feature, object_id=replace(feature.object_id, namespace=namespace))  # type: ignore[type-var]
            for feature in act.features
        ),
    )


def _active_feature_counts(session: Session) -> dict[str, int]:
    rows = session.execute(
        text(
            """
            SELECT pa.act_identifier, count(pf.id)
            FROM pog_registry_entries e
            JOIN planning_acts pa ON pa.id = e.planning_act_id
            LEFT JOIN planning_features pf ON pf.planning_act_version_id = e.planning_act_version_id
            WHERE e.state_to_id IS NULL AND pa.teryt IN (:a, :b)
            GROUP BY pa.act_identifier
            """
        ),
        {"a": TERYT, "b": "999902"},
    ).all()
    return {str(identifier): int(count) for identifier, count in rows}


def _state(session: Session) -> int:
    return int(session.execute(text("SELECT max(id) FROM pog_registry_states")).scalar_one())


def test_regression_is_checked_against_the_acts_own_previous_version_and_rejects_the_whole_package(
    session: Session, tmp_path: Path
) -> None:
    act_x = _act("1POG", FOUR_ZONES, boundary=_rect(0, 1000))
    act_y = _other_commune(_act("2POG", FOUR_ZONES, boundary=_rect(0, 1000)), "999902")
    assert _import(session, tmp_path, act_x, act_y).status == "succeeded"
    session.commit()
    state_before = _state(session)

    shrunk_x = _act("1POG", [("SW", _rect(0, 1000))], boundary=_rect(0, 1000), version="20260920T000000")
    ok_y = replace(act_y, version_started_at=act_y.version_started_at)  # niezmieniony akt innej gminy w tej samej paczce
    with pytest.raises(PogValidationError) as rejected:
        _import(session, tmp_path, shrunk_x, ok_y)
    session.rollback()

    message = str(rejected.value)
    assert act_x.act_identifier in message and "spadła z 4 do 1" in message
    assert act_y.act_identifier not in message, "regresja jednego aktu została przypisana innemu"
    # Paczka odrzucona w całości: rejestr i liczby obiektów bez zmian.
    assert _state(session) == state_before
    assert _active_feature_counts(session) == {act_x.act_identifier: 4, act_y.act_identifier: 4}


def test_explicit_consent_accepts_the_regression_and_reports_it_as_a_warning(
    session: Session, tmp_path: Path
) -> None:
    act_x = _act("1POG", FOUR_ZONES, boundary=_rect(0, 1000))
    assert _import(session, tmp_path, act_x).status == "succeeded"
    session.commit()

    shrunk = _act("1POG", [("SW", _rect(0, 1000))], boundary=_rect(0, 1000), version="20260920T000000")
    outcome = _import(session, tmp_path, shrunk, allow_regression=True)

    assert outcome.status == "succeeded"
    assert any(item.startswith("pog_regression_accepted:") and act_x.act_identifier in item for item in outcome.warnings)
    assert _active_feature_counts(session) == {act_x.act_identifier: 1}


def test_a_smaller_but_not_halved_version_and_a_brand_new_act_are_not_regressions(
    session: Session, tmp_path: Path
) -> None:
    act_x = _act("1POG", FOUR_ZONES, boundary=_rect(0, 1000))
    assert _import(session, tmp_path, act_x).status == "succeeded"
    session.commit()

    three = _act("1POG", FOUR_ZONES[:3], boundary=_rect(0, 1000), version="20260920T000000")  # 3 z 4 = 75%
    # Nowy akt innej gminy z jedną strefą: nie ma poprzedniej wersji, więc nie ma z czym go porównywać.
    new_commune = _other_commune(_act("2POG", [("SW", _rect(0, 1000))], boundary=_rect(0, 1000)), "999902")
    outcome = _import(session, tmp_path, three, new_commune)

    assert outcome.status == "succeeded", outcome.warnings
    assert _active_feature_counts(session) == {act_x.act_identifier: 3, new_commune.act_identifier: 1}


def test_cache_scope_follows_the_parcels_commune_only(session: Session, tmp_path: Path) -> None:
    parcel_a = f"{TERYT}_1.0001.1"
    parcel_b = "999902_1.0001.1"
    act_a = _act("1POG", FOUR_ZONES, boundary=_rect(0, 1000))
    act_b = _other_commune(_act("2POG", FOUR_ZONES, boundary=_rect(0, 1000)), "999902")

    assert _import(session, tmp_path, act_a).status == "succeeded"
    session.commit()
    scope_a_1 = pog_scope_version(session, parcel_a)
    signature_a_1 = current_cache_signature(session, parcel_a)[0]
    assert scope_a_1 == _state(session)
    assert pog_scope_version(session, parcel_b) == 0, "gmina bez aktów nie ma wersji zakresu"

    # Import innej gminy: sygnatura działki z gminy A bez zmian, gmina B dostaje własną wersję.
    assert _import(session, tmp_path, act_b).status == "succeeded"
    session.commit()
    assert pog_scope_version(session, parcel_a) == scope_a_1
    assert current_cache_signature(session, parcel_a)[0] == signature_a_1
    assert pog_scope_version(session, parcel_b) == _state(session) > scope_a_1

    # Nowa wersja aktu gminy A zmienia sygnaturę A, ale nie B.
    scope_b = pog_scope_version(session, parcel_b)
    signature_b = current_cache_signature(session, parcel_b)[0]
    new_a = _act("1POG", FOUR_ZONES[:3], boundary=_rect(0, 1000), version="20260920T000000")
    assert _import(session, tmp_path, new_a).status == "succeeded"
    session.commit()
    assert pog_scope_version(session, parcel_a) == _state(session) > scope_a_1
    assert current_cache_signature(session, parcel_a)[0] != signature_a_1
    assert pog_scope_version(session, parcel_b) == scope_b
    assert current_cache_signature(session, parcel_b)[0] == signature_b


def test_unrecognised_parcel_identifier_uses_the_global_state_number(session: Session, tmp_path: Path) -> None:
    act = _act("1POG", FOUR_ZONES, boundary=_rect(0, 1000))
    assert _import(session, tmp_path, act).status == "succeeded"
    session.commit()
    assert pog_scope_version(session, "bez-teryt") == _state(session)
    assert pog_scope_version(session, None) == _state(session)
