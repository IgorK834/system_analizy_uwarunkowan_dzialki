"""Testy systemowej walidacji i confidence scoring wyniku parsera MPZP."""

import json
from pathlib import Path

from app.schemas.mpzp import (
    MpzpParameter,
    MpzpParseResult,
    MpzpParserWarning,
    MpzpZoneResult,
)
from app.services.mpzp_parser_descriptive import extract_descriptive_parameters
from app.services.mpzp_parser_extract import TextExtractionResult
from app.services.mpzp_parser_numeric import extract_numeric_parameters
from app.services.mpzp_parser_segment import find_zone_sections, segment_document
from app.services.mpzp_parser_validate import validate_mpzp_result

REAL_FIXTURE_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "mpzp_documents"
    / "bielsko_biala_uchwala_viii_187_2024"
)


def _real_bielsko_biala_segments():
    data = json.loads((REAL_FIXTURE_DIR / "pages.json").read_text(encoding="utf-8"))
    return segment_document(TextExtractionResult(pages=data["pages"]))


def _parameter(**overrides) -> MpzpParameter:
    defaults = dict(
        name="max_building_height_m",
        normalized_value=9.0,
        unit="m",
        raw_value="9 m",
        source_text="ma. wysokość 9 m",
        page_number=5,
        confidence=0.9,
        manual_review_required=False,
    )
    defaults.update(overrides)
    return MpzpParameter(**defaults)


# --- konflikt wewnątrz strefy, przepuszczony przez cały pipeline --------


def test_real_pipeline_230_umw_height_conflict_ends_with_manual_review() -> None:
    # Test end-to-end: numeric -> validate na PRAWDZIWYM konflikcie wysokości
    # w strefie 230_UMW (15 m vs 13 m).
    segments = _real_bielsko_biala_segments()
    zone_section = find_zone_sections(segments, ["230_UMW"])[0]
    numeric_params = extract_numeric_parameters("230_UMW", zone_section, segments)
    descriptive_params = extract_descriptive_parameters(
        "230_UMW", zone_section, segments
    )
    zone = MpzpZoneResult(
        zone_symbol="230_UMW", parameters=numeric_params + descriptive_params
    )
    result = MpzpParseResult(status="complete", zones=[zone])

    validated = validate_mpzp_result(result)

    height_params = [
        p
        for p in validated.zones[0].parameters
        if p.name == "max_building_height_m"
    ]
    assert {p.normalized_value for p in height_params} == {15.0, 13.0}
    assert all(p.manual_review_required is True for p in height_params)
    assert any(
        "max_building_height_m" in flag and "230_UMW" in flag
        for flag in validated.conflict_flags
    )
    assert any(
        warning.code == "PARAMETER_CONFLICT"
        and warning.parameter_name == "max_building_height_m"
        for warning in validated.warnings
    )


def test_conflict_flags_lists_distinct_values_for_conflicting_parameter() -> None:
    zone = MpzpZoneResult(
        zone_symbol="230_UMW",
        parameters=[
            _parameter(normalized_value=15.0),
            _parameter(normalized_value=13.0),
        ],
    )
    result = MpzpParseResult(status="complete", zones=[zone])

    validated = validate_mpzp_result(result)

    assert len(validated.conflict_flags) == 1
    assert "230_UMW" in validated.conflict_flags[0]
    assert "max_building_height_m" in validated.conflict_flags[0]


def test_non_conflicting_parameters_across_zones_are_not_flagged() -> None:
    # Różne wartości MIĘDZY strefami nie są konfliktem.
    zone_u = MpzpZoneResult(
        zone_symbol="230_U", parameters=[_parameter(normalized_value=15.0)]
    )
    zone_umw = MpzpZoneResult(
        zone_symbol="230_UMW", parameters=[_parameter(normalized_value=13.0)]
    )
    result = MpzpParseResult(status="complete", zones=[zone_u, zone_umw])

    validated = validate_mpzp_result(result)

    assert validated.conflict_flags == []
    assert all(
        p.manual_review_required is False
        for zone in validated.zones
        for p in zone.parameters
    )


def test_descriptive_string_parameters_with_multiple_entries_are_not_conflicts() -> (
    None
):
    # roof_geometry (i inne parametry opisowe) mają z natury wiele wpisów tej
    # samej nazwy — to współistniejące ustalenia uchwały, nie sprzeczność.
    zone = MpzpZoneResult(
        zone_symbol="230_U",
        parameters=[
            _parameter(
                name="roof_geometry",
                normalized_value="dwuspadowy_lub_wielospadowy",
                unit=None,
            ),
            _parameter(
                name="roof_geometry", normalized_value="płaski", unit=None
            ),
        ],
    )
    result = MpzpParseResult(status="complete", zones=[zone])

    validated = validate_mpzp_result(result)

    assert validated.conflict_flags == []
    assert all(
        p.manual_review_required is False for p in validated.zones[0].parameters
    )


def test_duplicate_identical_value_is_not_a_conflict() -> None:
    zone = MpzpZoneResult(
        zone_symbol="230_U",
        parameters=[
            _parameter(normalized_value=15.0),
            _parameter(normalized_value=15.0),
        ],
    )
    result = MpzpParseResult(status="complete", zones=[zone])

    validated = validate_mpzp_result(result)

    assert validated.conflict_flags == []
    assert all(
        p.manual_review_required is False for p in validated.zones[0].parameters
    )


# --- source_text is None/empty -> confidence ceiling ---------------------


def test_parameter_without_source_text_gets_low_confidence() -> None:
    zone = MpzpZoneResult(
        zone_symbol="MN",
        parameters=[_parameter(source_text=None, confidence=0.95)],
    )
    result = MpzpParseResult(status="complete", zones=[zone])

    validated = validate_mpzp_result(result)

    validated_param = validated.zones[0].parameters[0]
    assert validated_param.confidence <= 0.4
    assert validated_param.manual_review_required is True


def test_parameter_with_empty_string_source_text_also_gets_low_confidence() -> None:
    zone = MpzpZoneResult(
        zone_symbol="MN",
        parameters=[_parameter(source_text="", confidence=0.9)],
    )
    result = MpzpParseResult(status="complete", zones=[zone])

    validated = validate_mpzp_result(result)

    assert validated.zones[0].parameters[0].confidence <= 0.4


def test_parameter_with_source_text_is_not_capped() -> None:
    zone = MpzpZoneResult(
        zone_symbol="MN",
        parameters=[_parameter(source_text="15 m", confidence=0.9)],
    )
    result = MpzpParseResult(status="complete", zones=[zone])

    validated = validate_mpzp_result(result)

    assert validated.zones[0].parameters[0].confidence == 0.9


# --- zakresy domenowe -----------------------------------------------------


def test_percent_parameter_above_100_triggers_domain_violation() -> None:
    zone = MpzpZoneResult(
        zone_symbol="230_U",
        parameters=[
            _parameter(
                name="min_biologically_active_percent",
                normalized_value=150.0,
                unit="percent",
            )
        ],
    )
    result = MpzpParseResult(status="complete", zones=[zone])

    validated = validate_mpzp_result(result)

    assert validated.zones[0].parameters[0].manual_review_required is True
    assert any(
        warning.code == "DOMAIN_RANGE_VIOLATION" for warning in validated.warnings
    )


def test_percent_parameter_negative_triggers_domain_violation() -> None:
    zone = MpzpZoneResult(
        zone_symbol="230_U",
        parameters=[
            _parameter(
                name="max_building_coverage_percent",
                normalized_value=-10.0,
                unit="percent",
            )
        ],
    )
    result = MpzpParseResult(status="complete", zones=[zone])

    validated = validate_mpzp_result(result)

    assert validated.zones[0].parameters[0].manual_review_required is True


def test_negative_max_building_height_triggers_domain_violation() -> None:
    zone = MpzpZoneResult(
        zone_symbol="230_U",
        parameters=[_parameter(name="max_building_height_m", normalized_value=-5.0)],
    )
    result = MpzpParseResult(status="complete", zones=[zone])

    validated = validate_mpzp_result(result)

    assert validated.zones[0].parameters[0].manual_review_required is True
    assert any(
        warning.code == "DOMAIN_RANGE_VIOLATION" for warning in validated.warnings
    )


def test_zero_max_intensity_triggers_domain_violation() -> None:
    zone = MpzpZoneResult(
        zone_symbol="230_U",
        parameters=[_parameter(name="max_intensity", normalized_value=0.0)],
    )
    result = MpzpParseResult(status="complete", zones=[zone])

    validated = validate_mpzp_result(result)

    assert validated.zones[0].parameters[0].manual_review_required is True


def test_valid_percent_within_range_does_not_trigger_violation() -> None:
    zone = MpzpZoneResult(
        zone_symbol="230_U",
        parameters=[
            _parameter(
                name="min_biologically_active_percent",
                normalized_value=10.0,
                unit="percent",
            )
        ],
    )
    result = MpzpParseResult(status="complete", zones=[zone])

    validated = validate_mpzp_result(result)

    assert validated.zones[0].parameters[0].manual_review_required is False
    assert not any(
        warning.code == "DOMAIN_RANGE_VIOLATION" for warning in validated.warnings
    )


def test_descriptive_string_parameter_is_not_subject_to_domain_range_check() -> None:
    zone = MpzpZoneResult(
        zone_symbol="230_ZP",
        parameters=[
            _parameter(
                name="prohibition",
                normalized_value="zakaz zabudowy",
                unit=None,
                confidence=0.85,
            )
        ],
    )
    result = MpzpParseResult(status="complete", zones=[zone])

    validated = validate_mpzp_result(result)

    assert validated.zones[0].parameters[0].manual_review_required is False


# --- kara globalna OCR ----------------------------------------------------


def test_ocr_warning_lowers_confidence_of_all_parameters() -> None:
    zone = MpzpZoneResult(zone_symbol="MN", parameters=[_parameter(confidence=0.9)])
    warning = MpzpParserWarning(
        stage="extract_text",
        code="NEEDS_OCR",
        message="Dokument wymaga OCR.",
        severity="warning",
    )
    result = MpzpParseResult(status="complete", zones=[zone], warnings=[warning])

    validated = validate_mpzp_result(result)

    assert validated.zones[0].parameters[0].confidence < 0.9


def test_no_ocr_warning_does_not_lower_confidence() -> None:
    zone = MpzpZoneResult(zone_symbol="MN", parameters=[_parameter(confidence=0.9)])
    result = MpzpParseResult(status="complete", zones=[zone])

    validated = validate_mpzp_result(result)

    assert validated.zones[0].parameters[0].confidence == 0.9


# --- kara dla strefy z ostrzeżeniem ZONE_SECTION_AMBIGUOUS ---------------


def test_ambiguous_zone_section_lowers_confidence_only_for_that_zone() -> None:
    zone_mn = MpzpZoneResult(zone_symbol="MN", parameters=[_parameter(confidence=0.9)])
    zone_u = MpzpZoneResult(zone_symbol="U", parameters=[_parameter(confidence=0.9)])
    warning = MpzpParserWarning(
        stage="segment_document",
        code="ZONE_SECTION_AMBIGUOUS",
        message="Wiele kandydackich sekcji dla strefy MN.",
        zone_symbol="MN",
        severity="warning",
    )
    result = MpzpParseResult(
        status="complete", zones=[zone_mn, zone_u], warnings=[warning]
    )

    validated = validate_mpzp_result(result)

    validated_by_symbol = {zone.zone_symbol: zone for zone in validated.zones}
    assert validated_by_symbol["MN"].parameters[0].confidence < 0.9
    assert validated_by_symbol["U"].parameters[0].confidence == 0.9


# --- kara za brak numeru strony -------------------------------------------


def test_missing_page_number_applies_small_confidence_penalty() -> None:
    zone = MpzpZoneResult(
        zone_symbol="MN", parameters=[_parameter(page_number=None, confidence=0.9)]
    )
    result = MpzpParseResult(status="complete", zones=[zone])

    validated = validate_mpzp_result(result)

    validated_confidence = validated.zones[0].parameters[0].confidence
    assert validated_confidence < 0.9
    assert validated_confidence > 0.8  # kara jest łagodna


# --- wymuszenie manual_review_required poniżej progu 0.5 -----------------


def test_confidence_below_threshold_forces_manual_review() -> None:
    zone = MpzpZoneResult(
        zone_symbol="MN",
        parameters=[_parameter(confidence=0.45, manual_review_required=False)],
    )
    result = MpzpParseResult(status="complete", zones=[zone])

    validated = validate_mpzp_result(result)

    assert validated.zones[0].parameters[0].manual_review_required is True


# --- funkcja nie mutuje wejścia -------------------------------------------


def test_validate_mpzp_result_does_not_mutate_input() -> None:
    original_parameter = _parameter(source_text=None, confidence=0.95)
    zone = MpzpZoneResult(zone_symbol="MN", parameters=[original_parameter])
    result = MpzpParseResult(status="complete", zones=[zone])

    validate_mpzp_result(result)

    assert result.zones[0].parameters[0].confidence == 0.95
    assert result.zones[0].parameters[0].manual_review_required is False
    assert result.conflict_flags == []


# --- komunikaty po polsku, brak szczegółów technicznych -------------------


def test_warning_messages_do_not_leak_technical_details() -> None:
    zone = MpzpZoneResult(
        zone_symbol="230_UMW",
        parameters=[
            _parameter(normalized_value=15.0),
            _parameter(normalized_value=13.0),
            _parameter(
                name="min_biologically_active_percent",
                normalized_value=150.0,
                unit="percent",
            ),
        ],
    )
    result = MpzpParseResult(status="complete", zones=[zone])

    validated = validate_mpzp_result(result)

    new_warnings = [
        w
        for w in validated.warnings
        if w.code in {"PARAMETER_CONFLICT", "DOMAIN_RANGE_VIOLATION"}
    ]
    assert new_warnings
    for warning in new_warnings:
        assert "Exception" not in warning.message
        assert "regex" not in warning.message.lower()
        assert "traceback" not in warning.message.lower()
        # Heurystyka obecności polskich znaków/słów, żeby wykryć regresję do
        # komunikatu czysto angielskiego.
        assert any(
            polish_word in warning.message
            for polish_word in ("strefie", "parametru", "weryfikacji", "zakresu")
        )
