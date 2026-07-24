from __future__ import annotations

import pytest

from app.modules.documents.domain.legal_structure import build_legal_units
from app.modules.documents.domain.models import (
    DocumentPageSnapshot,
    LegalUnitNode,
    build_legal_unit_tree,
)
from app.modules.documents.domain.parser_models import DocumentTextSegment


def test_document_page_validates_number_and_quality() -> None:
    page = DocumentPageSnapshot(
        page_number=2,
        text="§ 1.",
        quality=0.91,
        blocks=({"text": "§ 1.", "left": 4},),
    )
    assert page.blocks[0]["left"] == 4

    with pytest.raises(ValueError):
        DocumentPageSnapshot(page_number=0, text="")
    with pytest.raises(ValueError):
        DocumentPageSnapshot(page_number=1, text="", quality=1.1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"order_index": -1},
        {"page_from": 0},
        {"page_to": 0},
        {"page_from": 3, "page_to": 2},
    ],
)
def test_legal_unit_validates_order_and_page_range(
    kwargs: dict[str, int],
) -> None:
    with pytest.raises(ValueError):
        LegalUnitNode(
            unit_type="paragraph",
            order_index=kwargs.pop("order_index", 0),
            source_text="tekst",
            **kwargs,
        )


def test_build_legal_unit_tree_preserves_source_order() -> None:
    units = (
        LegalUnitNode(
            id=3,
            document_version_id=7,
            unit_type="point",
            order_index=2,
            source_text="2) drugi",
            parent_id=1,
        ),
        LegalUnitNode(
            id=1,
            document_version_id=7,
            unit_type="paragraph",
            order_index=0,
            source_text="§ 1.",
        ),
        LegalUnitNode(
            id=2,
            document_version_id=7,
            unit_type="point",
            order_index=1,
            source_text="1) pierwszy",
            parent_id=1,
        ),
    )

    tree = build_legal_unit_tree(units)

    assert [item.id for item in tree] == [1]
    assert [item.id for item in tree[0].children] == [2, 3]


def test_legal_structure_maps_heading_paragraph_section_point_and_table() -> None:
    segments = (
        DocumentTextSegment(
            segment_id="seg-1",
            heading="Rozdział 2. Parametry",
            page_number=4,
            source="paragraph",
            text="§ 8. 1. Zasady: 1) pierwszy punkt;\n2) drugi punkt.",
        ),
        DocumentTextSegment(
            segment_id="table-1",
            heading="Rozdział 2. Parametry",
            page_number=5,
            source="table",
            text="wysokość\u00a0zabudowy | 12 m",
        ),
    )

    units = build_legal_units(segments)

    assert [unit.unit_type for unit in units] == [
        "chapter",
        "paragraph",
        "section",
        "point",
        "point",
        "table_row",
    ]
    assert [unit.order_index for unit in units] == list(range(6))
    assert all(unit.page_from == unit.page_to for unit in units)
    assert units[1].parent_key == units[0].node_key
    assert units[2].parent_key == units[1].node_key
    assert units[3].parent_key == units[2].node_key
    assert units[-1].normalized_text == "wysokość zabudowy | 12 m"
