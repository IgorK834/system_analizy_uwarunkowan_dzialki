import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.settings import settings
from app.db.base import Base
import app.models  # noqa: F401

config = context.config
# BK-306: testy migracji/wersjonowanego modelu wykonują upgrade/downgrade na
# żywej bazie i nie mogą robić tego na bazie aplikacji (deweloperskiej ani
# używanej przez inne testy) — dropnięte kolumny nadal liczą się do limitu
# 1600 atrybutów tabeli w PostgreSQL. Zmienna środowiskowa pozwala testowemu
# fixture'owi (tests/conftest.py) przekierować alembic na jednorazową bazę
# bez modyfikowania settings.database_url używanego przez resztę aplikacji.
database_url = os.environ.get("ALEMBIC_DATABASE_URL_OVERRIDE") or settings.database_url
config.set_main_option("sqlalchemy.url", database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
