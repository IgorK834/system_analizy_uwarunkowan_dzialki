"""Migracja 017 (BK-107): provenance aktu, wersjonowane dokumenty i rekordy CSW."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import engine

MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "017_pog_act_provenance.py"
)


def test_migration_follows_016() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "017_pog_act_provenance"' in source
    assert 'down_revision: Union[str, None] = "016_pog_status_coverage"' in source


@pytest.mark.integration
def test_upgrade_adds_provenance_and_keeps_document_versions_apart() -> None:
    config = Config("alembic.ini")
    suffix = uuid4().hex[:8]
    ids: dict[str, int] = {}
    try:
        command.upgrade(config, "head")
        columns = {c["name"] for c in inspect(engine).get_columns("planning_act_versions")}
        assert {"publication_id", "version_started_at", "legal_valid_from", "legal_valid_to",
                "source_reference"} <= columns
        doc_columns = {c["name"] for c in inspect(engine).get_columns("pog_formal_documents")}
        assert {"record_sha256", "link_verified", "resolution_status", "relation",
                "repeal_date", "publication_id"} <= doc_columns
        assert "pog_act_metadata_records" in inspect(engine).get_table_names()

        with engine.begin() as conn:
            ids["source"] = conn.execute(
                text("INSERT INTO data_sources (source_id, owner, status) VALUES (:s, 't', 'production') RETURNING id"),
                {"s": f"mig017_{suffix}"},
            ).scalar_one()
            ids["artifact"] = conn.execute(
                text("INSERT INTO source_artifacts (data_source_id, uri, content_hash, fetched_at) "
                     "VALUES (:d, 'file:///x', :h, now()) RETURNING id"),
                {"d": ids["source"], "h": "e" * 64},
            ).scalar_one()
            ids["release"] = conn.execute(
                text("INSERT INTO data_releases (data_source_id, version_label, published_at) "
                     "VALUES (:d, 'mig017', now()) RETURNING id"),
                {"d": ids["source"]},
            ).scalar_one()
            ids["act"] = conn.execute(
                text("INSERT INTO planning_acts (act_identifier, teryt, kind) VALUES (:a, '226401', 'pog') RETURNING id"),
                {"a": f"mig017-{suffix}"},
            ).scalar_one()
            ids["version"] = conn.execute(
                text("INSERT INTO planning_act_versions (planning_act_id, legal_status, source_artifact_id, "
                     "data_release_id, valid_from, published_at, content_hash, publication_id, "
                     "version_started_at, legal_valid_from) VALUES (:a, 'unknown', :art, :rel, now(), now(), "
                     "'h', 'https://www.gov.pl/x', '2026-08-19T01:00:00Z', '2026-08-19') RETURNING id"),
                {"a": ids["act"], "art": ids["artifact"], "rel": ids["release"]},
            ).scalar_one()
            insert_doc = text(
                "INSERT INTO pog_formal_documents (planning_act_version_id, document_identifier, "
                "document_version, title, resolution_status) VALUES (:v, 'NS/D1', :ver, 'Ten sam tytuł', :st)"
            )
            conn.execute(insert_doc, {"v": ids["version"], "ver": "v1", "st": "resolved"})
            conn.execute(insert_doc, {"v": ids["version"], "ver": "v2", "st": "unresolved"})
            conn.execute(insert_doc, {"v": ids["version"], "ver": None, "st": "unavailable"})
            conn.execute(
                text("INSERT INTO pog_act_metadata_records (planning_act_version_id, record_id, reference_urls) "
                     "VALUES (:v, 'rec-1', CAST('[]' AS jsonb))"),
                {"v": ids["version"]},
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(insert_doc, {"v": ids["version"], "ver": "v1", "st": "resolved"})
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(insert_doc, {"v": ids["version"], "ver": None, "st": "resolved"})
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(insert_doc, {"v": ids["version"], "ver": "v3", "st": "merged"})

        command.downgrade(config, "016_pog_status_coverage")
        with engine.connect() as conn:
            remaining = conn.execute(
                text("SELECT count(*) FROM pog_formal_documents WHERE planning_act_version_id = :v"),
                {"v": ids["version"]},
            ).scalar_one()
        # Unikalność 015 dopuszcza jeden rekord na identyfikator dokumentu.
        assert remaining == 1
        assert "pog_act_metadata_records" not in inspect(engine).get_table_names()
        assert "publication_id" not in {
            c["name"] for c in inspect(engine).get_columns("planning_act_versions")
        }
    finally:
        command.upgrade(config, "head")
        if ids:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM pog_act_metadata_records WHERE planning_act_version_id = :v"),
                             {"v": ids.get("version")})
                conn.execute(text("DELETE FROM pog_formal_documents WHERE planning_act_version_id = :v"),
                             {"v": ids.get("version")})
                conn.execute(text("DELETE FROM planning_act_versions WHERE id = :v"), {"v": ids.get("version")})
                conn.execute(text("DELETE FROM planning_acts WHERE id = :v"), {"v": ids.get("act")})
                conn.execute(text("DELETE FROM data_releases WHERE id = :v"), {"v": ids.get("release")})
                conn.execute(text("DELETE FROM source_artifacts WHERE id = :v"), {"v": ids.get("artifact")})
                conn.execute(text("DELETE FROM data_sources WHERE id = :v"), {"v": ids.get("source")})
