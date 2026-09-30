"""Ścieżki pól ``AnalyzeResponse`` i ich obecność w snapshocie (BK-501).

Introspekcja modelu Pydantic daje listę ścieżek liści (``a.b[].c``), którą test
porównuje z tabelą mapowania ``reporting.domain.field_mapping``. Ten sam zbiór
ścieżek pozwala policzyć, które pola mają w danej analizie wartość, a które są
``null`` — załącznik raportu pokazuje to jawnie zamiast ukrywać braki.
"""

from __future__ import annotations

import types
import typing
from collections import defaultdict
from functools import lru_cache
from typing import Any

from pydantic import BaseModel

from app.modules.reporting.domain.field_mapping import FIELD_MAPPINGS, mapping_for
from app.schemas.analyze import AnalyzeResponse


def _model_of(annotation: Any) -> type[BaseModel] | None:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType, list, tuple):
        for argument in typing.get_args(annotation):
            model = _model_of(argument)
            if model is not None:
                return model
        return None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def _is_list(annotation: Any) -> bool:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        return any(_is_list(argument) for argument in typing.get_args(annotation))
    return origin is list


@lru_cache(maxsize=4)
def schema_leaf_paths(model: type[BaseModel] = AnalyzeResponse) -> tuple[str, ...]:
    """Ścieżki liści serializowanego kontraktu (bez pól ``exclude=True``)."""
    paths: list[str] = []

    def walk(current: type[BaseModel], prefix: str, seen: frozenset[type[BaseModel]]) -> None:
        for name, info in current.model_fields.items():
            if info.exclude:
                continue
            path = f"{prefix}{name}" + ("[]" if _is_list(info.annotation) else "")
            nested = _model_of(info.annotation)
            if nested is not None and nested not in seen:
                walk(nested, f"{path}.", seen | {nested})
            else:
                paths.append(path)
        for name in current.model_computed_fields:
            paths.append(f"{prefix}{name}")

    walk(model, "", frozenset({model}))
    return tuple(paths)


def _leaf_values(dumped: Any, leaves: frozenset[str]) -> dict[str, list[Any]]:
    values: dict[str, list[Any]] = defaultdict(list)

    def walk(node: Any, path: str) -> None:
        if path in leaves:
            values[path].append(node)
            return
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            child = f"{path}.{key}" if path else key
            if child in leaves or not isinstance(value, list):
                walk(value, child)
            elif f"{child}[]" in leaves:
                values[f"{child}[]"].append(value)
            else:
                for item in value:
                    walk(item, f"{child}[]")

    walk(dumped, "")
    return values


def field_presence(response: AnalyzeResponse) -> list[dict[str, Any]]:
    """Wiersze załącznika: wzorzec mapowania + liczba wartości obecnych i null."""
    leaves = frozenset(schema_leaf_paths())
    dumped = response.model_dump(mode="json", exclude={"access_token"})
    values = _leaf_values(dumped, leaves)
    counts: dict[str, list[int]] = {item.pattern: [0, 0] for item in FIELD_MAPPINGS}
    for path, items in values.items():
        mapping = mapping_for(path)
        if mapping is None:
            continue
        for item in items:
            empty = item is None or item == [] or item == {}
            counts[mapping.pattern][1 if empty else 0] += 1
    rows = []
    for item in FIELD_MAPPINGS:
        present, missing = counts[item.pattern]
        rows.append({"mapping": item, "present": present, "missing": missing})
    return rows


def render_field_mapping_markdown() -> str:
    """Dokumentacja ``docs/report/field-mapping.md`` generowana z tabeli mapowania."""
    from app.modules.reporting.domain.field_mapping import MAPPING_KIND_LABELS
    from app.modules.reporting.domain.sections import REPORT_SECTIONS, SECTION_BY_ID

    lines = [
        "# Mapowanie pól `AnalyzeResponse` na raport PDF v2",
        "",
        "> Plik generowany: `python backend/scripts/export_report_field_mapping.py`.",
        "> Źródło prawdy: `backend/app/modules/reporting/domain/field_mapping.py`;",
        "> test `backend/tests/test_report_field_mapping.py` wymaga, aby każda ścieżka",
        "> liścia kontraktu API pasowała do wzorca i aby ten plik był zgodny z kodem.",
        "",
        "Wzorce: `[]` — element listy, `*` — dowolny ciąg znaków (także kropki).",
        "Pierwszy pasujący wzorzec wygrywa. Ten sam wykaz jest załącznikiem A każdego PDF",
        "(z liczbą wartości obecnych i null w danej analizie).",
        "",
        "## Sekcje raportu",
        "",
        *[f"{section.number}. {section.title}" for section in REPORT_SECTIONS],
        "",
        "## Tabela mapowania",
        "",
        "| Pole API (wzorzec) | § | Element raportu / uzasadnienie pominięcia | Rodzaj |",
        "|---|---|---|---|",
    ]
    for item in FIELD_MAPPINGS:
        element = (
            f"*pominięte:* {item.omitted_reason}" if item.omitted_reason else item.element
        )
        lines.append(
            f"| `{item.pattern}` | {SECTION_BY_ID[item.section].number} | "
            f"{element.replace('|', '/')} | {MAPPING_KIND_LABELS[item.kind]} |"
        )
    lines.append("")
    lines.append(f"Liczba ścieżek liści kontraktu objętych mapowaniem: {len(schema_leaf_paths())}.")
    return "\n".join(lines) + "\n"
