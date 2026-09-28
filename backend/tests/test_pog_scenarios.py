"""BK-205: informacyjna ocena relacji MPZP–POG (compatibility_assessment).

Testy obejmują wszystkie pięć statusów, wielostrefowość bez uśredniania,
projekt POG, akt nieaktualny, brak źródła, brak reguły, pary bez identyfikacji
przestrzennej (symbol ręczny) oraz kontrakt „rozstrzygnięta para ma regułę”.
"""

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError
from shapely.geometry import Polygon, box

import app.services.pog_scenarios as pog_scenarios
from app.core.planning_compatibility import (
    COMPATIBILITY_INFORMATIONAL_NOTICE,
    RULE_SET_ID,
    RULE_SET_VERSION,
    PlanningCompatibilityResult,
)
from app.schemas.analyze import (
    CompatibilityAssessment,
    CompatibilityZonePair,
    MpzpZoneResult,
    PogResult,
    PogStatusEvidence,
    PogZoneResult,
    legacy_compatibility_assessment,
)
from app.schemas.source import SourceMetadata
from app.services.ouz import calculate_ouz_status
from app.services.pog_scenarios import (
    AGGREGATION_RULE,
    LEGAL_INFORMATION_DISCLAIMER,
    assess_mpzp_pog_compatibility,
    build_pog_scenario_result,
)

AS_OF = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
PARCEL = box(0, 0, 100, 10)  # 1000 m² w EPSG:2180
PARCEL_AREA = PARCEL.area


def _ouz_available():
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    return calculate_ouz_status(parcel, [parcel])


def _source(name: str = "POG_RU") -> SourceMetadata:
    return SourceMetadata(
        source_name=name,
        source_url="https://ru.example.gov.pl/pog",
        fetched_at=AS_OF,
        confidence=0.9,
        manual_review_required=False,
    )


def _mpzp(
    symbol: str,
    function: str | None,
    geometry=None,
    *,
    method: str = "vector_intersection",
    manual_review: bool = False,
) -> MpzpZoneResult:
    return MpzpZoneResult(
        zone_symbol=symbol,
        primary_use=function,
        intersection_area_sqm=geometry.area if geometry is not None else None,
        intersection_pct=geometry.area / PARCEL_AREA * 100 if geometry is not None else None,
        is_dominant=False,
        zone_id=f"mpzp:{symbol}",
        act_identifier="MPZP/1",
        act_version="v1",
        assignment_method=method,  # type: ignore[arg-type]
        manual_review_required=manual_review,
        intersection_wkt=geometry.wkt if geometry is not None else None,
        source=_source("MPZP_WEKTOR"),
    )


def _pog_zone(zone_id: str, zone_type: str, geometry=None) -> PogZoneResult:
    area = geometry.area if geometry is not None else 0.0
    return PogZoneResult(
        id=zone_id,
        symbol=zone_type,
        type=zone_type,
        area_sqm=area,
        area_pct=min(100.0, area / PARCEL_AREA * 100),
        intersection_wkt=geometry.wkt if geometry is not None else None,
    )


def _pog(
    zones: list[PogZoneResult],
    *,
    legal_status: str = "binding",
    coverage_status: str = "available",
) -> PogResult:
    evidence = (
        PogStatusEvidence(source_name="RU", official=True, reference="https://ru.example.gov.pl/act")
        if legal_status == "binding"
        else None
    )
    return PogResult(
        legal_status=legal_status,  # type: ignore[arg-type]
        coverage_status=coverage_status,  # type: ignore[arg-type]
        data_availability="current",
        status_confirmed_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
        legal_status_evidence=evidence,
        coverage_evidence=(
            PogStatusEvidence(source_name="RU", official=True, reference="ru:brak-aktu")
            if coverage_status == "no_act_confirmed"
            else None
        ),
        zones=zones,
        touches_ouz_boundary=False,
        source=_source(),
    )


def _assess(mpzp_zones, pog):
    return assess_mpzp_pog_compatibility(
        mpzp_zones, pog, as_of=AS_OF, parcel_area_sqm=PARCEL_AREA
    )


# --- pięć statusów --------------------------------------------------------


def test_compatible_requires_every_spatial_pair_compatible_and_full_coverage() -> None:
    assessment, warnings = _assess(
        [_mpzp("1MN", "single_family_housing", PARCEL)],
        _pog([_pog_zone("pog:sj", "SJ", PARCEL)]),
    )

    assert assessment.status == "compatible"
    assert assessment.reason_code == "PAIRS_EVALUATED"
    assert assessment.rule_id == RULE_SET_ID
    assert assessment.rule_version == RULE_SET_VERSION
    assert assessment.as_of == date(2026, 9, 20)
    assert assessment.manual_review_required is False
    pair = assessment.zone_pairs[0]
    assert pair.spatially_identified is True
    assert pair.overlap_area_sqm == pytest.approx(1000.0)
    assert pair.overlap_pct == pytest.approx(100.0)
    assert pair.rule_id == f"{RULE_SET_ID}:single_family_housing:SJ"
    assert warnings == []


def test_incompatible_pair_is_reported_as_informational_divergence() -> None:
    assessment, warnings = _assess(
        [_mpzp("1P", "production", PARCEL)],
        _pog([_pog_zone("pog:sn", "SN", PARCEL)]),
    )

    assert assessment.status == "incompatible"
    assert assessment.manual_review_required is True
    assert {warning.code for warning in warnings} == {"MPZP_POG_POTENTIAL_DIVERGENCE"}
    assert "rozbieżność" in warnings[0].message
    assert "informacyjna" in warnings[0].message


def test_uncertain_rule_keeps_pair_unresolved() -> None:
    assessment, _ = _assess(
        [_mpzp("1U", "services", PARCEL)],
        _pog([_pog_zone("pog:sj", "SJ", PARCEL)]),
    )

    assert assessment.status == "uncertain"
    assert assessment.zone_pairs[0].status == "uncertain"
    assert assessment.zone_pairs[0].rule_id is not None


def test_pair_without_rule_is_unknown_not_conflict() -> None:
    assessment, warnings = _assess(
        [_mpzp("1R", "agriculture", PARCEL)],
        _pog([_pog_zone("pog:sh", "SH", PARCEL)]),
    )

    assert assessment.status == "unknown"
    pair = assessment.zone_pairs[0]
    assert pair.status == "unknown"
    assert pair.rule_id is None
    assert pair.as_of == date(2026, 9, 20)
    assert "MPZP_POG_COMPATIBILITY_UNKNOWN" in {warning.code for warning in warnings}


def test_unnormalized_mpzp_function_is_unknown_without_keyword_guessing() -> None:
    assessment, _ = _assess(
        [_mpzp("1P", "zabudowa produkcyjna i magazyny", PARCEL)],
        _pog([_pog_zone("pog:sn", "SN", PARCEL)]),
    )

    assert assessment.status == "unknown"
    assert assessment.zone_pairs[0].mpzp_function is None
    assert "nie jest znormalizowana" in assessment.zone_pairs[0].rationale


@pytest.mark.parametrize(
    ("legal_status", "reason_code"),
    [
        ("project", "POG_PROJECT_NOT_BINDING"),
        ("in_progress", "POG_PROCEDURE_IN_PROGRESS"),
        ("superseded", "POG_SUPERSEDED"),
    ],
)
def test_project_procedure_and_superseded_act_are_not_applicable(
    legal_status: str, reason_code: str
) -> None:
    assessment, warnings = _assess(
        [_mpzp("1MN", "single_family_housing", PARCEL)],
        _pog([_pog_zone("pog:sj", "SJ", PARCEL)], legal_status=legal_status),
    )

    assert assessment.status == "not_applicable"
    assert assessment.reason_code == reason_code
    assert assessment.zone_pairs == []
    assert warnings
    if legal_status == "project":
        assert "nie ustanawia obowiązku" in assessment.rationale


def test_superseded_and_missing_source_have_separate_paths() -> None:
    superseded, _ = _assess([], _pog([], legal_status="superseded"))
    missing, _ = _assess([_mpzp("1MN", "single_family_housing", PARCEL)], None)
    unconfirmed, _ = _assess([], _pog([], legal_status="unknown"))

    assert (superseded.status, superseded.reason_code) == ("not_applicable", "POG_SUPERSEDED")
    assert (missing.status, missing.reason_code) == ("unknown", "POG_SOURCE_MISSING")
    assert (unconfirmed.status, unconfirmed.reason_code) == ("unknown", "POG_STATUS_UNKNOWN")
    assert "brak" in missing.rationale.lower()


def test_confirmed_absence_of_pog_is_not_applicable() -> None:
    assessment, _ = _assess(
        [_mpzp("1MN", "single_family_housing", PARCEL)],
        _pog([], legal_status="unknown", coverage_status="no_act_confirmed"),
    )

    assert (assessment.status, assessment.reason_code) == (
        "not_applicable",
        "POG_NO_ACT_CONFIRMED",
    )


@pytest.mark.parametrize(
    ("mpzp", "pog_zones", "reason"),
    [
        ([], [_pog_zone("pog:sj", "SJ", PARCEL)], "MPZP_ZONES_MISSING"),
        ([_mpzp("1MN", "single_family_housing", PARCEL)], [], "POG_ZONES_MISSING"),
        (
            [_mpzp("1MN", "single_family_housing", box(0, 0, 50, 10))],
            [_pog_zone("pog:sj", "SJ", box(50, 0, 100, 10))],
            "NO_SPATIAL_PAIRS",
        ),
    ],
)
def test_missing_inputs_are_unknown(mpzp, pog_zones, reason: str) -> None:
    assessment, warnings = _assess(mpzp, _pog(pog_zones))

    assert assessment.status == "unknown"
    assert assessment.reason_code == reason
    assert warnings[0].code == "MPZP_POG_INPUT_INCOMPLETE"


# --- wielostrefowość i agregacja bez uśredniania -------------------------


def test_multi_zone_pairs_use_spatial_overlaps_and_worst_case() -> None:
    """1MN(60%)×SJ i 2P(40%)×SN — rozbieżna para decyduje, bez średniej 60/40."""
    left, right = box(0, 0, 60, 10), box(60, 0, 100, 10)
    assessment, _ = _assess(
        [
            _mpzp("1MN", "single_family_housing", left),
            _mpzp("2P", "production", right),
        ],
        _pog([_pog_zone("pog:sj", "SJ", left), _pog_zone("pog:sn", "SN", right)]),
    )

    assert assessment.status == "incompatible"
    assert len(assessment.zone_pairs) == 2  # pary bez wspólnej części pominięte
    by_label = {(pair.mpzp_zone_symbol, pair.pog_zone_type): pair for pair in assessment.zone_pairs}
    assert by_label[("1MN", "SJ")].status == "compatible"
    assert by_label[("1MN", "SJ")].overlap_area_sqm == pytest.approx(600.0)
    assert by_label[("2P", "SN")].status == "incompatible"
    assert by_label[("2P", "SN")].overlap_area_sqm == pytest.approx(400.0)
    for pair in assessment.zone_pairs:
        assert (pair.rule_id, pair.rule_version, pair.source, pair.as_of) != (None,) * 4
        assert pair.rule_version == RULE_SET_VERSION
        assert pair.source and pair.as_of
    assert "bez uśredniania" in assessment.aggregation


def test_crossing_zones_produce_all_overlapping_pairs() -> None:
    mpzp = [
        _mpzp("1MN", "single_family_housing", box(0, 0, 50, 10)),
        _mpzp("2MN", "single_family_housing", box(50, 0, 100, 10)),
    ]
    pog = _pog([_pog_zone("pog:a", "SJ", box(0, 0, 30, 10)), _pog_zone("pog:b", "SJ", box(30, 0, 100, 10))])

    assessment, _ = _assess(mpzp, pog)

    assert assessment.status == "compatible"
    assert sorted(
        (pair.mpzp_zone_symbol, pair.pog_zone_id, round(pair.overlap_area_sqm or 0))
        for pair in assessment.zone_pairs
    ) == [("1MN", "pog:a", 300), ("1MN", "pog:b", 200), ("2MN", "pog:b", 500)]


def test_compatible_pairs_covering_part_of_parcel_are_uncertain() -> None:
    half = box(0, 0, 50, 10)
    assessment, _ = _assess(
        [_mpzp("1MN", "single_family_housing", half)],
        _pog([_pog_zone("pog:sj", "SJ", half)], coverage_status="partial"),
    )

    assert assessment.status == "uncertain"
    assert assessment.reason_code == "PAIRS_PARTIAL_COVERAGE"


def test_unknown_pair_outranks_uncertain_but_not_incompatible() -> None:
    left, mid, right = box(0, 0, 30, 10), box(30, 0, 60, 10), box(60, 0, 100, 10)
    base_pog = [_pog_zone("a", "SJ", left), _pog_zone("b", "SH", mid), _pog_zone("c", "SN", right)]
    unknown_and_uncertain, _ = _assess(
        [_mpzp("1U", "services", left), _mpzp("2R", "agriculture", mid)], _pog(base_pog[:2])
    )
    with_incompatible, _ = _assess(
        [
            _mpzp("1U", "services", left),
            _mpzp("2R", "agriculture", mid),
            _mpzp("3P", "production", right),
        ],
        _pog(base_pog),
    )

    assert unknown_and_uncertain.status == "unknown"
    assert with_incompatible.status == "incompatible"


# --- symbol ręczny / brak geometrii --------------------------------------


def test_manual_zone_without_geometry_is_never_resolved() -> None:
    assessment, warnings = _assess(
        [
            _mpzp(
                "230_U",
                "single_family_housing",
                None,
                method="manual_user_input",
                manual_review=True,
            )
        ],
        _pog([_pog_zone("pog:sj", "SJ", PARCEL)]),
    )

    pair = assessment.zone_pairs[0]
    assert assessment.status == "uncertain"
    assert pair.spatially_identified is False
    assert pair.overlap_area_sqm is None
    assert pair.rule_result == "compatible"
    assert pair.status == "uncertain"
    assert pair.manual_review_required is True
    assert "MPZP_POG_PAIRS_NOT_SPATIAL" in {warning.code for warning in warnings}


def test_touching_mpzp_zone_is_not_paired() -> None:
    touching = _mpzp("9ZP", "greenery", None).model_copy(update={"touches_boundary": True})
    assessment, _ = _assess(
        [_mpzp("1MN", "single_family_housing", PARCEL), touching],
        _pog([_pog_zone("pog:sj", "SJ", PARCEL)]),
    )

    assert [pair.mpzp_zone_symbol for pair in assessment.zone_pairs] == ["1MN"]


# --- źródła, data stanu prawnego, nota informacyjna ----------------------


def test_assessment_lists_rule_pog_and_mpzp_sources_with_dates() -> None:
    assessment, _ = _assess(
        [_mpzp("1MN", "single_family_housing", PARCEL)],
        _pog([_pog_zone("pog:sj", "SJ", PARCEL)]),
    )

    kinds = [source.kind for source in assessment.sources]
    assert kinds == ["rule_set", "pog", "mpzp"]
    assert assessment.sources[0].version == RULE_SET_VERSION
    assert assessment.sources[2].version == "v1"
    assert assessment.informational_notice == COMPATIBILITY_INFORMATIONAL_NOTICE
    assert "nie przesądza o prawnej możliwości zabudowy" in assessment.informational_notice


def test_as_of_falls_back_to_analysis_date_without_confirmation() -> None:
    pog = _pog([_pog_zone("pog:sj", "SJ", PARCEL)]).model_copy(
        update={"status_confirmed_at": None, "source": None}
    )

    assessment, _ = _assess([_mpzp("1MN", "single_family_housing", PARCEL)], pog)

    assert assessment.as_of == AS_OF.date()


def test_rule_lookup_comes_only_from_compatibility_table(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, object]] = []

    def fake_check(mpzp_function, pog_zone_type):
        calls.append((mpzp_function, pog_zone_type))
        return PlanningCompatibilityResult(
            result="incompatible",
            reasoning="Kontrolowany wynik tabeli.",
            confidence=0.9,
            rule_id="test:rule",
            rule_version="9.9",
            rule_source="tabela testowa",
        )

    monkeypatch.setattr(pog_scenarios, "check_mpzp_pog_compatibility", fake_check)
    assessment, _ = _assess(
        [_mpzp("1P", "production", PARCEL)], _pog([_pog_zone("pog:sp", "SP", PARCEL)])
    )

    assert calls == [("production", "SP")]
    assert assessment.zone_pairs[0].rule_id == "test:rule"
    assert assessment.zone_pairs[0].rationale.endswith("Kontrolowany wynik tabeli.")


# --- scenariusz POG/OUZ i klauzula informacyjna --------------------------


@pytest.mark.parametrize("status", ["project", "in_progress"])
def test_project_or_in_progress_pog_is_controlled_transition(status: str) -> None:
    result = build_pog_scenario_result(
        [], _pog([], legal_status=status), _ouz_available(), as_of=AS_OF
    )

    assert result.status == status
    assert result.assessment.status == "not_applicable"
    assert result.manual_review_required is True
    assert "1 stycznia 2026" in result.message
    assert "obowiązuje" not in result.message.lower()
    assert "nie jest aktem wiążącym" in result.message or "nie są wiążące" in result.message


@pytest.mark.parametrize(
    ("mpzp", "pog"),
    [
        ([], None),
        ([], _pog([], legal_status="unknown")),
        ([_mpzp("1MN", "single_family_housing", PARCEL)], _pog([_pog_zone("pog:sj", "SJ", PARCEL)])),
    ],
)
def test_every_scenario_contains_legal_information_clause(mpzp, pog) -> None:
    result = build_pog_scenario_result(
        mpzp, pog, _ouz_available(), as_of=AS_OF, parcel_area_sqm=PARCEL_AREA
    )

    assert result.legal_disclaimer == LEGAL_INFORMATION_DISCLAIMER
    assert LEGAL_INFORMATION_DISCLAIMER in result.message
    assert "brak planu" not in result.message.lower().replace("braku planu", "")


def test_as_of_accepts_plain_date() -> None:
    result = build_pog_scenario_result([], None, _ouz_available(), as_of=date(2026, 1, 2))

    assert result.assessment.as_of == date(2026, 1, 2)


# --- kontrakt Pydantic ---------------------------------------------------


def test_resolved_pair_without_rule_is_rejected() -> None:
    with pytest.raises(ValidationError, match="wymaga reguły"):
        CompatibilityZonePair(
            mpzp_zone_symbol="1MN",
            mpzp_assignment_method="vector_intersection",
            pog_zone_id="pog:sj",
            pog_zone_type="SJ",
            spatially_identified=True,
            status="compatible",
            rationale="bez reguły",
        )


def test_resolved_pair_must_be_spatially_identified() -> None:
    with pytest.raises(ValidationError):
        CompatibilityZonePair(
            mpzp_zone_symbol="1MN",
            mpzp_assignment_method="manual_user_input",
            pog_zone_id="pog:sj",
            pog_zone_type="SJ",
            spatially_identified=False,
            status="compatible",
            rule_id="r",
            rule_version="1.0",
            source="s",
            as_of=date(2026, 9, 25),
            rationale="bez geometrii",
        )


def test_resolved_assessment_requires_matching_pair() -> None:
    with pytest.raises(ValidationError):
        CompatibilityAssessment(
            status="compatible",
            reason_code="PAIRS_EVALUATED",
            rule_id=RULE_SET_ID,
            rule_version=RULE_SET_VERSION,
            aggregation=AGGREGATION_RULE,
            rationale="brak par",
            informational_notice=COMPATIBILITY_INFORMATIONAL_NOTICE,
        )


def test_legacy_boolean_is_kept_as_evidence_not_full_assessment() -> None:
    pog = PogResult.model_validate(
        {
            "touches_ouz_boundary": False,
            "conflict_with_mpzp": True,
            "raw_attributes": {
                "scenario": {"compatibility": {"result": "incompatible", "reasoning": "stara reguła"}}
            },
        }
    )

    assessment = pog.compatibility_assessment
    assert assessment is not None
    assert assessment.status == "unknown"
    assert assessment.reason_code == "LEGACY_BOOLEAN_ONLY"
    assert assessment.rule_id is None
    assert assessment.zone_pairs == []
    assert assessment.legacy_evidence is not None
    assert assessment.legacy_evidence.conflict_with_mpzp is True
    assert assessment.legacy_evidence.result == "incompatible"
    assert "conflict_with_mpzp" not in pog.model_dump()


def test_legacy_evidence_cannot_claim_resolved_status() -> None:
    payload = legacy_compatibility_assessment(False, None)
    assert payload is not None
    payload["status"] = "compatible"

    with pytest.raises(ValidationError, match="legacy"):
        CompatibilityAssessment.model_validate(payload)


def test_no_legacy_record_means_no_assessment() -> None:
    assert legacy_compatibility_assessment(None, {"scenario": {"message": "x"}}) is None
    assert PogResult(touches_ouz_boundary=False).compatibility_assessment is None


def test_internal_geometry_is_not_serialized() -> None:
    zone = _pog_zone("pog:sj", "SJ", PARCEL)
    mpzp = _mpzp("1MN", "single_family_housing", PARCEL)

    assert "intersection_wkt" not in zone.model_dump()
    assert "intersection_wkt" not in mpzp.model_dump(mode="json")
