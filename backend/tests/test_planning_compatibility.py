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
