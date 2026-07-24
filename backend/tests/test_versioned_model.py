"""Testy wersjonowanego modelu danych i zapytań as_of (Faza 10.3).

Wymagają PostGIS (marker integration). Sprawdzają strukturę migracji, constrainty
(FK, check, unique, częściowy indeks aktywnej wersji, GiST), reguły temporalne
oraz publiczne zapytania as_of.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.elements import WKTElement
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.models.versioned import (
    DataRelease,
    DataSource,
    DocumentVersion,
    ParcelVersion,
    PlanningAct,
    PlanningActVersion,
    SourceArtifact,
    SourceDocument,
)
from app.services.versioned_repository import (
    document_version_as_of,
    parcel_version_as_of,
    planning_act_version_as_of,
)

pytestmark = pytest.mark.integration

_SQUARE_WKT = "MULTIPOLYGON(((0 0,0 10,10 10,10 0,0 0)))"
_T0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
_T1 = datetime(2022, 1, 1, tzinfo=timezone.utc)
_T2 = datetime(2024, 1, 1, tzinfo=timezone.utc)

_REQUIRED_TABLES = {
    "data_sources",
    "source_artifacts",
    "data_releases",
    "import_runs",
    "parcel_versions",
    "planning_acts",
    "planning_act_versions",
    "plan_boundaries",
    "land_use_areas",
    "planning_symbols",
    "planning_features",
    "source_documents",
    "document_versions",
    "document_pages",
    "legal_units",
    "symbol_legal_units",
    "manual_reviews",
    "planning_rules",
}


@pytest.fixture
def session() -> Iterator[Session]:
    # Testy nie commitują — rollback izoluje dane i porządkuje aborted transaction.
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@dataclass
class _Prereq:
    parcel_id: int
    act_id: int
    artifact_id: int
    release_id: int
    document_id: int


def _multipolygon(srid: int = 2180) -> WKTElement:
    return WKTElement(_SQUARE_WKT, srid=srid)


def _prerequisites(session: Session) -> _Prereq:
    """Tworzy i flushuje minimalny łańcuch pochodzenia dla wersji."""
    suffix = uuid4().hex[:10]
    source = DataSource(
        source_id=f"src_{suffix}", owner="Test", status="production"
    )
    session.add(source)
    session.flush()

    artifact = SourceArtifact(
        data_source_id=source.id,
        uri=f"s3://bucket/{suffix}",
        content_hash=f"hash_{suffix}",
        fetched_at=_T0,
    )
    release = DataRelease(
        data_source_id=source.id, version_label=f"v_{suffix}", published_at=_T0
    )
    session.add_all([artifact, release])
    session.flush()

    parcel_id = session.execute(
        text(
            "INSERT INTO parcels (parcel_identifier, geometry) VALUES "
            "(:identifier, ST_Multi(ST_GeomFromText(:wkt, 2180))) RETURNING id"
        ),
        {"identifier": f"parcel_{suffix}", "wkt": "POLYGON((0 0,0 10,10 10,10 0,0 0))"},
    ).scalar_one()

    act = PlanningAct(act_identifier=f"act_{suffix}", kind="mpzp")
    session.add(act)
    session.flush()

    document = SourceDocument(planning_act_id=act.id, title="Uchwała testowa")
    session.add(document)
    session.flush()

    return _Prereq(
        parcel_id=parcel_id,
        act_id=act.id,
        artifact_id=artifact.id,
        release_id=release.id,
        document_id=document.id,
    )


def _parcel_version(prereq: _Prereq, **overrides: object) -> ParcelVersion:
    values: dict = {
        "parcel_id": prereq.parcel_id,
        "geometry": _multipolygon(),
        "source_artifact_id": prereq.artifact_id,
        "data_release_id": prereq.release_id,
        "valid_from": _T0,
        "valid_to": None,
        "published_at": _T0,
        "content_hash": "abc123",
        "review_status": "verified",
    }
    values.update(overrides)
    return ParcelVersion(**values)


# --- Struktura migracji ------------------------------------------------------


def test_upgrade_head_creates_all_required_tables() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    tables = set(inspect(engine).get_table_names())
    assert _REQUIRED_TABLES <= tables
    # Stary model pozostaje.
    assert {"analyses", "parcels", "mpzp_zones"} <= tables


def test_parcels_extended_with_teryt_column() -> None:
    columns = {c["name"] for c in inspect(engine).get_columns("parcels")}
    assert "teryt" in columns
    # Rozszerzenie kompatybilne: nowa kolumna jest nullable.
    teryt = next(
        c for c in inspect(engine).get_columns("parcels") if c["name"] == "teryt"
    )
    assert teryt["nullable"] is True


def test_parcel_versions_geometry_srid_and_gist() -> None:
    with engine.connect() as connection:
        srid = connection.execute(
            text(
                "SELECT srid FROM geometry_columns WHERE f_table_name = "
                "'parcel_versions' AND f_geometry_column = 'geometry'"
            )
        ).scalar_one()
        gist = connection.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE tablename = "
                "'parcel_versions' AND indexdef ILIKE '%gist%'"
            )
        ).scalars()
    assert srid == 2180
    assert "ix_parcel_versions_geometry_gist" in set(gist)


def test_parcel_versions_foreign_keys() -> None:
    fks = inspect(engine).get_foreign_keys("parcel_versions")
    referred = {fk["referred_table"] for fk in fks}
    assert {"parcels", "source_artifacts", "data_releases"} <= referred


def test_parcel_versions_check_constraints_present() -> None:
    checks = {c["name"] for c in inspect(engine).get_check_constraints("parcel_versions")}
    assert "ck_parcel_versions_valid_range" in checks
    assert "ck_parcel_versions_geometry_not_empty" in checks
    assert "ck_parcel_versions_review_status" in checks


def test_active_version_partial_unique_index_present() -> None:
    indexes = {i["name"]: i for i in inspect(engine).get_indexes("parcel_versions")}
    assert "uq_parcel_versions_active" in indexes
    assert indexes["uq_parcel_versions_active"]["unique"] is True


def test_source_artifacts_unique_dedup_constraint() -> None:
    uniques = {
        c["name"] for c in inspect(engine).get_unique_constraints("source_artifacts")
    }
    assert "uq_source_artifacts_dedup" in uniques


def test_document_legal_structure_and_planning_rule_constraints_present() -> None:
    document_columns = {
        column["name"] for column in inspect(engine).get_columns("document_versions")
    }
    assert {
        "media_type",
        "extraction_method",
        "ocr_engine_version",
        "quality_score",
    } <= document_columns
    page_columns = {
        column["name"] for column in inspect(engine).get_columns("document_pages")
    }
    assert "blocks" in page_columns
    legal_columns = {
        column["name"] for column in inspect(engine).get_columns("legal_units")
    }
    assert {
        "unit_type",
        "number",
        "parent_id",
        "order_index",
        "page_from",
        "page_to",
        "source_text",
        "normalized_text",
    } <= legal_columns
    checks = {
        check["name"]
        for check in inspect(engine).get_check_constraints("planning_rules")
    }
    assert {
        "ck_planning_rules_review_status",
        "ck_planning_rules_confidence",
        "ck_planning_rules_evidence_required",
        "ck_planning_rules_value_range",
    } <= checks


# --- Reguły temporalne i geometryczne ----------------------------------------


def test_two_historical_act_versions_coexist(session: Session) -> None:
    prereq = _prerequisites(session)
    common = {
        "planning_act_id": prereq.act_id,
        "source_artifact_id": prereq.artifact_id,
        "data_release_id": prereq.release_id,
        "published_at": _T0,
        "content_hash": "h",
        "review_status": "verified",
    }
    # Dwie zamknięte (historyczne) wersje — dozwolone.
    session.add(PlanningActVersion(valid_from=_T0, valid_to=_T1, **common))
    session.add(PlanningActVersion(valid_from=_T1, valid_to=_T2, **common))
    session.flush()

    count = session.execute(
        text(
            "SELECT count(*) FROM planning_act_versions WHERE planning_act_id = :id"
        ),
        {"id": prereq.act_id},
    ).scalar_one()
    assert count == 2


def test_two_active_versions_same_release_rejected(session: Session) -> None:
    prereq = _prerequisites(session)
    session.add(_parcel_version(prereq, valid_to=None, content_hash="a"))
    session.add(_parcel_version(prereq, valid_to=None, content_hash="b"))
    with pytest.raises(DBAPIError):
        session.flush()


def test_empty_geometry_rejected(session: Session) -> None:
    prereq = _prerequisites(session)
    session.add(
        _parcel_version(prereq, geometry=WKTElement("MULTIPOLYGON EMPTY", srid=2180))
    )
    with pytest.raises(DBAPIError):
        session.flush()


def test_wrong_srid_rejected(session: Session) -> None:
    prereq = _prerequisites(session)
    session.add(_parcel_version(prereq, geometry=_multipolygon(srid=4326)))
    with pytest.raises(DBAPIError):
        session.flush()


def test_invalid_valid_range_rejected(session: Session) -> None:
    prereq = _prerequisites(session)
    # valid_to <= valid_from narusza zakres prawostronnie otwarty.
    session.add(_parcel_version(prereq, valid_from=_T1, valid_to=_T0))
    with pytest.raises(DBAPIError):
        session.flush()


def test_invalid_review_status_rejected(session: Session) -> None:
    prereq = _prerequisites(session)
    session.add(_parcel_version(prereq, review_status="nieznany"))
    with pytest.raises(DBAPIError):
        session.flush()


# --- Zapytania as_of ---------------------------------------------------------


def test_parcel_version_as_of_returns_valid_version(session: Session) -> None:
    prereq = _prerequisites(session)
    # Wersja historyczna [T0, T1) i aktywna [T1, ∞).
    session.add(
        _parcel_version(prereq, valid_from=_T0, valid_to=_T1, content_hash="old")
    )
    session.add(
        _parcel_version(prereq, valid_from=_T1, valid_to=None, content_hash="new")
    )
    session.flush()

    at_before = _T0 - timedelta(days=1)
    in_first = _T0 + timedelta(days=10)
    after = _T2

    assert parcel_version_as_of(session, prereq.parcel_id, at_before) is None
    first = parcel_version_as_of(session, prereq.parcel_id, in_first)
    assert first is not None and first.content_hash == "old"
    latest = parcel_version_as_of(session, prereq.parcel_id, after)
    assert latest is not None and latest.content_hash == "new"


def test_parcel_version_as_of_excludes_expired(session: Session) -> None:
    prereq = _prerequisites(session)
    # Tylko wersja wygasła [T0, T1); zapytanie po T1 nie może jej zwrócić.
    session.add(
        _parcel_version(prereq, valid_from=_T0, valid_to=_T1, content_hash="old")
    )
    session.flush()
    assert parcel_version_as_of(session, prereq.parcel_id, _T2) is None
    # Na granicy valid_to (zakres prawostronnie otwarty) też wygasła.
    assert parcel_version_as_of(session, prereq.parcel_id, _T1) is None


def test_planning_act_version_as_of(session: Session) -> None:
    prereq = _prerequisites(session)
    common = {
        "planning_act_id": prereq.act_id,
        "source_artifact_id": prereq.artifact_id,
        "data_release_id": prereq.release_id,
        "published_at": _T0,
        "content_hash": "h",
        "review_status": "verified",
    }
    session.add(PlanningActVersion(valid_from=_T0, valid_to=None, **common))
    session.flush()
    result = planning_act_version_as_of(session, prereq.act_id, _T2)
    assert result is not None
    assert planning_act_version_as_of(session, prereq.act_id, _T0 - timedelta(1)) is None


def test_document_version_as_of(session: Session) -> None:
    prereq = _prerequisites(session)
    session.add(
        DocumentVersion(
            source_document_id=prereq.document_id,
            source_artifact_id=prereq.artifact_id,
            data_release_id=prereq.release_id,
            valid_from=_T0,
            valid_to=None,
            published_at=_T0,
            content_hash="h",
            review_status="unreviewed",
        )
    )
    session.flush()
    assert document_version_as_of(session, prereq.document_id, _T1) is not None


# --- Migracja: up / down / up i idempotencja ---------------------------------


def test_migration_upgrade_downgrade_upgrade_roundtrip() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    try:
        command.downgrade(config, "006_map_layer_geojson")
        tables_after_downgrade = set(inspect(engine).get_table_names())
        assert "parcel_versions" not in tables_after_downgrade
        # Stary model nietknięty przez downgrade.
        assert {"analyses", "parcels"} <= tables_after_downgrade

        command.upgrade(config, "head")
        tables_after_upgrade = set(inspect(engine).get_table_names())
        assert _REQUIRED_TABLES <= tables_after_upgrade
    finally:
        command.upgrade(config, "head")


def test_upgrade_head_is_idempotent() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    # Ponowne wywołanie nie może rzucić błędu.
    command.upgrade(config, "head")
