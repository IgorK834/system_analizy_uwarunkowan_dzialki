from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.mpzp import (
    ExtractedEvidence,
    MpzpParameter,
    MpzpParseRequest,
    MpzpParseResult,
    MpzpParserWarning,
    MpzpZoneResult,
)


def _parameter(name: str = "max_building_height_m") -> MpzpParameter:
    return MpzpParameter(
        name=name,
        normalized_value=9.0,
        unit="m",
        raw_value="9,0 m",
        source_text="maksymalna wysokość zabudowy wynosi 9,0 m",
        page_number=12,
        confidence=0.92,
        manual_review_required=False,
    )


def test_mpzp_parameter_has_all_required_fields() -> None:
    parameter = _parameter()

    assert parameter.name == "max_building_height_m"
    assert parameter.normalized_value == 9.0
    assert parameter.unit == "m"
    assert parameter.raw_value == "9,0 m"
    assert parameter.source_text.startswith("maksymalna")
    assert parameter.page_number == 12
    assert parameter.confidence == 0.92
    assert parameter.manual_review_required is False


def test_mpzp_parameter_rejects_confidence_above_one() -> None:
    with pytest.raises(ValidationError):
        MpzpParameter(
            name="height",
            confidence=1.5,
            manual_review_required=False,
        )


def test_mpzp_parameter_optional_fields_default_to_none() -> None:
    parameter = MpzpParameter(
        name="primary_use",
        confidence=0.5,
        manual_review_required=True,
    )

    assert parameter.normalized_value is None
    assert parameter.unit is None
    assert parameter.raw_value is None
    assert parameter.source_text is None
    assert parameter.page_number is None


def test_extracted_evidence_all_fields_optional() -> None:
    evidence = ExtractedEvidence()

    assert evidence.raw_value is None
    assert evidence.source_text is None
    assert evidence.page_number is None


def test_mpzp_zone_result_holds_multiple_parameters() -> None:
    zone = MpzpZoneResult(
        zone_symbol="MN",
        parameters=[_parameter("a"), _parameter("b"), _parameter("c")],
    )

    assert len(zone.parameters) == 3


def test_mpzp_parse_result_holds_multiple_zones() -> None:
    result = MpzpParseResult(
        plan_id="P-1",
        zones=[
            MpzpZoneResult(zone_symbol="MN", parameters=[_parameter()]),
            MpzpZoneResult(zone_symbol="U", parameters=[_parameter("max_floors")]),
        ],
        status="complete",
    )

    assert len(result.zones) == 2
    assert sum(len(zone.parameters) for zone in result.zones) == 2


def test_mpzp_parser_warning_rejects_invalid_severity() -> None:
    with pytest.raises(ValidationError):
        MpzpParserWarning(
            stage="extract_text",
            code="X",
            message="x",
            severity="critical",
        )


def test_mpzp_parser_warning_rejects_invalid_stage() -> None:
    with pytest.raises(ValidationError):
        MpzpParserWarning(
            stage="nonexistent_stage",
            code="X",
            message="x",
            severity="warning",
        )


def test_mpzp_parse_request_zone_symbols_defaults_to_empty_list() -> None:
    request = MpzpParseRequest(uchwala_url="https://example.test/plan.pdf")

    assert request.zone_symbols == []


def test_mpzp_parse_result_rejects_invalid_status() -> None:
    with pytest.raises(ValidationError):
        MpzpParseResult(status="done")


def test_mpzp_schema_module_does_not_import_orm_or_sqlalchemy() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "app" / "schemas" / "mpzp.py"
    ).read_text(encoding="utf-8")

    assert "app.models" not in source
    assert "sqlalchemy" not in source


def test_mpzp_zone_result_docstring_mentions_naming_collision() -> None:
    assert "analyze" in (MpzpZoneResult.__doc__ or "")


def test_package_reexports_parser_zone_with_unambiguous_alias() -> None:
    from app.schemas import MpzpParserZoneResult
    from app.schemas import MpzpZoneResult as AnalyzeMpzpZoneResult
    from app.schemas.analyze import MpzpZoneResult as DirectAnalyzeMpzpZoneResult

    assert MpzpParserZoneResult is MpzpZoneResult
    assert AnalyzeMpzpZoneResult is DirectAnalyzeMpzpZoneResult

