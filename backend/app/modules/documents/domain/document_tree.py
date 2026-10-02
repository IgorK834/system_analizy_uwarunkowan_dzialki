"""Wspólna reprezentacja struktury dokumentu planistycznego (PV3-05).

Drzewo ``rozdział → § → ust. → pkt → lit. → tiret`` oraz wiersze tabel i elementy list
HTML, zbudowane na ZNORMALIZOWANYM tekście dokumentu (``text_normalization``). Każdy
węzeł ma stronę (od–do), zakres znaków w tym tekście i ścieżkę (``§16 ust.2 pkt 1 lit.c``).

Gwarancje (sprawdza ``DocumentTree.verify_tiling``):

* węzły dzielą tekst bez luk i bez nakładania: tekst węzła to złożenie tekstów jego
  dzieci, a konkatenacja LIŚCI w kolejności dokumentu odtwarza cały znormalizowany tekst;
* numery stron rosną monotonicznie wzdłuż liści;
* ten sam dokument daje identyczne drzewo i skrót (``digest``).

Tekst przed pierwszym znacznikiem węzła nadrzędnego (np. „Dla terenu X ustala się:”
przed punktami) jest liściem ``fragment`` o roli ``head``; tekst przed pierwszym
znacznikiem dokumentu — ``preamble``. Drzewo nie wnioskuje o strefach — to zadanie
resolvera zakresu (planning/domain/zone_scope).

Kompatybilność: ``legal_structure.build_legal_units`` i ``segment_document`` zachowują
dotychczasowe zachowanie; to jest osobna, addytywna reprezentacja.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from app.modules.documents.domain.parser_models import StructureHint
from app.modules.documents.domain.text_normalization import NormalizedDocument, normalize_document

NodeType = Literal[
    "document", "chapter", "paragraph", "section", "point", "letter", "dash",
    "table_row", "html_list_item", "fragment",
]
STRUCTURE_LEVELS: Final[dict[str, int]] = {
    "chapter": 0, "paragraph": 1, "section": 2, "point": 3, "letter": 4, "dash": 5,
}
TREE_SCHEMA_VERSION: Final[str] = "document-tree/1"

_LETTERS = "a-ząćęłńóśźż"
_CHAPTER = re.compile(r"(?m)^(?:ROZDZIAŁ|Rozdział|DZIAŁ|Dział)\s+([0-9IVXLC]+[A-Za-z]?)\b[^\n]*")
_PARAGRAPH = re.compile(r"§\s*(\d+[A-Za-ząćęłńóśźż]*)\s*\.")
_SECTION_LINE = re.compile(r"(?m)^(\d+[a-z]?)\.(?=\s)")
_SECTION_AFTER_PARAGRAPH = re.compile(r"[ \n]*(\d+[a-z]?)\.(?=\s)")
_POINT_LINE = re.compile(r"(?m)^(\d+[a-z]?)\)(?=\s)")
_POINT_INLINE = re.compile(r"(?<=[:;] )(\d+[a-z]?)\)(?=\s)")
_LETTER_LINE = re.compile(rf"(?m)^([{_LETTERS}])\)(?=\s)")
_LETTER_INLINE = re.compile(rf"(?<=[:;,] )([{_LETTERS}])\)(?=\s)")
_DASH_LINE = re.compile(r"(?m)^([–—-])(?=\s)")
_REFERENCE_BEFORE = re.compile(r"(?:\b(?:w|z|do|od|po|na|pkt|ust|art|par|oraz|i)\.?|\()\s*$", re.IGNORECASE)
_POLISH_LETTERS = "abcdefghijklmnopqrstuvwxyz"


@dataclass(frozen=True)
class StructureNode:
    """Węzeł drzewa: typ, etykieta, ścieżka, strony i zakres w znormalizowanym tekście."""

    node_id: str
    node_type: NodeType
    label: str | None
    path: str
    parent_id: str | None
    start: int
    end: int
    page_from: int
    page_to: int
    depth: int
    children: tuple[str, ...] = ()
    markup: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class DocumentTree:
    """Niemutowalne drzewo struktury z tekstem, flagami artefaktów i ostrzeżeniami."""

    document: NormalizedDocument
    nodes: tuple[StructureNode, ...]
    warnings: tuple[str, ...] = ()
    unmapped: tuple[str, ...] = ()
    _index: dict[str, StructureNode] = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_index", {node.node_id: node for node in self.nodes})

    @property
    def root(self) -> StructureNode:
        return self.nodes[0]

    def node(self, node_id: str) -> StructureNode:
        return self._index[node_id]

    def children(self, node: StructureNode) -> tuple[StructureNode, ...]:
        return tuple(self._index[child] for child in node.children)

    def text_of(self, node: StructureNode) -> str:
        return self.document.text[node.start : node.end]

    def leaves(self) -> tuple[StructureNode, ...]:
        return tuple(node for node in self.nodes if not node.children and node.node_type != "document")

    def ancestors(self, node: StructureNode) -> tuple[StructureNode, ...]:
        chain: list[StructureNode] = []
        current = node
        while current.parent_id is not None:
            current = self._index[current.parent_id]
            chain.append(current)
        return tuple(reversed(chain))

    def verify_tiling(self) -> list[str]:
        """Zwraca listę naruszeń gwarancji (pusta = drzewo poprawne)."""
        problems: list[str] = []
        text = self.document.text
        leaves = [self.root] if not self.root.children else list(self.leaves())
        position = 0
        last_page = 0
        for leaf in leaves:
            if leaf.start != position:
                problems.append(f"luka lub nakładanie przed {leaf.node_id} ({position} → {leaf.start})")
            if leaf.end <= leaf.start and text:
                problems.append(f"pusty liść {leaf.node_id}")
            if leaf.page_from < last_page:
                problems.append(f"strona maleje w {leaf.node_id} ({last_page} → {leaf.page_from})")
            last_page = max(last_page, leaf.page_to)
            position = leaf.end
        if position != len(text):
            problems.append(f"liście kończą się na {position}, tekst ma {len(text)} znaków")
        if "".join(text[leaf.start : leaf.end] for leaf in leaves) != text:
            problems.append("konkatenacja liści nie odtwarza tekstu")
        for node in self.nodes:
            if node.children:
                kids = self.children(node)
                if kids[0].start != node.start or kids[-1].end != node.end or any(
                    left.end != right.start for left, right in zip(kids, kids[1:], strict=False)
                ):
                    problems.append(f"dzieci {node.node_id} nie dzielą jego tekstu")
        return problems

    def digest(self) -> str:
        payload = {
            "schema": TREE_SCHEMA_VERSION,
            "text_sha256": hashlib.sha256(self.document.text.encode("utf-8")).hexdigest(),
            "nodes": [
                [n.node_id, n.node_type, n.label, n.path, n.parent_id, n.start, n.end, n.page_from, n.page_to,
                 n.markup, n.role]
                for n in self.nodes
            ],
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


# --- budowa -------------------------------------------------------------------------------


@dataclass
class _Draft:
    node_type: NodeType
    label: str | None
    start: int
    end: int = -1
    parent: _Draft | None = None
    children: list[_Draft] = field(default_factory=list)
    markup: str | None = None
    role: str | None = None
    ordinal: str | None = None

    def contains(self, start: int, end: int) -> bool:
        return self.start <= start and end <= self.end


@dataclass(frozen=True)
class _Marker:
    position: int
    node_type: NodeType
    label: str
    line_start: bool


def _next_labels(label: str) -> set[str]:
    """Etykiety, które mogą następować po ``label`` (z lekką tolerancją na pominięcia)."""
    digits = re.match(r"(\d+)([a-z]?)$", label)
    if digits:
        number, suffix = int(digits.group(1)), digits.group(2)
        following = {str(number + 1)}
        if not suffix:
            following.add(f"{number}a")
        else:
            following.add(f"{number}{chr(ord(suffix) + 1)}")
        return following
    if len(label) == 1:
        return {chr(ord(label) + 1), chr(ord(label) + 2)}
    return set()


def _first_labels(node_type: NodeType) -> set[str]:
    return {"1", "a"} if node_type in {"point", "section"} else {"a", "ą"} if node_type == "letter" else set()


def _find_markers(text: str) -> tuple[list[_Marker], list[str]]:
    markers: list[_Marker] = []
    warnings: list[str] = []
    for match in _CHAPTER.finditer(text):
        markers.append(_Marker(match.start(), "chapter", match.group(1), True))
    paragraph_ends: list[int] = []
    for match in _PARAGRAPH.finditer(text):
        if _REFERENCE_BEFORE.search(text[max(0, match.start() - 8) : match.start()]):
            continue
        markers.append(_Marker(match.start(), "paragraph", match.group(1), True))
        paragraph_ends.append(match.end())
    for end in paragraph_ends:
        follow = _SECTION_AFTER_PARAGRAPH.match(text, end)
        if follow is not None:
            markers.append(_Marker(follow.start(1), "section", follow.group(1), True))
    for pattern, kind, line in (
        (_SECTION_LINE, "section", True), (_POINT_LINE, "point", True), (_POINT_INLINE, "point", False),
        (_LETTER_LINE, "letter", True), (_LETTER_INLINE, "letter", False), (_DASH_LINE, "dash", True),
    ):
        for match in pattern.finditer(text):
            markers.append(_Marker(match.start(1), kind, match.group(1), line))  # type: ignore[arg-type]
    markers.sort(key=lambda marker: (marker.position, STRUCTURE_LEVELS[marker.node_type]))
    unique: list[_Marker] = []
    for marker in markers:
        if unique and unique[-1].position == marker.position:
            continue  # ten sam znak nie otwiera dwóch węzłów
        unique.append(marker)
    return unique, warnings


def _level(node: _Draft) -> int:
    return STRUCTURE_LEVELS.get(node.node_type, -1)


def _build_structure(text: str) -> tuple[_Draft, list[str]]:
    root = _Draft("document", None, 0, len(text))
    markers, warnings = _find_markers(text)
    stack: list[_Draft] = [root]
    last_label: dict[tuple[int, str], str] = {}
    dash_count: dict[int, int] = {}
    for marker in markers:
        level = STRUCTURE_LEVELS[marker.node_type]
        depth_index = len(stack) - 1
        while depth_index > 0 and _level(stack[depth_index]) >= level:
            depth_index -= 1
        parent = stack[depth_index]
        previous = last_label.get((id(parent), marker.node_type))
        sequence_ok = (
            marker.node_type in {"chapter", "paragraph", "dash"}
            or (previous is None and marker.label in _first_labels(marker.node_type))
            or (previous is not None and marker.label in _next_labels(previous))
        )
        if not sequence_ok:
            if not marker.line_start:
                continue  # znacznik w środku zdania bez ciągłości numeracji to nie węzeł
            warnings.append(f"numbering_gap:{marker.node_type}:{marker.label}@{marker.position}")
        for closed in stack[depth_index + 1 :]:
            closed.end = marker.position
        del stack[depth_index + 1 :]
        node = _Draft(marker.node_type, marker.label, marker.position, -1, parent)
        if marker.node_type == "dash":
            dash_count[id(parent)] = dash_count.get(id(parent), 0) + 1
            node.ordinal = str(dash_count[id(parent)])
        parent.children.append(node)
        last_label[(id(parent), marker.node_type)] = marker.label
        stack.append(node)
    for open_node in stack[1:]:
        open_node.end = len(text)
    return root, warnings


def _add_heads(node: _Draft) -> None:
    """Tekst przed pierwszym dzieckiem staje się liściem ``fragment`` (``head``/``preamble``)."""
    for child in node.children:
        _add_heads(child)
    if node.children and node.children[0].start > node.start:
        role = "preamble" if node.node_type == "document" else "head"
        head = _Draft("fragment", None, node.start, node.children[0].start, node, role=role)
        node.children.insert(0, head)


def _ensure_leaf(root: _Draft) -> None:
    """Dokument bez żadnego znacznika ma jeden liść ``preamble`` z całym tekstem."""
    if not root.children and root.end > root.start:
        root.children.append(_Draft("fragment", None, root.start, root.end, root, role="preamble"))


def _deepest_containing(node: _Draft, start: int, end: int) -> _Draft:
    while True:
        child = next((c for c in node.children if c.contains(start, end)), None)
        if child is None:
            return node
        node = child


def _carve(root: _Draft, start: int, end: int, node_type: NodeType, label: str, markup: str) -> bool:
    """Wydziela ``[start, end)`` jako węzeł; ``False``, gdy zakres przecina istniejącą strukturę."""
    if end <= start:
        return False
    target = _deepest_containing(root, start, end)
    if (target.start, target.end) == (start, end):
        if target.node_type == "fragment":
            target.node_type, target.label, target.markup, target.role = node_type, label, markup, None
        elif target.node_type == "document":
            return False
        else:
            target.markup = markup
        return True
    if target.children:
        return False
    holder = target
    siblings = holder.children
    if target.node_type == "fragment":  # liść fragmentu nie ma dzieci: zastępujemy go w rodzicu
        parent = target.parent
        assert parent is not None
        holder, siblings = parent, parent.children
        at = siblings.index(target)
        del siblings[at]
    else:
        at = 0
    pieces: list[_Draft] = []
    if start > target.start:
        pieces.append(_Draft("fragment", None, target.start, start, holder, role="head" if target.node_type != "fragment" else target.role))
    pieces.append(_Draft(node_type, label, start, end, holder, markup=markup))
    if end < target.end:
        pieces.append(_Draft("fragment", None, end, target.end, holder))
    siblings[at:at] = pieces
    return True


def _collapse_with_map(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            if chars and chars[-1] != " ":
                chars.append(" ")
                origin.append(index)
            continue
        chars.append(char)
        origin.append(index)
    return "".join(chars), origin


def _row_span(
    collapsed: str, origin: list[int], page_start: int, cursor: int, cells: Sequence[str | None]
) -> tuple[int, int, int] | None:
    """Zakres wiersza tabeli w tekście strony: komórki w kolejności, tolerancja na odstępy."""
    first = last = None
    for cell in cells:
        needle = " ".join((cell or "").split())
        if not needle:
            continue
        found = collapsed.find(needle, cursor)
        if found < 0:
            continue
        if first is None:
            first = found
        last = found + len(needle)
        cursor = last
    if first is None or last is None:
        return None
    return page_start + origin[first], page_start + origin[last - 1] + 1, last


def build_document_tree(
    pages: Sequence[str],
    *,
    page_numbers: Sequence[int] | None = None,
    tables: Sequence[tuple[int, Sequence[Sequence[str | None]]]] = (),
    hints: Sequence[StructureHint] = (),
) -> DocumentTree:
    """Buduje drzewo struktury z surowych stron, tabel i wskazówek znaczników HTML."""
    document = normalize_document(pages, page_numbers)
    root, warnings = _build_structure(document.text)
    _add_heads(root)
    _ensure_leaf(root)
    unmapped: list[str] = []
    page_index = {page.page_number: index for index, page in enumerate(document.pages)}
    table_number = 0
    for table_page, rows in tables:
        table_number += 1
        index = page_index.get(table_page, table_page - 1 if 1 <= table_page <= len(document.pages) else None)
        if index is None:
            unmapped.append(f"table {table_number}: strona {table_page} nie istnieje")
            continue
        page = document.pages[index]
        collapsed, origin = _collapse_with_map(page.text)
        cursor = 0
        for row_number, cells in enumerate(rows, start=1):
            span = _row_span(collapsed, origin, document.page_starts[index], cursor, cells)
            if span is None:
                unmapped.append(f"table {table_number} row {row_number}: tekstu wiersza nie znaleziono na stronie {page.page_number}")
                continue
            start, end, cursor_after = span
            cursor = cursor_after
            if not _carve(root, start, end, "table_row", f"{table_number}.{row_number}", "table_row"):
                unmapped.append(f"table {table_number} row {row_number}: wiersz przecina strukturę dokumentu")
    ordinals: dict[str, int] = {}
    for hint in sorted(hints, key=lambda item: (item.page_number, item.raw_start)):
        index = page_index.get(hint.page_number)
        if index is None:
            unmapped.append(f"{hint.kind}: strona {hint.page_number} nie istnieje")
            continue
        page = document.pages[index]
        start = document.page_starts[index] + page.normalized_index(hint.raw_start)
        end = document.page_starts[index] + page.normalized_index(hint.raw_end)
        ordinals[hint.kind] = ordinals.get(hint.kind, 0) + 1
        if not _carve(root, start, end, hint.kind, str(ordinals[hint.kind]), hint.kind):
            unmapped.append(f"{hint.kind} {ordinals[hint.kind]}: zakres przecina strukturę dokumentu")
    nodes = _freeze(root, document)
    return DocumentTree(document=document, nodes=tuple(nodes), warnings=tuple(warnings), unmapped=tuple(unmapped))


_COMPONENT: Final[dict[str, str]] = {
    "chapter": "rozdz. {label}", "paragraph": "§{label}", "section": "ust.{label}", "point": "pkt {label}",
    "letter": "lit.{label}", "dash": "tiret {label}", "table_row": "tabela {table} wiersz {row}",
    "html_list_item": "pozycja {label}",
}


def _component(draft: _Draft) -> str | None:
    if draft.node_type in {"document", "fragment"}:
        return None
    if draft.node_type == "table_row":
        table, _, row = (draft.label or "").partition(".")
        return _COMPONENT["table_row"].format(table=table, row=row)
    label = draft.ordinal if draft.node_type == "dash" and draft.ordinal else draft.label
    return _COMPONENT[draft.node_type].format(label=label)


def _freeze(root: _Draft, document: NormalizedDocument) -> list[StructureNode]:
    """Numeruje węzły w kolejności dokumentu i oblicza ścieżki oraz strony."""
    ordered: list[tuple[_Draft, int, str]] = []

    def visit(draft: _Draft, depth: int, path_parts: tuple[str, ...]) -> None:
        component = _component(draft)
        parts = (*path_parts, component) if component else path_parts
        ordered.append((draft, depth, " ".join(parts)))
        for child in draft.children:
            visit(child, depth + 1, parts)

    visit(root, 0, ())
    ids = {id(draft): f"n{index:05d}" for index, (draft, _, _) in enumerate(ordered)}
    frozen: list[StructureNode] = []
    for draft, depth, path in ordered:
        page_from = document.page_number_at(draft.start) if document.text else 1
        page_to = document.page_number_at(max(draft.start, draft.end - 1)) if document.text else 1
        if draft.node_type == "fragment":
            suffix = "(preambuła)" if draft.role == "preamble" else "(treść wprowadzająca)" if draft.role == "head" else "(tekst)"
            path = f"{path} {suffix}".strip()
        frozen.append(
            StructureNode(
                node_id=ids[id(draft)], node_type=draft.node_type, label=draft.label, path=path,
                parent_id=ids[id(draft.parent)] if draft.parent is not None else None,
                start=draft.start, end=draft.end, page_from=page_from, page_to=page_to, depth=depth,
                children=tuple(ids[id(child)] for child in draft.children), markup=draft.markup, role=draft.role,
            )
        )
    return frozen


__all__ = [
    "DocumentTree", "NodeType", "STRUCTURE_LEVELS", "StructureNode", "TREE_SCHEMA_VERSION", "build_document_tree",
]
