from app.core.settings import Settings


def test_settings_has_database_url() -> None:
    settings = Settings()

    assert settings.database_url


def test_settings_loads_default_cors_origins_as_list() -> None:
    settings = Settings()

    assert isinstance(settings.backend_cors_origins, list)
    assert "http://localhost:3000" in settings.backend_cors_origins
    assert "http://frontend:3000" in settings.backend_cors_origins


def test_settings_parses_comma_separated_cors_env(monkeypatch) -> None:
    monkeypatch.setenv(
        "BACKEND_CORS_ORIGINS",
        "http://localhost:3000,http://frontend:3000",
    )

    settings = Settings()

    assert settings.backend_cors_origins == [
        "http://localhost:3000",
        "http://frontend:3000",
    ]
