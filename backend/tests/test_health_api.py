from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_returns_http_200() -> None:
    response = client.get("/health")

    assert response.status_code == 200


def test_health_returns_expected_json() -> None:
    response = client.get("/health")

    assert response.json() == {"status": "ok", "service": "backend"}


def test_docs_returns_http_200() -> None:
    response = client.get("/docs")

    assert response.status_code == 200


def test_openapi_contains_health_path() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/health" in response.json()["paths"]


def test_cors_allows_configured_origin() -> None:
    response = client.get("/health", headers={"Origin": "http://localhost:3000"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
