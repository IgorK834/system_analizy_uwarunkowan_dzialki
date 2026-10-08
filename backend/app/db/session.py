from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.settings import settings


# Kontenery bazy i backendu mogą restartować się niezależnie, więc sprawdzamy
# połączenie przed użyciem i unikamy pracy na przeterminowanych socketach.
engine = create_engine(settings.database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@lru_cache(maxsize=1)
def advisory_lock_engine() -> Engine:
    """Silnik dla sesyjnych blokad doradczych single-flight (AU-007).

    Blokada trzyma jedno połączenie przez cały czas analizy (14–26 s i więcej), więc połączenia są poza
    pulą żądań (``NullPool``: otwarte na czas blokady, zamykane przy jej zdjęciu) i w trybie
    ``AUTOCOMMIT`` — bez otwartej transakcji. Silnik nie utrzymuje połączeń, gdy nikt nie czeka.
    """
    return create_engine(
        settings.database_url, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
