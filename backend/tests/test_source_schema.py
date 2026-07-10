import pytest
from pydantic import ValidationError

from app.schemas.source import (
    SourceMetadata,
    WarningMessage,
    warnings_from_domain_messages,
)


def _source_payload() -> dict:
    return {
        "source_name": "ISOK",
        "confidence": 0.85,
        "manual_review_required": False,
    }


def test_source_metadata_accepts_response_status() -> None:
    source = SourceMetadata.model_validate({**_source_payload(), "response_status": 200})

    assert source.response_status == 200


def test_source_metadata_response_status_defaults_to_none() -> None:
    source = SourceMetadata.model_validate(_source_payload())

    assert source.response_status is None


def test_source_metadata_rejects_confidence_above_one() -> None:
    with pytest.raises(ValidationError):
        SourceMetadata.model_validate({**_source_payload(), "confidence": 1.1})


def test_warning_message_requires_severity() -> None:
    with pytest.raises(ValidationError):
        WarningMessage.model_validate({"code": "X", "message": "Ostrzeżenie"})


def test_warning_message_rejects_invalid_severity_value() -> None:
    with pytest.raises(ValidationError):
        WarningMessage.model_validate(
            {"code": "X", "message": "Ostrzeżenie", "severity": "critical"}
        )


def test_warning_message_accepts_source_name() -> None:
    warning = WarningMessage(
        code="KIUT_WARNING",
        message="Nieznany typ sieci.",
        severity="warning",
        source_name="kiut",
    )

    assert warning.source_name == "kiut"


def test_warning_message_source_name_defaults_to_none() -> None:
    warning = WarningMessage(code="X", message="Informacja", severity="info")

    assert warning.source_name is None


def test_warnings_from_domain_messages_maps_each_string_to_warning_message() -> None:
    warnings = warnings_from_domain_messages("kiut", ["a", "b"])

    assert [warning.message for warning in warnings] == ["a", "b"]
    assert all(warning.source_name == "kiut" for warning in warnings)
    assert all(isinstance(warning, WarningMessage) for warning in warnings)


def test_warnings_from_domain_messages_empty_list_returns_empty_list() -> None:
    assert warnings_from_domain_messages("gdos", []) == []


def test_warnings_from_domain_messages_uses_default_severity_warning() -> None:
    warnings = warnings_from_domain_messages("isok", ["brak klasy"])

    assert warnings[0].severity == "warning"


def test_warnings_from_domain_messages_code_is_uppercased_source_name() -> None:
    warnings = warnings_from_domain_messages("kiut", ["brak typu"])

    assert warnings[0].code == "KIUT_WARNING"


def test_analyze_schemas_reexport_same_classes() -> None:
    from app.schemas.analyze import SourceMetadata as AnalyzeSourceMetadata
    from app.schemas.analyze import WarningMessage as AnalyzeWarningMessage

    assert AnalyzeSourceMetadata is SourceMetadata
    assert AnalyzeWarningMessage is WarningMessage
