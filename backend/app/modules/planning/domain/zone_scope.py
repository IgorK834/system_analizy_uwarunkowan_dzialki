"""Resolver zakresu strefy: bloki tekstu planu dla symboli, z jawną strategią (PV3-06).

Przypisuje parametry do strefy na poziomie BLOKU drzewa struktury, a nie paragrafu, i nigdy
nie skleja niepowiązanych fragmentów. Każdy blok niesie rodzaj zakresu (``scope_kind``),
strategię 0–6, uzasadnienie, strony i zakres znaków, więc wartość z bloku ma prawdziwe
źródło.

Strategie (numeracja z Task 20.6):

1. osobny § (ustęp) na strefę: węzeł wprowadzający jeden symbol;
2. wspólny § dla listy lub zakresu symboli;
3. numerowane podpunkty „N) dla terenu X:”;
4. wartości przypisane symbolom listą w akapicie: z listy „w terenie X – …” bierzemy
   pozycje dotyczące symbolu (bramkowanie po symbolu), pozycję „w pozostałych terenach”
   jako klauzulę resztową tylko wtedy, gdy symbol nie występuje wprost w liście, a pozycje
   „w strefie …” (zależne od rysunku) jako zakres nierozstrzygnięty;
5. klauzula ogólna: lista poza sekcją strefy, w której symbol jest wymieniony (wartość
   ``general_clause``, nigdy mieszana z sekcją strefy);
6. tabela: sekcja wprowadzająca symbol zawiera wiersze tabeli (``table_row``);
0. zapas: cały paragraf, gdy nic z powyższego nie rozpoznano — z niską pewnością,
   ostrzeżeniem ``ZONE_SCOPE_AMBIGUOUS`` i ręczną weryfikacją.

Reguły rozstrzygające: sekcja wprowadzająca KILKA symboli jest ``zone_section`` tylko
wtedy, gdy symbol nie ma własnej, jednosymbolowej sekcji (inaczej to ``general_clause``);
dopasowanie symbolu jest dokładne (odstępy tolerowane), a pomyłki OCR (I/1, O/0, l/1) są
tylko zapasem z karą pewności i ostrzeżeniem ``ZONE_SYMBOL_OCR_MATCH``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.modules.planning.domain.zone_blocks import (
    ZONE_SCOPE_AMBIGUOUS,
    DocumentStructureView,
    ScopeKind,
    StructureNodeView,
    ZoneBlock,
    block_from_span,
)
from app.shared.zone_symbol import find_zone_symbol_mentions, zone_symbol_text_pattern

ZONE_SECTION_NOT_FOUND: Final[str] = "ZONE_SECTION_NOT_FOUND"
ZONE_SYMBOL_OCR_MATCH: Final[str] = "ZONE_SYMBOL_OCR_MATCH"
ZONE_SYMBOL_NEAR_MATCH: Final[str] = "ZONE_SYMBOL_NEAR_MATCH"
ZONE_SCOPE_MULTIPLE_SECTIONS: Final[str] = "ZONE_SCOPE_MULTIPLE_SECTIONS"
OCR_CONFIDENCE_FACTOR: Final[float] = 0.75

_ITEM_TYPES: Final[frozenset[str]] = frozenset({"dash", "letter", "point", "html_list_item", "table_row", "section"})
_CONTAINER_TYPES: Final[tuple[str, ...]] = ("paragraph", "section", "chapter")
_LEAD_LIMIT: Final[int] = 500
_MARKER_PREFIX: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:(?:§\s*\d+\w*\.?|\d+\w*[.)]|[a-ząćęłńóśźż]\)|[–—-])\s*)+"
)
_INTRO_START: Final[re.Pattern[str]] = re.compile(
    r"^(?:ustalenia\s+)?(?:dla\s+(?:wszystkich\s+)?(?:teren\w*|obszar\w*|stref\w*|jednostk\w*)"
    r"|w\s+zakresie\b[^:.]{0,240}?\bteren\w*|na\s+teren\w*|teren\w*|obszar\w*|stref[ay]\b"
    r"|(?:ustala|wyznacza|określa)\s+się\b[^:.]{0,300}?\b(?:teren|obszar)\w*)",
    re.IGNORECASE,
)
_SYMBOL_LIKE: Final[str] = (
    r"(?<![\w.])(?=[0-9A-Za-zĄ-ż._/\-]*[A-ZĄĆĘŁŃÓŚŹŻ])(?:[0-9A-ZĄĆĘŁŃÓŚŹŻ][0-9A-Za-ząćęłńóśźż]*"
    r"(?:[._/\-][0-9A-Za-ząćęłńóśźż]+)*(?:,[0-9A-ZĄĆĘŁŃÓŚŹŻ][0-9A-Za-ząćęłńóśźż]*)*)"
)
_SYMBOL_LIKE_RE: Final[re.Pattern[str]] = re.compile(_SYMBOL_LIKE)
_INTRO_END: Final[re.Pattern[str]] = re.compile(
    r":|\b(?:ustala\w*|obowiązuj\w*|przeznacza\w*|przeznaczen\w*|dopuszcza\w*|wprowadza\w*|znajduj\w*|wskazuje\w*)\b"
    r"|\.\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ])"
)
_LIST_SPLIT: Final[re.Pattern[str]] = re.compile(r"\s*(?:,\s+|[:;–—]|\si\s|\soraz\s)\s*")
_BARE_SYMBOL: Final[re.Pattern[str]] = re.compile(r"[0-9A-ZĄĆĘŁŃÓŚŹŻ][0-9A-Za-ząćęłńóśźż._/()+\-]{0,24}")
_SYMBOL_KEYWORD: Final[re.Pattern[str]] = re.compile(r"\b(?:symbol\w*|oznaczon\w*|wyznaczon\w*)", re.IGNORECASE)
_RANGE: Final[re.Pattern[str]] = re.compile(r"\S\s*[–—]\s*\S|\S\s+-\s+\S")
_SEPARATOR: Final[str] = r"(?:\s+[–—-](?=\s)|\s*[–—](?=\s)|\s*:(?=\s|$))"
_KEYED_BY_TERRAIN: Final[re.Pattern[str]] = re.compile(
    rf"^(?:w|dla|na)\s+(?:terenie|terenach|terenu|terenów|obszarze|obszarach)\s*:?\s*(?P<key>.+?){_SEPARATOR}", re.IGNORECASE | re.DOTALL
)
_KEYED_BARE: Final[re.Pattern[str]] = re.compile(
    rf"^(?P<key>{_SYMBOL_LIKE}(?:\s*(?:,|i|oraz)\s*{_SYMBOL_LIKE})*){_SEPARATOR}"
)
_KEYED_RESIDUAL: Final[re.Pattern[str]] = re.compile(r"^(?:w|dla|na)\s+pozosta\w+\s+(?:terenach|terenów|obszarach|strefach)", re.IGNORECASE)
_KEYED_ZONE_AREA: Final[re.Pattern[str]] = re.compile(r"^(?:w|dla)\s+(?:strefie|strefach)\b", re.IGNORECASE)
_KEY_SPLIT: Final[re.Pattern[str]] = re.compile(r"\s*(?:,\s+|;|\si\s|\soraz\s)\s*")
_ORDINAL: Final[re.Pattern[str]] = re.compile(r"^(?P<pre>.*?)(?P<num>\d+)(?P<suf>\D*)$")
_RANGE_TOKEN: Final[str] = r"[0-9A-Za-zĄ-ż][0-9A-Za-ząćęłńóśźż._/()+\-]*"
# Początek ``a`` tylko na granicy słowa: start w środku tokenu i tak kończy się porażką (ten sam koniec
# tokenu), a bez tej granicy długi token bez odstępu (np. śmieci OCR) dawał koszt kwadratowy (PV3-16).
_RANGE_MENTION: Final[re.Pattern[str]] = re.compile(
    rf"(?<![0-9A-Za-zĄ-ż])(?P<a>{_RANGE_TOKEN})(?:\s*[–—]\s*|\s+-\s+)(?P<b>{_RANGE_TOKEN})"
)
_MAX_RANGE: Final[int] = 200


@dataclass(frozen=True)
class ScopeResolution:
    """Bloki dla wszystkich żądanych symboli, w kolejności deterministycznej."""

    blocks: tuple[ZoneBlock, ...]
    symbol_blocks: tuple[tuple[str, tuple[str, ...]], ...]
    warnings: tuple[tuple[str, tuple[str, ...]], ...]

    def blocks_for(self, symbol: str) -> tuple[ZoneBlock, ...]:
        ids = dict(self.symbol_blocks).get(symbol, ())
        by_id = {block.block_id: block for block in self.blocks}
        return tuple(by_id[block_id] for block_id in ids)

    def warnings_for(self, symbol: str) -> tuple[str, ...]:
        return dict(self.warnings).get(symbol, ())


@dataclass(frozen=True)
class _Piece:
    node: StructureNodeView
    segments: tuple[tuple[int, int], ...]
    scope_kind: ScopeKind
    strategy: int
    reason: str
    confidence: float
    warnings: tuple[str, ...] = ()
    node_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Intro:
    """Węzeł wprowadzający strefę wraz z zakresem (ciąg ustępów do następnego wprowadzenia)."""

    node: StructureNodeView
    start: int
    end: int
    shared: bool
    node_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Key:
    kind: str  # symbols | residual | zone_area
    tokens: tuple[str, ...] = ()


class _Resolver:
    def __init__(self, view: DocumentStructureView) -> None:
        self.view = view
        self._parents = {node.node_id: node for node in view.nodes}

    # --- węzły ----------------------------------------------------------------------------

    def deepest(self, offset: int) -> StructureNodeView:
        best = self.view.nodes[0]
        for node in self.view.nodes:
            if node.start <= offset < node.end and node.depth >= best.depth:
                best = node
        return best

    def owner(self, offset: int) -> StructureNodeView:
        node = self.deepest(offset)
        while node.node_type == "fragment" and node.parent_id is not None:
            node = self.view.node(node.parent_id)
        return node

    def head_span(self, node: StructureNodeView) -> tuple[int, int]:
        """Tekst własny węzła: wstęp przed pierwszym dzieckiem albo cały liść."""
        if not node.children:
            return node.start, node.end
        first = self.view.node(node.children[0])
        if first.node_type == "fragment":
            return first.start, first.end
        return node.start, first.start

    def lead(self, node: StructureNodeView) -> tuple[int, str]:
        """Początek wstępu bez numeracji i jego tekst (do ``_LEAD_LIMIT`` znaków)."""
        start, end = self.head_span(node)
        text = self.view.text[start : min(end, start + _LEAD_LIMIT)]
        prefix = _MARKER_PREFIX.match(text)
        cut = prefix.end() if prefix else 0
        return start + cut, text[cut:]

    def items_of(self, node: StructureNodeView) -> list[StructureNodeView]:
        return [self.view.node(child) for child in node.children if self.view.node(child).node_type in _ITEM_TYPES]

    def contains_node(self, outer: StructureNodeView, inner: StructureNodeView) -> bool:
        return outer.start <= inner.start and inner.end <= outer.end

    def descendants(self, node: StructureNodeView) -> list[StructureNodeView]:
        return [n for n in self.view.nodes if n.node_id != node.node_id and self.contains_node(node, n)]

    # --- wprowadzenie strefy ---------------------------------------------------------------

    @staticmethod
    def symbol_like(token: str) -> bool:
        """Token wygląda na symbol strefy: ma cyfrę albo co najmniej dwie wielkie litery."""
        return any(ch.isdigit() for ch in token) or sum(ch.isupper() for ch in token) >= 2

    def is_intro(self, node: StructureNodeView, mention: tuple[int, int]) -> tuple[bool, list[str], bool]:
        """Czy wzmianka jest we wstępie węzła wprowadzającym strefę: (tak/nie, symbole, wspólny).

        Wstęp zaczyna się od frazy o terenie (``Dla terenu…``, ``Teren oznaczony…``), a
        wzmianka leży przed końcem zdania wprowadzającego (``:``, ``ustala się``, ``obowiązują``).
        Symbole wspólne to kolejne symbole albo zakres od frazy „symbolem/symbolami” do końca zdania.
        """
        lead_start, lead = self.lead(node)
        if not _INTRO_START.match(lead):
            return False, [], False
        rel = mention[0] - lead_start
        if not 0 <= rel < len(lead):
            return False, [], False
        end = _INTRO_END.search(lead, mention[1] - lead_start)
        if end is None:  # „teren X, dla terenu … symbolem Y;” to wyliczenie, nie wprowadzenie sekcji
            return False, [], False
        region = lead[: end.start()]
        keyword = _SYMBOL_KEYWORD.search(region)
        if keyword is not None and keyword.start() <= rel:
            # po „symbolem/symbolami” lista: krótkie człony rozdzielone przecinkiem, „i”, „oraz”, dwukropkiem
            zone = region[keyword.end() :]
            parts = (part.strip() for part in _LIST_SPLIT.split(zone))
            tokens = [part for part in parts if _BARE_SYMBOL.fullmatch(part)]
            zone = region[keyword.start() :]
        else:
            zone = region
            tokens = [t.rstrip(".,;") for t in _SYMBOL_LIKE_RE.findall(zone) if self.symbol_like(t.rstrip(".,;"))]
        tokens.append(self.view.text[mention[0] : mention[1]])
        unique = list(dict.fromkeys(" ".join(token.split()).casefold() for token in tokens))
        shared = len(unique) >= 2 or bool(_RANGE.search(zone))
        return True, unique, shared

    def climb(self, node: StructureNodeView) -> StructureNodeView:
        """Jeśli wstęp rodzica to tylko numer, a węzeł jest pierwszym dzieckiem — strefą jest rodzic."""
        current = node
        while current.parent_id is not None:
            parent = self.view.node(current.parent_id)
            if parent.node_type not in {"paragraph", "section"}:
                break
            kids = self.view.children(parent)
            content = [k for k in kids if not (k.node_type == "fragment" and not self.view.text_of(k).strip())]
            head_text = ""
            if kids and kids[0].node_type == "fragment":
                head_text = _MARKER_PREFIX.sub("", self.view.text_of(kids[0])).strip()
            first_item = next((k for k in content if k.node_type != "fragment"), None)
            if head_text or first_item is None or first_item.node_id != current.node_id:
                break
            current = parent
        return current

    def introduces_zone(self, node: StructureNodeView) -> bool:
        """Czy wstęp węzła wprowadza jakąkolwiek strefę (fraza o terenie, symbol, koniec zdania)."""
        _, lead = self.lead(node)
        match = _INTRO_START.match(lead)
        end = _INTRO_END.search(lead) if match else None
        return bool(match and end and any(self.symbol_like(t) for t in _SYMBOL_LIKE_RE.findall(lead[: end.start()])))

    def extend_run(self, node: StructureNodeView) -> tuple[int, tuple[str, ...]]:
        """Ustępy bezpośrednio pod rozdziałem: zakres sięga do następnego wprowadzenia innej strefy.

        W planach bez paragrafów (``Rozdział → 1. 2. 3.``) strefie należą kolejne ustępy aż do
        ustępu wprowadzającego inną strefę (albo do końca rozdziału).
        """
        if node.node_type != "section" or node.parent_id is None:
            return node.end, (node.node_id,)
        parent = self.view.node(node.parent_id)
        if parent.node_type != "chapter":
            return node.end, (node.node_id,)
        siblings = [k for k in self.view.children(parent) if k.node_type != "fragment"]
        position = next(i for i, k in enumerate(siblings) if k.node_id == node.node_id)
        end, ids = node.end, [node.node_id]
        for sibling in siblings[position + 1 :]:
            if sibling.node_type != "section" or self.introduces_zone(sibling) or sibling.label == "1":
                break
            end = sibling.end
            ids.append(sibling.node_id)
        return end, tuple(ids)

    # --- listy bramkowane po symbolu ---------------------------------------------------------

    def item_key(self, item: StructureNodeView) -> _Key | None:
        _, lead = self.lead(item)
        lead = lead.lstrip()
        if _KEYED_RESIDUAL.match(lead):
            return _Key("residual")
        if _KEYED_ZONE_AREA.match(lead):
            return _Key("zone_area")
        match = _KEYED_BY_TERRAIN.match(lead) or _KEYED_BARE.match(lead)
        if match is None:
            return None
        tokens = tuple(token.strip(" .") for token in _KEY_SPLIT.split(match.group("key")) if token.strip(" ."))
        return _Key("symbols", tokens) if tokens else None

    def names(self, key: _Key, symbol: str) -> bool:
        pattern = re.compile(zone_symbol_text_pattern(symbol, with_boundaries=False), re.IGNORECASE)
        return any(pattern.fullmatch(" ".join(token.split())) for token in key.tokens)

    @staticmethod
    def _loose(text: str) -> str:
        return re.sub(r"[\s.]", "", text).casefold()

    def names_loosely(self, key: _Key, symbol: str) -> bool:
        """Symbol w pozycji listy różni się od żądanego tylko kropkami/odstępami (literówka w planie)."""
        return any(self._loose(token) == self._loose(symbol) for token in key.tokens) and not self.names(key, symbol)

    def gated_lists(self, scope: StructureNodeView | None) -> list[tuple[StructureNodeView, list[tuple[StructureNodeView, _Key | None]]]]:
        found = []
        for node in self.view.nodes:
            if scope is not None and not (self.contains_node(scope, node)):
                continue
            if not node.children:
                continue
            keyed = [(item, self.item_key(item)) for item in self.items_of(node)]
            if sum(1 for _, key in keyed if key is not None) >= 2:
                found.append((node, keyed))
        return found

    # --- zakresy symboli („1MN-U – 5MN-U”) -------------------------------------------------------

    @staticmethod
    def _ordinal(token: str) -> tuple[str, int, str] | None:
        match = _ORDINAL.match(token.strip(" .,;:"))
        return (match.group("pre"), int(match.group("num")), match.group("suf")) if match else None

    def range_mentions(self, symbol: str) -> list[tuple[int, int]]:
        """Zakresy typu ``1MN-U – 5MN-U`` obejmujące symbol, który nie jest ich końcem."""
        target = self._ordinal(" ".join(symbol.split()))
        if target is None:
            return []
        found: list[tuple[int, int]] = []
        for match in _RANGE_MENTION.finditer(self.view.text):
            first, last = self._ordinal(match.group("a")), self._ordinal(match.group("b"))
            if first is None or last is None or first[0] != last[0] or first[2] != last[2]:
                continue
            if (first[0], first[2]) != (target[0], target[2]):
                continue
            low, high = first[1], last[1]
            if low < target[1] < high and high - low <= _MAX_RANGE:
                found.append((match.start(), match.end()))
        return found

    # --- jeden symbol -------------------------------------------------------------------------

    def resolve(self, symbol: str) -> tuple[list[_Piece], list[str]]:
        warnings: list[str] = []
        mentions = list(find_zone_symbol_mentions(self.view.text, symbol))
        mentions.extend(self.range_mentions(symbol))
        mentions.sort()
        ocr_only = False
        if not mentions:
            mentions = find_zone_symbol_mentions(self.view.text, symbol, ocr_tolerant=True)
            ocr_only = bool(mentions)
            if ocr_only:
                warnings.append(ZONE_SYMBOL_OCR_MATCH)
        if not mentions:
            return [], [ZONE_SECTION_NOT_FOUND]
        factor = OCR_CONFIDENCE_FACTOR if ocr_only else 1.0

        found: dict[tuple[int, int], _Intro] = {}
        for mention in mentions:
            owner = self.owner(mention[0])
            if owner.node_type == "document":
                continue
            ok, _tokens, shared = self.is_intro(owner, mention)
            if not ok:
                continue
            node = self.climb(owner)
            end, ids = self.extend_run(node)
            key = (node.start, end)
            previous = found.get(key)
            found[key] = _Intro(node, node.start, end, shared if previous is None else previous.shared and shared, ids)

        def outermost(items: list[_Intro]) -> list[_Intro]:
            ordered = sorted(items, key=lambda i: (i.start, -i.end))
            kept_items: list[_Intro] = []
            for item in ordered:
                if not any(o.start <= item.start and item.end <= o.end for o in kept_items):
                    kept_items.append(item)
            return kept_items

        dedicated_intros = outermost([i for i in found.values() if not i.shared])
        shared_intros = [
            i for i in outermost([i for i in found.values() if i.shared])
            if not any(d.start <= i.start and i.end <= d.end for d in dedicated_intros)
        ]
        dedicated = bool(dedicated_intros)
        pieces: list[_Piece] = []
        zone_nodes: list[StructureNodeView] = []
        for intro in sorted([*dedicated_intros, *shared_intros], key=lambda i: (i.start, -i.end)):
            node, shared = intro.node, intro.shared
            zone_nodes.append(node)
            has_rows = sum(
                1 for n in self.descendants(node) if n.node_type == "table_row" and self.informative_row(n)
            ) >= 2
            if shared and dedicated:
                kind: ScopeKind = "general_clause"
                strategy, reason, confidence = 5, "klauzula wspólna dla kilku symboli; strefa ma własną sekcję", 0.65
            elif shared:
                kind, strategy, confidence = "zone_section", 2, 0.8
                reason = "wspólny § dla listy lub zakresu symboli"
            else:
                kind, confidence = "zone_section", 0.9
                numbered = node.node_type in {"point", "letter"}
                strategy = 3 if numbered else 1
                reason = "podpunkt „N) dla terenu X:”" if numbered else "osobny § (ustęp) na strefę"
                if len(intro.node_ids) > 1:
                    reason += "; ustępy do następnego wprowadzenia strefy"
            if has_rows and kind == "zone_section":
                strategy, reason, confidence = 6, reason + "; wiersze tabeli pod odwołaniem do strefy", min(confidence, 0.75)
            inner = [(d.start, d.end) for d in dedicated_intros if shared and d.start >= intro.start and d.end <= intro.end]
            base, extra, notes = self.gate_node(node, symbol, kind, factor, intro.start, intro.end, inner)
            base = base or ((intro.start, intro.end),)
            pieces.append(
                _Piece(node, base, kind, strategy, reason, confidence * factor,
                       (*(warnings if ocr_only else ()), *notes), intro.node_ids)
            )
            pieces.extend(extra)
        # klauzule ogólne poza sekcjami strefy
        pieces.extend(self.general_clauses(symbol, zone_nodes, factor))
        if not pieces:
            owner = self.owner(mentions[0][0])
            container = owner
            for ancestor_id in self.ancestor_ids(owner):
                ancestor = self.view.node(ancestor_id)
                if ancestor.node_type in _CONTAINER_TYPES:
                    container = ancestor
                    break
            note = "symbol występuje tylko w odwołaniach; zakres nie został rozstrzygnięty (cały paragraf)"
            pieces.append(
                _Piece(container, ((container.start, container.end),), "fallback", 0, note, 0.3 * factor,
                       (ZONE_SCOPE_AMBIGUOUS,), (container.node_id,))
            )
            warnings.append(ZONE_SCOPE_AMBIGUOUS)
        zone_sections = [p for p in pieces if p.scope_kind == "zone_section"]
        if len(zone_sections) > 1 and sum(1 for p in zone_sections if p.strategy in (1, 3)) > 1:
            warnings.append(ZONE_SCOPE_MULTIPLE_SECTIONS)
        return pieces, list(dict.fromkeys(warnings))

    def informative_row(self, row: StructureNodeView) -> bool:
        """Wiersz tabeli z literami i cyframi (nie linia podziału ani pusta komórka)."""
        text = self.view.text_of(row)
        return any(ch.isalpha() for ch in text) and any(ch.isdigit() for ch in text) and len(text.strip()) > 10

    def other_intro_inside(self, container: StructureNodeView, excluded: StructureNodeView) -> bool:
        """Czy w kontenerze, poza ``excluded``, jest wprowadzenie jakiejkolwiek (innej) strefy."""
        for node in self.view.nodes:
            if node.node_type == "fragment" or not self.contains_node(container, node) or self.contains_node(excluded, node):
                continue
            if node.node_id == container.node_id:
                continue
            start, lead = self.lead(node)
            match = _INTRO_START.match(lead)
            end = _INTRO_END.search(lead) if match else None
            if match and end and any(self.symbol_like(t) for t in _SYMBOL_LIKE_RE.findall(lead[: end.start()])):
                return True
        return False

    def ancestor_ids(self, node: StructureNodeView) -> list[str]:
        chain: list[str] = []
        current = node
        while current.parent_id is not None:
            chain.append(current.parent_id)
            current = self.view.node(current.parent_id)
        return chain

    def gate_node(
        self, node: StructureNodeView, symbol: str, kind: ScopeKind, factor: float,
        start: int, end: int, extra_holes: list[tuple[int, int]],
    ) -> tuple[tuple[tuple[int, int], ...], list[_Piece], tuple[str, ...]]:
        """Segmenty bloku po wyjęciu pozycji list, które nie dotyczą symbolu, klauzule z list i ostrzeżenia."""
        holes: list[tuple[int, int]] = list(extra_holes)
        extra: list[_Piece] = []
        notes: list[str] = []
        scope = StructureNodeView(
            node.node_id, node.node_type, node.label, node.path, node.parent_id, start, end, node.page_from,
            node.page_to, node.depth,
        )
        for holder, keyed in self.gated_lists(scope):
            exact = [item for item, key in keyed if key and key.kind == "symbols" and self.names(key, symbol)]
            loose = [item for item, key in keyed if key and key.kind == "symbols" and self.names_loosely(key, symbol)]
            named = [*exact, *loose]
            if loose:
                notes.append(ZONE_SYMBOL_NEAR_MATCH)
            others = [
                item for item, key in keyed
                if key and key.kind == "symbols" and item not in exact and item not in loose
            ]
            residual = [item for item, key in keyed if key and key.kind == "residual"]
            zone_area = [item for item, key in keyed if key and key.kind == "zone_area"]
            holes.extend((item.start, item.end) for item in [*others, *residual, *zone_area])
            head = self.head_span(holder)
            if residual and not named:
                extra.append(self.list_piece(holder, head, residual, "residual_clause", 4,
                                             "klauzula „w pozostałych terenach”: symbol nie występuje wprost w liście", 0.6 * factor))
            if zone_area:
                extra.append(self.list_piece(holder, head, zone_area, "fallback", 4,
                                             "pozycje „w strefie …” zależą od rysunku planu; zakres nierozstrzygnięty", 0.3 * factor,
                                             (ZONE_SCOPE_AMBIGUOUS,)))
        return self.subtract(start, end, holes), extra, tuple(dict.fromkeys(notes))

    def list_piece(
        self, holder: StructureNodeView, head: tuple[int, int], items: list[StructureNodeView], kind: ScopeKind,
        strategy: int, reason: str, confidence: float, warnings: tuple[str, ...] = (),
    ) -> _Piece:
        segments = [head, *((item.start, item.end) for item in items)]
        return _Piece(holder, self.merge(segments), kind, strategy, reason, confidence, warnings, (holder.node_id,))

    def general_clauses(self, symbol: str, zone_nodes: list[StructureNodeView], factor: float) -> list[_Piece]:
        pieces: list[_Piece] = []
        for holder, keyed in self.gated_lists(None):
            if any(self.contains_node(zone, holder) for zone in zone_nodes):
                continue
            named = [item for item, key in keyed if key and key.kind == "symbols" and self.names(key, symbol)]
            named = [item for item in named if not any(self.contains_node(zone, item) for zone in zone_nodes)]
            if not named:
                continue
            pieces.append(
                self.list_piece(holder, self.head_span(holder), named, "general_clause", 5,
                                "klauzula ogólna: lista poza sekcją strefy wymienia symbol", 0.7 * factor)
            )
        return pieces

    # --- zakresy ---------------------------------------------------------------------------------

    def subtract(self, start: int, end: int, holes: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
        merged: list[tuple[int, int]] = []
        for low, high in sorted(holes):
            if merged and low <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], high))
            else:
                merged.append((low, high))
        segments: list[tuple[int, int]] = []
        cursor = start
        for low, high in merged:
            if low > cursor:
                segments.append((cursor, min(low, end)))
            cursor = max(cursor, high)
        if cursor < end:
            segments.append((cursor, end))
        return tuple(seg for seg in segments if self.view.text[seg[0] : seg[1]].strip())

    def merge(self, segments: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
        merged: list[tuple[int, int]] = []
        for low, high in sorted(seg for seg in segments if seg[0] < seg[1]):
            if merged and low <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], high))
            else:
                merged.append((low, high))
        return tuple(seg for seg in merged if self.view.text[seg[0] : seg[1]].strip())


def resolve_zone_scope(view: DocumentStructureView, symbols: Sequence[str]) -> ScopeResolution:
    """Bloki dla symboli: ten sam dokument i symbole dają identyczną, uporządkowaną listę."""
    resolver = _Resolver(view)
    grouped: dict[tuple[tuple[tuple[int, int], ...], str], tuple[_Piece, list[str]]] = {}
    per_symbol: dict[str, list[tuple[tuple[tuple[int, int], ...], str]]] = {}
    warnings: dict[str, tuple[str, ...]] = {}
    for symbol in dict.fromkeys(symbols):
        pieces, symbol_warnings = resolver.resolve(symbol)
        warnings[symbol] = tuple(symbol_warnings)
        per_symbol[symbol] = []
        for piece in pieces:
            key = (piece.segments, piece.scope_kind)
            if key in grouped:
                grouped[key][1].append(symbol)
            else:
                grouped[key] = (piece, [symbol])
            per_symbol[symbol].append(key)
    ordered = sorted(grouped, key=lambda key: (key[0][0][0], key[0][-1][1], key[1], key[0]))
    blocks: dict[tuple[tuple[tuple[int, int], ...], str], ZoneBlock] = {}
    for block_key in ordered:
        piece, block_symbols = grouped[block_key]
        start, end = block_key[0][0][0], block_key[0][-1][1]
        shared_warnings = tuple(dict.fromkeys(w for s in block_symbols for w in warnings[s] if w in (ZONE_SCOPE_AMBIGUOUS, ZONE_SYMBOL_OCR_MATCH)))
        blocks[block_key] = block_from_span(
            view, start, end, symbols=sorted(block_symbols), scope_kind=piece.scope_kind,
            scope_confidence=round(min(1.0, piece.confidence), 4), path=piece.node.path, strategy=piece.strategy,
            strategy_reason=piece.reason, warnings=tuple(dict.fromkeys((*piece.warnings, *shared_warnings))),
            node_ids=piece.node_ids, segments=piece.segments,
        )
    symbol_blocks = tuple(
        (symbol, tuple(dict.fromkeys(blocks[key].block_id for key in keys))) for symbol, keys in per_symbol.items()
    )
    return ScopeResolution(
        blocks=tuple(blocks[key] for key in ordered),
        symbol_blocks=symbol_blocks,
        warnings=tuple(warnings.items()),
    )


__all__ = [
    "OCR_CONFIDENCE_FACTOR", "ScopeResolution", "ZONE_SCOPE_AMBIGUOUS", "ZONE_SCOPE_MULTIPLE_SECTIONS",
    "ZONE_SECTION_NOT_FOUND", "ZONE_SYMBOL_NEAR_MATCH", "ZONE_SYMBOL_OCR_MATCH", "resolve_zone_scope",
]
