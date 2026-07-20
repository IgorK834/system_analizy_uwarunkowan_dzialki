"""add_map_layer_geojson

Revision ID: 006_map_layer_geojson
Revises: 005_infrastructure_audit
Create Date: 2026-07-20

Przechowuje gotowe warstwy GeoJSON sieci, efektywnych stref ochronnych i
ryzyk, aby odpowiedź odtworzona z cache miała ten sam komplet geometrii co
świeża analiza.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "006_map_layer_geojson"
down_revision: Union[str, None] = "005_infrastructure_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "infrastructure_records",
        sa.Column(
            "network_geometry_geojson",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "infrastructure_records",
        sa.Column(
            "protection_zone_geojson",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "risk_records",
        sa.Column(
            "geometry_geojson",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("risk_records", "geometry_geojson")
    op.drop_column("infrastructure_records", "protection_zone_geojson")
    op.drop_column("infrastructure_records", "network_geometry_geojson")
