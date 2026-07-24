"""Czyste typy cytowalnego dokumentu prawnego."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

LegalUnitType = Literal[
    "document",
    "chapter",
    "paragraph",
    "section",
    "point",
    "letter",
    "position",
    "table_row",
    "document_fragment",
]


@dataclass(frozen=True)
class DocumentPageSnapshot:
    """Tekst i opcjonalne współrzędne OCR jednej strony źródłowej."""

    page_number: int
    text: str
    ocr_used: bool = False
    quality: float | None = None
    blocks: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError("Numer strony musi być dodatni.")
        if self.quality is not None and not 0 <= self.quality <= 1:
            raise ValueError("Jakość strony musi mieścić się w zakresie 0–1.")


@dataclass(frozen=True)
class LegalUnitNode:
    """Jednostka prawna zachowująca rodzica, kolejność, stronę i dowód."""

    unit_type: LegalUnitType
    order_index: int
    source_text: str
    number: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    normalized_text: str | None = None
    id: int | None = None
    document_version_id: int | None = None
    parent_id: int | None = None
    node_key: str | None = None
    parent_key: str | None = None
    children: tuple["LegalUnitNode", ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.order_index < 0:
            raise ValueError("Kolejność jednostki prawnej nie może być ujemna.")
        if self.page_from is not None and self.page_from < 1:
            raise ValueError("Początkowy numer strony musi być dodatni.")
        if self.page_to is not None and self.page_to < 1:
            raise ValueError("Końcowy numer strony musi być dodatni.")
        if (
            self.page_from is not None
            and self.page_to is not None
            and self.page_to < self.page_from
        ):
            raise ValueError("Końcowa strona nie może poprzedzać początkowej.")


@dataclass(frozen=True)
class DocumentSnapshot:
    """Niezmienna wersja dokumentu wraz z tekstem i metadanymi ekstrakcji."""

    document_version_id: int
    source_document_id: int
    source_artifact_id: int
    content_hash: str
    media_type: str | None = None
    extraction_method: str | None = None
    ocr_engine_version: str | None = None
    quality_score: float | None = None
    original_uri: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    published_at: datetime | None = None
    review_status: str = "unreviewed"
    pages: tuple[DocumentPageSnapshot, ...] = ()
    legal_units: tuple[LegalUnitNode, ...] = ()


@dataclass(frozen=True)
class DocumentRegistration:
    """Dane potrzebne do idempotentnej rejestracji nowej wersji źródła."""

    planning_act_identifier: str
    source_id: str
    source_owner: str
    original_uri: str
    media_type: str
    content_hash: str
    published_at: datetime
    title: str | None = None
    document_type: str = "uchwala"


def build_legal_unit_tree(
    units: tuple[LegalUnitNode, ...],
) -> tuple[LegalUnitNode, ...]:
    """Buduje niemutowalne drzewo z płaskich rekordów ``parent_id``."""
    by_parent: dict[int | None, list[LegalUnitNode]] = {}
    for unit in units:
        by_parent.setdefault(unit.parent_id, []).append(unit)
    for children in by_parent.values():
        children.sort(key=lambda item: (item.order_index, item.id or 0))

    def attach(unit: LegalUnitNode) -> LegalUnitNode:
        children = tuple(attach(child) for child in by_parent.get(unit.id, []))
        return replace(unit, children=children)

    return tuple(attach(root) for root in by_parent.get(None, []))
