from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_analyze_map_request_returns_501() -> None:
    response = client.post("/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06})

    assert response.status_code == 501
    assert response.json()["detail"] == "Analiza nie jest jeszcze zaimplementowana."


def test_analyze_address_request_returns_501() -> None:
    response = client.post(
        "/analyze",
        json={"method": "address", "query": "ul. Testowa 1, Kraków"},
    )

    assert response.status_code == 501


def test_analyze_parcel_id_request_returns_501() -> None:
    response = client.post(
        "/analyze",
        json={"method": "parcel_id", "parcel_identifier": "122101_1.0001.1"},
    )

    assert response.status_code == 501


def test_analyze_invalid_method_returns_422() -> None:
    response = client.post("/analyze", json={"method": "invalid_method"})

    assert response.status_code == 422


def test_analyze_invalid_map_lon_returns_422() -> None:
    response = client.post("/analyze", json={"method": "map", "lon": 999, "lat": 50.0})

    assert response.status_code == 422


def test_analyze_missing_method_returns_422() -> None:
    response = client.post("/analyze", json={})

    assert response.status_code == 422


def test_analyze_short_address_query_returns_422() -> None:
    response = client.post("/analyze", json={"method": "address", "query": "ab"})

    assert response.status_code == 422


def test_openapi_returns_http_200() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200


def test_openapi_contains_analyze_response_schema() -> None:
    response = client.get("/openapi.json")
    openapi = response.json()

    assert "AnalyzeResponse" in openapi["components"]["schemas"]


def test_openapi_contains_analyze_path() -> None:
    response = client.get("/openapi.json")

    assert "/analyze" in response.json()["paths"]


def test_openapi_contains_analyze_request_contract() -> None:
    response = client.get("/openapi.json")
    request_schema = response.json()["paths"]["/analyze"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]

    assert request_schema["title"] == "AnalyzeRequest"
    assert request_schema["discriminator"]["propertyName"] == "method"


def test_docs_returns_http_200() -> None:
    response = client.get("/docs")

    assert response.status_code == 200
