"""Most między drzewem struktury dokumentu a domeną planowania (PV3-05).

Warstwa ``services`` może widzieć domeny obu modułów, więc tutaj drzewo z modułu
``documents`` (``DocumentTree``) jest zamieniane na ``DocumentStructureView`` z modułu
``planning``, a ekstrakcja tekstu parsera — na drzewo. Istniejące API segmentacji
(``segment_document``) zachowuje zachowanie; to jest osobna, addytywna ścieżka.
"""

from __future__ import annotations

from app.modules.documents.domain.document_tree import DocumentTree, build_document_tree
from app.modules.planning.domain.zone_blocks import DocumentStructureView, StructureNodeView
from app.services.mpzp_parser_extract import TextExtractionResult


def build_tree_from_extraction(extraction: TextExtractionResult) -> DocumentTree:
    """Drzewo struktury dla wyniku ekstrakcji (strony, wiersze tabel, znaczniki HTML)."""
    return build_document_tree(
        extraction.pages,
        tables=[(table.page_number, table.rows) for table in extraction.tables],
        hints=extraction.structure_hints,
    )


def structure_view(tree: DocumentTree) -> DocumentStructureView:
    """Widok drzewa niezależny od modułu ``documents``."""
    return DocumentStructureView(
        text=tree.document.text,
        nodes=tuple(
            StructureNodeView(
                node_id=node.node_id, node_type=node.node_type, label=node.label, path=node.path,
                parent_id=node.parent_id, start=node.start, end=node.end, page_from=node.page_from,
                page_to=node.page_to, depth=node.depth, children=node.children, markup=node.markup, role=node.role,
            )
            for node in tree.nodes
        ),
        page_starts=tree.document.page_starts,
        page_numbers=tuple(page.page_number for page in tree.document.pages),
    )
