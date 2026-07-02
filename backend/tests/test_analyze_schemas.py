from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.analyze import (
    AddressAnalyzeRequest,
    AnalyzeResponse,
    MapAnalyzeRequest,
    ParcelIdAnalyzeRequest,
    SourceMetadata,
    WarningMessage,
)


def test_map_analyze_request_validates_successfully() -> None:
    request = MapAnalyzeRequest.model_validate(
        {"method": "map", "lon": 19.94, "lat": 50.06}
    )

    assert request.method == "map"
    assert request.lon == 19.94
    assert request.lat == 50.06


def test_map_analyze_request_rejects_invalid_lon() -> None:
    with pytest.raises(ValidationError):
        MapAnalyzeRequest.model_validate({"method": "map", "lon": 200, "lat": 50.06})


def test_map_analyze_request_rejects_invalid_lat() -> None:
    with pytest.raises(ValidationError):
        MapAnalyzeRequest.model_validate({"method": "map", "lon": 19.94, "lat": -100})


def test_map_analyze_request_requires_lat() -> None:
    with pytest.raises(ValidationError):
        MapAnalyzeRequest.model_validate({"method": "map", "lon": 19.94})


def test_address_analyze_request_validates_successfully() -> None:
    request = AddressAnalyzeRequest.model_validate(
        {"method": "address", "query": "ul. Marszałkowska 1"}
    )

    assert request.method == "address"
    assert request.query == "ul. Marszałkowska 1"


def test_address_analyze_request_rejects_too_short_query() -> None:
    with pytest.raises(ValidationError):
        AddressAnalyzeRequest.model_validate({"method": "address", "query": ""})


def test_parcel_id_analyze_request_validates_successfully() -> None:
    request = ParcelIdAnalyzeRequest.model_validate(
        {"method": "parcel_id", "parcel_identifier": "122101_1.0001.AR_1.1"}
    )

    assert request.method == "parcel_id"
    assert request.parcel_identifier == "122101_1.0001.AR_1.1"


def test_parcel_id_analyze_request_rejects_empty_identifier() -> None:
    with pytest.raises(ValidationError):
        ParcelIdAnalyzeRequest.model_validate(
            {"method": "parcel_id", "parcel_identifier": ""}
        )


def test_source_metadata_rejects_confidence_above_one() -> None:
    with pytest.raises(ValidationError):
        SourceMetadata.model_validate(
            {
                "source_name": "ULDK",
                "confidence": 1.5,
                "manual_review_required": False,
            }
        )


def test_source_metadata_validates_successfully() -> None:
    source = SourceMetadata.model_validate(
        {
            "source_name": "ULDK",
            "source_url": "https://uldk.gugik.gov.pl/",
            "fetched_at": "2026-07-02T12:00:00Z",
            "confidence": 0.9,
            "manual_review_required": False,
        }
    )

    assert source.confidence == 0.9
    assert source.manual_review_required is False


def test_warning_message_has_expected_fields() -> None:
    warning = WarningMessage.model_validate(
        {
            "code": "POG_NOT_AVAILABLE",
            "message": "Plan Ogólny Gminy nie jest dostępny.",
            "section": "pog",
        }
    )

    assert warning.code == "POG_NOT_AVAILABLE"
    assert warning.message == "Plan Ogólny Gminy nie jest dostępny."
    assert warning.section == "pog"


def test_analyze_response_can_be_built_with_required_fields() -> None:
    response = AnalyzeResponse.model_validate(
        {
            "analysis_id": None,
            "status": "not_implemented",
            "analyzed_at": datetime.now(UTC),
            "parcel": None,
            "mpzp_zones": [],
            "pog": None,
            "infrastructure": [],
            "risks": [],
            "buildable_area_sqm": None,
            "warnings": [
                {
                    "code": "ANALYSIS_NOT_IMPLEMENTED",
                    "message": "Analiza nie jest jeszcze zaimplementowana.",
                    "section": None,
                }
            ],
            "sources": [],
        }
    )

    assert response.status == "not_implemented"
    assert response.warnings[0].code == "ANALYSIS_NOT_IMPLEMENTED"


def test_analyze_response_accepts_empty_warnings_and_sources() -> None:
    response = AnalyzeResponse.model_validate(
        {
            "analysis_id": None,
            "status": "partial",
            "analyzed_at": datetime.now(UTC),
            "parcel": None,
            "mpzp_zones": [],
            "pog": None,
            "infrastructure": [],
            "risks": [],
            "buildable_area_sqm": None,
            "warnings": [],
            "sources": [],
        }
    )

    assert response.warnings == []
    assert response.sources == []


def test_analyze_schemas_do_not_import_orm_or_sqlalchemy() -> None:
    schema_source = (
        Path(__file__).resolve().parents[1] / "app" / "schemas" / "analyze.py"
    ).read_text(encoding="utf-8")

    assert "app.models" not in schema_source
    assert "sqlalchemy" not in schema_source
