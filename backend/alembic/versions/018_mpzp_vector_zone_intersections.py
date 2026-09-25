"""Wersjonowane wydzielenia MPZP w analizie pełnego obrysu działki (BK-202).

Revision ID: 018_mpzp_vector_zones
Revises: 017_pog_act_provenance

- ``land_use_areas.zone_identifier``: stabilne ID wydzielenia. Istniejące
  rekordy dostają ID deterministyczne ``akt:symbol:sha256(geometria)[:16]``,
  czyli ten sam wzór co nowy import (``stable_zone_identifier``) — kanoniczna
  geometria ``ST_Normalize`` jak w ``repair_geometry``.
- ``planning_act_versions.document_url``: dokument uchwały przypisany wersji
  aktu przez źródło (opcjonalny).
- ``mpzp_zones``: snapshot przypisania strefy w analizie — akt/wersja,
  wydanie, styczność, metoda przypisania i pełny wynik JSON
  (``result_snapshot``), aby odczyt historyczny nie zależał od aktywnego
  wydania. Starsze wiersze mają ``assignment_method='legacy'``.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "018_mpzp_vector_zones"
down_revision: Union[str, None] = "017_pog_act_provenance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ASSIGNMENT_METHODS = (
    "vector_intersection",
    "document_candidate",
    "manual_user_input",
    "legacy",
)


def upgrade() -> None:
    op.add_column("land_use_areas", sa.Column("zone_identifier", sa.String(500), nullable=True))
    op.execute(
        """
        UPDATE land_use_areas lua
        SET zone_identifier = pa.act_identifier || ':' || coalesce(lua.symbol, '') || ':'
            || substr(encode(sha256(ST_AsBinary(ST_Normalize(lua.geometry))), 'hex'), 1, 16)
        FROM planning_act_versions pav
        JOIN planning_acts pa ON pa.id = pav.planning_act_id
        WHERE pav.id = lua.planning_act_version_id
        """
    )
    # Dwa identyczne wydzielenia (ten sam symbol i geometria) w starej wersji
    # dostają sufiks id, aby unikalność nie usuwała danych historycznych.
    op.execute(
        """
        UPDATE land_use_areas lua SET zone_identifier = lua.zone_identifier || ':' || lua.id
        FROM (
          SELECT id, row_number() OVER (
            PARTITION BY planning_act_version_id, zone_identifier ORDER BY id
          ) AS position FROM land_use_areas
        ) ranked
        WHERE ranked.id = lua.id AND ranked.position > 1
        """
    )
    op.alter_column("land_use_areas", "zone_identifier", nullable=False)
    op.create_unique_constraint(
        "uq_land_use_areas_version_zone",
        "land_use_areas",
        ["planning_act_version_id", "zone_identifier"],
    )
    op.create_index("ix_land_use_areas_zone_identifier", "land_use_areas", ["zone_identifier"])

    op.add_column("planning_act_versions", sa.Column("document_url", sa.String(1000), nullable=True))

    for name, column in (
        ("zone_identifier", sa.String(500)),
        ("act_identifier", sa.String(200)),
        ("act_version", sa.String(128)),
        ("act_version_id", sa.Integer()),
        ("data_release_id", sa.Integer()),
        ("result_snapshot", postgresql.JSONB(astext_type=sa.Text())),
    ):
        op.add_column("mpzp_zones", sa.Column(name, column, nullable=True))
    op.add_column(
        "mpzp_zones",
        sa.Column("touches_boundary", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "mpzp_zones",
        sa.Column("assignment_method", sa.String(40), server_default="legacy", nullable=False),
    )
    op.create_check_constraint(
        "ck_mpzp_zones_assignment_method",
        "mpzp_zones",
        f"assignment_method IN ({', '.join(repr(value) for value in ASSIGNMENT_METHODS)})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_mpzp_zones_assignment_method", "mpzp_zones", type_="check")
    for name in (
        "assignment_method", "touches_boundary", "result_snapshot", "data_release_id",
        "act_version_id", "act_version", "act_identifier", "zone_identifier",
    ):
        op.drop_column("mpzp_zones", name)
    op.drop_column("planning_act_versions", "document_url")
    op.drop_index("ix_land_use_areas_zone_identifier", table_name="land_use_areas")
    op.drop_constraint("uq_land_use_areas_version_zone", "land_use_areas", type_="unique")
    op.drop_column("land_use_areas", "zone_identifier")
