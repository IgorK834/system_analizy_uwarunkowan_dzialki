"""Rozdzielenie statusu prawnego POG od pokrycia i dostępności (BK-106).

Revision ID: 016_pog_status_coverage
Revises: 015_pog_v2_release

Kanoniczne wartości (ADR-002):

- ``legal_status``: binding | project | in_progress | superseded | unknown,
- ``coverage_status``: available | partial | act_without_spatial_data |
  no_act_confirmed | unknown,
- ``data_availability``: current | stale | unavailable.

Mapowanie historyczne: ``adopted`` → ``binding`` WYŁĄCZNIE z zachowanym
potwierdzeniem źródłowym (urzędowy kod statusu albo przypięte wydanie z SHA i
wersją aktu), w przeciwnym razie ``unknown``; ``not_available`` → ``unknown``;
alias ``outdated`` → ``superseded``; ``complete`` → ``available``. Oryginalne
wartości trafiają do ``legacy_legal_status`` / ``legacy_status``, więc audyt i
downgrade nie tracą informacji. Akty MPZP zachowują swoje wartości.

Lista kodów urzędowych jest zamrożoną kopią z ``app.shared.planning_status``
(migracja nie importuje kodu aplikacji); zgodność sprawdza test migracji.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "016_pog_status_coverage"
down_revision: Union[str, None] = "015_pog_v2_release"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGAL = ("binding", "project", "in_progress", "superseded", "unknown")
COVERAGE = ("available", "partial", "act_without_spatial_data", "no_act_confirmed", "unknown")
AVAILABILITY = ("current", "stale", "unavailable")
# Zamrożona kopia kodów urzędowych z dnia migracji (patrz docstring).
OFFICIAL_BINDING_CODES = frozenset(
    {"legalforce", "prawniewiazacylubrealizowany", "obowiazujacy", "obowiazuje"}
)
OFFICIAL_CODES = OFFICIAL_BINDING_CODES | frozenset(
    {
        "adoption", "elaboration", "obsolete", "wtrakcieprzyjmowania", "wopracowaniu",
        "nieaktualny", "projekt", "projektplanu", "wtrakciesporzadzania", "uchylony",
        "zastapiony", "nieobowiazujacy",
    }
)


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c) and c.isalnum())


def _official_code(raw: Any) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    for candidate in (raw, raw.rstrip("/").rsplit("/", 1)[-1], raw.rsplit(".", 1)[-1]):
        folded = _fold(candidate)
        if folded in OFFICIAL_CODES:
            return folded
    return None


def legacy_to_canonical(value: str | None, *, confirmed: bool) -> str:
    if value in LEGAL:
        return value  # type: ignore[return-value]
    if value == "outdated":
        return "superseded"
    if value == "adopted":
        return "binding" if confirmed else "unknown"
    return "unknown"


def coverage_to_canonical(value: str | None) -> str:
    if value in COVERAGE:
        return value  # type: ignore[return-value]
    if value == "complete":
        return "available"
    return "unknown"


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _snapshot_evidence(result: dict[str, Any], raw_attributes: Any) -> dict[str, Any] | None:
    """Ta sama reguła co ``_legacy_status_evidence`` w schemacie API."""
    app_metadata = raw_attributes.get("app_metadata") if isinstance(raw_attributes, dict) else None
    raw_status = app_metadata.get("raw_legal_status") if isinstance(app_metadata, dict) else None
    source = result.get("source") if isinstance(result.get("source"), dict) else {}
    act = result.get("act") if isinstance(result.get("act"), dict) else {}
    if _official_code(raw_status) in OFFICIAL_BINDING_CODES:
        return {
            "source_name": source.get("source_name") or "POG",
            "source_id": source.get("source_id"),
            "official": True,
            "reference": source.get("source_url"),
            "raw_value": raw_status,
            "confirmed_at": source.get("fetched_at"),
        }
    release_id = source.get("data_release_id")
    sha = source.get("artifact_sha256")
    act_version = act.get("version") or source.get("act_version")
    if release_id and sha and act_version:
        return {
            "source_name": source.get("source_name") or "POG",
            "source_id": source.get("source_id"),
            "official": True,
            "reference": f"data_release:{release_id};sha256:{sha};act_version:{act_version}",
            "raw_value": None,
            "confirmed_at": source.get("fetched_at"),
        }
    return None


def upgrade() -> None:
    bind = op.get_bind()

    # --- Wersje aktów POG --------------------------------------------------
    op.add_column(
        "planning_act_versions",
        sa.Column("legacy_legal_status", sa.String(30), nullable=True),
    )
    versions = bind.execute(
        sa.text(
            "SELECT pav.id, pav.legal_status, pav.raw_legal_status "
            "FROM planning_act_versions pav "
            "JOIN planning_acts pa ON pa.id = pav.planning_act_id "
            "WHERE pa.kind = 'pog'"
        )
    ).mappings().all()
    for row in versions:
        confirmed = _official_code(row["raw_legal_status"]) in OFFICIAL_BINDING_CODES
        canonical = legacy_to_canonical(row["legal_status"], confirmed=confirmed)
        bind.execute(
            sa.text(
                "UPDATE planning_act_versions SET legacy_legal_status = :legacy, "
                "legal_status = :canonical, "
                "manual_review_required = manual_review_required OR :review, "
                "review_status = CASE WHEN :review THEN 'unreviewed' ELSE review_status END "
                "WHERE id = :id"
            ),
            {
                "id": row["id"],
                "legacy": row["legal_status"],
                "canonical": canonical,
                "review": canonical != "binding",
            },
        )

    # --- Snapshoty analiz ----------------------------------------------------
    op.add_column("pog_data", sa.Column("legal_status", sa.String(30), server_default="unknown", nullable=False))
    op.add_column("pog_data", sa.Column("coverage_status", sa.String(40), server_default="unknown", nullable=False))
    op.add_column("pog_data", sa.Column("data_availability", sa.String(20), server_default="unavailable", nullable=False))
    op.add_column("pog_data", sa.Column("status_confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("pog_data", sa.Column("legacy_status", sa.String(30), nullable=True))

    rows = bind.execute(
        sa.text("SELECT id, status, planning_zone, raw_attributes, result_v2 FROM pog_data")
    ).mappings().all()
    for row in rows:
        result = row["result_v2"] if isinstance(row["result_v2"], dict) else None
        legacy = row["status"]
        evidence = _snapshot_evidence(result or {}, row["raw_attributes"])
        if legacy in LEGAL:
            canonical = legacy
        else:
            canonical = legacy_to_canonical(legacy, confirmed=evidence is not None)
        if result is not None:
            coverage = coverage_to_canonical(result.get("coverage_status"))
        else:
            coverage = "partial" if row["planning_zone"] else "unknown"
        availability = "unavailable" if legacy in {None, "unknown"} else "current"
        confirmed_at = evidence.get("confirmed_at") if evidence else None
        if result is not None:
            result = dict(result)
            result.update(
                legal_status=canonical,
                coverage_status=coverage,
                data_availability=availability,
                status=canonical,
                status_confirmed_at=confirmed_at,
                legal_status_evidence=evidence if canonical == "binding" else None,
                coverage_evidence=None,
            )
        bind.execute(
            sa.text(
                "UPDATE pog_data SET legacy_status = :legacy, status = :canonical, "
                "legal_status = :canonical, coverage_status = :coverage, "
                "data_availability = :availability, "
                "status_confirmed_at = CAST(:confirmed_at AS timestamptz), "
                "result_v2 = CAST(:result AS jsonb) WHERE id = :id"
            ),
            {
                "id": row["id"],
                "legacy": legacy,
                "canonical": canonical,
                "coverage": coverage,
                "availability": availability,
                "confirmed_at": confirmed_at,
                "result": json.dumps(result) if result is not None else None,
            },
        )

    op.create_check_constraint("ck_pog_data_legal_status", "pog_data", _in("legal_status", LEGAL))
    op.create_check_constraint("ck_pog_data_coverage_status", "pog_data", _in("coverage_status", COVERAGE))
    op.create_check_constraint(
        "ck_pog_data_data_availability", "pog_data", _in("data_availability", AVAILABILITY)
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_constraint("ck_pog_data_data_availability", "pog_data", type_="check")
    op.drop_constraint("ck_pog_data_coverage_status", "pog_data", type_="check")
    op.drop_constraint("ck_pog_data_legal_status", "pog_data", type_="check")

    rows = bind.execute(
        sa.text("SELECT id, status, legacy_status, result_v2 FROM pog_data")
    ).mappings().all()
    for row in rows:
        legacy = row["legacy_status"] or {
            "binding": "adopted",
            "unknown": "unknown",
        }.get(row["status"], row["status"])
        result = row["result_v2"] if isinstance(row["result_v2"], dict) else None
        if result is not None:
            # Kontrakt 015: legal_status było "adopted" albo "not_available",
            # coverage_status "complete" dla analizy ze strefami.
            result = {
                key: value
                for key, value in result.items()
                if key
                not in {
                    "data_availability",
                    "status_confirmed_at",
                    "legal_status_evidence",
                    "coverage_evidence",
                }
            }
            result["legal_status"] = "adopted" if legacy == "adopted" else "not_available"
            result["coverage_status"] = "complete" if result.get("zones") else "unknown"
            result["status"] = legacy
        bind.execute(
            sa.text(
                "UPDATE pog_data SET status = :legacy, result_v2 = CAST(:result AS jsonb) "
                "WHERE id = :id"
            ),
            {
                "id": row["id"],
                "legacy": legacy,
                "result": json.dumps(result) if result is not None else None,
            },
        )
    for name in ("legacy_status", "status_confirmed_at", "data_availability", "coverage_status", "legal_status"):
        op.drop_column("pog_data", name)

    bind.execute(
        sa.text(
            "UPDATE planning_act_versions pav SET legal_status = COALESCE("
            "pav.legacy_legal_status, CASE pav.legal_status "
            "WHEN 'binding' THEN 'adopted' WHEN 'unknown' THEN 'not_available' "
            "WHEN 'superseded' THEN 'not_available' ELSE pav.legal_status END) "
            "FROM planning_acts pa WHERE pa.id = pav.planning_act_id AND pa.kind = 'pog'"
        )
    )
    op.drop_column("planning_act_versions", "legacy_legal_status")
