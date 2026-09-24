"""Odczyt świeżych, zakończonych analiz z prostego cache bazodanowego."""

from __future__ import annotations

import logging
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Final

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.analysis import Analysis
from app.models.parcel import Parcel
from app.models.versioned import DataRelease, DataSource

logger = logging.getLogger(__name__)

DEFAULT_CACHE_MAX_AGE_DAYS: Final[int] = 30
DEFAULT_PARTIAL_CACHE_MAX_AGE_MINUTES: Final[int] = 15
_CACHEABLE_STATUSES: Final[tuple[str, ...]] = ("complete", "partial")
RESULT_CONTRACT_VERSION: Final[str] = "pog-v2"


def current_cache_signature(db: Session) -> tuple[str, list[int]]:
    """Hashuje kontrakt odpowiedzi i pełny zestaw aktywnych wydań danych."""
    rows = db.execute(
        select(DataSource.source_id, DataRelease.id, DataRelease.version_label)
        .join(DataRelease, DataRelease.data_source_id == DataSource.id)
        .where(DataRelease.is_active.is_(True))
        .order_by(DataSource.source_id, DataRelease.id)
    ).all()
    releases = [
        {"source_id": str(source_id), "release_id": int(release_id), "version": str(version)}
        for source_id, release_id, version in rows
    ]
    encoded = json.dumps(
        {"contract": RESULT_CONTRACT_VERSION, "releases": releases},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), [item["release_id"] for item in releases]


def get_cached_analysis(
    parcel_identifier: str,
    db: Session,
    max_age_days: int = DEFAULT_CACHE_MAX_AGE_DAYS,
    partial_max_age_minutes: int = DEFAULT_PARTIAL_CACHE_MAX_AGE_MINUTES,
) -> Analysis | None:
    """Zwraca najnowszą świeżą analizę dla działki albo ``None``.

    Świeżość jest liczona na żywo względem ``Analysis.analyzed_at``. Pole
    ``Parcel.created_at`` i historyczne ``cache_valid_until`` nie wpływają na
    decyzję. Wynik ``complete`` ma dłuższy TTL, natomiast ``partial`` jest
    cache'owany tylko krótko: jego braki często wynikają z przejściowej awarii
    źródła i nie mogą blokować ponownej próby przez 30 dni. Analiza nieudana
    albo oczekująca na symbol nie może wyglądać jak gotowy, aktualny wynik.

    Informacja ``cache_hit``/``cache_miss`` trafia do logu. Kontrakt
    ``AnalyzeResponse`` nie ma osobnego pola metadanych cache i ten moduł go
    nie rozszerza.
    """
    now = datetime.now(timezone.utc)
    complete_cutoff = now - timedelta(days=max_age_days)
    partial_cutoff = now - timedelta(minutes=partial_max_age_minutes)
    signature, _release_ids = current_cache_signature(db)
    statement = (
        select(Analysis)
        .join(Parcel, Analysis.parcel_id == Parcel.id)
        .where(
            Parcel.parcel_identifier == parcel_identifier,
            Analysis.status.in_(_CACHEABLE_STATUSES),
            Analysis.cache_signature == signature,
            or_(
                and_(
                    Analysis.status == "complete",
                    Analysis.analyzed_at >= complete_cutoff,
                ),
                and_(
                    Analysis.status == "partial",
                    Analysis.analyzed_at >= partial_cutoff,
                ),
            ),
        )
        .order_by(Analysis.analyzed_at.desc())
        .limit(1)
    )
    cached = db.execute(statement).scalar_one_or_none()
    if cached is None:
        logger.info("cache_miss parcel_identifier=%s", parcel_identifier)
    else:
        logger.info("cache_hit parcel_identifier=%s", parcel_identifier)
    return cached


def should_refresh_analysis(
    force_refresh: bool,
    cached: Analysis | None,
) -> bool:
    """Rozstrzyga, czy uruchomić analizę po wcześniejszym odczycie cache.

    Funkcja nie przelicza wieku drugi raz — świeżość rozstrzyga wyłącznie
    ``get_cached_analysis``.
    """
    return force_refresh or cached is None
