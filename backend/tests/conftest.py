"""Wspólne fixture'y testów backendu."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.rate_limit import reset_all_rate_limiters
from app.core.settings import settings
from app.modules.analysis.infrastructure.terrain_raster import clear_native_grid_cache

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


@pytest.fixture(autouse=True)
def terrain_relief_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zwykłe CI nie odpytuje WCS NMT (BK-302).

    Pochodne rastra są domyślnie wyłączone, a testy BK-302 włączają je jawnie i
    wstrzykują zamrożone odpowiedzi z ``tests/fixtures/terrain``. Pamięć
    DescribeCoverage jest czyszczona, aby testy nie zależały od kolejności.
    """
    monkeypatch.setattr(settings, "terrain_relief_enabled", False)
    clear_native_grid_cache()


@pytest.fixture(autouse=True)
def enable_app_loggers() -> None:
    """``alembic/env.py`` woła ``fileConfig``, które domyślnie wyłącza istniejące loggery.

    Testy migracji wykonują ``alembic upgrade`` w procesie pytest, więc bez tego loggery ``app.*``
    byłyby wyłączone dla testów uruchamianych po nich i ``caplog`` nie widziałby ich wpisów.
    """
    for name, logger in list(logging.root.manager.loggerDict.items()):
        if name.startswith("app.") and isinstance(logger, logging.Logger):
            logger.disabled = False


@pytest.fixture(autouse=True)
def reset_rate_limiters() -> None:
    """Liczniki limitów są globalne w procesie — testy nie mogą się nimi dzielić."""
    reset_all_rate_limiters()


# BK-306: moduły testowe, które wykonują ``alembic upgrade``/``downgrade`` (a
# więc modyfikują strukturę tabel) na żywej bazie. Uruchamiane wielokrotnie na
# tej samej bazie co aplikacja, zostawiały ją w pośredniej rewizji i zjadały
# limit 1600 atrybutów PostgreSQL na tabelę (dropnięte kolumny nadal liczą się
# do attnum) — patrz sekcja "Reset bazy danych" w README. Te moduły dostają
# jednorazową bazę utworzoną z szablonu ``template_postgis`` zamiast bazy
# aplikacji; pozostałe testy integracyjne (współdzielące marker
# ``pytest.mark.integration``, ale nieużywające alembic downgrade) nadal
# korzystają z bazy wskazanej przez ``DATABASE_URL``.
_MIGRATION_TEST_MODULES = frozenset(
    {
        "test_alembic_integration",
        "test_migration_016_pog_status",
        "test_migration_017_pog_provenance",
        "test_migration_018_019_mpzp",
        "test_migration_020_021",
        "test_migration_022",
        "test_migration_023",
        "test_migration_026",
        "test_migration_029",
        "test_migration_030",
        "test_migration_031",
        "test_versioned_model",
    }
)


def _server_url(dbname: str, *, driver: bool) -> str:
    """URL do innej bazy na tym samym serwerze co ``settings.database_url``.

    Z ``driver=True`` zwraca URL SQLAlchemy (np. ``postgresql+psycopg2://``);
    z ``driver=False`` zwraca DSN akceptowany bezpośrednio przez ``psycopg2``
    (bez sufiksu sterownika), używany do operacji administracyjnych
    (CREATE/DROP DATABASE), których nie można wykonać w transakcji SQLAlchemy.
    """
    parts = urlsplit(settings.database_url)
    scheme = parts.scheme if driver else parts.scheme.split("+", 1)[0]
    return urlunsplit((scheme, parts.netloc, f"/{dbname}", "", ""))


@pytest.fixture(scope="module", autouse=True)
def _isolated_migration_database(request: pytest.FixtureRequest):
    """Izoluje testy alembic/wersjonowanego modelu w jednorazowej bazie.

    Tworzy bazę ``test_migrations_<losowy sufiks>`` z szablonu
    ``template_postgis`` (te same rozszerzenia PostGIS co baza aplikacji),
    przełącza na nią silnik zaimportowany przez testowany moduł
    (``from app.db.session import engine``/``SessionLocal``) oraz URL,
    którego ``alembic/env.py`` używa do migracji (zmienna
    ``ALEMBIC_DATABASE_URL_OVERRIDE``), a po zakończeniu modułu bazę usuwa —
    niezależnie od wyniku testów (upgrade do ``head`` na końcu jest
    obowiązkiem samych testów, patrz zadanie 2 w README).
    """
    module_name = request.module.__name__.rsplit(".", 1)[-1]
    if module_name not in _MIGRATION_TEST_MODULES:
        yield
        return

    db_name = f"test_migrations_{uuid.uuid4().hex[:16]}"
    admin_dsn = _server_url("postgres", driver=False)

    admin_conn = psycopg2.connect(admin_dsn)
    admin_conn.autocommit = True
    try:
        with admin_conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{db_name}" TEMPLATE template_postgis')
    finally:
        admin_conn.close()

    fresh_url = _server_url(db_name, driver=True)
    new_engine = create_engine(fresh_url, pool_pre_ping=True)

    patcher = pytest.MonkeyPatch()
    patcher.setenv("ALEMBIC_DATABASE_URL_OVERRIDE", fresh_url)
    # Niektóre testy w tych modułach (np. przypadki bez własnego
    # ``command.upgrade``) zakładają bazę już zmigrowaną do head — tak jak
    # dotąd zakładały to, korzystając ze współdzielonej bazy aplikacji.
    # Testy, które same wykonują upgrade/downgrade, robią to niezależnie
    # (upgrade do head jest idempotentny).
    alembic_command.upgrade(AlembicConfig(str(_ALEMBIC_INI)), "head")
    if hasattr(request.module, "engine"):
        patcher.setattr(request.module, "engine", new_engine)
    if hasattr(request.module, "SessionLocal"):
        patcher.setattr(
            request.module,
            "SessionLocal",
            sessionmaker(autocommit=False, autoflush=False, bind=new_engine),
        )

    try:
        yield
    finally:
        patcher.undo()
        new_engine.dispose()
        admin_conn = psycopg2.connect(admin_dsn)
        admin_conn.autocommit = True
        try:
            with admin_conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (db_name,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        finally:
            admin_conn.close()
