"""Modele infrastruktury lokalnego indeksu adresowego.

Warstwa domenowa nie importuje SQLAlchemy. Model należy do infrastruktury i
wiąże rekord wyszukiwarki z istniejącym, wersjonowanym wydaniem danych.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AddressSearchEntry(Base):
    __tablename__ = "address_search_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    data_release_id: Mapped[int] = mapped_column(
        ForeignKey("data_releases.id", ondelete="CASCADE"), nullable=False
    )
    source_artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_artifacts.id", ondelete="SET NULL"), nullable=True
    )
    source_object_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    result_type: Mapped[str] = mapped_column(String(30), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_label: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    voivodeship: Mapped[str | None] = mapped_column(String(160), nullable=True)
    county: Mapped[str | None] = mapped_column(String(160), nullable=True)
    municipality: Mapped[str | None] = mapped_column(String(160), nullable=True)
    city: Mapped[str | None] = mapped_column(String(200), nullable=True)
    street: Mapped[str | None] = mapped_column(String(240), nullable=True)
    house_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    teryt: Mapped[str | None] = mapped_column(String(20), nullable=True)
    simc: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ulic: Mapped[str | None] = mapped_column(String(20), nullable=True)
    geometry: Mapped[Any] = mapped_column(
        Geometry("POINT", srid=2180, spatial_index=False), nullable=False
    )
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "result_type IN ('city', 'street', 'house_number')",
            name="ck_address_search_entries_result_type",
        ),
        CheckConstraint(
            "normalized_label <> ''",
            name="ck_address_search_entries_normalized_label",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from",
            name="ck_address_search_entries_valid_range",
        ),
        CheckConstraint(
            "NOT ST_IsEmpty(geometry)",
            name="ck_address_search_entries_geometry_not_empty",
        ),
        UniqueConstraint(
            "data_release_id",
            "source_object_id",
            "result_type",
            name="uq_address_search_entries_release_object_type",
        ),
        Index(
            "ix_address_search_entries_release_type",
            "data_release_id",
            "result_type",
        ),
        Index("ix_address_search_entries_teryt", "teryt"),
        Index(
            "ix_address_search_entries_geometry_gist",
            "geometry",
            postgresql_using="gist",
        ),
        Index(
            "ix_address_search_entries_normalized_trgm",
            "normalized_label",
            postgresql_using="gin",
            postgresql_ops={"normalized_label": "gin_trgm_ops"},
        ),
    )

