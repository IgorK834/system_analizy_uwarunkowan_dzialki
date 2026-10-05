"""Testy systemowej walidacji i confidence scoring wyniku parsera MPZP."""

import json
from pathlib import Path

from app.modules.planning.domain import evidence_confidence
from app.schemas.mpzp import (
    MpzpParameter,
    MpzpParseResult,
    MpzpParserWarning,
    MpzpZoneResult,
    ParserDocumentAudit,
    ParserDocumentPage,
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


def test_real_pipeline_230_umw_heights_are_conditional_and_raise_no_conflict() -> None:
    # Test end-to-end: numeric -> validate na PRAWDZIWYCH dwóch wysokościach w strefie 230_UMW
    # (15 m dla zabudowy pierzejowej, 13 m dla pozostałej części terenu). Od PV3-08 różne warunki
    # nie są sprzecznością: brak grupy konfliktu, ostrzeżenia i ręcznej weryfikacji.
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
    assert {p.value_kind for p in height_params} == {"conditional"}
    assert all(p.conflict_group_id is None and p.manual_review_required is False for p in height_params)
    assert validated.conflict_flags == []
    assert not any(w.code == "PARAMETER_CONFLICT" for w in validated.warnings)


def test_real_conflict_needs_the_same_premise_and_still_ends_with_manual_review() -> None:
    def height(value: float, *conditions: tuple[str, str]) -> MpzpParameter:
        return _parameter(
            normalized_value=value,
            conditions=[{"kind": kind, "label": label, "quote": label} for kind, label in conditions],
        )

    zone = MpzpZoneResult(
        zone_symbol="230_UMW",
        parameters=[
            height(15.0),
            height(13.0),  # dwie wartości bez warunku — ta sama przesłanka
            height(9.0, ("roof_type", "dach płaski")),
            height(8.0, ("roof_type", "dach płaski")),  # ten sam warunek, różne wartości
            height(12.0, ("roof_type", "dach stromy")),  # inny warunek: wartość warunkowa
        ],
    )
    validated = validate_mpzp_result(MpzpParseResult(status="complete", zones=[zone]))
    kinds = {(p.normalized_value, p.value_kind) for p in validated.zones[0].parameters}
    assert kinds == {
        (15.0, "conflict"),
        (13.0, "conflict"),
        (9.0, "conflict"),
        (8.0, "conflict"),
        (12.0, "conditional"),
    }
    by_value = {p.normalized_value: p for p in validated.zones[0].parameters}
    assert by_value[15.0].conflict_group_id == by_value[13.0].conflict_group_id == "230_UMW:max_building_height_m"
    flat_roof_group = by_value[9.0].conflict_group_id
    assert flat_roof_group == by_value[8.0].conflict_group_id and flat_roof_group.startswith("230_UMW:max_building_height_m#")
    assert flat_roof_group != by_value[15.0].conflict_group_id and len(flat_roof_group) < 80
    assert by_value[12.0].conflict_group_id is None and by_value[12.0].value_kind == "conditional"
    assert all(by_value[v].manual_review_required for v in (15.0, 13.0, 9.0, 8.0))
    assert len(validated.conflict_flags) == 2 and any("dach płaski" in flag for flag in validated.conflict_flags)
    assert [w.code for w in validated.warnings].count("PARAMETER_CONFLICT") == 1  # jedno ostrzeżenie na parametr


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


def _validated(parameter: MpzpParameter, **result_kwargs) -> MpzpParameter:
    zone = MpzpZoneResult(zone_symbol="MN", parameters=[parameter])
    result = MpzpParseResult(status="complete", zones=[zone], **result_kwargs)
    return validate_mpzp_result(result).zones[0].parameters[0]


def test_parameter_without_source_text_loses_confidence_and_needs_review() -> None:
    # Cecha ``quote_unverified``: brak dowodu źródłowego nie da się „odrobić” wysoką pewnością ekstraktora.
    with_quote = _validated(_parameter(source_text="15 m", confidence=0.95))
    without = _validated(_parameter(source_text=None, confidence=0.95))
    assert without.confidence < with_quote.confidence
    assert without.manual_review_required is True and without.confidence_band == "low"
    assert without.confidence_features["quote_verified"] is False


def test_parameter_with_empty_string_source_text_also_loses_confidence() -> None:
    empty = _validated(_parameter(source_text="", confidence=0.9))
    assert empty.confidence_features["quote_verified"] is False and empty.manual_review_required is True


def test_parameter_with_source_text_gets_a_calibrated_high_band_confidence() -> None:
    validated = _validated(_parameter(source_text="15 m", confidence=0.1))  # pewność ekstraktora jest tylko wstępna
    artifact = evidence_confidence.current_artifact()
    assert validated.confidence >= artifact.band_thresholds["high"]
    assert validated.confidence_band == "high" and validated.manual_review_required is False
    assert validated.confidence_calibration == artifact.calibration_id


def test_quote_is_verified_against_the_page_text_when_the_document_audit_is_present() -> None:
    audit = ParserDocumentAudit(
        media_type="application/pdf", extraction_method="pdf_text", quality_score=1.0, manual_review_required=False,
        pages=[ParserDocumentPage(page_number=1, text="Strona pierwsza.\nMaksymalna wysokość\nzabudowy: 15 m.", ocr_used=False)],
    )
    found = _validated(_parameter(source_text="Maksymalna wysokość zabudowy: 15 m", page_number=1), document_audit=audit)
    missing = _validated(_parameter(source_text="wysokość 99 m", page_number=1), document_audit=audit)
    assert found.confidence_features["quote_verified"] is True and found.confidence_band == "high"
    assert missing.confidence_features["quote_verified"] is False and missing.manual_review_required is True


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


# --- cechy dokumentu: OCR, wieloznaczność sekcji, symbole odkryte w tekście ---------


def test_ocr_lowers_confidence_and_noise_lowers_it_further() -> None:
    plain = _validated(_parameter())
    needs_ocr = MpzpParserWarning(stage="extract_text", code="NEEDS_OCR", message="Dokument wymaga OCR.", severity="warning")
    by_warning = _validated(_parameter(), warnings=[needs_ocr])
    assert by_warning.confidence < plain.confidence and by_warning.confidence_features["extraction_method"] == "ocr"
    audit = ParserDocumentAudit(
        media_type="application/pdf", extraction_method="ocr", quality_score=0.5, manual_review_required=True,
        pages=[ParserDocumentPage(page_number=5, text="15 m", ocr_used=True, quality=0.4)],
    )
    noisy = _validated(_parameter(page_number=5, source_text="15 m"), document_audit=audit)
    clean_audit = audit.model_copy(update={"pages": [ParserDocumentPage(page_number=5, text="15 m", ocr_used=True, quality=0.95)]})
    clean = _validated(_parameter(page_number=5, source_text="15 m"), document_audit=clean_audit)
    assert noisy.confidence < clean.confidence < plain.confidence  # jakość OCR strony ma znaczenie
    assert noisy.confidence_features["ocr_quality"] == 0.4 and noisy.manual_review_required is True


def test_no_ocr_leaves_the_confidence_to_the_model_not_to_the_extractor() -> None:
    first = _validated(_parameter(confidence=0.9))
    second = _validated(_parameter(confidence=0.3))
    assert first.confidence == second.confidence  # wstępna pewność ekstraktora nie przechodzi do wyniku


def test_ambiguous_zone_section_lowers_confidence_only_for_that_zone() -> None:
    zone_mn = MpzpZoneResult(zone_symbol="MN", parameters=[_parameter()])
    zone_u = MpzpZoneResult(zone_symbol="U", parameters=[_parameter()])
    warning = MpzpParserWarning(
        stage="segment_document",
        code="ZONE_SECTION_AMBIGUOUS",
        message="Wiele kandydackich sekcji dla strefy MN.",
        zone_symbol="MN",
        severity="warning",
    )
    result = MpzpParseResult(status="complete", zones=[zone_mn, zone_u], warnings=[warning])

    validated_by_symbol = {zone.zone_symbol: zone for zone in validate_mpzp_result(result).zones}
    assert validated_by_symbol["MN"].parameters[0].confidence < validated_by_symbol["U"].parameters[0].confidence
    assert validated_by_symbol["MN"].parameters[0].confidence_features["zone_ambiguous"] is True
    assert validated_by_symbol["U"].parameters[0].confidence_features["zone_ambiguous"] is False


def test_symbols_discovered_in_the_text_are_a_feature_not_a_fixed_multiplier() -> None:
    warning = MpzpParserWarning(stage="segment_document", code="ZONE_SYMBOLS_INFERRED", message="m", severity="warning")
    inferred = _validated(_parameter(), warnings=[warning])
    assert inferred.confidence < _validated(_parameter()).confidence
    assert inferred.confidence_features["symbols_inferred"] is True and inferred.manual_review_required is True


def test_missing_page_number_is_no_longer_a_separate_penalty() -> None:
    # Brak numeru strony nie jest cechą pewności: liczy się, czy fragment dowodowy leży w tekście.
    assert _validated(_parameter(page_number=None)).confidence == _validated(_parameter(page_number=3)).confidence


def test_conflict_candidates_always_need_review_and_conditional_values_do_not_by_themselves() -> None:
    zone = MpzpZoneResult(zone_symbol="MN", parameters=[_parameter(normalized_value=9.0), _parameter(normalized_value=12.0)])
    candidates = validate_mpzp_result(MpzpParseResult(status="complete", zones=[zone])).zones[0].parameters
    assert all(p.manual_review_required and p.confidence_band == "low" and p.value_kind == "conflict" for p in candidates)
    block_scoped = _parameter(
        value_kind="conditional", scope_kind="zone_section", scope_strategy=3, scope_confidence=0.9,
        extraction_strategy="adjective",
    )
    conditional = _validated(block_scoped)
    assert conditional.confidence_band == "high" and conditional.manual_review_required is False


def test_manual_review_threshold_comes_from_the_calibration_artifact() -> None:
    artifact = evidence_confidence.current_artifact()
    for parameter in (_parameter(), _parameter(source_text=None), _parameter(page_number=2, confidence=0.99)):
        validated = _validated(parameter)
        below = validated.confidence < artifact.review_threshold
        assert validated.manual_review_required == (below or parameter.manual_review_required)
        assert validated.confidence_band == evidence_confidence.band_for(validated.confidence, artifact)


def test_descriptive_values_are_not_calibrated_and_never_reach_the_high_band() -> None:
    text = _validated(
        _parameter(name="prohibition", normalized_value="zakaz zabudowy", unit=None, confidence=0.99)
    )
    artifact = evidence_confidence.current_artifact()
    assert text.confidence <= artifact.uncalibrated_cap < artifact.band_thresholds["high"]
    assert text.confidence_band is None and text.confidence_calibration is None


# --- funkcja nie mutuje wejścia -------------------------------------------


def test_validate_mpzp_result_does_not_mutate_input() -> None:
    original_parameter = _parameter(source_text=None, confidence=0.95)
    zone = MpzpZoneResult(zone_symbol="MN", parameters=[original_parameter])
    result = MpzpParseResult(status="complete", zones=[zone])

    validate_mpzp_result(result)

    assert result.zones[0].parameters[0].confidence == 0.95
    assert result.zones[0].parameters[0].confidence_band is None  # wejście nie dostało pasma
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
