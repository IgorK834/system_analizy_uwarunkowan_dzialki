"""Blok strefy: fragment tekstu planu, który dotyczy konkretnej strefy (PV3-05).

``ZoneBlock`` jest wejściem dla ekstrakcji parametrów (deterministycznej i modelu
językowego) i nośnikiem źródła wartości: strona, zakres znaków i ścieżka redakcyjna.
Moduł jest niezależny od modułu ``documents`` — drzewo struktury dokumentu jest tu
widziane przez minimalny ``DocumentStructureView`` (jak ``LegalTextUnit`` w regułach
planistycznych); adapter z drzewa leży w warstwie ``services``.

Blok jest serializowalny i deterministyczny: ten sam dokument i te same symbole dają
identyczną listę bloków w identycznej kolejności, a ``blocks_digest`` jest jej skrótem.

Blok może składać się z kilku ``segments`` (rozłącznych zakresów tekstu dokumentu),
gdy z listy „w terenie X – …, w terenie Y – …” wybrano tylko pozycje dotyczące danego
symbolu (bramkowanie klauzul po symbolu). ``text`` to wtedy teksty segmentów rozdzielone
znakiem nowej linii, a ``locate`` odwzorowuje pozycje w ``text`` na pozycje w dokumencie.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

ZONE_BLOCK_SCHEMA_VERSION: Final[str] = "zone-block/1"
ScopeKind = Literal["zone_section", "general_clause", "residual_clause", "fallback"]
SCOPE_KINDS: Final[tuple[str, ...]] = ("zone_section", "general_clause", "residual_clause", "fallback")
ZONE_SCOPE_AMBIGUOUS: Final[str] = "ZONE_SCOPE_AMBIGUOUS"


@dataclass(frozen=True)
class StructureNodeView:
    """Węzeł drzewa struktury widziany przez domenę planowania."""

    node_id: str
    node_type: str
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
class DocumentStructureView:
    """Znormalizowany tekst dokumentu i jego drzewo struktury (bez zależności od ``documents``)."""

    text: str
    nodes: tuple[StructureNodeView, ...]
    page_starts: tuple[int, ...]
    page_numbers: tuple[int, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_index", {node.node_id: node for node in self.nodes})

    def node(self, node_id: str) -> StructureNodeView:
        return self._index[node_id]  # type: ignore[attr-defined, no-any-return]

    def children(self, node: StructureNodeView) -> tuple[StructureNodeView, ...]:
        return tuple(self.node(child) for child in node.children)

    def parent(self, node: StructureNodeView) -> StructureNodeView | None:
        return self.node(node.parent_id) if node.parent_id is not None else None

    def text_of(self, node: StructureNodeView) -> str:
        return self.text[node.start : node.end]

    def pages_between(self, start: int, end: int) -> tuple[int, ...]:
        """Numery stron, na które sięga zakres ``[start, end)``, rosnąco."""
        if not self.page_starts or end <= start:
            return ()
        pages: list[int] = []
        for index, page_start in enumerate(self.page_starts):
            page_end = self.page_starts[index + 1] if index + 1 < len(self.page_starts) else len(self.text)
            if page_start < end and start < page_end:
                pages.append(self.page_numbers[index])
        return tuple(pages)

    def page_at(self, offset: int) -> int:
        pages = self.pages_between(offset, offset + 1)
        return pages[0] if pages else (self.page_numbers[0] if self.page_numbers else 1)


@dataclass(frozen=True)
class ZoneBlock:
    """Fragment tekstu dotyczący jednej lub kilku stref, z uzasadnieniem zakresu.

    ``scope_kind``: ``zone_section`` (ustalenia strefy), ``general_clause`` (klauzula
    ogólna wymieniająca symbol), ``residual_clause`` („w pozostałych terenach”),
    ``fallback`` (cały paragraf, gdy zakresu nie da się rozstrzygnąć). Klauzula ogólna
    i resztowa nigdy nie są ``zone_section``. ``strategy`` to numer strategii zakresu
    0–6, ``strategy_reason`` — dlaczego ją wybrano.
    """

    block_id: str
    scope_kind: ScopeKind
    symbols: tuple[str, ...]
    text: str
    char_span: tuple[int, int]
    pages: tuple[int, ...]
    path: str
    scope_confidence: float
    warnings: tuple[str, ...] = ()
    strategy: int = 0
    strategy_reason: str = ""
    node_ids: tuple[str, ...] = ()
    segments: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        if self.scope_kind not in SCOPE_KINDS:
            raise ValueError(f"Nieznany rodzaj zakresu: {self.scope_kind!r}.")
        if not 0.0 <= self.scope_confidence <= 1.0:
            raise ValueError("scope_confidence musi mieścić się w 0–1.")
        if self.char_span[0] < 0 or self.char_span[1] < self.char_span[0]:
            raise ValueError("Zakres znaków bloku jest niepoprawny.")
        if not self.symbols:
            raise ValueError("Blok musi dotyczyć co najmniej jednego symbolu.")
        if not self.segments:
            object.__setattr__(self, "segments", (self.char_span,))
        ordered = all(
            a[0] < a[1] and (i == 0 or self.segments[i - 1][1] <= a[0]) for i, a in enumerate(self.segments)
        )
        if not ordered or self.segments[0][0] != self.char_span[0] or self.segments[-1][1] != self.char_span[1]:
            raise ValueError("Segmenty bloku muszą być rozłączne, rosnące i obejmować jego zakres.")

    @property
    def is_contiguous(self) -> bool:
        return len(self.segments) == 1

    def locate(self, start: int, end: int) -> tuple[tuple[int, int], ...]:
        """Pozycje ``[start, end)`` w ``text`` jako zakresy w tekście dokumentu."""
        spans: list[tuple[int, int]] = []
        offset = 0
        for segment_start, segment_end in self.segments:
            length = segment_end - segment_start
            low, high = max(start, offset), min(end, offset + length)
            if low < high:
                spans.append((segment_start + low - offset, segment_start + high - offset))
            offset += length + 1  # separator „\n” między segmentami
        return tuple(spans)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": ZONE_BLOCK_SCHEMA_VERSION,
            "block_id": self.block_id,
            "scope_kind": self.scope_kind,
            "symbols": list(self.symbols),
            "text": self.text,
            "char_span": list(self.char_span),
            "pages": list(self.pages),
            "path": self.path,
            "scope_confidence": self.scope_confidence,
            "warnings": list(self.warnings),
            "strategy": self.strategy,
            "strategy_reason": self.strategy_reason,
            "node_ids": list(self.node_ids),
            "segments": [list(segment) for segment in self.segments],
            "text_sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ZoneBlock:
        if data.get("schema") != ZONE_BLOCK_SCHEMA_VERSION:
            raise ValueError(f"Nieobsługiwany schemat bloku: {data.get('schema')!r}.")
        block = cls(
            block_id=str(data["block_id"]),
            scope_kind=data["scope_kind"],
            symbols=tuple(data["symbols"]),
            text=str(data["text"]),
            char_span=(int(data["char_span"][0]), int(data["char_span"][1])),
            pages=tuple(int(page) for page in data["pages"]),
            path=str(data["path"]),
            scope_confidence=float(data["scope_confidence"]),
            warnings=tuple(data.get("warnings", ())),
            strategy=int(data.get("strategy", 0)),
            strategy_reason=str(data.get("strategy_reason", "")),
            node_ids=tuple(data.get("node_ids", ())),
            segments=tuple((int(a), int(b)) for a, b in data.get("segments", ())),
        )
        if data.get("text_sha256") not in (None, block.sha256):
            raise ValueError("Skrót tekstu bloku nie zgadza się z jego treścią.")
        return block


def block_id_for(
    char_span: tuple[int, int],
    scope_kind: str,
    symbols: Sequence[str],
    segments: Sequence[tuple[int, int]] = (),
) -> str:
    """Identyfikator zależny wyłącznie od treści: zakres, segmenty, rodzaj i symbole."""
    key = json.dumps(
        [list(char_span), [list(part) for part in segments], scope_kind, sorted(symbols)],
        ensure_ascii=False, separators=(",", ":"),
    )
    return f"zb-{char_span[0]:07d}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:10]}"


def block_from_span(
    view: DocumentStructureView,
    start: int,
    end: int,
    *,
    symbols: Sequence[str],
    scope_kind: ScopeKind,
    scope_confidence: float,
    path: str,
    strategy: int,
    strategy_reason: str,
    warnings: Sequence[str] = (),
    node_ids: Sequence[str] = (),
    segments: Sequence[tuple[int, int]] | None = None,
) -> ZoneBlock:
    """Buduje blok z zakresu znormalizowanego tekstu dokumentu (opcjonalnie z segmentów)."""
    ordered_symbols = tuple(dict.fromkeys(symbols))
    parts = tuple(segments) if segments else ((start, end),)
    pages: list[int] = []
    for part in parts:
        pages.extend(page for page in view.pages_between(*part) if page not in pages)
    return ZoneBlock(
        block_id=block_id_for((start, end), scope_kind, ordered_symbols, parts),
        scope_kind=scope_kind,
        symbols=ordered_symbols,
        text="\n".join(view.text[a:b] for a, b in parts),
        char_span=(start, end),
        pages=tuple(pages),
        path=path,
        scope_confidence=scope_confidence,
        warnings=tuple(warnings),
        strategy=strategy,
        strategy_reason=strategy_reason,
        node_ids=tuple(node_ids),
        segments=parts,
    )


def block_from_node(
    view: DocumentStructureView,
    node_id: str,
    *,
    symbols: Sequence[str],
    scope_kind: ScopeKind,
    scope_confidence: float,
    strategy: int,
    strategy_reason: str,
    warnings: Sequence[str] = (),
) -> ZoneBlock:
    """Blok obejmujący cały węzeł drzewa (np. punkt „N) dla terenu X:” z literami)."""
    node = view.node(node_id)
    return block_from_span(
        view, node.start, node.end, symbols=symbols, scope_kind=scope_kind, scope_confidence=scope_confidence,
        path=node.path, strategy=strategy, strategy_reason=strategy_reason, warnings=warnings, node_ids=(node_id,),
    )


def blocks_digest(blocks: Sequence[ZoneBlock]) -> str:
    """Skrót listy bloków: ten sam dokument, symbole i wersja reguł dają ten sam skrót."""
    payload = json.dumps([block.to_dict() for block in blocks], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def blocks_to_json(blocks: Sequence[ZoneBlock]) -> str:
    return json.dumps([block.to_dict() for block in blocks], ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def blocks_from_json(payload: str) -> tuple[ZoneBlock, ...]:
    return tuple(ZoneBlock.from_dict(item) for item in json.loads(payload))
