"""Walidator podzbioru JSON Schema (PV3-10): słowa kluczowe, ścieżki i brak wartości w naruszeniach."""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.planning.domain.extraction_contract import RESPONSE_SCHEMA
from app.modules.planning.infrastructure.llm.json_schema import MAX_VIOLATIONS, validate_json_schema


def check(instance: Any, schema: dict[str, Any]) -> list[str]:
    return [str(item) for item in validate_json_schema(instance, schema)]


@pytest.mark.parametrize(
    ("instance", "type_name", "valid"),
    [
        ({}, "object", True), ([], "object", False),
        ([], "array", True), ({}, "array", False),
        ("a", "string", True), (1, "string", False),
        (True, "boolean", True), (1, "boolean", False),
        (None, "null", True), (0, "null", False),
        (3, "integer", True), (3.0, "integer", False), (True, "integer", False),
        (3, "number", True), (3.5, "number", True), (True, "number", False), ("3", "number", False),
        (1, "weird", False),
    ],
)
def test_types(instance: Any, type_name: str, valid: bool) -> None:
    assert (check(instance, {"type": type_name}) == []) is valid


def test_union_types_and_enum() -> None:
    schema = {"type": ["number", "null"]}
    assert check(None, schema) == [] and check(2, schema) == [] and check("x", schema) == ["$:type"]
    assert check("a", {"enum": ["a", "b"]}) == [] and check("c", {"enum": ["a", "b"]}) == ["$:enum"]


def test_string_and_number_bounds() -> None:
    assert check("ab", {"type": "string", "minLength": 3}) == ["$:minLength"]
    assert check("abcd", {"type": "string", "maxLength": 3}) == ["$:maxLength"]
    assert check(1, {"type": "number", "minimum": 2}) == ["$:minimum"]
    assert check(5, {"type": "number", "maximum": 4}) == ["$:maximum"]
    assert check(3, {"type": "number", "minimum": 3, "maximum": 3}) == []


def test_objects_required_additional_properties_and_nested_paths() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "object", "properties": {"c": {"type": "integer"}}, "required": ["c"]}},
        "required": ["a", "b"],
        "additionalProperties": False,
    }
    assert check({"a": "x", "b": {"c": 1}}, schema) == []
    assert check({"b": {}}, schema) == ["$.a:required", "$.b.c:required"]
    assert check({"a": "x", "b": {"c": 1}, "secret-name": 1}, schema) == ["$:additionalProperties"]  # bez nazwy z odpowiedzi
    assert check({"a": 1, "b": {"c": "z"}}, schema) == ["$.a:type", "$.b.c:type"]


def test_arrays_items_and_counts() -> None:
    schema = {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 2}
    assert check([], schema) == ["$:minItems"] and check([1, 2, 3], schema) == ["$:maxItems"]
    assert check([1, "x", 2.5], {"type": "array", "items": {"type": "integer"}}) == ["$[1]:type", "$[2]:type"]


def test_violations_never_contain_the_offending_values() -> None:
    secret = "SECRET-DOCUMENT-QUOTE"
    out = check({"items": [secret], "count": secret, "kind": secret}, {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "string", "maxLength": 3}},
                       "count": {"type": "integer"}, "kind": {"enum": ["a"]}},
    })
    assert out and all(secret not in line for line in out)


def test_the_number_of_violations_and_the_depth_are_bounded() -> None:
    schema = {"type": "array", "items": {"type": "integer"}}
    assert len(check(["x"] * 1000, schema)) == MAX_VIOLATIONS
    deep: Any = "x"
    nested: dict[str, Any] = {"type": "integer"}
    for _ in range(100):
        deep = [deep]
        nested = {"type": "array", "items": nested}
    assert check(deep, nested) == []  # głębia ponad limit jest ucinana, nie zawiesza walidacji


def test_unknown_keywords_are_ignored() -> None:
    assert check("x", {"type": "string", "pattern": "^y$", "format": "date"}) == []


def test_the_extraction_schema_accepts_a_minimal_answer_and_rejects_a_wrong_enum() -> None:
    assert check({"candidates": [], "not_found": []}, dict(RESPONSE_SCHEMA)) == []
    bad = {"candidates": [{"parameter": "height"}], "not_found": [{"zone_symbol": "1MN", "parameter": "nope"}]}
    out = check(bad, dict(RESPONSE_SCHEMA))
    assert "$.candidates[0].parameter:enum" in out and "$.candidates[0].zone_symbol:required" in out
    assert "$.not_found[0].parameter:enum" in out
