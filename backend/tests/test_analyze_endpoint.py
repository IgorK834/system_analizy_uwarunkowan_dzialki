from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.schemas.analyze import (
    AnalyzeResponse,
    GeometryMetrics,
    ParcelGeometryResponse,
    SourceMetadata,
    UtilitiesPreviewResult,
)
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


def _analyze_response(result: ParcelLookupResult) -> AnalyzeResponse:
    return AnalyzeResponse(
        status="partial",
        analyzed_at=datetime.now(timezone.utc),
        parcel=None,
        mpzp_zones=[],
        pog=None,
        infrastructure=[],
        risks=[],
        buildable_area_sqm=None,
        warnings=[],
        sources=[result.source_metadata],
    )


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
        "app.routers.analyze.run_analysis", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.return_value = _analyze_response(mock_parcel_result)

        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    assert response.status_code == 200
    assert response.json()["status"] == "partial"


def test_analyze_address_request_returns_200_partial(
    mock_parcel_result: ParcelLookupResult,
) -> None:
    with patch(
        "app.routers.analyze.run_analysis", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.return_value = _analyze_response(mock_parcel_result)

        response = client.post(
            "/analyze",
            json={
                "method": "address",
                "query": "ul. Testowa 1, Kraków",
                "selected_lon": 19.94,
                "selected_lat": 50.06,
            },
        )

    assert response.status_code == 200


def test_analyze_parcel_id_request_returns_200_partial(
    mock_parcel_result: ParcelLookupResult,
) -> None:
    with patch(
        "app.routers.analyze.run_analysis", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.return_value = _analyze_response(mock_parcel_result)

        response = client.post(
            "/analyze",
            json={"method": "parcel_id", "parcel_identifier": "122101_1.0001.1"},
        )

    assert response.status_code == 200


def test_analyze_force_refresh_query_is_forwarded_to_orchestrator(
    mock_parcel_result: ParcelLookupResult,
) -> None:
    with patch(
        "app.routers.analyze.run_analysis", new_callable=AsyncMock
    ) as mock_run:
        mock_run.return_value = _analyze_response(mock_parcel_result)

        response = client.post(
            "/analyze?force_refresh=true",
            json={"method": "parcel_id", "parcel_identifier": "122101_1.0001.1"},
        )

    assert response.status_code == 200
    assert mock_run.await_args.kwargs["force_refresh"] is True


def test_analyze_response_parcel_includes_buildable_area_geojson(
    mock_parcel_result: ParcelLookupResult,
) -> None:
    response_with_parcel = _analyze_response(mock_parcel_result).model_copy(
        update={
            "parcel": ParcelGeometryResponse(
                parcel_identifier=mock_parcel_result.parcel_identifier,
                geometry_geojson={"type": "Polygon", "coordinates": []},
                metrics=GeometryMetrics(
                    area_sqm=10000.0,
                    area_ha=1.0,
                    perimeter_m=400.0,
                    is_valid=True,
                    geometry_repaired=False,
                ),
                source=mock_parcel_result.source_metadata,
                buildable_area_geojson={
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": []},
                    "properties": {
                        "layer": "buildable_area",
                        "setback_m": 4.0,
                        "is_technical_approximation": True,
                    },
                },
            )
        }
    )
    with patch(
        "app.routers.analyze.run_analysis", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.return_value = response_with_parcel

        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    parcel = response.json()["parcel"]
    assert parcel is not None
    assert parcel["buildable_area_geojson"]["properties"][
        "is_technical_approximation"
    ] is True


def test_analyze_response_parcel_allows_null_buildable_area_geojson(
    mock_parcel_result: ParcelLookupResult,
) -> None:
    # None jest sygnałem, że techniczne odsunięcie zredukowało obszar do zera
    # albo wynik pochodzi z cache bez przeliczonej geometrii.
    response_with_parcel = _analyze_response(mock_parcel_result).model_copy(
        update={
            "parcel": ParcelGeometryResponse(
                parcel_identifier=mock_parcel_result.parcel_identifier,
                geometry_geojson={"type": "Polygon", "coordinates": []},
                metrics=GeometryMetrics(
                    area_sqm=10000.0,
                    area_ha=1.0,
                    perimeter_m=400.0,
                    is_valid=True,
                    geometry_repaired=False,
                ),
                source=mock_parcel_result.source_metadata,
                buildable_area_geojson=None,
            )
        }
    )
    with patch(
        "app.routers.analyze.run_analysis", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.return_value = response_with_parcel

        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    assert response.json()["parcel"]["buildable_area_geojson"] is None


def test_analyze_response_contains_sources(
    mock_parcel_result: ParcelLookupResult,
) -> None:
    with patch(
        "app.routers.analyze.run_analysis", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.return_value = _analyze_response(mock_parcel_result)

        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    sources = response.json()["sources"]
    assert len(sources) == 1
    assert sources[0]["source_name"] == "ULDK"


def test_analyze_response_exposes_utilities_preview(
    mock_parcel_result: ParcelLookupResult,
) -> None:
    result = _analyze_response(mock_parcel_result).model_copy(
        update={
            "utilities_preview": UtilitiesPreviewResult(
                coverage_status="not_covered",
                county_name=None,
                layer_available=False,
                note="Pusty podgląd nie jest dowodem braku sieci.",
                source=SourceMetadata(
                    source_name="KIUT (GUGiK)",
                    source_url="https://kiut.example.test/wms",
                    confidence=0.9,
                    manual_review_required=False,
                ),
            )
        }
    )
    with patch(
        "app.routers.analyze.run_analysis",
        new=AsyncMock(return_value=result),
    ):
        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    assert response.status_code == 200
    preview = response.json()["utilities_preview"]
    assert preview["coverage_status"] == "not_covered"
    assert preview["layer_available"] is False
    assert "braku sieci" in preview["note"]


def test_analyze_parcel_not_found_returns_404() -> None:
    with patch(
        "app.routers.analyze.run_analysis", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.side_effect = ParcelNotFoundError("Brak działki")

        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    assert response.status_code == 404


def test_analyze_address_not_found_returns_404() -> None:
    with patch(
        "app.routers.analyze.run_analysis", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.side_effect = AddressNotFoundError("Brak adresu")

        response = client.post(
            "/analyze",
            json={
                "method": "address",
                "query": "Nieistniejące miejsce 999",
                "selected_lon": 19.94,
                "selected_lat": 50.06,
            },
        )

    assert response.status_code == 404


def test_analyze_uldk_unavailable_returns_503() -> None:
    with patch(
        "app.routers.analyze.run_analysis", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.side_effect = UldkServiceUnavailableError("Timeout")

        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    assert response.status_code == 503


def test_analyze_geocoding_unavailable_returns_503() -> None:
    with patch(
        "app.routers.analyze.run_analysis", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.side_effect = GeocodingServiceUnavailableError("Timeout")

        response = client.post(
            "/analyze",
            json={
                "method": "address",
                "query": "ul. Testowa 1, Kraków",
                "selected_lon": 19.94,
                "selected_lat": 50.06,
            },
        )

    assert response.status_code == 503


def test_analyze_coordinates_outside_poland_returns_422() -> None:
    with patch(
        "app.routers.analyze.run_analysis", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.side_effect = CoordinatesOutsidePolandError("Poza granicami")

        response = client.post(
            "/analyze", json={"method": "map", "lon": 19.94, "lat": 50.06}
        )

    assert response.status_code == 422


def test_analyze_invalid_identifier_returns_422() -> None:
    with patch(
        "app.routers.analyze.run_analysis", new_callable=AsyncMock
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
        "app.routers.analyze.run_analysis", new_callable=AsyncMock
    ) as mock_resolve:
        mock_resolve.return_value = _analyze_response(mock_parcel_result)

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
