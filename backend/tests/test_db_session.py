import inspect

from sqlalchemy.orm import DeclarativeBase

from app.db.base import Base
from app.db import session
from tests.repo_structure import find_repo_root


def test_get_db_is_generator_function() -> None:
    assert inspect.isgeneratorfunction(session.get_db)


def test_engine_uses_pool_pre_ping() -> None:
    assert session.engine.pool._pre_ping is True


def test_session_local_disables_autocommit_and_autoflush() -> None:
    assert session.SessionLocal.kw["autocommit"] is False
    assert session.SessionLocal.kw["autoflush"] is False


def test_env_example_database_url_uses_db_host() -> None:
    env_example = find_repo_root() / ".env.example"
    content = env_example.read_text(encoding="utf-8")
    database_url_line = next(
        line for line in content.splitlines() if line.startswith("DATABASE_URL=")
    )

    assert "@db:5432/" in database_url_line
    assert "localhost" not in database_url_line


def test_base_subclasses_declarative_base() -> None:
    assert issubclass(Base, DeclarativeBase)


def test_get_db_closes_session(monkeypatch) -> None:
    class FakeSession:
        closed = False

        def close(self) -> None:
            self.closed = True

    fake_session = FakeSession()
    monkeypatch.setattr(session, "SessionLocal", lambda: fake_session)

    db_generator = session.get_db()
    assert next(db_generator) is fake_session

    db_generator.close()

    assert fake_session.closed is True
