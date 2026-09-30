"""Migracja 026 (BK-504) i zapis macierzy jakości: trwałość, odczyt i stare wiersze."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.db.session import SessionLocal, engine
from app.models.analysis import Analysis
from app.schemas.source import SectionQualityMatrix
from app.services.persistence import (
    build_analyze_response_from_analysis,
    refresh_report_map_snapshot,
    save_analysis,
)
from app.services.report import generate_analysis_report_pdf
from app.services.section_quality import build_section_quality, quality_to_snapshot
from shapely.geometry import box
from tests.test_persistence import _PARCEL_PREFIX, _cleanup, _rich_response

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def test_migration_follows_head_025_and_is_additive() -> None:
    migration = (VERSIONS / "026_section_quality_matrix.py").read_text(encoding="utf-8")
    assert 'revision: str = "026_section_quality_matrix"' in migration
    assert 'down_revision: Union[str, None] = "025_report_map_snapshot"' in migration
    upgrade = migration.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    assert "op.add_column" in upgrade and "UPDATE" not in upgrade.upper()  # bez ocen wstecz
    assert 'op.drop_column("analyses", "section_quality")' in migration
    assert len([p for p in VERSIONS.glob("02[0-9]_*.py")]) >= 7  # historia nie została przepisana


@pytest.mark.integration
def test_old_row_keeps_null_and_is_reconstructed_on_read_without_backfill() -> None:
    config = Config("alembic.ini")
    suffix = uuid4().hex[:8]
    ids: dict[str, int] = {}
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "025_report_map_snapshot")
        assert "section_quality" not in {c["name"] for c in inspect(engine).get_columns("analyses")}
        with engine.begin() as conn:
            ids["parcel"] = conn.execute(
                text(
                    "INSERT INTO parcels (parcel_identifier, geometry, area_sqm) VALUES "
                    "(:p, ST_Multi(ST_GeomFromText('POLYGON((566000 244000,566060 244000,"
                    "566060 244040,566000 244040,566000 244000))',2180)), 2400) RETURNING id"
                ),
                {"p": f"MIG026_{suffix}"},
            ).scalar_one()
            ids["analysis"] = conn.execute(
                text("INSERT INTO analyses (parcel_id, status) VALUES (:p, 'partial') RETURNING id"),
                {"p": ids["parcel"]},
            ).scalar_one()

        command.upgrade(config, "head")
        assert "section_quality" in {c["name"] for c in inspect(engine).get_columns("analyses")}
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT section_quality FROM analyses WHERE id = :a"), {"a": ids["analysis"]}
            ).scalar_one() is None

        with SessionLocal() as db:
            analysis = db.get(Analysis, ids["analysis"])
            response = build_analyze_response_from_analysis(analysis, db)
            matrix = response.section_quality
            assert matrix is not None and matrix.origin == "reconstructed"
            assert len(matrix.sections) == 10 and matrix.integrity_ok()
            assert all("LEGACY_QUALITY_RECONSTRUCTED" in item.reason_codes for item in matrix.sections)
            pdf = generate_analysis_report_pdf(ids["analysis"], db)
        assert pdf.startswith(b"%PDF")
        with engine.connect() as conn:  # odczyt i raport nie uzupełniają zapisu
            assert conn.execute(
                text("SELECT section_quality FROM analyses WHERE id = :a"), {"a": ids["analysis"]}
            ).scalar_one() is None

        command.downgrade(config, "025_report_map_snapshot")
        assert "section_quality" not in {c["name"] for c in inspect(engine).get_columns("analyses")}
    finally:
        command.upgrade(config, "head")
        if ids:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM analyses WHERE id = :a"), {"a": ids["analysis"]})
                conn.execute(text("DELETE FROM parcels WHERE id = :p"), {"p": ids["parcel"]})


@pytest.fixture
def clean_rows():
    _cleanup()
    yield
    _cleanup()


@pytest.mark.integration
def test_save_analysis_stores_the_matrix_it_was_given_or_issues_one(clean_rows: None) -> None:
    geometry = box(566000, 244000, 566060, 244040)
    issued = _rich_response()
    with SessionLocal() as db:
        saved = save_analysis(issued, f"{_PARCEL_PREFIX}Q_ISSUED", geometry, db)
        stored = saved.section_quality
        assert stored is not None and "legend" not in stored
        matrix = SectionQualityMatrix.model_validate(stored)
        assert matrix.origin == "stored" and matrix.integrity_ok()
        assert matrix.reference_at == issued.analyzed_at  # punkt odniesienia = chwila analizy
        assert [item.section for item in matrix.sections][:3] == ["parcel", "mpzp", "pog"]
        assert saved.result_contract_version and "+quality-v1.0" in saved.result_contract_version
        read = build_analyze_response_from_analysis(saved, db)
        assert read.section_quality == matrix

    provided = build_section_quality(issued, reference_at=issued.analyzed_at + timedelta(days=1))
    with SessionLocal() as db:
        saved = save_analysis(
            issued.model_copy(update={"section_quality": provided}), f"{_PARCEL_PREFIX}Q_GIVEN", geometry, db
        )
        assert saved.section_quality == quality_to_snapshot(provided)  # nie jest nadpisywana


@pytest.mark.integration
def test_stored_matrix_does_not_move_when_time_or_policy_changes(
    clean_rows: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    geometry = box(566000, 244000, 566060, 244040)
    with SessionLocal() as db:
        saved = save_analysis(_rich_response(), f"{_PARCEL_PREFIX}Q_STABLE", geometry, db)
        before = dict(saved.section_quality or {})
        analysis_id = saved.id
    # Zmiana polityki w katalogu (inna wersja) nie zmienia zapisanej oceny.
    import app.services.section_quality as module

    monkeypatch.setattr(module, "get_catalog", lambda: (_ for _ in ()).throw(module.CatalogFileError("x")))
    with SessionLocal() as db:
        read = build_analyze_response_from_analysis(db.get(Analysis, analysis_id), db)
        assert read.section_quality is not None
        assert read.section_quality.policy_version == before["policy_version"]
        assert quality_to_snapshot(read.section_quality) == before


@pytest.mark.integration
def test_refresh_reissues_the_matrix_after_analysis_content_changes(clean_rows: None) -> None:
    geometry = box(566000, 244000, 566060, 244040)
    with SessionLocal() as db:
        saved = save_analysis(_rich_response(), f"{_PARCEL_PREFIX}Q_REFRESH", geometry, db)
        first_hash = saved.section_quality["matrix_sha256"]  # type: ignore[index]
        saved.analyzed_at = saved.analyzed_at + timedelta(days=3)
        refresh_report_map_snapshot(saved, db)
        db.commit()
        db.refresh(saved)
        assert saved.section_quality["matrix_sha256"] != first_hash  # type: ignore[index]
        refreshed = SectionQualityMatrix.model_validate(saved.section_quality)
        assert refreshed.reference_at == saved.analyzed_at and refreshed.integrity_ok()
