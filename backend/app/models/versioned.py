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

from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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
from app.models.types import ClippedString

# Dozwolone statusy weryfikacji (Human in the Loop). Trzymane w jednym miejscu i
# wymuszane check constraintem na każdej tabeli wersji.
REVIEW_STATUS_VALUES: tuple[str, ...] = (
    "unreviewed",
    "verified",
    "rejected",
    "superseded",
)

_REVIEW_STATUS_SQL = ", ".join(f"'{value}'" for value in REVIEW_STATUS_VALUES)

DOCUMENT_TYPE_VALUES: tuple[str, ...] = (
    "uchwala",
    "zalacznik_tekstowy",
    "rysunek",
    "uzasadnienie",
)
_DOCUMENT_TYPE_SQL = ", ".join(f"'{value}'" for value in DOCUMENT_TYPE_VALUES)

PLANNING_RULE_REVIEW_STATUS_VALUES: tuple[str, ...] = (
    *REVIEW_STATUS_VALUES,
    "ai_candidate",
)
_PLANNING_RULE_REVIEW_STATUS_SQL = ", ".join(
    f"'{value}'" for value in PLANNING_RULE_REVIEW_STATUS_VALUES
)


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
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str | None] = mapped_column(ClippedString(120), nullable=True)
    # SHA-256 oryginalnej odpowiedzi/pliku — podstawa idempotencji importu.
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    etag: Mapped[str | None] = mapped_column(ClippedString(255), nullable=True)
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
    version_label: Mapped[str] = mapped_column(ClippedString(120), nullable=False)
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


# Rodzaje aktów planistycznych obsługiwane przez wersjonowany model. MPZP i POG
# są odrębnymi aktami prawa miejscowego wersjonowanymi niezależnie.
PLANNING_ACT_KIND_VALUES: tuple[str, ...] = ("mpzp", "pog")
_PLANNING_ACT_KIND_SQL = ", ".join(f"'{value}'" for value in PLANNING_ACT_KIND_VALUES)


class PlanningAct(Base):
    __tablename__ = "planning_acts"

    id: Mapped[int] = mapped_column(primary_key=True)
    act_identifier: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True, index=True
    )
    teryt: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(
            f"kind IN ({_PLANNING_ACT_KIND_SQL})", name="ck_planning_acts_kind"
        ),
    )


class PlanningActVersion(Base, _VersionMixin):
    __tablename__ = "planning_act_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    planning_act_id: Mapped[int] = mapped_column(
        ForeignKey("planning_acts.id"), nullable=False, index=True
    )
    # Dla aktów POG: kanoniczny status BK-106 (app.shared.planning_status).
    # Akty MPZP zachowują dotychczasowe wartości (adopted/raster_only).
    legal_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    raw_legal_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    legacy_legal_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Provenance publikacji APP (BK-107): gml:identifier wersji, początek wersji
    # obiektu, okres obowiązywania z APP i URL usługi, z której ją pobrano.
    publication_id: Mapped[str | None] = mapped_column(ClippedString(500), nullable=True)
    version_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    legal_valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    legal_valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Dokument uchwały wskazany przez źródło wersji aktu MPZP (BK-202/203).
    document_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    object_version_id: Mapped[str | None] = mapped_column(ClippedString(120), nullable=True)
    version_label: Mapped[str | None] = mapped_column(ClippedString(120), nullable=True)
    resolution_number: Mapped[str | None] = mapped_column(
        ClippedString(200), nullable=True
    )
    resolution_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    manual_review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
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
    name: Mapped[str | None] = mapped_column(ClippedString(255), nullable=True)
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
    symbol: Mapped[str | None] = mapped_column(ClippedString(60), nullable=True)
    # Stabilne ID wydzielenia publikowane w wyniku analizy (BK-202).
    zone_identifier: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    raw_attributes: Mapped[Any] = mapped_column(JSONB, nullable=True)
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
        UniqueConstraint(
            "planning_act_version_id",
            "zone_identifier",
            name="uq_land_use_areas_version_zone",
        ),
    )


class PlanningFeature(Base):
    __tablename__ = "planning_features"

    id: Mapped[int] = mapped_column(primary_key=True)
    planning_act_version_id: Mapped[int] = mapped_column(
        ForeignKey("planning_act_versions.id"), nullable=False, index=True
    )
    feature_type: Mapped[str] = mapped_column(String(80), nullable=False)
    feature_identifier: Mapped[str | None] = mapped_column(ClippedString(500), nullable=True)
    feature_version: Mapped[str | None] = mapped_column(ClippedString(120), nullable=True)
    act_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_legal_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbol: Mapped[str | None] = mapped_column(ClippedString(80), nullable=True)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    parameters: Mapped[Any] = mapped_column(JSONB, nullable=True)
    primary_profiles: Mapped[Any] = mapped_column(JSONB, nullable=True)
    additional_profiles: Mapped[Any] = mapped_column(JSONB, nullable=True)
    raw_attributes: Mapped[Any] = mapped_column(JSONB, nullable=True)
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
        UniqueConstraint(
            "planning_act_version_id",
            "feature_identifier",
            name="uq_planning_features_version_identifier",
        ),
    )


POG_DOCUMENT_RESOLUTION_VALUES: tuple[str, ...] = ("resolved", "unresolved", "unavailable")


class PogFormalDocument(Base):
    __tablename__ = "pog_formal_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    planning_act_version_id: Mapped[int] = mapped_column(
        ForeignKey("planning_act_versions.id"), nullable=False, index=True
    )
    document_identifier: Mapped[str] = mapped_column(String(500), nullable=False)
    document_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    act_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_attributes: Mapped[Any] = mapped_column(JSONB, nullable=True)
    publication_id: Mapped[str | None] = mapped_column(ClippedString(500), nullable=True)
    short_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    identification_number: Mapped[str | None] = mapped_column(ClippedString(200), nullable=True)
    relation: Mapped[str | None] = mapped_column(String(40), nullable=True)
    document_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    repeal_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    record_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    link_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    resolution_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="resolved"
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        # Unikalność obejmuje wersję: dwie wersje dokumentu nie są scalane.
        Index(
            "uq_pog_documents_version_identifier_version",
            "planning_act_version_id",
            "document_identifier",
            sql_text("coalesce(document_version, '')"),
            unique=True,
        ),
        CheckConstraint(
            "resolution_status IN ("
            + ", ".join(repr(value) for value in POG_DOCUMENT_RESOLUTION_VALUES)
            + ")",
            name="ck_pog_formal_documents_resolution_status",
        ),
    )


class PogActMetadataRecord(Base):
    """Rekord ISO 19139 z CSW RU zamrożony razem z wersją aktu (BK-107)."""

    __tablename__ = "pog_act_metadata_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    planning_act_version_id: Mapped[int] = mapped_column(
        ForeignKey("planning_act_versions.id"), nullable=False, index=True
    )
    record_id: Mapped[str] = mapped_column(ClippedString(200), nullable=False)
    resource_identifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    revision_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    creation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_stamp: Mapped[date | None] = mapped_column(Date, nullable=True)
    metadata_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_urls: Mapped[Any] = mapped_column(JSONB, nullable=True)
    record_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint(
            "planning_act_version_id",
            "record_id",
            name="uq_pog_act_metadata_records_record",
        ),
    )


# --- Agregaty powierzchniowe stref POG (BK-405) -------------------------------

POG_AREA_SUMMARY_SCOPE_VALUES: tuple[str, ...] = ("act", "municipality")
POG_AREA_SUMMARY_EDITION_VALUES: tuple[str, ...] = ("binding", "project")
_POG_AREA_SUMMARY_SCOPE_SQL = ", ".join(
    f"'{value}'" for value in POG_AREA_SUMMARY_SCOPE_VALUES
)
_POG_AREA_SUMMARY_EDITION_SQL = ", ".join(
    f"'{value}'" for value in POG_AREA_SUMMARY_EDITION_VALUES
)


class PogAreaSummary(Base):
    """Struktura powierzchniowa stref aktu albo gminy w obrębie wydania (BK-405).

    Liczona w EPSG:2180 podczas publikacji wydania (w tej samej transakcji co
    jego aktywacja), więc HTTP czyta wyłącznie gotowe liczby. Mianownik to
    powierzchnia granicy aktu ze źródła; jego brak daje ``NULL`` (udziały też
    ``NULL``) i ``is_complete = false`` — nigdy 100%.
    """

    __tablename__ = "pog_area_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    data_release_id: Mapped[int] = mapped_column(
        ForeignKey("data_releases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    # Zakres ``act``: dokładna wersja aktu w wydaniu. Zakres ``municipality``:
    # gmina (TERYT) i edycja — nakładające się akty nie są liczone podwójnie.
    planning_act_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("planning_act_versions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    act_identifier: Mapped[str | None] = mapped_column(String(200), nullable=True)
    act_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    teryt: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    edition: Mapped[str | None] = mapped_column(String(20), nullable=True)
    legal_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    denominator_area_sqm: Mapped[float | None] = mapped_column(Float, nullable=True)
    denominator_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    zones_area_sqm: Mapped[float] = mapped_column(Float, nullable=False)
    missing_area_sqm: Mapped[float | None] = mapped_column(Float, nullable=True)
    overlap_area_sqm: Mapped[float] = mapped_column(Float, nullable=False)
    outside_area_sqm: Mapped[float] = mapped_column(Float, nullable=False)
    deduplicated_area_sqm: Mapped[float | None] = mapped_column(Float, nullable=True)
    share_sum_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    zone_count: Mapped[int] = mapped_column(Integer, nullable=False)
    act_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    incomplete_reasons: Mapped[Any] = mapped_column(JSONB, nullable=False)
    act_identifiers: Mapped[Any] = mapped_column(JSONB, nullable=False)
    area_tolerance_sqm: Mapped[float] = mapped_column(Float, nullable=False)
    share_tolerance_pct: Mapped[float] = mapped_column(Float, nullable=False)
    method_version: Mapped[str] = mapped_column(String(40), nullable=False)
    computed_at: Mapped[datetime] = _timestamp_column()

    __table_args__ = (
        CheckConstraint(
            f"scope IN ({_POG_AREA_SUMMARY_SCOPE_SQL})",
            name="ck_pog_area_summaries_scope",
        ),
        CheckConstraint(
            f"edition IS NULL OR edition IN ({_POG_AREA_SUMMARY_EDITION_SQL})",
            name="ck_pog_area_summaries_edition",
        ),
        CheckConstraint(
            "(scope = 'act' AND planning_act_version_id IS NOT NULL AND edition IS NULL)"
            " OR (scope = 'municipality' AND teryt IS NOT NULL AND edition IS NOT NULL)",
            name="ck_pog_area_summaries_scope_key",
        ),
        CheckConstraint(
            "denominator_area_sqm IS NULL OR denominator_area_sqm > 0",
            name="ck_pog_area_summaries_denominator",
        ),
        # Brak mianownika nie może dać „pełnego” wyniku ani udziałów.
        CheckConstraint(
            "denominator_area_sqm IS NOT NULL OR "
            "(is_complete = false AND share_sum_pct IS NULL AND missing_area_sqm IS NULL)",
            name="ck_pog_area_summaries_null_denominator",
        ),
        Index(
            "uq_pog_area_summaries_act",
            "data_release_id",
            "planning_act_version_id",
            unique=True,
            postgresql_where=sql_text("scope = 'act'"),
        ),
        Index(
            "uq_pog_area_summaries_municipality",
            "data_release_id",
            "teryt",
            "edition",
            unique=True,
            postgresql_where=sql_text("scope = 'municipality'"),
        ),
    )


class PogAreaSummaryZone(Base):
    """Powierzchnia i udział jednego typu strefy w agregacie (BK-405)."""

    __tablename__ = "pog_area_summary_zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    summary_id: Mapped[int] = mapped_column(
        ForeignKey("pog_area_summaries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    zone_code: Mapped[str] = mapped_column(String(20), nullable=False)
    area_sqm: Mapped[float] = mapped_column(Float, nullable=False)
    area_sqkm: Mapped[float] = mapped_column(Float, nullable=False)
    share_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    zone_count: Mapped[int] = mapped_column(Integer, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("summary_id", "zone_code", name="uq_pog_area_summary_zones_code"),
        CheckConstraint("area_sqm >= 0", name="ck_pog_area_summary_zones_area"),
        CheckConstraint(
            "share_pct IS NULL OR share_pct >= 0", name="ck_pog_area_summary_zones_share"
        ),
        CheckConstraint("zone_count >= 0", name="ck_pog_area_summary_zones_count"),
    )


# --- Dokumenty źródłowe (wersjonowane) ---------------------------------------


class RasterAsset(Base):
    """Zgeoreferencjonowany rysunek planu (raster) skonwertowany do COG.

    Raster służy do prezentacji i wspomagania weryfikacji, a NIE do udawania
    wektorowej granicy strefy. Dopóki ``review_status`` nie jest ``verified``
    (ręczna akceptacja), raster nie może być źródłem precyzyjnych przecięć ani
    publikowany do warstwy mapy.

    Pełny ślad pochodzenia: ``source_artifact_id`` wskazuje oryginał (PDF/GeoTIFF),
    ``cog_artifact_id`` wskazuje wynikowy, zwalidowany COG. Oba są zapisane w tym
    samym content-addressed magazynie co pozostałe importy (LocalArtifactStore).
    """

    __tablename__ = "raster_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable: raster może istnieć bez powiązania z konkretną wersją aktu
    # (np. samodzielny rysunek pomocniczy do weryfikacji).
    planning_act_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("planning_act_versions.id"), nullable=True, index=True
    )
    source_artifact_id: Mapped[int] = mapped_column(
        ForeignKey("source_artifacts.id"), nullable=False, index=True
    )
    cog_artifact_id: Mapped[int] = mapped_column(
        ForeignKey("source_artifacts.id"), nullable=False, index=True
    )
    transform_method: Mapped[str] = mapped_column(String(40), nullable=False)
    # Punkty kontrolne (piksel -> współrzędna 2180) jako dowód georeferencji.
    control_points: Mapped[Any] = mapped_column(JSONB, nullable=True)
    rmse_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    pixel_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    nodata: Mapped[float | None] = mapped_column(Float, nullable=True)
    width_px: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height_px: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Zasięg jako geometria (a nie luźne liczby) — spójne z resztą schematu i z
    # indeksem GiST do szybkiego filtrowania po obszarze.
    bounds: Mapped[Any] = mapped_column(
        Geometry("POLYGON", srid=2180, spatial_index=False),
        nullable=False,
    )
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="unreviewed"
    )
    qa_report: Mapped[Any] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(
            f"review_status IN ({_REVIEW_STATUS_SQL})",
            name="ck_raster_assets_review_status",
        ),
        CheckConstraint(
            "rmse_m IS NULL OR rmse_m >= 0", name="ck_raster_assets_rmse"
        ),
        CheckConstraint(
            "NOT ST_IsEmpty(bounds)", name="ck_raster_assets_bounds_not_empty"
        ),
        Index("ix_raster_assets_bounds_gist", "bounds", postgresql_using="gist"),
    )


class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    planning_act_id: Mapped[int | None] = mapped_column(
        ForeignKey("planning_acts.id"), nullable=True, index=True
    )
    data_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_sources.id"), nullable=True, index=True
    )
    title: Mapped[str | None] = mapped_column(ClippedString(500), nullable=True)
    document_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(
            f"document_type IS NULL OR document_type IN ({_DOCUMENT_TYPE_SQL})",
            name="ck_source_documents_document_type",
        ),
    )


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
    media_type: Mapped[str | None] = mapped_column(ClippedString(120), nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    ocr_engine_version: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        *_version_constraints("document_versions", "source_document_id"),
        CheckConstraint(
            "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 1)",
            name="ck_document_versions_quality_score",
        ),
    )


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
    blocks: Mapped[Any] = mapped_column(JSONB, nullable=True)
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
    unit_type: Mapped[str] = mapped_column(String(30), nullable=False)
    number: Mapped[str | None] = mapped_column(ClippedString(60), nullable=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("legal_units.id", ondelete="CASCADE"), nullable=True, index=True
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint("order_index >= 0", name="ck_legal_units_order_index"),
        CheckConstraint(
            "page_from IS NULL OR page_from >= 1",
            name="ck_legal_units_page_from",
        ),
        CheckConstraint(
            "page_to IS NULL OR page_to >= 1",
            name="ck_legal_units_page_to",
        ),
        CheckConstraint(
            "page_from IS NULL OR page_to IS NULL OR page_to >= page_from",
            name="ck_legal_units_page_range",
        ),
    )


class SymbolLegalUnit(Base):
    __tablename__ = "symbol_legal_units"

    id: Mapped[int] = mapped_column(primary_key=True)
    planning_symbol_id: Mapped[int] = mapped_column(
        ForeignKey("planning_symbols.id"), nullable=False, index=True
    )
    legal_unit_id: Mapped[int] = mapped_column(
        ForeignKey("legal_units.id", ondelete="CASCADE"), nullable=False, index=True
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


class PlanningRule(Base):
    """Ustrukturyzowane ustalenie planu z dosłownym dowodem prawnym."""

    __tablename__ = "planning_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    legal_unit_id: Mapped[int] = mapped_column(
        ForeignKey("legal_units.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    operator: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    text_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    conditions: Mapped[Any] = mapped_column(JSONB, nullable=True)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_version: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="unreviewed"
    )
    conflict_group: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(
            f"review_status IN ({_PLANNING_RULE_REVIEW_STATUS_SQL})",
            name="ck_planning_rules_review_status",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_planning_rules_confidence",
        ),
        CheckConstraint(
            "NOT ((source_text IS NULL OR btrim(source_text) = '') "
            "AND (review_status = 'verified' OR confidence > 0.8))",
            name="ck_planning_rules_evidence_required",
        ),
        CheckConstraint(
            "min_value IS NULL OR max_value IS NULL OR min_value <= max_value",
            name="ck_planning_rules_value_range",
        ),
    )
