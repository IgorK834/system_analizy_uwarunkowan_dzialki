"""Zapis, odczyt i przeliczenie kompletności analizy w PostGIS (AU-104)."""

from __future__ import annotations

import pytest
from shapely.geometry import box
from sqlalchemy import select, update

from app.db.session import SessionLocal
from app.models.analysis import Analysis
from app.models.mpzp_zone import MpzpZone
from app.services.completeness import build_completeness, completeness_to_snapshot
from app.services.persistence import (
    build_analyze_response_from_analysis,
    refresh_report_map_snapshot,
    save_analysis,
)
from tests.test_persistence import _PARCEL_PREFIX, _cleanup, _context_result, _rich_response

pytestmark = pytest.mark.integration

GEOMETRY = box(500000, 200000, 500100, 200100)
IDENTIFIER = f"{_PARCEL_PREFIX}COMPLETENESS"


@pytest.fixture(autouse=True)
def clean() -> None:
    _cleanup()
    yield
    _cleanup()


def _save() -> int:
    response = _rich_response().model_copy(update={"parcel": _rich_response().parcel.model_copy(update={"parcel_identifier": IDENTIFIER})})
    with SessionLocal() as db:
        return save_analysis(response, IDENTIFIER, GEOMETRY, db, context_result=_context_result()).id


def test_saved_analysis_stores_the_completeness_assessed_at_analysis_time() -> None:
    analysis_id = _save()

    with SessionLocal() as db:
        saved = db.get(Analysis, analysis_id)
        assert saved is not None and saved.completeness is not None
        loaded = build_analyze_response_from_analysis(saved, db)
        expected = completeness_to_snapshot(build_completeness(loaded.model_copy(update={"completeness": None})))

    assert saved.completeness["rule_version"] == "completeness/1.2"
    assert saved.completeness["headline"] == expected["headline"]
    assert saved.completeness["checked"] == expected["checked"] and saved.completeness["total"] == expected["total"]
    assert loaded.completeness is not None and loaded.completeness.origin == "stored"
    assert loaded.headline == loaded.completeness.headline == saved.completeness["headline"]


def test_record_from_before_au104_is_reconstructed_on_read_and_keeps_its_stored_status() -> None:
    analysis_id = _save()
    with SessionLocal() as db:
        db.execute(update(Analysis).where(Analysis.id == analysis_id).values(completeness=None, status="complete"))
        db.commit()

    with SessionLocal() as db:
        loaded = build_analyze_response_from_analysis(db.get(Analysis, analysis_id), db)
        stored_after_read = db.get(Analysis, analysis_id).completeness

    assert loaded.completeness is not None and loaded.completeness.origin == "reconstructed"
    assert loaded.headline == loaded.completeness.headline
    assert loaded.status == "complete"  # zapisany status nagłówkowy ze starej definicji nie jest przepisywany
    assert stored_after_read is None  # odczyt nie zapisuje wstecz


def test_changing_the_content_of_the_analysis_recomputes_the_completeness() -> None:
    """Wznowienie ręcznego symbolu zmienia strefy: symbol ręczny nie jest ustaleniem systemu."""
    analysis_id = _save()
    with SessionLocal() as db:
        before = db.get(Analysis, analysis_id).completeness
        for zone in db.scalars(select(MpzpZone).where(MpzpZone.analysis_id == analysis_id)):
            zone.assignment_method = "manual_user_input"  # kolumna i snapshot odpowiedzi (odczyt idzie ze snapshotu)
            zone.result_snapshot = {**zone.result_snapshot, "assignment_method": "manual_user_input"}
        db.commit()
        analysis = db.get(Analysis, analysis_id)
        refresh_report_map_snapshot(analysis, db)
        db.commit()
        after = db.get(Analysis, analysis_id).completeness
        zones = db.scalars(select(MpzpZone).where(MpzpZone.analysis_id == analysis_id)).all()

    assert zones and all(zone.assignment_method == "manual_user_input" for zone in zones)
    mpzp_after = next(item for item in after["sections"] if item["section"] == "mpzp")
    assert mpzp_after["state"] == "missing" and after["complete"] is False
    assert before["sections"] != after["sections"]
