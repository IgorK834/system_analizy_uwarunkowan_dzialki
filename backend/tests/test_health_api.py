from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from app.main import app


client = TestClient(app)


def test_health_returns_http_200() -> None:
    response = client.get("/health")

    assert response.status_code == 200


def test_health_returns_expected_json() -> None:
    response = client.get("/health")

    assert response.json() == {"status": "ok", "service": "backend"}


def test_health_live_returns_200_without_touching_database() -> None:
    with patch(
        "app.routers.health.SessionLocal",
        side_effect=RuntimeError("baza nie działa"),
    ) as session_factory:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "backend"}
    session_factory.assert_not_called()


def test_health_ready_returns_200_when_database_answers() -> None:
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    with patch("app.routers.health.SessionLocal", return_value=session):
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "backend"}
    session.execute.assert_called_once()


def test_health_ready_returns_503_without_leaking_driver_error() -> None:
    with patch(
        "app.routers.health.SessionLocal",
        side_effect=RuntimeError("postgresql://app:sekret@db/dzialki"),
    ):
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Baza danych jest niedostępna."}
    assert "sekret" not in response.text


def test_docs_returns_http_200() -> None:
    response = client.get("/docs")

    assert response.status_code == 200


def test_openapi_contains_health_path() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/health" in response.json()["paths"]
    assert "/health/live" in response.json()["paths"]
    assert "/health/ready" in response.json()["paths"]


def test_cors_allows_configured_origin() -> None:
    response = client.get("/health", headers={"Origin": "http://localhost:3000"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
