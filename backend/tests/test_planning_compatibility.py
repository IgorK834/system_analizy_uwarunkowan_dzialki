from app.core.planning_compatibility import (
    MpzpFunction,
    PogPlanningZoneType,
    check_mpzp_pog_compatibility,
)


def test_single_family_housing_is_compatible_with_sj() -> None:
    result = check_mpzp_pog_compatibility(
        MpzpFunction.SINGLE_FAMILY_HOUSING,
        PogPlanningZoneType.MULTIFUNCTIONAL_SINGLE_FAMILY,
    )

    assert result.result == "compatible"
    assert result.confidence > 0


def test_services_are_compatible_with_service_zone() -> None:
    result = check_mpzp_pog_compatibility("services", "SU")

    assert result.result == "compatible"
    assert "usług" in result.reasoning.lower()


def test_services_in_housing_zone_are_uncertain_not_conflict() -> None:
    result = check_mpzp_pog_compatibility("services", "SJ")

    assert result.result == "uncertain"
    assert result.confidence < 0.9


def test_production_is_compatible_with_economic_zone() -> None:
    result = check_mpzp_pog_compatibility("production", "SP")

    assert result.result == "compatible"


def test_production_is_incompatible_with_greenery_zone_with_reasoning() -> None:
    result = check_mpzp_pog_compatibility("production", "SN")

    assert result.result == "incompatible"
    assert result.reasoning
    assert "zieleni" in result.reasoning.lower()


def test_greenery_is_compatible_with_greenery_zone() -> None:
    result = check_mpzp_pog_compatibility("greenery", "SN")

    assert result.result == "compatible"


def test_unknown_category_never_becomes_false_incompatible() -> None:
    result = check_mpzp_pog_compatibility("local_symbol_x", "SJ")

    assert result.result == "unknown"
    assert result.confidence == 0.0
    assert result.warnings[0].code == "MPZP_POG_COMPATIBILITY_UNKNOWN"


def test_known_pair_without_rule_is_unknown() -> None:
    result = check_mpzp_pog_compatibility("agriculture", "SH")

    assert result.result == "unknown"
    assert result.warnings


def test_compatibility_is_deterministic() -> None:
    first = check_mpzp_pog_compatibility("production", "SN")
    second = check_mpzp_pog_compatibility("production", "SN")

    assert first == second


def test_every_table_rule_has_stable_id_version_and_source() -> None:
    from app.core.planning_compatibility import (
        PLANNING_COMPATIBILITY_RULES,
        RULE_SET_ID,
        RULE_SET_SOURCE,
        RULE_SET_VERSION,
    )

    ids = set()
    for mpzp_function, zone_type in PLANNING_COMPATIBILITY_RULES:
        result = check_mpzp_pog_compatibility(mpzp_function, zone_type)
        assert result.rule_id == f"{RULE_SET_ID}:{mpzp_function.value}:{zone_type.value}"
        assert result.rule_version == RULE_SET_VERSION
        assert result.rule_source == RULE_SET_SOURCE
        ids.add(result.rule_id)
    assert len(ids) == len(PLANNING_COMPATIBILITY_RULES)


def test_unknown_result_has_no_rule_identity() -> None:
    result = check_mpzp_pog_compatibility("agriculture", "SH")

    assert (result.rule_id, result.rule_version, result.rule_source) == (None, None, None)


def test_status_labels_never_claim_legal_buildability() -> None:
    from app.core.planning_compatibility import COMPATIBILITY_STATUS_LABELS_PL

    assert set(COMPATIBILITY_STATUS_LABELS_PL) == {
        "compatible", "incompatible", "uncertain", "not_applicable", "unknown"
    }
    for label in COMPATIBILITY_STATUS_LABELS_PL.values():
        lowered = label.lower()
        assert "dopuszczal" not in lowered
        assert "można zabudować" not in lowered
        assert "zgodność potwierdzona" not in lowered


def test_normalize_mpzp_function_accepts_only_exact_catalog_values() -> None:
    from app.core.planning_compatibility import MpzpFunction, normalize_mpzp_function

    assert normalize_mpzp_function("services") is MpzpFunction.SERVICES
    assert normalize_mpzp_function("usługi") is None
    assert normalize_mpzp_function(None) is None
