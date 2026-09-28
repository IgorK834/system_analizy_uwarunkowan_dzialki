"""Informacyjna ocena relacji MPZP–POG zamiast booleana (BK-205).

Revision ID: 021_compatibility_assessment
Revises: 020_manual_zone_pending_doc

``pog_data.conflict_with_mpzp`` (i kopia w ``result_v2``) nie miał reguły,
wersji, par stref ani daty stanu prawnego. Migracja przenosi go do nowej
kolumny ``compatibility_assessment`` jako ``legacy_evidence`` ze statusem
``unknown`` — historyczne stwierdzenie zostaje zachowane, ale nie udaje pełnej
oceny. Uwzględniane jest też stwierdzenie z ``raw_attributes.scenario``.
Downgrade odtwarza boolean z ``legacy_evidence`` (albo z rozstrzygniętego
statusu nowej oceny) i usuwa kolumnę.

Tekst noty jest zamrożoną kopią z ``app.core.planning_compatibility`` (migracja
nie importuje kodu aplikacji).
"""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "021_compatibility_assessment"
down_revision: Union[str, None] = "020_manual_zone_pending_doc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOTICE = (
    "Ocena relacji MPZP–POG jest analizą informacyjną opartą na jawnej tabeli "
    "reguł systemu. Nie jest opinią prawną i nie przesądza o prawnej możliwości "
    "zabudowy działki — o niej rozstrzygają ustalenia obowiązujących aktów i "
    "właściwy organ."
)
AGGREGATION = "Brak agregacji: zapis historyczny nie zawiera par stref."
RATIONALE = (
    "Snapshot sprzed BK-205 zawiera wyłącznie uproszczone stwierdzenie bez "
    "identyfikatora reguły, par stref i daty stanu prawnego; zachowano je "
    "jako historyczny dowód, a nie pełną ocenę."
)


def legacy_assessment(conflict: Any, raw_attributes: Any) -> dict[str, Any] | None:
    scenario = raw_attributes.get("scenario") if isinstance(raw_attributes, dict) else None
    compatibility = scenario.get("compatibility") if isinstance(scenario, dict) else None
    if not isinstance(compatibility, dict):
        compatibility = None
    if conflict is None and compatibility is None:
        return None
    return {
        "schema_version": "1.0",
        "status": "unknown",
        "reason_code": "LEGACY_BOOLEAN_ONLY",
        "as_of": None,
        "rule_id": None,
        "rule_version": None,
        "aggregation": AGGREGATION,
        "sources": [],
        "rationale": RATIONALE,
        "manual_review_required": True,
        "zone_pairs": [],
        "informational_notice": NOTICE,
        "legacy_evidence": {
            "origin": (
                "pog_data.conflict_with_mpzp"
                if compatibility is None
                else "raw_attributes.scenario.compatibility"
            ),
            "conflict_with_mpzp": conflict,
            "result": (compatibility or {}).get("result"),
            "reasoning": (compatibility or {}).get("reasoning"),
            "confidence": (compatibility or {}).get("confidence"),
        },
    }


def conflict_from_assessment(assessment: Any) -> bool | None:
    if not isinstance(assessment, dict):
        return None
    legacy = assessment.get("legacy_evidence")
    if isinstance(legacy, dict):
        value = legacy.get("conflict_with_mpzp")
        return value if isinstance(value, bool) else None
    return {"incompatible": True, "compatible": False}.get(assessment.get("status"))


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column(
        "pog_data",
        sa.Column("compatibility_assessment", postgresql.JSONB(), nullable=True),
    )
    rows = bind.execute(
        sa.text("SELECT id, conflict_with_mpzp, raw_attributes, result_v2 FROM pog_data")
    ).mappings().all()
    for row in rows:
        result = dict(row["result_v2"]) if isinstance(row["result_v2"], dict) else None
        conflict = row["conflict_with_mpzp"]
        raw = row["raw_attributes"]
        if result is not None:
            conflict = result.pop("conflict_with_mpzp", conflict)
            raw = result.get("raw_attributes") or raw
        assessment = legacy_assessment(conflict, raw)
        if result is not None:
            result["compatibility_assessment"] = assessment
        bind.execute(
            sa.text(
                "UPDATE pog_data SET compatibility_assessment = CAST(:assessment AS jsonb), "
                "result_v2 = CAST(:result AS jsonb) WHERE id = :id"
            ),
            {
                "id": row["id"],
                "assessment": json.dumps(assessment) if assessment is not None else None,
                "result": json.dumps(result) if result is not None else None,
            },
        )
    op.drop_column("pog_data", "conflict_with_mpzp")


def downgrade() -> None:
    bind = op.get_bind()
    op.add_column(
        "pog_data", sa.Column("conflict_with_mpzp", sa.Boolean(), nullable=True)
    )
    rows = bind.execute(
        sa.text("SELECT id, compatibility_assessment, result_v2 FROM pog_data")
    ).mappings().all()
    for row in rows:
        result = dict(row["result_v2"]) if isinstance(row["result_v2"], dict) else None
        assessment = row["compatibility_assessment"]
        if result is not None:
            assessment = result.pop("compatibility_assessment", assessment)
        conflict = conflict_from_assessment(assessment)
        if result is not None:
            result["conflict_with_mpzp"] = conflict
        bind.execute(
            sa.text(
                "UPDATE pog_data SET conflict_with_mpzp = :conflict, "
                "result_v2 = CAST(:result AS jsonb) WHERE id = :id"
            ),
            {
                "id": row["id"],
                "conflict": conflict,
                "result": json.dumps(result) if result is not None else None,
            },
        )
    op.drop_column("pog_data", "compatibility_assessment")
