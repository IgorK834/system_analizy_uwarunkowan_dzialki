"""Odczyt świeżych, zakończonych analiz z prostego cache bazodanowego."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import Analysis
from app.models.parcel import Parcel

logger = logging.getLogger(__name__)

DEFAULT_CACHE_MAX_AGE_DAYS: Final[int] = 30
_CACHEABLE_STATUSES: Final[tuple[str, ...]] = ("complete", "partial")


def get_cached_analysis(
    parcel_identifier: str,
    db: Session,
    max_age_days: int = DEFAULT_CACHE_MAX_AGE_DAYS,
) -> Analysis | None:
    """Zwraca najnowszą świeżą analizę dla działki albo ``None``.

    Świeżość jest liczona na żywo względem ``Analysis.analyzed_at``. Pole
    ``Parcel.created_at`` i historyczne ``cache_valid_until`` nie wpływają na
    decyzję. Do cache kwalifikują się tylko wyniki ``complete`` i ``partial``;
    analiza nieudana albo oczekująca na symbol nie może wyglądać jak gotowy,
    aktualny wynik.

    Informacja ``cache_hit``/``cache_miss`` trafia do logu. Kontrakt
    ``AnalyzeResponse`` nie ma osobnego pola metadanych cache i ten moduł go
    nie rozszerza.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    statement = (
        select(Analysis)
        .join(Parcel, Analysis.parcel_id == Parcel.id)
        .where(
            Parcel.parcel_identifier == parcel_identifier,
            Analysis.status.in_(_CACHEABLE_STATUSES),
            Analysis.analyzed_at >= cutoff,
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
