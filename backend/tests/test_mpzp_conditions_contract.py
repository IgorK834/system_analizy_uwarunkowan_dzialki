"""Kontrakt warunków wartości: pola strefy, evidence, zgodność ze starymi snapshotami (PV3-08)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.schemas import analyze as analyze_schemas
from app.schemas import mpzp as mpzp_schemas
from app.schemas.source import SourceMetadata
from app.services import section_quality
from app.services.mpzp_zones import apply_parser_zone, unassigned_share_zone

SOURCE = SourceMetadata(source_name="MPZP_BIP", source_url=None, confidence=0.9, manual_review_required=False)


def parameter(value: float, *conditions: tuple[str, str], kind: str | None = None, name: str = "max_building_height_m",
              group: str | None = None, **extra: Any) -> mpzp_schemas.MpzpParameter:
    models = [mpzp_schemas.ValueCondition(kind=k, label=label, quote=label) for k, label in conditions]  # type: ignore[arg-type]
    value_kind = kind or ("conditional" if conditions else "unconditional")
    return mpzp_schemas.MpzpParameter(
        name=name, normalized_value=value, unit="m", raw_value=f"{value} m", source_text=f"wysokość {value} m",
        page_number=2, confidence=0.8, manual_review_required=value_kind == "conflict",
        conditions=models, value_kind=value_kind, conflict_group_id=group, **extra,  # type: ignore[arg-type]
    )


def zone_with(*parameters: mpzp_schemas.MpzpParameter) -> tuple[analyze_schemas.MpzpZoneResult, list[str], list[str]]:
    base = unassigned_share_zone("MN", SOURCE)
    return apply_parser_zone(base, mpzp_schemas.MpzpZoneResult(zone_symbol="MN", parameters=list(parameters)))


FLAT = ("roof_type", "dach płaski")
STEEP = ("roof_type", "dach stromy")


def test_flat_and_steep_roof_heights_are_two_conditional_values_not_a_conflict() -> None:
    zone, _skipped, conflicts = zone_with(parameter(8.0, FLAT), parameter(10.0, STEEP))
    assert conflicts == [] and zone.manual_review_required is False
    assert zone.max_building_height_m is None  # nie ma jednej wartości bezwarunkowej: null, nie 0
    kinds = [(p.normalized_value, p.value_kind, [(c.kind, c.label) for c in p.conditions]) for p in zone.parameters]
    assert kinds == [(8.0, "conditional", [FLAT]), (10.0, "conditional", [STEEP])]
    assert all(p.conflict_group_id is None and not p.manual_review_required for p in zone.parameters)


def test_one_unconditional_value_fills_the_flat_field_next_to_conditional_ones() -> None:
    zone, _skipped, conflicts = zone_with(parameter(11.0), parameter(9.5, FLAT))
    assert conflicts == [] and zone.max_building_height_m == 11.0 and zone.manual_review_required is False


def test_real_conflict_keeps_manual_review_and_a_null_flat_field() -> None:
    group = "MN:max_building_height_m"
    zone, _skipped, conflicts = zone_with(
        parameter(9.0, kind="conflict", group=group), parameter(12.0, kind="conflict", group=group)
    )
    assert conflicts == ["max_building_height_m"] and zone.max_building_height_m is None
    assert zone.manual_review_required is True
    assert {p.value_kind for p in zone.parameters} == {"conflict"}
    assert all(p.conflict_group_id == group and p.manual_review_required for p in zone.parameters)


def test_conflict_among_values_of_the_same_condition_does_not_hide_the_unconditional_value() -> None:
    zone, _skipped, conflicts = zone_with(
        parameter(11.0),
        parameter(8.0, FLAT, kind="conflict", group="MN:max_building_height_m#abc"),
        parameter(9.0, FLAT, kind="conflict", group="MN:max_building_height_m#abc"),
    )
    assert conflicts == ["max_building_height_m"] and zone.manual_review_required is True
    assert zone.max_building_height_m == 11.0  # jedyna wartość bezwarunkowa nie jest sprzeczna sama ze sobą


def test_two_different_unconditional_values_without_marked_kind_are_still_a_conflict() -> None:
    zone, _skipped, conflicts = zone_with(parameter(9.0), parameter(12.0))  # parser starszy niż PV3-08 nie ustawia kind
    assert conflicts == ["max_building_height_m"] and zone.max_building_height_m is None


def test_condition_carries_through_to_the_api_evidence() -> None:
    zone, _s, _c = zone_with(
        parameter(9.5, ("roof_type", "dach płaski"), extraction_strategy="adjective", normalization_flags=["inherited_noun"])
    )
    (evidence,) = zone.parameters
    assert evidence.conditions == [
        analyze_schemas.MpzpValueCondition(kind="roof_type", label="dach płaski", quote="dach płaski")
    ]
    assert evidence.extraction_strategy == "adjective" and evidence.normalization_flags == ["inherited_noun"]
    dumped = evidence.model_dump(mode="json")
    assert dumped["conditions"] == [{"kind": "roof_type", "label": "dach płaski", "quote": "dach płaski"}]
    assert dumped["value_kind"] == "conditional"


# --- zgodność wsteczna ---------------------------------------------------------------------------


def old_evidence(**overrides: Any) -> dict[str, Any]:
    """Zapis evidence w kształcie sprzed PV3-08 (bez ``conditions`` i ``value_kind``)."""
    payload: dict[str, Any] = {
        "name": "max_building_height_m", "normalized_value": 9.0, "raw_value": "9 m", "unit": "m",
        "evidence_text": "wysokość 9 m", "page_number": 3, "segment_id": None, "legal_unit_id": None,
        "document_sha256": "d" * 64, "document_version_id": 1, "parser_version": "mpzp-parser/2.0",
        "extraction_method": "pdf_text", "confidence": 0.7, "conflict_group_id": None, "manual_review_required": False,
    }
    payload.update(overrides)
    return payload


def test_snapshot_from_the_previous_contract_reads_as_values_without_conditions() -> None:
    plain = analyze_schemas.MpzpParameterEvidence.model_validate(old_evidence())
    assert plain.conditions == [] and plain.value_kind == "unconditional" and plain.normalization_flags == []
    conflict = analyze_schemas.MpzpParameterEvidence.model_validate(old_evidence(conflict_group_id="sha:MN:h"))
    assert conflict.value_kind == "conflict" and conflict.conditions == []
    explicit = analyze_schemas.MpzpParameterEvidence.model_validate(
        old_evidence(value_kind="conditional", conditions=[{"kind": "other", "label": "x", "quote": "x"}])
    )
    assert explicit.value_kind == "conditional"
    derived = analyze_schemas.MpzpParameterEvidence.model_validate(
        old_evidence(conditions=[{"kind": "subzone", "label": "strefa A", "quote": "strefie A"}])
    )
    assert derived.value_kind == "conditional"


def test_whole_zone_snapshot_without_the_new_fields_still_validates() -> None:
    zone = unassigned_share_zone("MN", SOURCE).model_dump(mode="json")
    zone["parameters"] = [old_evidence(), old_evidence(normalized_value=12.0, conflict_group_id="g")]
    restored = analyze_schemas.MpzpZoneResult.model_validate(zone)
    assert [p.value_kind for p in restored.parameters] == ["unconditional", "conflict"]
    assert analyze_schemas.MPZP_RESULT_SCHEMA_VERSION == "2.5"


def test_unknown_condition_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        analyze_schemas.MpzpValueCondition(kind="nieznany", label="x", quote="x")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        mpzp_schemas.ValueCondition(kind="nieznany", label="x", quote="x")  # type: ignore[arg-type]


# --- jakość sekcji: tylko prawdziwa sprzeczność czyni sekcję częściową ---------------------------


def _section_status(*parameters: mpzp_schemas.MpzpParameter) -> tuple[str, list[str]]:
    zone, _s, _c = zone_with(*parameters)
    zone = zone.model_copy(update={"assignment_method": "vector_intersection", "intersection_pct": 100.0,
                                   "intersection_area_sqm": 100.0})
    draft = section_quality._mpzp(SimpleNamespace(manual_zone_required=False, mpzp_zones=[zone]))
    return draft.status, draft.codes


def test_conditional_values_do_not_make_the_mpzp_section_partial_but_a_conflict_does() -> None:
    assert _section_status(parameter(8.0, FLAT), parameter(10.0, STEEP)) == ("available", [])
    status, codes = _section_status(
        parameter(9.0, kind="conflict", group="MN:max_building_height_m"),
        parameter(12.0, kind="conflict", group="MN:max_building_height_m"),
    )
    assert status == "partial" and "MPZP_PARAMETER_CONFLICT" in codes
