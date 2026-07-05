from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.services.geocoding import GeocodeSuggestion, GeocodingServiceUnavailableError

client = TestClient(app)


def _suggestion() -> GeocodeSuggestion:
    return GeocodeSuggestion(
        label="Marki, Generała Władysława Andersa 1",
        x=644234.29,
        y=499514.03,
        confidence=0.44,
        teryt="143402",
        city="Marki",
        street="Generała Władysława Andersa",
        number="1",
    )


def test_get_geocode_suggest_returns_200_with_suggestions() -> None:
    with patch(
        "app.routers.geocode.geocode_address", new_callable=AsyncMock
    ) as mock_geocode:
        mock_geocode.return_value = [_suggestion()]

        response = client.get("/geocode/suggest?q=Marki, Andersa 1")

    assert response.status_code == 200
    assert "suggestions" in response.json()
    assert len(response.json()["suggestions"]) >= 1


def test_get_geocode_suggest_empty_result_returns_200_with_empty_list() -> None:
    with patch(
        "app.routers.geocode.geocode_address", new_callable=AsyncMock
    ) as mock_geocode:
        mock_geocode.return_value = []

        response = client.get("/geocode/suggest?q=Nieistniejący adres")

    assert response.status_code == 200
    assert response.json()["suggestions"] == []
    assert response.json()["total_returned"] == 0


def test_get_geocode_suggest_timeout_returns_503() -> None:
    with patch(
        "app.routers.geocode.geocode_address", new_callable=AsyncMock
    ) as mock_geocode:
        mock_geocode.side_effect = GeocodingServiceUnavailableError("timeout")

        response = client.get("/geocode/suggest?q=Marki, Andersa 1")

    assert response.status_code == 503


def test_get_geocode_suggest_query_too_short_returns_422() -> None:
    response = client.get("/geocode/suggest?q=ab")

    assert response.status_code == 422


def test_get_geocode_suggest_missing_query_returns_422() -> None:
    response = client.get("/geocode/suggest")

    assert response.status_code == 422


def test_get_geocode_suggest_response_contains_query_field() -> None:
    with patch(
        "app.routers.geocode.geocode_address", new_callable=AsyncMock
    ) as mock_geocode:
        mock_geocode.return_value = [_suggestion()]

        response = client.get("/geocode/suggest?q=  Marki  ")

    assert response.json()["query"] == "Marki"


def test_get_geocode_suggest_openapi_has_geocode_path() -> None:
    response = client.get("/openapi.json")

    assert "/geocode/suggest" in response.json()["paths"]


def test_get_geocode_suggest_suggestion_has_x_y_in_epsg2180_range() -> None:
    with patch(
        "app.routers.geocode.geocode_address", new_callable=AsyncMock
    ) as mock_geocode:
        mock_geocode.return_value = [_suggestion()]

        response = client.get("/geocode/suggest?q=Marki, Andersa 1")

    suggestion = response.json()["suggestions"][0]
    assert suggestion["x"] == pytest.approx(644234.29, abs=0.01)
    assert suggestion["y"] == pytest.approx(499514.03, abs=0.01)
