"""Wersjonowany model danych źródłowych (Faza 10.3).

Model przechowuje kanoniczne, wersjonowane dane przestrzenne i dokumentowe wraz
z pełnym śladem pochodzenia (artefakt → wydanie danych). Jest **addytywny**:
nie zastępuje starego modelu analiz (``app/models/analysis.py`` itd.), a
istniejącą tabelę ``parcels`` rozszerza kompatybilnie (kolumny nullable), zamiast
tworzyć duplikat.

Zasady wersjonowania (zakres prawostronnie otwarty [valid_from, valid_to)):
- ``valid_from`` — początek obowiązywania (wymagany),
- ``valid_to`` — koniec obowiązywania; ``NULL`` oznacza wersję aktywną,
- w obrębie jednego wydania danych (``data_release_id``) może istnieć co
  najwyżej jedna aktywna wersja danego rekordu (częściowy indeks unikalny),
- geometrie kanoniczne są w EPSG:2180, mają indeks GiST oraz zakaz pustej
  geometrii (CHECK ``NOT ST_IsEmpty``); nieprawidłowy SRID blokuje typ kolumny.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Dozwolone statusy weryfikacji (Human in the Loop). Trzymane w jednym miejscu i
# wymuszane check constraintem na każdej tabeli wersji.
REVIEW_STATUS_VALUES: tuple[str, ...] = (
    "unreviewed",
    "verified",
    "rejected",
    "superseded",
)

_REVIEW_STATUS_SQL = ", ".join(f"'{value}'" for value in REVIEW_STATUS_VALUES)


def _timestamp_column(nullable: bool = False) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=nullable)


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class _VersionMixin:
    """Wspólne kolumny i ograniczenia tabel wersji (temporalnych)."""

    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="unreviewed"
    )


def _version_constraints(table: str, owner_column: str) -> tuple[Any, ...]:
    """Zwraca wspólne ograniczenia tabeli wersji.

    - zakres prawostronnie otwarty: valid_to NULL lub większy od valid_from,
    - niepusty content_hash,
    - dozwolony review_status,
    - częściowy indeks unikalny: max. jedna AKTYWNA wersja (valid_to IS NULL)
      danego rekordu w obrębie jednego wydania danych.
    """
    return (
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name=f"ck_{table}_valid_range",
        ),
        CheckConstraint("content_hash <> ''", name=f"ck_{table}_content_hash"),
        CheckConstraint(
            f"review_status IN ({_REVIEW_STATUS_SQL})",
            name=f"ck_{table}_review_status",
        ),
        Index(
            f"uq_{table}_active",
            owner_column,
            "data_release_id",
            unique=True,
            postgresql_where=sql_text("valid_to IS NULL"),
        ),
    )


# --- Rejestr źródeł i pochodzenie danych -------------------------------------


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Spójny z katalogiem docs/data_sources/catalog.yaml (jedyne źródło prawdy
    # dla kontraktów). Tabela jest jego odzwierciedleniem w bazie.
    source_id: Mapped[str] = mapped_column(
        String(80), nullable=False, unique=True, index=True
    )
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    access_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    license: Mapped[str | None] = mapped_column(Text, nullable=True)
    attribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class SourceArtifact(Base):
    __tablename__ = "source_artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    data_source_id: Mapped[int] = mapped_column(
        ForeignKey("data_sources.id"), nullable=False, index=True
    )
    uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # SHA-256 oryginalnej odpowiedzi/pliku — podstawa idempotencji importu.
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fetched_at: Mapped[datetime] = _timestamp_column()
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint(
            "data_source_id", "content_hash", name="uq_source_artifacts_dedup"
        ),
        CheckConstraint(
            "content_hash <> ''", name="ck_source_artifacts_content_hash"
        ),
    )


class DataRelease(Base):
    __tablename__ = "data_releases"

    id: Mapped[int] = mapped_column(primary_key=True)
    data_source_id: Mapped[int] = mapped_column(
        ForeignKey("data_sources.id"), nullable=False, index=True
    )
    version_label: Mapped[str] = mapped_column(String(120), nullable=False)
    published_at: Mapped[datetime] = _timestamp_column()
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    importer_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint(
            "data_source_id", "version_label", name="uq_data_releases_version"
        ),
        # Najwyżej jedno aktywne wydanie na źródło (rollback zmienia aktywne
        # wydanie, nie usuwa poprzedniego).
        Index(
            "uq_data_releases_active",
            "data_source_id",
            unique=True,
            postgresql_where=sql_text("is_active"),
        ),
    )


class ImportRun(Base):
    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    data_source_id: Mapped[int] = mapped_column(
        ForeignKey("data_sources.id"), nullable=False, index=True
    )
    data_release_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_releases.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    importer_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    stats: Mapped[Any] = mapped_column(JSONB, nullable=True)
    checkpoint: Mapped[Any] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()


# --- Działki (wersjonowana geometria) ----------------------------------------


class ParcelVersion(Base, _VersionMixin):
    __tablename__ = "parcel_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    parcel_id: Mapped[int] = mapped_column(
        ForeignKey("parcels.id"), nullable=False, index=True
    )
    geometry: Mapped[Any] = mapped_column(
        Geometry("MULTIPOLYGON", srid=2180, spatial_index=False),
        nullable=False,
    )
    source_artifact_id: Mapped[int] = mapped_column(
        ForeignKey("source_artifacts.id"), nullable=False, index=True
    )
    data_release_id: Mapped[int] = mapped_column(
        ForeignKey("data_releases.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        *_version_constraints("parcel_versions", "parcel_id"),
        CheckConstraint(
            "NOT ST_IsEmpty(geometry)", name="ck_parcel_versions_geometry_not_empty"
        ),
        Index(
            "ix_parcel_versions_geometry_gist",
            "geometry",
            postgresql_using="gist",
        ),
    )


# --- Akty planistyczne (wersjonowane) ----------------------------------------


class PlanningAct(Base):
    __tablename__ = "planning_acts"

    id: Mapped[int] = mapped_column(primary_key=True)
    act_identifier: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True, index=True
    )
    teryt: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class PlanningActVersion(Base, _VersionMixin):
    __tablename__ = "planning_act_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    planning_act_id: Mapped[int] = mapped_column(
        ForeignKey("planning_acts.id"), nullable=False, index=True
    )
    legal_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    version_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_artifact_id: Mapped[int] = mapped_column(
        ForeignKey("source_artifacts.id"), nullable=False, index=True
    )
    data_release_id: Mapped[int] = mapped_column(
        ForeignKey("data_releases.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = _version_constraints("planning_act_versions", "planning_act_id")


class PlanBoundary(Base):
    __tablename__ = "plan_boundaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    planning_act_version_id: Mapped[int] = mapped_column(
        ForeignKey("planning_act_versions.id"), nullable=False, index=True
    )
    geometry: Mapped[Any] = mapped_column(
        Geometry("MULTIPOLYGON", srid=2180, spatial_index=False),
        nullable=False,
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(
            "NOT ST_IsEmpty(geometry)", name="ck_plan_boundaries_geometry_not_empty"
        ),
        Index(
            "ix_plan_boundaries_geometry_gist", "geometry", postgresql_using="gist"
        ),
    )


class PlanningSymbol(Base):
    __tablename__ = "planning_symbols"

    id: Mapped[int] = mapped_column(primary_key=True)
    planning_act_version_id: Mapped[int] = mapped_column(
        ForeignKey("planning_act_versions.id"), nullable=False, index=True
    )
    local_symbol: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint(
            "planning_act_version_id",
            "local_symbol",
            name="uq_planning_symbols_symbol",
        ),
    )


class LandUseArea(Base):
    __tablename__ = "land_use_areas"

    id: Mapped[int] = mapped_column(primary_key=True)
    planning_act_version_id: Mapped[int] = mapped_column(
        ForeignKey("planning_act_versions.id"), nullable=False, index=True
    )
    planning_symbol_id: Mapped[int | None] = mapped_column(
        ForeignKey("planning_symbols.id"), nullable=True, index=True
    )
    symbol: Mapped[str | None] = mapped_column(String(60), nullable=True)
    geometry: Mapped[Any] = mapped_column(
        Geometry("MULTIPOLYGON", srid=2180, spatial_index=False),
        nullable=False,
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(
            "NOT ST_IsEmpty(geometry)", name="ck_land_use_areas_geometry_not_empty"
        ),
        Index("ix_land_use_areas_geometry_gist", "geometry", postgresql_using="gist"),
    )


class PlanningFeature(Base):
    __tablename__ = "planning_features"

    id: Mapped[int] = mapped_column(primary_key=True)
    planning_act_version_id: Mapped[int] = mapped_column(
        ForeignKey("planning_act_versions.id"), nullable=False, index=True
    )
    feature_type: Mapped[str] = mapped_column(String(80), nullable=False)
    # Geometria ogólna: linie zabudowy, osie, strefy ochronne itd.
    geometry: Mapped[Any] = mapped_column(
        Geometry("GEOMETRY", srid=2180, spatial_index=False),
        nullable=False,
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(
            "NOT ST_IsEmpty(geometry)",
            name="ck_planning_features_geometry_not_empty",
        ),
        Index(
            "ix_planning_features_geometry_gist", "geometry", postgresql_using="gist"
        ),
    )


# --- Dokumenty źródłowe (wersjonowane) ---------------------------------------


class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    planning_act_id: Mapped[int | None] = mapped_column(
        ForeignKey("planning_acts.id"), nullable=True, index=True
    )
    data_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_sources.id"), nullable=True, index=True
    )
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    document_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = _created_at()


class DocumentVersion(Base, _VersionMixin):
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id"), nullable=False, index=True
    )
    source_artifact_id: Mapped[int] = mapped_column(
        ForeignKey("source_artifacts.id"), nullable=False, index=True
    )
    data_release_id: Mapped[int] = mapped_column(
        ForeignKey("data_releases.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = _version_constraints("document_versions", "source_document_id")


class DocumentPage(Base):
    __tablename__ = "document_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id"), nullable=False, index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    quality: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint(
            "document_version_id", "page_number", name="uq_document_pages_page"
        ),
        CheckConstraint("page_number >= 1", name="ck_document_pages_page_number"),
    )


class LegalUnit(Base):
    __tablename__ = "legal_units"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id"), nullable=False, index=True
    )
    chapter: Mapped[str | None] = mapped_column(String(60), nullable=True)
    paragraph: Mapped[str | None] = mapped_column(String(60), nullable=True)
    section: Mapped[str | None] = mapped_column(String(60), nullable=True)
    point: Mapped[str | None] = mapped_column(String(60), nullable=True)
    position: Mapped[str | None] = mapped_column(String(60), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class SymbolLegalUnit(Base):
    __tablename__ = "symbol_legal_units"

    id: Mapped[int] = mapped_column(primary_key=True)
    planning_symbol_id: Mapped[int] = mapped_column(
        ForeignKey("planning_symbols.id"), nullable=False, index=True
    )
    legal_unit_id: Mapped[int] = mapped_column(
        ForeignKey("legal_units.id"), nullable=False, index=True
    )
    relation_type: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default="describes"
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="unreviewed"
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint(
            "planning_symbol_id",
            "legal_unit_id",
            "relation_type",
            name="uq_symbol_legal_units_relation",
        ),
        CheckConstraint(
            f"review_status IN ({_REVIEW_STATUS_SQL})",
            name="ck_symbol_legal_units_review_status",
        ),
    )


class ManualReview(Base):
    __tablename__ = "manual_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(60), nullable=False)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="unreviewed"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_manual_reviews_subject", "subject_type", "subject_id"),
        CheckConstraint(
            f"review_status IN ({_REVIEW_STATUS_SQL})",
            name="ck_manual_reviews_review_status",
        ),
    )
