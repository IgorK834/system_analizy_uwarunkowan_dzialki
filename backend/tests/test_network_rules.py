from app.core.network_rules import NetworkRule, load_network_rules

EXPECTED_NETWORK_TYPES = {"water", "sewage", "gas", "power", "telecoms", "heating"}


def test_load_network_rules_returns_dict_of_network_rule() -> None:
    rules = load_network_rules()

    assert isinstance(rules["water"], NetworkRule)


def test_load_network_rules_contains_all_expected_types() -> None:
    rules = load_network_rules()

    assert EXPECTED_NETWORK_TYPES <= rules.keys()


def test_load_network_rules_no_rule_for_unknown_type() -> None:
    rules = load_network_rules()

    assert "unknown" not in rules


def test_each_rule_has_positive_buffer() -> None:
    rules = load_network_rules()

    assert all(rule.default_buffer_m > 0 for rule in rules.values())


def test_each_rule_has_confidence_between_0_and_1() -> None:
    rules = load_network_rules()

    assert all(0.0 <= rule.confidence <= 1.0 for rule in rules.values())


def test_each_rule_has_non_empty_source_and_note() -> None:
    rules = load_network_rules()

    assert all(rule.source.strip() and rule.note.strip() for rule in rules.values())


def test_gas_rule_has_apply_even_outside_parcel_true() -> None:
    rules = load_network_rules()

    assert rules["gas"].apply_even_outside_parcel is True


def test_load_network_rules_is_cached() -> None:
    assert load_network_rules() is load_network_rules()
