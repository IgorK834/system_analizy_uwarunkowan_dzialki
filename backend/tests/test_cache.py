import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import delete

from app.db.session import SessionLocal
from app.models.analysis import Analysis
from app.models.parcel import Parcel
from app.services.cache import (
    DEFAULT_CACHE_MAX_AGE_DAYS,
    get_cached_analysis,
    should_refresh_analysis,
)

pytestmark = pytest.mark.integration

_PREFIX = "CACHE_TEST_"
_NOW = datetime.now(timezone.utc)


def _cleanup() -> None:
    with SessionLocal() as db:
        parcels = db.query(Parcel).filter(Parcel.parcel_identifier.like(f"{_PREFIX}%"))
        parcel_ids = [parcel.id for parcel in parcels]
        if parcel_ids:
            db.execute(delete(Analysis).where(Analysis.parcel_id.in_(parcel_ids)))
            db.execute(delete(Parcel).where(Parcel.id.in_(parcel_ids)))
        db.commit()


@pytest.fixture(autouse=True)
def cleanup_cache_rows():
    _cleanup()
    yield
    _cleanup()


def _create_analysis(
    identifier: str,
    *,
    analyzed_at: datetime,
    status: str = "complete",
    parcel_created_at: datetime | None = None,
) -> int:
    with SessionLocal() as db:
        parcel = Parcel(
            parcel_identifier=identifier,
            geometry="SRID=2180;MULTIPOLYGON(((0 0,10 0,10 10,0 10,0 0)))",
            area_sqm=100.0,
            created_at=parcel_created_at or _NOW,
        )
        db.add(parcel)
        db.flush()
        analysis = Analysis(
            parcel_id=parcel.id,
            analyzed_at=analyzed_at,
            status=status,
            cache_valid_until=_NOW - timedelta(days=365),
        )
        db.add(analysis)
        db.commit()
        return analysis.id


def test_analysis_younger_than_default_ttl_is_cache_hit() -> None:
    identifier = f"{_PREFIX}FRESH"
    analysis_id = _create_analysis(
        identifier,
        analyzed_at=_NOW - timedelta(days=1),
    )

    with SessionLocal() as db:
        cached = get_cached_analysis(identifier, db)

    assert DEFAULT_CACHE_MAX_AGE_DAYS == 30
    assert cached is not None
    assert cached.id == analysis_id
    assert should_refresh_analysis(False, cached) is False


def test_analysis_older_than_default_ttl_requires_refresh() -> None:
    identifier = f"{_PREFIX}STALE"
    _create_analysis(identifier, analyzed_at=_NOW - timedelta(days=31))

    with SessionLocal() as db:
        cached = get_cached_analysis(identifier, db)

    assert cached is None
    assert should_refresh_analysis(False, cached) is True


def test_force_refresh_always_bypasses_fresh_cache() -> None:
    identifier = f"{_PREFIX}FORCE"
    _create_analysis(identifier, analyzed_at=_NOW - timedelta(hours=1))

    with SessionLocal() as db:
        cached = get_cached_analysis(identifier, db)

    assert cached is not None
    assert should_refresh_analysis(True, cached) is True


def test_cache_uses_analysis_time_not_old_parcel_created_at() -> None:
    identifier = f"{_PREFIX}PARCEL_AGE"
    analysis_id = _create_analysis(
        identifier,
        analyzed_at=_NOW - timedelta(days=1),
        parcel_created_at=_NOW - timedelta(days=3650),
    )

    with SessionLocal() as db:
        cached = get_cached_analysis(identifier, db)

    assert cached is not None
    assert cached.id == analysis_id


@pytest.mark.parametrize("status", ["failed", "waiting_for_zone_symbol"])
def test_non_cacheable_status_is_ignored(status: str) -> None:
    identifier = f"{_PREFIX}{status.upper()}"
    _create_analysis(
        identifier,
        analyzed_at=_NOW - timedelta(hours=1),
        status=status,
    )

    with SessionLocal() as db:
        assert get_cached_analysis(identifier, db) is None


def test_cache_returns_newest_eligible_analysis() -> None:
    identifier = f"{_PREFIX}NEWEST"
    older_id = _create_analysis(
        identifier,
        analyzed_at=_NOW - timedelta(days=2),
    )
    with SessionLocal() as db:
        parcel = db.query(Parcel).filter_by(parcel_identifier=identifier).one()
        newer = Analysis(
            parcel_id=parcel.id,
            analyzed_at=_NOW - timedelta(hours=1),
            status="partial",
        )
        db.add(newer)
        db.commit()
        newer_id = newer.id

    with SessionLocal() as db:
        cached = get_cached_analysis(identifier, db)

    assert cached.id == newer_id
    assert cached.id != older_id


def test_cache_logs_hit_and_miss(caplog: pytest.LogCaptureFixture) -> None:
    identifier = f"{_PREFIX}LOG"
    _create_analysis(identifier, analyzed_at=_NOW - timedelta(hours=1))

    cache_logger = logging.getLogger("app.services.cache")
    caplog.set_level(logging.INFO, logger=cache_logger.name)
    # Alembic uruchamiany wcześniej w tym samym procesie stosuje fileConfig,
    # który wyłącza istniejące, nienazwane w alembic.ini loggery aplikacji.
    with patch.object(cache_logger, "disabled", False), SessionLocal() as db:
        get_cached_analysis(identifier, db)
        get_cached_analysis(f"{_PREFIX}MISSING", db)

    assert "cache_hit" in caplog.text
    assert "cache_miss" in caplog.text
    assert identifier in caplog.text
