from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.schemas.analyze import SourceMetadata
from app.services.geocoding import GeocodingServiceUnavailableError
from app.services.geometry import CoordinatesOutsidePolandError
from app.services.initiation import AddressNotFoundError
from app.services.uldk import (
    InvalidParcelIdentifierError,
    ParcelLookupResult,
    ParcelNotFoundError,
    UldkServiceUnavailableError,
)

client = TestClient(app)


@pytest.fixture
def mock_parcel_result() -> ParcelLookupResult:
    return ParcelLookupResult(
        parcel_identifier="122101_1.0001.1234",
        wkt="MULTIPOLYGON(((500000 200000,500100 200000,500100 200100,500000 200000)))",
        teryt="122101",
        source_metadata=SourceMetadata(
            source_name="ULDK",
            source_url="https://uldk.gugik.gov.pl/",
            fetched_at=datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc),
            confidence=1.0,
            manual_review_required=False,
        ),
    )


def test_analyze_map_request_returns_200_partial(
    mock_parcel_result: ParcelLookupResult,
) -> None:
    with patch(
        "app.routers.analyze.resolve_parcel", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.return_value = mock_parcel_result

        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    assert response.status_code == 200
    assert response.json()["status"] == "partial"


def test_analyze_address_request_returns_200_partial(
    mock_parcel_result: ParcelLookupResult,
) -> None:
    with patch(
        "app.routers.analyze.resolve_parcel", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.return_value = mock_parcel_result

        response = client.post(
            "/analyze",
            json={"method": "address", "query": "ul. Testowa 1, Kraków"},
        )

    assert response.status_code == 200


def test_analyze_parcel_id_request_returns_200_partial(
    mock_parcel_result: ParcelLookupResult,
) -> None:
    with patch(
        "app.routers.analyze.resolve_parcel", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.return_value = mock_parcel_result

        response = client.post(
            "/analyze",
            json={"method": "parcel_id", "parcel_identifier": "122101_1.0001.1"},
        )

    assert response.status_code == 200


def test_analyze_response_contains_sources(
    mock_parcel_result: ParcelLookupResult,
) -> None:
    with patch(
        "app.routers.analyze.resolve_parcel", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.return_value = mock_parcel_result

        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    sources = response.json()["sources"]
    assert len(sources) == 1
    assert sources[0]["source_name"] == "ULDK"


def test_analyze_parcel_not_found_returns_404() -> None:
    with patch(
        "app.routers.analyze.resolve_parcel", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.side_effect = ParcelNotFoundError("Brak działki")

        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    assert response.status_code == 404


def test_analyze_address_not_found_returns_404() -> None:
    with patch(
        "app.routers.analyze.resolve_parcel", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.side_effect = AddressNotFoundError("Brak adresu")

        response = client.post(
            "/analyze",
            json={"method": "address", "query": "Nieistniejące miejsce 999"},
        )

    assert response.status_code == 404


def test_analyze_uldk_unavailable_returns_503() -> None:
    with patch(
        "app.routers.analyze.resolve_parcel", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.side_effect = UldkServiceUnavailableError("Timeout")

        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    assert response.status_code == 503


def test_analyze_geocoding_unavailable_returns_503() -> None:
    with patch(
        "app.routers.analyze.resolve_parcel", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.side_effect = GeocodingServiceUnavailableError("Timeout")

        response = client.post(
            "/analyze",
            json={"method": "address", "query": "ul. Testowa 1, Kraków"},
        )

    assert response.status_code == 503


def test_analyze_coordinates_outside_poland_returns_422() -> None:
    with patch(
        "app.routers.analyze.resolve_parcel", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.side_effect = CoordinatesOutsidePolandError("Poza granicami")

        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    assert response.status_code == 422


def test_analyze_invalid_identifier_returns_422() -> None:
    with patch(
        "app.routers.analyze.resolve_parcel", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.side_effect = InvalidParcelIdentifierError("Zły format")

        response = client.post(
            "/analyze",
            json={"method": "parcel_id", "parcel_identifier": "122101_1.0001.1"},
        )

    assert response.status_code == 422


def test_analyze_analyzed_at_is_present(
    mock_parcel_result: ParcelLookupResult,
) -> None:
    with patch(
        "app.routers.analyze.resolve_parcel", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.return_value = mock_parcel_result

        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    assert "analyzed_at" in response.json()


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
