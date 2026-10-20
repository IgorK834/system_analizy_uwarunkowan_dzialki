"""Lokalne wydania BDOT10k (osie jezdni, obrysy budynków, zasięg powiatu) — migracja 036.

Wydanie to jedna paczka powiatowa (``data_releases.scope_key = 'powiat:<TERYT>'``). Obiekty należą do wydania, więc
wycofanie wydania nie usuwa danych poprzedniego, a analiza zapisuje ``data_release_id``, z którego policzono wynik.
Geometrie są w EPSG:2180 (układ kanoniczny), mają indeks GiST i zakaz pustej geometrii. Pola tekstu wolnego z paczki
(nazwa, informacja dodatkowa, uwagi) oraz identyfikator EGiB nie są przechowywane — wynik i eksport nie mają danych
osobowych (minimalizacja zakresu).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.types import ClippedString


class BdotRelease(Base):
    """Opis wydania BDOT10k: powiat, zasięg (granica powiatu), liczności klas i data stanu danych."""

    __tablename__ = "bdot_releases"

    id: Mapped[int] = mapped_column(primary_key=True)
    data_release_id: Mapped[int] = mapped_column(
        ForeignKey("data_releases.id"), nullable=False, unique=True
    )
    teryt_powiat: Mapped[str] = mapped_column(String(4), nullable=False, index=True)
    powiat_name: Mapped[str | None] = mapped_column(ClippedString(255), nullable=True)
    package_name: Mapped[str] = mapped_column(ClippedString(255), nullable=False)
    package_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Najnowsza wersja obiektu w paczce (``wersja``): data, na którą dane są aktualne — nie data pobrania.
    data_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    road_count: Mapped[int] = mapped_column(Integer, nullable=False)
    building_count: Mapped[int] = mapped_column(Integer, nullable=False)
    class_stats: Mapped[Any] = mapped_column(JSONB, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    extent: Mapped[Any] = mapped_column(
        Geometry("MULTIPOLYGON", srid=2180, spatial_index=False), nullable=False
    )

    __table_args__ = (
        CheckConstraint("NOT ST_IsEmpty(extent)", name="ck_bdot_releases_extent_not_empty"),
        CheckConstraint("road_count >= 0 AND building_count >= 0", name="ck_bdot_releases_counts"),
        Index("ix_bdot_releases_extent_gist", "extent", postgresql_using="gist"),
    )


class BdotRoad(Base):
    """Oś jezdni (``OT_SKJZ_L``) — fakt geometryczny, nie ocena prawnego dostępu do drogi publicznej."""

    __tablename__ = "bdot_roads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    data_release_id: Mapped[int] = mapped_column(ForeignKey("data_releases.id"), nullable=False)
    local_id: Mapped[str] = mapped_column(String(64), nullable=False)
    existence_category: Mapped[str | None] = mapped_column(ClippedString(40), nullable=True)
    management_category: Mapped[str | None] = mapped_column(ClippedString(40), nullable=True)
    road_class: Mapped[str | None] = mapped_column(ClippedString(80), nullable=True)
    surface_material: Mapped[str | None] = mapped_column(ClippedString(80), nullable=True)
    road_position: Mapped[str | None] = mapped_column(ClippedString(80), nullable=True)
    road_number: Mapped[str | None] = mapped_column(ClippedString(40), nullable=True)
    street_name: Mapped[str | None] = mapped_column(ClippedString(255), nullable=True)
    surface_width_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    feature_version_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    geometry: Mapped[Any] = mapped_column(
        Geometry("MULTILINESTRING", srid=2180, spatial_index=False), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("data_release_id", "local_id", name="uq_bdot_roads_release_local_id"),
        CheckConstraint("NOT ST_IsEmpty(geometry)", name="ck_bdot_roads_geometry_not_empty"),
        Index("ix_bdot_roads_geometry_gist", "geometry", postgresql_using="gist"),
    )


class BdotBuilding(Base):
    """Obrys budynku (``OT_BUBD_A``); bez nazw, uwag i identyfikatora EGiB."""

    __tablename__ = "bdot_buildings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    data_release_id: Mapped[int] = mapped_column(ForeignKey("data_releases.id"), nullable=False)
    local_id: Mapped[str] = mapped_column(String(64), nullable=False)
    existence_category: Mapped[str | None] = mapped_column(ClippedString(40), nullable=True)
    general_function: Mapped[str | None] = mapped_column(ClippedString(160), nullable=True)
    detailed_function: Mapped[str | None] = mapped_column(ClippedString(160), nullable=True)
    predominant_function: Mapped[str | None] = mapped_column(ClippedString(160), nullable=True)
    floors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    geometry_source: Mapped[str | None] = mapped_column(ClippedString(60), nullable=True)
    feature_version_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    area_sqm: Mapped[float] = mapped_column(Float, nullable=False)
    geometry: Mapped[Any] = mapped_column(
        Geometry("MULTIPOLYGON", srid=2180, spatial_index=False), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("data_release_id", "local_id", name="uq_bdot_buildings_release_local_id"),
        CheckConstraint("NOT ST_IsEmpty(geometry)", name="ck_bdot_buildings_geometry_not_empty"),
        CheckConstraint("area_sqm > 0", name="ck_bdot_buildings_area_positive"),
        Index("ix_bdot_buildings_geometry_gist", "geometry", postgresql_using="gist"),
    )


class BdotLandCover(Base):
    """Pokrycie terenu, dwanaście klas PT (bez nazw i danych osobowych)."""
    __tablename__ = "bdot_landcover"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    data_release_id: Mapped[int] = mapped_column(ForeignKey("data_releases.id"), nullable=False)
    local_id: Mapped[str] = mapped_column(String(64), nullable=False)
    class_code: Mapped[str] = mapped_column(String(9), nullable=False)
    kind: Mapped[str | None] = mapped_column(ClippedString(160), nullable=True)
    feature_version_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    geometry: Mapped[Any] = mapped_column(Geometry("MULTIPOLYGON", srid=2180, spatial_index=False), nullable=False)
    __table_args__ = (
        UniqueConstraint("data_release_id", "local_id", name="uq_bdot_landcover_release_local_id"),
        CheckConstraint("NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry)", name="ck_bdot_landcover_geometry"),
        Index("ix_bdot_landcover_geometry_gist", "geometry", postgresql_using="gist"),
        Index("ix_bdot_landcover_release_class", "data_release_id", "class_code"),
    )
