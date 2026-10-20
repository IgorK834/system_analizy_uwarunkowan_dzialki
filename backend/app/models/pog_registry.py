"""Krajowy rejestr aktywnych wersji aktów POG, katalog RU, kolejka importu i tabele mapy (ADR-026).

Model sprzed ADR-026 miał jedno aktywne *wydanie* na źródło ``pog_app``, więc import drugiej gminy ukrywał pierwszą.
Teraz:

* ``data_releases`` jest rekordem audytowym jednego artefaktu/przebiegu importu (nie wyznacza już „stanu mapy”);
* **stan krajowy** to zbiór wersji aktów aktywnych w danej chwili, zapisany w czasowym rejestrze
  ``pog_registry_entries`` (zakres ``[state_from_id, state_to_id)`` w numerach stanów ``pog_registry_states``);
* numer stanu rośnie przy każdej zmianie rejestru (import, wycofanie, wycofanie wersji), więc kafle, inspektor i
  agregaty przypięte do numeru stanu odtwarzają dokładnie ten sam zbiór wersji — także po późniejszych importach.

Tabele ``pog_map_*`` są kopiami wyłącznie do wyświetlania (geometria w EPSG:3857 w trzech stopniach uproszczenia i
gotowe atrybuty kafla); powstają przy publikacji wersji aktu i nigdy nie są zmieniane — o widoczności decyduje rejestr.
Geometrie kanoniczne (EPSG:2180, analiza) zostają w ``planning_features`` i ``plan_boundaries``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
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
    func,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.types import ClippedString

POG_REGISTRY_STATE_KINDS: tuple[str, ...] = ("import", "withdrawal", "rollback", "migration")
POG_REGISTRY_END_REASONS: tuple[str, ...] = ("replaced", "withdrawn", "not_current", "rolled_back")
IMPORT_JOB_STATUSES: tuple[str, ...] = ("queued", "running", "succeeded", "failed", "cancelled")
IMPORT_JOB_KINDS: tuple[str, ...] = ("pog_commune", "bdot_county")

_STATE_KINDS_SQL = ", ".join(f"'{value}'" for value in POG_REGISTRY_STATE_KINDS)
_END_REASONS_SQL = ", ".join(f"'{value}'" for value in POG_REGISTRY_END_REASONS)
_JOB_STATUSES_SQL = ", ".join(f"'{value}'" for value in IMPORT_JOB_STATUSES)
_JOB_KINDS_SQL = ", ".join(f"'{value}'" for value in IMPORT_JOB_KINDS)


class PogRegistryState(Base):
    """Numer stanu krajowego POG: jedna zmiana rejestru = jeden numer (rosnący, zatwierdzany w kolejności)."""

    __tablename__ = "pog_registry_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    data_release_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_releases.id"), nullable=True, index=True
    )
    summary: Mapped[Any] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(f"kind IN ({_STATE_KINDS_SQL})", name="ck_pog_registry_states_kind"),
    )


class PogRegistryEntry(Base):
    """Okres aktywności jednej wersji aktu w rejestrze: stany ``[state_from_id, state_to_id)``.

    Najwyżej jeden wpis na akt jest otwarty (``state_to_id IS NULL``) — to gwarantuje częściowy indeks unikalny.
    ``ru_published_act_id`` to identyfikator wersji aktu w katalogu RU (token porównania w trybie przyrostowym).
    """

    __tablename__ = "pog_registry_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    planning_act_id: Mapped[int] = mapped_column(
        ForeignKey("planning_acts.id"), nullable=False, index=True
    )
    planning_act_version_id: Mapped[int] = mapped_column(
        ForeignKey("planning_act_versions.id"), nullable=False, index=True
    )
    state_from_id: Mapped[int] = mapped_column(
        ForeignKey("pog_registry_states.id"), nullable=False, index=True
    )
    state_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("pog_registry_states.id"), nullable=True, index=True
    )
    end_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ru_published_act_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(state_to_id IS NULL) = (end_reason IS NULL)", name="ck_pog_registry_entries_end"
        ),
        CheckConstraint(
            "state_to_id IS NULL OR state_to_id > state_from_id",
            name="ck_pog_registry_entries_range",
        ),
        CheckConstraint(
            f"end_reason IS NULL OR end_reason IN ({_END_REASONS_SQL})",
            name="ck_pog_registry_entries_reason",
        ),
        Index(
            "uq_pog_registry_entries_active",
            "planning_act_id",
            unique=True,
            postgresql_where=sql_text("state_to_id IS NULL"),
        ),
    )


class PogRuCatalogEntry(Base):
    """Migawka listy aktów POG z Rejestru Urbanistycznego (publiczny wykaz opublikowanych aktów).

    ``is_current`` — akt jest bieżącą wersją na ostatniej *kompletnej* liście; brak wpisu dla gminy nie jest
    dowodem braku planu (to „brak aktu POG w RU w chwili synchronizacji”).
    """

    __tablename__ = "pog_ru_catalog"

    published_act_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    terc: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    plan_type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str | None] = mapped_column(ClippedString(300), nullable=True)
    voivodeship: Mapped[str | None] = mapped_column(ClippedString(40), nullable=True)
    district: Mapped[str | None] = mapped_column(ClippedString(80), nullable=True)
    commune: Mapped[str | None] = mapped_column(ClippedString(120), nullable=True)
    status_text: Mapped[str | None] = mapped_column(ClippedString(80), nullable=True)
    legal_status: Mapped[str] = mapped_column(String(30), nullable=False)
    publish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_sync_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_runs.id"), nullable=True
    )

    __table_args__ = (
        Index("ix_pog_ru_catalog_current", "terc", postgresql_where=sql_text("is_current")),
    )


class ImportJob(Base):
    """Kolejka zleceń importu (Postgres, ``FOR UPDATE SKIP LOCKED``); unikalne aktywne zlecenie na zakres."""

    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="queued")
    requested_by: Mapped[str] = mapped_column(String(30), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    last_error: Mapped[str | None] = mapped_column(ClippedString(500), nullable=True)
    result: Mapped[Any] = mapped_column(JSONB, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(f"status IN ({_JOB_STATUSES_SQL})", name="ck_import_jobs_status"),
        CheckConstraint(f"kind IN ({_JOB_KINDS_SQL})", name="ck_import_jobs_kind"),
        Index(
            "uq_import_jobs_active",
            "kind",
            "scope_key",
            unique=True,
            postgresql_where=sql_text("status IN ('queued', 'running')"),
        ),
        Index("ix_import_jobs_queue", "status", "not_before", "id"),
    )


class PogMapAct(Base):
    """Granica aktu do wyświetlania (warstwa przeglądowa statusów): trzy stopnie uproszczenia w EPSG:3857."""

    __tablename__ = "pog_map_acts"

    planning_act_version_id: Mapped[int] = mapped_column(
        ForeignKey("planning_act_versions.id", ondelete="CASCADE"), primary_key=True
    )
    act_id: Mapped[str] = mapped_column(String(200), nullable=False)
    teryt: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    legal_status: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str | None] = mapped_column(ClippedString(300), nullable=True)
    act_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolution_number: Mapped[str | None] = mapped_column(ClippedString(200), nullable=True)
    resolution_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    legal_valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    legal_valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Początek wersji obiektu w rejestrze (``poczatekWersjiObiektu``), data wejścia w życie i dziennik urzędowy
    # pochodzą z aktu i jego dokumentu uchwalającego (``DokumentFormalny``, relacja ``uchwala``).
    version_started_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    official_journal: Mapped[str | None] = mapped_column(ClippedString(200), nullable=True)
    document_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    geom_overview: Mapped[Any] = mapped_column(
        Geometry("GEOMETRY", srid=3857, spatial_index=False), nullable=True
    )
    geom_low: Mapped[Any] = mapped_column(
        Geometry("GEOMETRY", srid=3857, spatial_index=False), nullable=True
    )
    geom_full: Mapped[Any] = mapped_column(
        Geometry("GEOMETRY", srid=3857, spatial_index=False), nullable=False
    )

    __table_args__ = (
        Index("ix_pog_map_acts_geom_full_gist", "geom_full", postgresql_using="gist"),
    )


class PogMapZone(Base):
    """Strefa planistyczna do wyświetlania: gotowe atrybuty kafla i geometria w trzech stopniach uproszczenia."""

    __tablename__ = "pog_map_zones"

    feature_id: Mapped[int] = mapped_column(
        ForeignKey("planning_features.id", ondelete="CASCADE"), primary_key=True
    )
    planning_act_version_id: Mapped[int] = mapped_column(
        ForeignKey("planning_act_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    legal_status: Mapped[str] = mapped_column(String(30), nullable=False)
    feature_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    feature_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(60), nullable=True)
    designation: Mapped[str | None] = mapped_column(String(60), nullable=True)
    label: Mapped[str | None] = mapped_column(ClippedString(200), nullable=True)
    zone_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    max_overground_floor_area_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_building_height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_building_coverage_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_biologically_active_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    parameters_informational: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    primary_profiles: Mapped[str | None] = mapped_column(String(256), nullable=True)
    additional_profiles: Mapped[str | None] = mapped_column(String(256), nullable=True)
    teryt: Mapped[str | None] = mapped_column(String(20), nullable=True)
    act_id: Mapped[str] = mapped_column(String(200), nullable=False)
    geom_low: Mapped[Any] = mapped_column(
        Geometry("GEOMETRY", srid=3857, spatial_index=False), nullable=True
    )
    geom_mid: Mapped[Any] = mapped_column(
        Geometry("GEOMETRY", srid=3857, spatial_index=False), nullable=True
    )
    geom_full: Mapped[Any] = mapped_column(
        Geometry("GEOMETRY", srid=3857, spatial_index=False), nullable=False
    )

    __table_args__ = (
        Index("ix_pog_map_zones_geom_full_gist", "geom_full", postgresql_using="gist"),
    )


class PogMapOverlay(Base):
    """OUZ, OZS albo OSDIS do wyświetlania (``layer`` rozróżnia klasę; atrybuty jak w kaflu)."""

    __tablename__ = "pog_map_overlays"

    feature_id: Mapped[int] = mapped_column(
        ForeignKey("planning_features.id", ondelete="CASCADE"), primary_key=True
    )
    planning_act_version_id: Mapped[int] = mapped_column(
        ForeignKey("planning_act_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    layer: Mapped[str] = mapped_column(String(40), nullable=False)
    legal_status: Mapped[str] = mapped_column(String(30), nullable=False)
    feature_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    feature_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(60), nullable=True)
    label: Mapped[str | None] = mapped_column(ClippedString(200), nullable=True)
    teryt: Mapped[str | None] = mapped_column(String(20), nullable=True)
    act_id: Mapped[str] = mapped_column(String(200), nullable=False)
    geom_low: Mapped[Any] = mapped_column(
        Geometry("GEOMETRY", srid=3857, spatial_index=False), nullable=True
    )
    geom_mid: Mapped[Any] = mapped_column(
        Geometry("GEOMETRY", srid=3857, spatial_index=False), nullable=True
    )
    geom_full: Mapped[Any] = mapped_column(
        Geometry("GEOMETRY", srid=3857, spatial_index=False), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "layer IN ('ouz', 'downtown', 'social_infrastructure_standard')",
            name="ck_pog_map_overlays_layer",
        ),
        Index("ix_pog_map_overlays_geom_full_gist", "geom_full", postgresql_using="gist"),
    )
