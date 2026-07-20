from app.core.settings import Settings


def test_settings_has_database_url() -> None:
    settings = Settings()

    assert settings.database_url


def test_settings_loads_default_cors_origins_as_list(monkeypatch) -> None:
    # Test wartości domyślnej nie może zależeć od developerskiego pliku .env
    # ani zmiennych wstrzykniętych przez Docker Compose.
    monkeypatch.delenv("BACKEND_CORS_ORIGINS", raising=False)
    settings = Settings(_env_file=None)

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
