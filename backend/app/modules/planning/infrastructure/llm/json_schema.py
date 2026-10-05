"""Walidator podzbioru JSON Schema używanego w kontrakcie wyjścia (bez nowej zależności).

Obsługuje słowa kluczowe, których potrzebuje schemat odpowiedzi: ``type`` (nazwa albo lista),
``enum``, ``properties``, ``required``, ``additionalProperties`` (bool), ``items``, ``minItems``,
``maxItems``, ``minLength``, ``maxLength``, ``minimum``, ``maximum``. Nieznane słowa kluczowe są
ignorowane. Naruszenia niosą wyłącznie ścieżkę i rodzaj — nigdy wartość z odpowiedzi, bo
odpowiedź zawiera dosłowne cytaty z dokumentu i nie może trafić do wyjątków ani logów.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

MAX_VIOLATIONS: Final[int] = 20
_MAX_DEPTH: Final[int] = 32


@dataclass(frozen=True)
class SchemaViolation:
    path: str
    keyword: str

    def __str__(self) -> str:
        return f"{self.path}:{self.keyword}"


def _is_type(value: Any, name: str) -> bool:
    if name == "object":
        return isinstance(value, Mapping)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def validate_json_schema(instance: Any, schema: Mapping[str, Any]) -> list[SchemaViolation]:
    """Zwraca naruszenia (najwyżej ``MAX_VIOLATIONS``); pusta lista = zgodne."""
    violations: list[SchemaViolation] = []
    _validate(instance, schema, "$", violations, 0)
    return violations


def _add(violations: list[SchemaViolation], path: str, keyword: str) -> None:
    if len(violations) < MAX_VIOLATIONS:
        violations.append(SchemaViolation(path, keyword))


def _validate(instance: Any, schema: Mapping[str, Any], path: str, out: list[SchemaViolation], depth: int) -> None:
    if depth > _MAX_DEPTH or len(out) >= MAX_VIOLATIONS:
        return
    expected = schema.get("type")
    if expected is not None:
        names: Sequence[str] = [expected] if isinstance(expected, str) else list(expected)
        if not any(_is_type(instance, name) for name in names):
            _add(out, path, "type")
            return
    if "enum" in schema and instance not in schema["enum"]:
        _add(out, path, "enum")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            _add(out, path, "minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            _add(out, path, "maxLength")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            _add(out, path, "minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            _add(out, path, "maximum")
    if isinstance(instance, Mapping):
        properties: Mapping[str, Any] = schema.get("properties", {})
        for name in schema.get("required", ()):
            if name not in instance:
                _add(out, f"{path}.{name}", "required")
        for name, value in instance.items():
            if name in properties:
                _validate(value, properties[name], f"{path}.{name}", out, depth + 1)
            elif schema.get("additionalProperties") is False:
                _add(out, path, "additionalProperties")  # nazwa pochodzi z odpowiedzi: nie zapisujemy jej
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            _add(out, path, "minItems")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            _add(out, path, "maxItems")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(instance):
                _validate(item, item_schema, f"{path}[{index}]", out, depth + 1)
