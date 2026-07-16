from datetime import datetime, timezone

from app.schemas.analyze import (
    AnalyzeResponse,
    GeometryMetrics,
    MpzpZoneResult,
    ParcelGeometryResponse,
    WarningMessage,
)
from app.schemas.source import SourceMetadata
from app.services.context import ContextResult, ContextSectionResult
from app.services.persistence import collect_source_records

_FETCHED_AT = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


def _source(
    name: str,
    url: str | None = None,
    *,
    response_status: int | None = None,
    confidence: float = 0.9,
    manual_review_required: bool = False,
) -> SourceMetadata:
    return SourceMetadata(
        source_name=name,
        source_url=url,
        fetched_at=_FETCHED_AT,
        response_status=response_status,
        confidence=confidence,
        manual_review_required=manual_review_required,
    )


def _response(**updates) -> AnalyzeResponse:
    values = {
        "status": "partial",
        "analyzed_at": _FETCHED_AT,
        "parcel": None,
        "mpzp_zones": [],
        "pog": None,
        "infrastructure": [],
        "risks": [],
        "buildable_area_sqm": None,
        "warnings": [],
        "sources": [],
    }
    values.update(updates)
    return AnalyzeResponse(**values)


def test_collect_source_records_contains_uldk_from_parcel_and_deduplicates() -> None:
    uldk = _source(
        "ULDK",
        "https://uldk.gugik.gov.pl/",
        response_status=200,
    )
    result = _response(
        parcel=ParcelGeometryResponse(
            parcel_identifier="122101_1.0001.123",
            geometry_geojson={"type": "MultiPolygon", "coordinates": []},
            metrics=GeometryMetrics(
                area_sqm=100.0,
                area_ha=0.01,
                perimeter_m=40.0,
                is_valid=True,
                geometry_repaired=False,
            ),
            source=uldk,
        ),
        sources=[uldk],
    )

    records = collect_source_records(result)

    assert len(records) == 1
    assert records[0].source_name == "ULDK"
    assert records[0].response_status == "200"
    assert records[0].checksum is None


def test_collect_source_records_preserves_manual_mpzp_provenance() -> None:
    result = _response(
        mpzp_zones=[
            MpzpZoneResult(
                zone_symbol="230_U",
                intersection_area_sqm=100.0,
                intersection_pct=100.0,
                is_dominant=True,
                source=_source(
                    "manual_user_input",
                    "https://bip.example.test/plan.pdf",
                    confidence=0.5,
                    manual_review_required=True,
                ),
            )
        ]
    )

    record = collect_source_records(result)[0]

    assert record.source_name == "manual_user_input"
    assert record.confidence == 0.5
    assert record.manual_review_required is True


def test_collect_source_records_maps_context_statuses_and_warnings() -> None:
    context = ContextResult(
        kiut=ContextSectionResult(
            section="kiut",
            status="available",
            source_metadata=_source(
                "KIUT",
                "https://kiut.example.test",
                response_status=200,
            ),
            warnings=["Ostrzeżenie KIUT."],
        ),
        isok=ContextSectionResult(
            section="isok",
            status="unavailable",
            warnings=["ISOK niedostępny."],
        ),
        gdos=ContextSectionResult(
            section="gdos",
            status="error",
            warnings=["Błąd GDOŚ."],
        ),
    )

    records = collect_source_records(_response(), context)
    by_name = {record.source_name: record for record in records}

    assert len(records) == 3
    assert by_name["KIUT"].response_status == "200"
    assert by_name["KIUT"].warnings == ["Ostrzeżenie KIUT."]
    assert by_name["isok"].response_status == "unavailable"
    assert by_name["isok"].manual_review_required is True
    assert by_name["gdos"].response_status == "error"


def test_collect_source_records_does_not_fabricate_context_without_result() -> None:
    records = collect_source_records(_response())

    assert records == []


def test_collect_source_records_attaches_matching_api_warning() -> None:
    result = _response(
        sources=[_source("KIMPZP", "https://kimpzp.example.test")],
        warnings=[
            WarningMessage(
                code="KIMPZP_WARNING",
                message="Źródło zwróciło dane częściowe.",
                severity="warning",
                source_name="kimpzp",
            )
        ],
    )

    record = collect_source_records(result)[0]

    assert record.warnings == ["Źródło zwróciło dane częściowe."]
    assert record.response_status == "available"


def test_collect_source_records_merges_duplicate_context_warning() -> None:
    source = _source("KIUT", "https://kiut.example.test", response_status=200)
    context = ContextResult(
        kiut=ContextSectionResult(
            section="kiut",
            status="available",
            source_metadata=source,
            warnings=["Warning z ContextResult."],
        ),
        isok=ContextSectionResult(section="isok", status="available"),
        gdos=ContextSectionResult(section="gdos", status="available"),
    )

    records = collect_source_records(_response(sources=[source]), context)
    kiut = next(record for record in records if record.source_name == "KIUT")

    assert [record.source_name for record in records].count("KIUT") == 1
    assert kiut.warnings == ["Warning z ContextResult."]
