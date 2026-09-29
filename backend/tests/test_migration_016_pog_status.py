"""Migracja 016 (BK-106): kanoniczny enum, aliasy i dokładny rollback.

Test działa na wspólnej bazie testowej: cofa schemat do 015, zapisuje dane w
starych wartościach, wykonuje upgrade → downgrade → upgrade i sprząta tylko
własne rekordy. Zamrożona lista kodów w migracji jest porównywana z mapperem.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.session import engine
from app.schemas.analyze import PogResult
from app.shared import planning_status

LEGAL_FORCE = "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/legalForce"
MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "016_pog_legal_status_coverage.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location("migration_016", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_follows_015_and_freezes_the_shared_enum() -> None:
    module = _migration_module()
    assert module.revision == "016_pog_status_coverage"
    assert module.down_revision == "015_pog_v2_release"
    assert module.LEGAL == planning_status.LEGAL_STATUS_VALUES
    assert module.COVERAGE == planning_status.COVERAGE_STATUS_VALUES
    assert module.AVAILABILITY == planning_status.DATA_AVAILABILITY_VALUES
    shared_codes = set(planning_status._OFFICIAL_STATUS_CODES)
    assert module.OFFICIAL_CODES == shared_codes
    assert module.OFFICIAL_BINDING_CODES == {
        code for code, status in planning_status._OFFICIAL_STATUS_CODES.items()
        if status == "binding"
    }


@pytest.mark.parametrize(
    ("legacy", "confirmed"),
    [
        ("adopted", True), ("adopted", False), ("not_available", False),
        ("outdated", False), ("in_progress", False), ("project", False),
        ("binding", False), ("unknown", False), (None, False),
    ],
)
def test_migration_mapper_matches_shared_mapper(legacy: str | None, confirmed: bool) -> None:
    module = _migration_module()
    assert module.legacy_to_canonical(legacy, confirmed=confirmed) == (
        planning_status.upgrade_legacy_legal_status(legacy, confirmed=confirmed)
    )
    assert module.coverage_to_canonical("complete") == "available"


@pytest.mark.integration
def test_upgrade_maps_aliases_and_downgrade_restores_exact_values() -> None:
    config = Config("alembic.ini")
    suffix = uuid4().hex[:8]
    ids: dict[str, object] = {}
    try:
        # BK-306: upgrade/downgrade też wewnątrz try — nieudany downgrade nie
        # może zostawić bazy w pośredniej rewizji bez przywrócenia head.
        command.upgrade(config, "head")
        command.downgrade(config, "015_pog_v2_release")
        with engine.begin() as conn:
            source_id = conn.execute(
                text(
                    "INSERT INTO data_sources (source_id, owner, status) "
                    "VALUES (:sid, 'test', 'production') RETURNING id"
                ),
                {"sid": f"mig016_{suffix}"},
            ).scalar_one()
            artifact_id = conn.execute(
                text(
                    "INSERT INTO source_artifacts (data_source_id, uri, content_hash, fetched_at) "
                    "VALUES (:ds, 'file:///x', :hash, '2026-08-19T01:00:00Z') RETURNING id"
                ),
                {"ds": source_id, "hash": "c" * 64},
            ).scalar_one()
            release_id = conn.execute(
                text(
                    "INSERT INTO data_releases (data_source_id, version_label, published_at) "
                    "VALUES (:ds, 'mig016', now()) RETURNING id"
                ),
                {"ds": source_id},
            ).scalar_one()
            versions = {
                "pog_confirmed": ("pog", "adopted", LEGAL_FORCE),
                "pog_unconfirmed": ("pog", "adopted", None),
                "pog_label_only": ("pog", "adopted", "uchwalony"),
                "pog_not_available": ("pog", "not_available", None),
                "pog_outdated": ("pog", "outdated", None),
                "pog_in_progress": ("pog", "in_progress", "w opracowaniu"),
                "mpzp_adopted": ("mpzp", "adopted", None),
            }
            version_ids: dict[str, int] = {}
            act_ids: list[int] = []
            for key, (kind, status, raw) in versions.items():
                act_id = conn.execute(
                    text(
                        "INSERT INTO planning_acts (act_identifier, teryt, kind) "
                        "VALUES (:ident, '1261011', :kind) RETURNING id"
                    ),
                    {"ident": f"mig016-{key}-{suffix}", "kind": kind},
                ).scalar_one()
                act_ids.append(act_id)
                version_ids[key] = conn.execute(
                    text(
                        "INSERT INTO planning_act_versions (planning_act_id, legal_status, "
                        "raw_legal_status, source_artifact_id, data_release_id, valid_from, "
                        "published_at, content_hash, review_status) VALUES (:act, :status, :raw, "
                        ":art, :rel, now(), now(), :hash, 'verified') RETURNING id"
                    ),
                    {"act": act_id, "status": status, "raw": raw, "art": artifact_id,
                     "rel": release_id, "hash": f"h-{key}"},
                ).scalar_one()
            parcel_id = conn.execute(
                text(
                    "INSERT INTO parcels (parcel_identifier, geometry) VALUES (:ident, "
                    "ST_Multi(ST_GeomFromText('POLYGON((0 0,10 0,10 10,0 10,0 0))', 2180))) "
                    "RETURNING id"
                ),
                {"ident": f"MIG016_{suffix}"},
            ).scalar_one()
            v2_pinned = {
                "schema_version": "2.0", "legal_status": "adopted", "coverage_status": "complete",
                "status": "adopted", "touches_ouz_boundary": False,
                "zones": [{"id": "z", "type": "SJ", "area_sqm": 1, "area_pct": 100}],
                "act": {"id": "pog-1", "version": "20260819T010000"},
                "source": {"source_id": "pog_app", "source_name": "POG_APP_LOCAL_POSTGIS",
                           "artifact_sha256": "d" * 64, "data_release_id": 3,
                           "fetched_at": "2026-08-19T01:00:00+00:00", "confidence": 1.0,
                           "manual_review_required": False},
            }
            v2_unpinned = {
                "schema_version": "2.0", "legal_status": "adopted", "coverage_status": "unknown",
                "status": "adopted", "touches_ouz_boundary": False,
            }
            snapshots = {
                "v1_unconfirmed": ("adopted", None, None),
                "v1_confirmed": ("adopted", {"app_metadata": {"raw_legal_status": LEGAL_FORCE}}, None),
                "v1_not_available": ("not_available", None, None),
                "v2_pinned": ("adopted", None, v2_pinned),
                "v2_unpinned": ("adopted", None, v2_unpinned),
            }
            analysis_ids: list[int] = []
            pog_ids: dict[str, int] = {}
            for key, (status, raw_attrs, result) in snapshots.items():
                analysis_id = conn.execute(
                    text("INSERT INTO analyses (parcel_id, status) VALUES (:p, 'partial') RETURNING id"),
                    {"p": parcel_id},
                ).scalar_one()
                analysis_ids.append(analysis_id)
                pog_ids[key] = conn.execute(
                    text(
                        "INSERT INTO pog_data (analysis_id, status, planning_zone, "
                        "touches_ouz_boundary, raw_attributes, result_v2, schema_version) "
                        "VALUES (:a, :s, 'SJ', false, CAST(:raw AS jsonb), CAST(:res AS jsonb), "
                        ":schema) RETURNING id"
                    ),
                    {"a": analysis_id, "s": status,
                     "raw": json.dumps(raw_attrs) if raw_attrs else None,
                     "res": json.dumps(result) if result else None,
                     "schema": "2.0" if result else "1.0"},
                ).scalar_one()
        ids.update(source_id=source_id, artifact_id=artifact_id, release_id=release_id,
                   act_ids=act_ids, parcel_id=parcel_id, analysis_ids=analysis_ids)

        command.upgrade(config, "head")
        with engine.connect() as conn:
            pav = dict(
                conn.execute(
                    text("SELECT id, legal_status FROM planning_act_versions WHERE id = ANY(:ids)"),
                    {"ids": list(version_ids.values())},
                ).all()
            )
            legacy_pav = dict(
                conn.execute(
                    text("SELECT id, legacy_legal_status FROM planning_act_versions WHERE id = ANY(:ids)"),
                    {"ids": list(version_ids.values())},
                ).all()
            )
            pog_rows = {
                row.id: row
                for row in conn.execute(
                    text(
                        "SELECT id, status, legal_status, coverage_status, data_availability, "
                        "legacy_status, status_confirmed_at, result_v2 FROM pog_data WHERE id = ANY(:ids)"
                    ),
                    {"ids": list(pog_ids.values())},
                )
            }
        assert pav[version_ids["pog_confirmed"]] == "binding"
        assert pav[version_ids["pog_unconfirmed"]] == "unknown"
        assert pav[version_ids["pog_label_only"]] == "unknown"
        assert pav[version_ids["pog_not_available"]] == "unknown"
        assert pav[version_ids["pog_outdated"]] == "superseded"
        assert pav[version_ids["pog_in_progress"]] == "in_progress"
        assert pav[version_ids["mpzp_adopted"]] == "adopted"  # MPZP poza zakresem BK-106
        assert legacy_pav[version_ids["pog_unconfirmed"]] == "adopted"
        assert legacy_pav[version_ids["mpzp_adopted"]] is None

        expected = {
            "v1_unconfirmed": ("unknown", "partial"),
            "v1_confirmed": ("binding", "partial"),
            "v1_not_available": ("unknown", "partial"),
            "v2_pinned": ("binding", "available"),
            "v2_unpinned": ("unknown", "unknown"),
        }
        for key, (legal, coverage) in expected.items():
            row = pog_rows[pog_ids[key]]
            assert (row.legal_status, row.coverage_status, row.status) == (legal, coverage, legal), key
        assert pog_rows[pog_ids["v2_pinned"]].status_confirmed_at is not None
        assert pog_rows[pog_ids["v1_unconfirmed"]].legacy_status == "adopted"
        pinned_json = pog_rows[pog_ids["v2_pinned"]].result_v2
        assert pinned_json["legal_status"] == "binding"
        assert pinned_json["coverage_status"] == "available"
        assert pinned_json["legal_status_evidence"]["reference"].startswith("data_release:3;")
        # Zmigrowany snapshot jest poprawnym kontraktem API bez dalszej konwersji.
        assert PogResult.model_validate(pinned_json).legal_status == "binding"
        assert PogResult.model_validate(pog_rows[pog_ids["v2_unpinned"]].result_v2).legal_status == "unknown"

        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE pog_data SET legal_status = 'adopted' WHERE id = :id"),
                    {"id": pog_ids["v1_unconfirmed"]},
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE pog_data SET coverage_status = 'complete' WHERE id = :id"),
                    {"id": pog_ids["v1_unconfirmed"]},
                )

        command.downgrade(config, "015_pog_v2_release")
        with engine.connect() as conn:
            restored_pav = dict(
                conn.execute(
                    text("SELECT id, legal_status FROM planning_act_versions WHERE id = ANY(:ids)"),
                    {"ids": list(version_ids.values())},
                ).all()
            )
            restored_pog = {
                row.id: row
                for row in conn.execute(
                    text("SELECT id, status, result_v2 FROM pog_data WHERE id = ANY(:ids)"),
                    {"ids": list(pog_ids.values())},
                )
            }
        assert restored_pav == {version_ids[key]: status for key, (_k, status, _r) in versions.items()}
        assert {key: restored_pog[pog_id].status for key, pog_id in pog_ids.items()} == {
            key: status for key, (status, _raw, _res) in snapshots.items()
        }
        assert restored_pog[pog_ids["v2_pinned"]].result_v2["legal_status"] == "adopted"
        assert restored_pog[pog_ids["v2_pinned"]].result_v2["coverage_status"] == "complete"
    finally:
        command.upgrade(config, "head")
        if ids:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM pog_data WHERE analysis_id = ANY(:ids)"),
                             {"ids": ids["analysis_ids"]})
                conn.execute(text("DELETE FROM analyses WHERE id = ANY(:ids)"),
                             {"ids": ids["analysis_ids"]})
                conn.execute(text("DELETE FROM parcels WHERE id = :id"), {"id": ids["parcel_id"]})
                conn.execute(
                    text("DELETE FROM planning_act_versions WHERE planning_act_id = ANY(:ids)"),
                    {"ids": ids["act_ids"]},
                )
                conn.execute(text("DELETE FROM planning_acts WHERE id = ANY(:ids)"),
                             {"ids": ids["act_ids"]})
                conn.execute(text("DELETE FROM data_releases WHERE id = :id"), {"id": ids["release_id"]})
                conn.execute(text("DELETE FROM source_artifacts WHERE id = :id"), {"id": ids["artifact_id"]})
                conn.execute(text("DELETE FROM data_sources WHERE id = :id"), {"id": ids["source_id"]})
