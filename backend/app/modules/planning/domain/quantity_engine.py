"""Silnik ekstrakcji ilości z ustaleń planów miejscowych oparty na leksykonie (PV3-07).

Zastępuje rozproszone wzorce regex z ``mpzp_parser_numeric``, ``mpzp_parser_descriptive`` i
``rules`` jednym mechanizmem. Przebieg:

1. **Maskowanie szumu** — stopki stron, odwołania do przepisów i identyfikatory (symbole stref)
   zamieniane spacjami tej samej długości, więc zakresy znaków pozostają prawdziwe.
2. **Podział na klauzule po znacznikach list** (``§``, ``1.``, ``1)``, ``a)``, tiret). Klauzula
   podrzędna bez własnego rzeczownika dziedziczy rzeczownik z nagłówka klauzuli nadrzędnej
   (``wysokość budynków:`` → ``- usługowych – maksimum 12 m``).
3. **Rzeczowniki parametrów** (leksykon) i **atomy wartości**: liczba z jednostką, zakres
   ``od X do Y``/``X – Y``, liczba słowna, zapis podwójny ``0,50 (50%)``.
4. **Wiązanie** atomu z rzeczownikiem (najbliższy poprzedni, bez bariery zdania, w limicie
   odległości), wyznaczenie operatora (przymiotnik przy rzeczowniku, ``maksimum``/``minimum``,
   ``nie większą niż``, ``do N``), normalizacja (``quantity_normalization``) i wykluczenia.
5. **Zakres symboli**: ``w terenie 6.6.MW/U – maksimum 55%`` obowiązuje tylko dla wskazanej
   strefy; dla innej strefy wartość jest odrzucana (``dropped``).

Wynik niesie dla każdej wartości zakres znaków i strategię, flagi przeróbek zapisu oraz
rozpiętości tekstu kwalifikatorów (warunki dla PV3-08 — wartości alternatywne, np. ``dla
dachu płaskiego``). Silnik niczego nie zgaduje: bez rzeczownika i bez zgodnej jednostki
wartość nie powstaje.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field, replace
from typing import Final

from app.modules.planning.domain.quantity_lexicon import (
    BASE_CONFIDENCE,
    COMPOUND_STOREY_PATTERN,
    DELTA_CONTEXT,
    DEGREE_LETTER_PATTERN,
    OCR_DEGREE_GARBLE,
    FAMILY_BIO,
    FAMILY_HEIGHT,
    FAMILY_IGNORE,
    FAMILY_PARKING,
    FAMILY_RETAIL,
    FAMILY_ROOF,
    FAMILY_SETBACK,
    FAMILY_STOREYS,
    HEIGHT_OTHER_NOUN,
    LEXICON_VERSION,
    MARKER_PATTERN,
    MAX_GAP_FIRST,
    MAX_GAP_NEXT,
    METER_TRAILING_STOP,
    NEUTRALIZING_NOUNS,
    NOISE_PATTERNS,
    NOUN_PREFIX_MAX,
    NOUN_PREFIX_MIN,
    NOUN_PREFIX_WINDOW,
    NUMBER_WORD_STOREY_PATTERN,
    OP_DELTA,
    OP_EXACT,
    OP_GT,
    OP_LT,
    OP_MAX,
    OP_MIN,
    OP_RANGE,
    OPERATOR_BEFORE,
    OTHER_MEASURE_BEFORE,
    PARAMETER_FAMILIES,
    PARAMETER_ORDER,
    RANK_BULLET,
    RANK_LETTER,
    RANK_PARAGRAPH,
    RANK_POINT,
    RANK_SECTION,
    UNDERGROUND_FOLLOWING,
    UNIT_AREA,
    UNIT_DEGREE,
    UNIT_HECTARE,
    UNIT_METER,
    UNIT_NONE,
    UNIT_PERCENT,
    UNIT_PLACE,
    UNIT_REGEXES,
    UNIT_STOREY,
)
from app.modules.planning.domain.quantity_normalization import (
    NormalizedQuantity,
    engine_confidence,
    normalize_quantity,
)
from app.shared.numbers import NUMBER_PATTERN, NUMBER_WORD_PATTERN, parse_number_prefix, parse_number_word, parse_polish_number

ENGINE_VERSION: Final[str] = f"quantity-engine/1.0+{LEXICON_VERSION}"

STRATEGY_ADJECTIVE: Final[str] = "adjective"
STRATEGY_MAX_MIN_NOUN: Final[str] = "max_min_noun"
STRATEGY_COMPARATIVE: Final[str] = "comparative"
STRATEGY_UP_TO: Final[str] = "up_to"
STRATEGY_RANGE_FROM_TO: Final[str] = "range_from_to"
STRATEGY_RANGE_DASH: Final[str] = "range_dash"
STRATEGY_EXACT_WORD: Final[str] = "exact_word"
STRATEGY_BARE: Final[str] = "bare"
STRATEGY_FRAME_SETBACK: Final[str] = "frame_setback"
STRATEGY_FRAME_PARKING: Final[str] = "frame_parking"
STRATEGY_FRAME_VALUE_FIRST: Final[str] = "frame_value_first"
STRATEGY_STOREY_WORD: Final[str] = "storey_word"

_SCOPE_SYMBOL_CHARS: Final[re.Pattern[str]] = re.compile(r"^[0-9A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż._/\-+]+$")
_QUALIFIER_CAP: Final[int] = 220
_ABBREVIATIONS: Final[frozenset[str]] = frozenset(
    "ust pkt lit poz art nr np tzw max min ok ww zm tj tzn m pn itp itd szt godz ha pow dz ul al im ks "
    "św dr inż mgr wg tel r w".split()
)
_SENTENCE_END: Final[re.Pattern[str]] = re.compile(r"[.!?]\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ§])")
_PRIOR_ITEM_END: Final[re.Pattern[str]] = re.compile(r"[:;,.]\s*$")
_CONNECTOR_ONLY: Final[re.Pattern[str]] = re.compile(
    r"^[\s,;:–—\-]*(?:i|oraz|lub|albo|a|natomiast|przy\s+czym)?[\s,;:–—\-]*$", re.IGNORECASE
)
_BETWEEN_CONJUNCTION: Final[re.Pattern[str]] = re.compile(
    r"(?:,\s*)?(?:\ba\s+(?=dla|w\b|z\b|przy)|przy\s+czym|natomiast|\boraz\b|\blub\b|\balbo\b|"
    r"\bi\s+(?=dla|w\s+przypadku))",
    re.IGNORECASE,
)
# Tekst po wartości jest jej warunkiem tylko, gdy zaczyna się OD RAZU słowem wprowadzającym
# (``10,0 m w przypadku budynków…``, ``15 m dla zabudowy pierzejowej``). Po przecinku, średniku,
# dwukropku albo myślniku zaczyna się zwykle inne ustalenie (``do 30°, dla zabudowy frontowej –
# kalenica równolegle…``), a ``lub dachy płaskie`` to inna alternatywa, nie warunek tej wartości.
_POST_INTRODUCER: Final[re.Pattern[str]] = re.compile(
    r"\s+(?:w\s+przypadku|dla|przy|z\s+dach\w+|w\s+(?:strefie|strefach|pasie|odległości)|"
    r"na\s+(?:terenie|części)|od\s+strony|poza|pozostał\w+)\b",
    re.IGNORECASE,
)
_APPROXIMATE_BEFORE: Final[re.Pattern[str]] = re.compile(r"(?:\bok\.|około|\bca\b\.?)\s*$", re.IGNORECASE)
_RANGE_LEAD_BEFORE: Final[re.Pattern[str]] = re.compile(r"\bod\s+$", re.IGNORECASE)
# Zakres może być zawinięty w nowej linii (``od 30° do⏎50°``), więc separator toleruje białe znaki.
_RANGE_CONNECTOR: Final[re.Pattern[str]] = re.compile(r"\s*(?P<sep>[–—−-]|do)\s*(?=\d)", re.IGNORECASE)
# Liczba nie jest wartością, gdy zamyka numer pozycji listy (``4)``): w tekście z OCR znaczniki
# list bywają w jednej linii z treścią.
_NUMBER_TOKEN: Final[re.Pattern[str]] = re.compile(
    rf"(?<![\wąćęłńóśźż.,])(?P<num>{NUMBER_PATTERN})(?!\d)(?!\s?\))"
)
_PARENTHESIZED_PERCENT: Final[re.Pattern[str]] = re.compile(
    rf"\s*\(\s*(?P<pct>{NUMBER_PATTERN})\s*%\s*\)"
)
# Zakres symboli: ``w terenie 6.6.MW/U``, ``dla terenów A, B``, ``w pozostałych terenach``. Tylko
# słowo „teren” wiąże klauzulę z symbolami stref; ``w strefie SWZ 22`` to podstrefa (warunek).
_SCOPE_ANYWHERE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:dla|w|na)\s+(?P<kind>(?:pozostał\w+\s+)?teren\w*)\b(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_SCOPE_FILLER: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:oznaczon\w*\s+(?:na\s+rysunku\s+planu\s+)?symbol\w*\s*:?\s*|symbol\w*\s*:?\s*|:)\s*",
    re.IGNORECASE,
)
_SETBACK_FRAME: Final[re.Pattern[str]] = re.compile(
    rf"(?:w\s+odległo\w+|odległo\w+|lini\w+\s+zabudowy)\s*"
    rf"(?P<op>do|co\s+najmniej|nie\s+mniejszej\s+niż|minimum|min\.?)?\s*[:\-–—]?\s*"
    rf"(?P<num>{NUMBER_PATTERN})\s*m\b\.?\s+od\s+"
    rf"(?:tej\s+|tych\s+|wspomnian\w+\s+)?"
    rf"(?:granic\w+|lini\w+\s+rozgraniczając\w+|lini\w+\s+zabudowy|krawędzi|osi\b|pasa)",
    re.IGNORECASE,
)
_BIO_VALUE_FIRST: Final[re.Pattern[str]] = re.compile(
    rf"(?P<num>{NUMBER_PATTERN})[ \t]*%\s+(?:powierzchni\w*|teren\w*|działk\w*)(?:\s+\w+){{0,3}}?\s+"
    rf"biologicznie\s+czynn\w*",
    re.IGNORECASE,
)
_PARKING_FRAME: Final[re.Pattern[str]] = re.compile(
    rf"(?P<num>{NUMBER_PATTERN}|\b(?:{NUMBER_WORD_PATTERN})\b)\s*(?:miejsc\w*|stanowisk\w*)\s+"
    rf"(?:(?:parkingow\w*|postojow\w*|do\s+parkowania)\s+)?(?:dla\s+samochod\w+\s+)?na\s+"
    rf"(?P<per>(?:{NUMBER_PATTERN}\s+)?[a-ząćęłńóśźż²0-9\s]+?)(?=[,.;:]|$)",
    re.IGNORECASE,
)


# --- struktury danych -------------------------------------------------------------------


@dataclass(frozen=True)
class ClauseScope:
    """Ograniczenie klauzuli do wskazanych stref (``w terenie 6.6.MW/U``) albo „pozostałych”."""

    kind: str  # restrict | complement | unresolved
    symbols: tuple[str, ...]
    start: int
    end: int
    # Przedziały symboli (``MN.2 – MN.11``, ``1MN-U – 5MN-U``): pary końców, rozwijane przy porównaniu.
    ranges: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Clause:
    """Klauzula: treść od znacznika listy do następnego znacznika."""

    index: int
    marker_start: int
    start: int
    end: int
    rank: int
    parent: int | None
    scope: ClauseScope | None = None


@dataclass(frozen=True)
class NounHit:
    family: str
    start: int
    end: int
    text: str
    op_hint: str | None
    inherited: bool = False


@dataclass(frozen=True)
class Atom:
    start: int
    end: int
    kind: str  # single | range
    numbers: tuple[float, ...]
    raw_numbers: tuple[str, ...]
    unit_kind: str
    unit_text: str = ""
    range_style: str | None = None
    parenthesized_percent: float | None = None
    degree_symbol: str | None = None
    number_word: bool = False
    approximate: bool = False


@dataclass(frozen=True)
class Qualifier:
    """Rozpiętość tekstu obok wartości (kandydat na warunek).

    ``role``: ``pre`` (przed wartością), ``post`` (po wartości), ``lead`` (człon klauzuli przed
    rzeczownikiem), ``clause`` (kwalifikator całej klauzuli dla kolejnych wartości), ``header``
    (nagłówek rodzica).
    """

    start: int
    end: int
    role: str


@dataclass(frozen=True)
class QuantityMatch:
    """Wartość z tekstu wraz z zakresem znaków, strategią i śladem dopasowania."""

    parameter: str
    family: str
    value: float
    unit: str | None
    raw_value: str
    start: int
    end: int
    operator: str
    strategy: str
    flags: tuple[str, ...]
    noun: str | None
    noun_start: int | None
    noun_end: int | None
    clause_start: int
    clause_end: int
    qualifiers: tuple[Qualifier, ...] = ()
    scope_symbols: tuple[str, ...] = ()
    scope_kind: str | None = None
    per_text: str | None = None

    @property
    def confidence(self) -> float:
        """Pewność silnika (przed kalibracją PV3-09): bazowa × kary za przeróbki zapisu."""
        return engine_confidence(self.flags)


@dataclass(frozen=True)
class DroppedQuantity:
    """Wartość rozpoznana, ale świadomie nieprzypisana (inna strefa, odległość, wykluczenie)."""

    start: int
    end: int
    reason: str
    raw_value: str


@dataclass
class QuantityExtraction:
    matches: list[QuantityMatch] = field(default_factory=list)
    dropped: list[DroppedQuantity] = field(default_factory=list)


# --- maskowanie i klauzule --------------------------------------------------------------


def mask_text(text: str) -> str:
    """Zastępuje szum (stopki, odwołania, identyfikatory) spacjami; zachowuje długość i nowe linie."""
    chars = list(text)
    for pattern in NOISE_PATTERNS:
        for match in pattern.finditer(text):
            for index in range(match.start(), match.end()):
                if chars[index] != "\n":
                    chars[index] = " "
    return "".join(chars)


def _has_sentence_barrier(gap: str) -> bool:
    for match in _SENTENCE_END.finditer(gap):
        token = re.search(r"([A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]+)$", gap[: match.start()])
        if token is not None and token.group(1).lower() in _ABBREVIATIONS:
            continue
        return True
    return False


def _previous_line_closes_item(text: str, position: int) -> bool:
    """Czy poprzednia niepusta linia kończy pozycję (``:;,.``) albo jest samym znacznikiem."""
    before = text[:position].rstrip("\n \t")
    line = before.rsplit("\n", 1)[-1]
    if not line.strip():
        return True
    if _PRIOR_ITEM_END.search(line):
        return True
    return MARKER_PATTERN.fullmatch(line.strip() + " ") is not None or bool(re.fullmatch(r"[-–—−•·]", line.strip()))


def split_clauses(text: str) -> list[Clause]:
    """Dzieli tekst na klauzule po znacznikach list i wiąże każdą z klauzulą nadrzędną."""
    ranks = {
        "section": RANK_SECTION,
        "paragraph": RANK_PARAGRAPH,
        "point": RANK_POINT,
        "letter": RANK_LETTER,
        "bullet": RANK_BULLET,
    }
    markers: list[tuple[int, int, int]] = []
    for match in MARKER_PATTERN.finditer(text):
        kind = match.lastgroup or ""
        if kind == "bullet":
            tail = text[match.end() : match.end() + 12].lstrip(" \t\n")
            # ``– 0,3 (30%)`` w środku zawiniętego zdania to kontynuacja, nie tiret listy.
            if tail[:1].isdigit() and not _previous_line_closes_item(text, match.start()):
                continue
        # Powtórzone tiret (``--``) to zagnieżdżenie głębiej o poziom na każdy dodatkowy znak.
        depth = len(match.group("bullet")) - 1 if kind == "bullet" else 0
        markers.append((match.start(), match.end(), ranks[kind] + depth))

    spans: list[tuple[int, int, int, int]] = []  # (marker_start, content_start, end, rank)
    first_start = markers[0][0] if markers else len(text)
    if first_start > 0 or not markers:
        spans.append((0, 0, first_start if markers else len(text), -1))
    for position, (marker_start, content_start, rank) in enumerate(markers):
        end = markers[position + 1][0] if position + 1 < len(markers) else len(text)
        spans.append((marker_start, content_start, end, rank))

    clauses: list[Clause] = []
    stack: list[Clause] = []
    for index, (marker_start, content_start, end, rank) in enumerate(spans):
        while stack and stack[-1].rank >= rank:
            stack.pop()
        parent = stack[-1].index if stack else None
        clause = Clause(
            index=index,
            marker_start=marker_start,
            start=content_start,
            end=end,
            rank=rank,
            parent=parent,
        )
        clauses.append(clause)
        if rank >= 0:  # tekst przed pierwszym znacznikiem nie jest nagłówkiem niczego
            stack.append(clause)
    return clauses


def _is_symbol_token(token: str) -> bool:
    if not _SCOPE_SYMBOL_CHARS.match(token):
        return False
    if token.islower():
        return False
    uppercase = sum(1 for char in token if char.isupper())
    # Same cyfry (``22``, ``0,01``) to liczba, nie symbol; symbol ma literę wielką i drugą wielką
    # albo cyfrę (``MN``, ``1U``, ``230_U``).
    return uppercase >= 2 or (uppercase >= 1 and any(char.isdigit() for char in token))


_SYMBOL_SEPARATOR: Final[re.Pattern[str]] = re.compile(
    r"\s*(?P<sep>,|\boraz\b|\blub\b|\bi\b|[–—−-]|\bdo\b)\s*(?=\S)", re.IGNORECASE
)
_SYMBOL_TOKEN: Final[re.Pattern[str]] = re.compile(r"\s*(?P<tok>[^\s,;:()]+)")


def _parse_symbol_list(rest: str, cursor: int) -> tuple[list[str], list[tuple[str, str]], int]:
    """Lista symboli stref od ``cursor``: ``(symbole, przedziały, koniec)``."""
    symbols: list[str] = []
    ranges: list[tuple[str, str]] = []
    while cursor < len(rest):
        token_match = _SYMBOL_TOKEN.match(rest, cursor)
        if token_match is None:
            break
        token = token_match.group("tok").rstrip(".")
        if not _is_symbol_token(token):
            break
        cursor = token_match.end()
        separator = _SYMBOL_SEPARATOR.match(rest, cursor)
        following = _SYMBOL_TOKEN.match(rest, separator.end()) if separator is not None else None
        follower = following.group("tok").rstrip(".") if following is not None else ""
        if (
            separator is not None
            and following is not None
            and _is_symbol_token(follower)
            and separator.group("sep").lower() in {"–", "—", "−", "-", "do"}
        ):
            ranges.append((token, follower))  # ``MN.2 – MN.11``: przedział, rozwijany przy porównaniu
            cursor = following.end()
            separator = _SYMBOL_SEPARATOR.match(rest, cursor)
            following = _SYMBOL_TOKEN.match(rest, separator.end()) if separator is not None else None
            if separator is None or following is None or not _is_symbol_token(following.group("tok").rstrip(".")):
                break
            cursor = separator.end()
            continue
        symbols.append(token)
        if separator is None or following is None or not _is_symbol_token(follower):
            break
        cursor = separator.end()
    return symbols, ranges, cursor


_DESCRIPTION_THEN_DASH: Final[re.Pattern[str]] = re.compile(r"\s[–—-]\s+")


def _detect_scope(text: str, start: int, end: int, prefix_end: int | None = None) -> ClauseScope | None:
    """Zakres symboli w prefiksie klauzuli: ``w terenie 6.6.MW/U -``, ``dla terenu MN.2:``.

    ``prefix_end`` to początek pierwszej wartości klauzuli — zakres szukany jest przed nią, bo
    po wartości symbole są objaśnieniem (``60% powierzchni działki … w terenie 230_U``).
    """
    region_end = min(end, start + 320)
    if prefix_end is not None:
        region_end = min(region_end, max(prefix_end, start))
    region = text[start:region_end]
    match = _SCOPE_ANYWHERE.search(region)
    if match is None:
        return None
    kind_word = match.group("kind").lower()
    rest = match.group("rest")
    rest_offset = start + match.start("rest")
    if kind_word.startswith("pozosta"):
        return ClauseScope("complement", (), start + match.start(), rest_offset)
    filler = _SCOPE_FILLER.match(rest)
    symbols, ranges, cursor = _parse_symbol_list(rest, filler.end() if filler else 0)
    if not symbols and not ranges:
        # ``w Terenie zabudowy mieszkaniowej jednorodzinnej - MN.1:``: opis terenu, myślnik, symbole.
        for dash in _DESCRIPTION_THEN_DASH.finditer(rest[:140]):
            symbols, ranges, cursor = _parse_symbol_list(rest, dash.end())
            if symbols or ranges:
                break
    if not symbols and not ranges:
        return None
    return ClauseScope("restrict", tuple(symbols), start + match.start(), rest_offset + cursor, tuple(ranges))


_SYMBOL_HEADER: Final[re.Pattern[str]] = re.compile(r"\bsymbol\w*", re.IGNORECASE)


def _detect_symbol_prefix(text: str, clause: Clause, clauses: list[Clause]) -> ClauseScope | None:
    """Pozycja listy typu ``MN – 60 % powierzchni działki`` pod nagłówkiem „oznaczonych … symbolami:”.

    Sam symbol na początku klauzuli to zakres tylko wtedy, gdy nagłówek wyraźnie wylicza symbole
    (inaczej ``MN – tereny zabudowy mieszkaniowej`` byłoby definicją, nie ograniczeniem wartości).
    """
    head = text[clause.start : min(clause.end, clause.start + 160)]
    symbols, ranges, cursor = _parse_symbol_list(head, 0)
    if not symbols and not ranges:
        return None
    after = re.match(r"\s*[–—:-]\s*(?=\S)", head[cursor:])
    if after is None:
        return None
    for ancestor in _ancestors(clauses, clause):
        header = text[ancestor.start : min(ancestor.end, ancestor.start + 400)]
        colon = header.find(":")
        if _SYMBOL_HEADER.search(header[: colon if colon != -1 else len(header)]) is not None:
            return ClauseScope("restrict", tuple(symbols), clause.start, clause.start + cursor, tuple(ranges))
    return None


# --- rzeczowniki ------------------------------------------------------------------------


def _compile_noun_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for family, spec in PARAMETER_FAMILIES.items():
        for noun in spec.nouns:
            patterns.append((family, re.compile(noun, re.IGNORECASE)))
    for noun in (HEIGHT_OTHER_NOUN, *NEUTRALIZING_NOUNS):
        patterns.append((FAMILY_IGNORE, re.compile(noun, re.IGNORECASE)))
    return tuple(patterns)


_NOUN_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = _compile_noun_patterns()


def _prefix_hint(masked: str, noun_start: int, floor: int) -> str | None:
    window = masked[max(floor, noun_start - NOUN_PREFIX_WINDOW) : noun_start]
    cut = max(window.rfind(":"), window.rfind(";"), window.rfind("\n\n"))
    digit = re.search(r"\d(?!.*\d)", window)
    if digit is not None:
        cut = max(cut, digit.end())
    window = window[cut + 1 :] if cut >= 0 else window
    has_max = NOUN_PREFIX_MAX.search(window) is not None
    has_min = NOUN_PREFIX_MIN.search(window) is not None
    if has_max == has_min:
        return None
    return OP_MAX if has_max else OP_MIN


def find_nouns(masked: str, start: int, end: int, families: Collection[str] | None = None) -> list[NounHit]:
    """Rzeczowniki parametrów w ``[start, end)`` bez nakładania; dłuższe i konkretne wygrywają."""
    candidates: list[NounHit] = []
    for family, pattern in _NOUN_PATTERNS:
        for match in pattern.finditer(masked, start, end):
            candidates.append(
                NounHit(family, match.start(), match.end(), match.group(0), _prefix_hint(masked, match.start(), start))
            )
    candidates.sort(key=lambda hit: (hit.start, -(hit.end - hit.start), hit.family == FAMILY_IGNORE))
    chosen: list[NounHit] = []
    for hit in candidates:
        if chosen and hit.start < chosen[-1].end:
            continue
        chosen.append(hit)
    if families is not None:
        wanted = set(families)
        chosen = [hit for hit in chosen if hit.family in wanted or hit.family == FAMILY_IGNORE]
    return chosen


# --- atomy wartości ---------------------------------------------------------------------


def _detect_unit(masked: str, position: int, limit: int) -> tuple[str, str, int, str | None]:
    """Jednostka po liczbie: ``(rodzaj, tekst, koniec, znak_stopnia)``; spacja opcjonalna."""
    rest = masked[position:limit]
    space = 1 if rest[:1] in {" ", "\u00a0"} else 0
    for kind, regex in UNIT_REGEXES:
        match = regex.match(rest, space)
        if match is not None:
            symbol = match.group(0) if kind == UNIT_DEGREE else None
            if symbol is not None and symbol not in {"°", "º", "˚"}:
                symbol = "stopni"
            return kind, match.group(0), position + match.end(), symbol
    letter = DEGREE_LETTER_PATTERN.match(rest, 0)
    if letter is not None:
        return UNIT_DEGREE, "o", position + letter.end(), "o"
    garble = OCR_DEGREE_GARBLE.match(rest, 0)
    if garble is not None:
        return UNIT_DEGREE, garble.group(0), position + garble.end(), "garble"
    return UNIT_NONE, "", position, None


_BARE_NUMBER_WORD: Final[re.Pattern[str]] = re.compile(
    rf"(?<![\wąćęłńóśźż])(?P<word>{NUMBER_WORD_PATTERN})(?![\wąćęłńóśźż])", re.IGNORECASE
)
_INLINE_LIST_NUMBER: Final[re.Pattern[str]] = re.compile(r"\.\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ])")


def _is_inline_list_number(masked: str, match: re.Match[str], start: int) -> bool:
    """``5. W zakresie…`` w jednej linii z poprzednim zdaniem: numer ustępu, nie wartość."""
    raw = match.group("num")
    if not raw.isdigit() or len(raw) > 2:
        return False
    if _INLINE_LIST_NUMBER.match(masked, match.end()) is None:
        return False
    before = masked[max(start, match.start() - 3) : match.start()].rstrip(" \t")
    return before == "" or before[-1] in ".;:!?"


def find_atoms(masked: str, start: int, end: int) -> list[Atom]:
    """Atomy wartości w ``[start, end)``: liczby z jednostką, zakresy, liczby słowne."""
    atoms: list[Atom] = []
    cursor = start
    while True:
        match = _NUMBER_TOKEN.search(masked, cursor, end)
        if match is None:
            break
        raw = match.group("num")
        try:
            number = parse_polish_number(raw)
        except ValueError:
            cursor = match.end()
            continue
        if _is_inline_list_number(masked, match, start):
            cursor = match.end()
            continue
        unit_kind, unit_text, atom_end, symbol = _detect_unit(masked, match.end(), end)
        raw_numbers = [raw]
        numbers = [number]
        range_style: str | None = None
        connector = _RANGE_CONNECTOR.match(masked, atom_end)
        if connector is not None and match.end() <= atom_end:
            sep = connector.group("sep").lower()
            lead_in = _RANGE_LEAD_BEFORE.search(masked[max(start, match.start() - 24) : match.start()]) is not None
            if sep != "do" or lead_in:
                second = _NUMBER_TOKEN.match(masked, connector.end(), end)
                if second is not None:
                    second_kind, second_text, second_end, second_symbol = _detect_unit(masked, second.end(), end)
                    compatible = unit_kind == second_kind or UNIT_NONE in (unit_kind, second_kind)
                    if compatible:
                        try:
                            numbers.append(parse_polish_number(second.group("num")))
                        except ValueError:
                            numbers = [number]
                        else:
                            raw_numbers.append(second.group("num"))
                            if unit_kind == UNIT_NONE:
                                unit_kind, unit_text, symbol = second_kind, second_text, second_symbol
                            atom_end = second_end
                            range_style = "from_to" if sep == "do" else "dash"
        atom_start = match.start()
        if range_style == "from_to":
            # ``od 30° do 45°``: zapis surowy zakresu zaczyna się od ``od``.
            window_start = max(start, match.start() - 24)
            lead = _RANGE_LEAD_BEFORE.search(masked[window_start : match.start()])
            if lead is not None:
                atom_start = window_start + lead.start()
        parenthesized: float | None = None
        if len(numbers) == 1 and unit_kind == UNIT_NONE:
            paren = _PARENTHESIZED_PERCENT.match(masked, atom_end)
            if paren is not None:
                try:
                    parenthesized = parse_polish_number(paren.group("pct"))
                except ValueError:
                    parenthesized = None
                else:
                    atom_end = paren.end()
        approximate = _APPROXIMATE_BEFORE.search(masked[max(start, match.start() - 8) : match.start()]) is not None
        atoms.append(
            Atom(
                start=atom_start,
                end=atom_end,
                kind="range" if len(numbers) == 2 else "single",
                numbers=tuple(numbers),
                raw_numbers=tuple(raw_numbers),
                unit_kind=unit_kind,
                unit_text=unit_text,
                range_style=range_style,
                parenthesized_percent=parenthesized,
                degree_symbol=symbol,
                approximate=approximate,
            )
        )
        cursor = max(atom_end, match.end())
    for word_match in NUMBER_WORD_STOREY_PATTERN.finditer(masked, start, end):
        value = parse_number_word(word_match.group("word"))
        if value is not None:
            atoms.append(
                Atom(
                    start=word_match.start(),
                    end=word_match.end(),
                    kind="single",
                    numbers=(float(value),),
                    raw_numbers=(word_match.group(0),),
                    unit_kind=UNIT_STOREY,
                    unit_text=word_match.group("unit"),
                    number_word=True,
                )
            )
    for bare_word in _BARE_NUMBER_WORD.finditer(masked, start, end):
        value = parse_number_word(bare_word.group("word"))
        if value is not None:
            atoms.append(
                Atom(
                    start=bare_word.start(),
                    end=bare_word.end(),
                    kind="single",
                    numbers=(float(value),),
                    raw_numbers=(bare_word.group(0),),
                    unit_kind=UNIT_NONE,
                    number_word=True,
                )
            )
    for compound in COMPOUND_STOREY_PATTERN.finditer(masked, start, end):
        value = parse_number_prefix(compound.group("word"))
        if value is not None:
            atoms.append(
                Atom(
                    start=compound.start(),
                    end=compound.end(),
                    kind="single",
                    numbers=(float(value),),
                    raw_numbers=(compound.group(0),),
                    unit_kind=UNIT_STOREY,
                    unit_text="",
                    number_word=True,
                )
            )
    atoms.sort(key=lambda atom: (atom.start, -atom.end))  # przy tym samym początku wygrywa dłuższy atom
    unique: list[Atom] = []
    for atom in atoms:
        if unique and atom.start < unique[-1].end:
            continue
        unique.append(atom)
    return unique


# --- ramki jednostkowe: odsunięcie od granicy i miejsca postojowe -----------------------


@dataclass(frozen=True)
class Frame:
    parameter: str
    family: str
    value: float
    unit: str | None
    start: int
    end: int
    strategy: str
    operator: str
    flags: tuple[str, ...]
    per_text: str | None = None
    location_only: bool = False


def _find_frames(masked: str, start: int, end: int) -> list[Frame]:
    frames: list[Frame] = []
    for match in _SETBACK_FRAME.finditer(masked, start, end):
        try:
            number = parse_polish_number(match.group("num"))
        except ValueError:
            continue
        op_word = (match.group("op") or "").lower()
        # ``w odległości do 4,0 m od tej granicy`` to strefa obowiązywania innego limitu, nie odsunięcie.
        location_only = op_word == "do"
        normalized = normalize_quantity(FAMILY_SETBACK, number, unit_kind=UNIT_METER, raw_number=match.group("num"))
        if normalized is None:
            continue
        frames.append(
            Frame(
                parameter="setback_m",
                family=FAMILY_SETBACK,
                value=normalized.value,
                unit="m",
                start=match.start(),
                end=match.end(),
                strategy=STRATEGY_FRAME_SETBACK,
                operator=OP_MIN if op_word and not location_only else OP_EXACT,
                flags=normalized.flags,
                location_only=location_only,
            )
        )
    for match in _PARKING_FRAME.finditer(masked, start, end):
        raw = match.group("num")
        number = parse_number_word(raw) if not raw[:1].isdigit() else None
        if number is None:
            try:
                number = parse_polish_number(raw)
            except ValueError:
                continue
        normalized = normalize_quantity(FAMILY_PARKING, float(number), unit_kind=UNIT_PLACE, raw_number=raw)
        if normalized is None:
            continue
        flags = (*normalized.flags, *(("number_word",) if not raw[:1].isdigit() else ()))
        frames.append(
            Frame(
                parameter="parking_minimum",
                family=FAMILY_PARKING,
                value=normalized.value,
                unit="miejsca/lokal",
                start=match.start(),
                end=match.end(),
                strategy=STRATEGY_FRAME_PARKING,
                operator=OP_MIN,
                flags=flags,
                per_text=" ".join(match.group("per").split()),
            )
        )
    for match in _BIO_VALUE_FIRST.finditer(masked, start, end):
        number_start = match.start("num")
        percent_end = masked.index("%", match.end("num")) + 1
        op_found = _operator_before(masked, number_start, start)
        operator = op_found[0] if op_found else OP_MIN
        if operator != OP_MIN and operator != OP_EXACT:
            continue  # ``do 30% powierzchni biologicznie czynnej`` nie jest minimum
        try:
            number = parse_polish_number(match.group("num"))
        except ValueError:
            continue
        normalized = normalize_quantity(FAMILY_BIO, number, unit_kind=UNIT_PERCENT, raw_number=match.group("num"))
        if normalized is None:
            continue
        frames.append(
            Frame(
                parameter="min_biologically_active_percent",
                family=FAMILY_BIO,
                value=normalized.value,
                unit="percent",
                start=op_found[1] if op_found else number_start,
                end=percent_end,
                strategy=STRATEGY_FRAME_VALUE_FIRST,
                operator=OP_MIN,
                flags=normalized.flags,
            )
        )
    frames.sort(key=lambda frame: frame.start)
    return frames


# --- operator wartości ------------------------------------------------------------------


def _operator_before(masked: str, atom_start: int, floor: int) -> tuple[str, int, str] | None:
    window_start = max(floor, atom_start - 48)
    window = masked[window_start:atom_start]
    best: tuple[str, int, str] | None = None
    for operator, regex in OPERATOR_BEFORE:
        match = regex.search(window)
        # Najdalej na lewo = najdłuższe dopasowanie: ``nie większą niż`` wygrywa z ``większą niż``.
        if match is not None and (best is None or window_start + match.start() < best[1]):
            word = match.group(0).strip(" :-–—,")
            best = (operator, window_start + match.start(), word)
    return best


def _operator_strategy(word: str) -> str:
    lowered = word.lower()
    if re.search(r"niż|przekrac|nieprzekrac", lowered):
        return STRATEGY_COMPARATIVE
    if lowered == "do":
        return STRATEGY_UP_TO
    if lowered.startswith(("maksimum", "minimum", "max", "min")) and not lowered.startswith(("maksymaln", "minimaln")):
        return STRATEGY_MAX_MIN_NOUN
    if lowered.startswith(("maksymaln", "minimaln")):
        return STRATEGY_ADJECTIVE
    return STRATEGY_EXACT_WORD


# --- wiązanie ---------------------------------------------------------------------------


def _clean_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Obcina separatory i spójniki z brzegów rozpiętości (nie zmienia treści wewnątrz)."""
    segment = text[start:end]
    lead = re.match(r"[\s:–—\-,;]*(?:(?:a|i|oraz|lub|albo|natomiast|przy\s+czym)\b[\s:–—\-,;]*)?", segment, re.IGNORECASE)
    start += lead.end() if lead else 0
    trail = re.search(r"[\s:–—\-,;]*(?:\b(?:a|i|oraz|lub|albo)\b[\s:–—\-,;]*)?$", text[start:end], re.IGNORECASE)
    if trail is not None and trail.start() > 0:
        end = start + trail.start()
    return start, max(start, end)


def _cap_pre(text: str, start: int, end: int) -> tuple[int, int]:
    """Skraca kwalifikator PRZED wartością do ostatniego zdania i ``_QUALIFIER_CAP`` znaków."""
    start = max(start, end - _QUALIFIER_CAP)
    barrier = None
    # Granica zdania wymaga wielkiej litery po kropce, więc patrzymy o jeden znak za ``end``.
    for match in _SENTENCE_END.finditer(text, start, min(len(text), end + 1)):
        token = re.search(r"([A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]+)$", text[start : match.start()])
        if token is None or token.group(1).lower() not in _ABBREVIATIONS:
            barrier = match.end()
    return (min(barrier, end) if barrier is not None else start), end


def _cap_post(text: str, start: int, end: int) -> tuple[int, int]:
    end = min(end, start + _QUALIFIER_CAP)
    for match in _SENTENCE_END.finditer(text, start, end):
        token = re.search(r"([A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]+)$", text[start : match.start()])
        if token is None or token.group(1).lower() not in _ABBREVIATIONS:
            end = match.start() + 1
            break
    semicolon = text.find(";", start, end)
    if semicolon != -1:
        end = semicolon
    return start, end


@dataclass
class _Bound:
    atom: Atom
    noun: NounHit
    clause: Clause
    family: str
    op: str | None
    op_start: int | None
    op_word: str | None
    pre_floor: int
    gap_connector: bool
    extra_flags: tuple[str, ...] = ()


def _resolve_family(noun_family: str, atom: Atom) -> str | None:
    """Rodzina wartości dla pary rzeczownik–jednostka; ``None`` = niezgodne."""
    if noun_family in (FAMILY_HEIGHT, FAMILY_STOREYS):
        if atom.unit_kind == UNIT_STOREY:
            return FAMILY_STOREYS
        if atom.unit_kind == UNIT_METER:
            return FAMILY_HEIGHT
        if atom.unit_kind == UNIT_NONE and noun_family == FAMILY_STOREYS:
            return FAMILY_STOREYS
        return None
    spec = PARAMETER_FAMILIES.get(noun_family)
    if spec is None or atom.unit_kind not in spec.accepted_units:
        return None
    if atom.number_word and atom.unit_kind == UNIT_NONE:
        return None  # liczba słowna bez jednostki (``jednej działce``) to wartość tylko dla kondygnacji
    return noun_family


def _build_matches(
    text: str,
    bound: _Bound,
    *,
    clause_start: int,
    pre_span: tuple[int, int],
    post_span: tuple[int, int],
    header_spans: tuple[tuple[int, int], ...],
    scope: ClauseScope | None,
    extra_qualifiers: tuple[Qualifier, ...] = (),
) -> list[QuantityMatch]:
    atom, noun, family = bound.atom, bound.noun, bound.family
    spec = PARAMETER_FAMILIES[family]
    flags: list[str] = []
    if noun.inherited:
        flags.append("inherited_noun")
    if atom.number_word:
        flags.append("number_word")
    if atom.approximate:
        flags.append("approximate")
    flags.extend(bound.extra_flags)

    if atom.kind == "range":
        operators: list[str] = [OP_MIN, OP_MAX]
        strategy = STRATEGY_RANGE_FROM_TO if atom.range_style == "from_to" else STRATEGY_RANGE_DASH
        if atom.range_style == "dash":
            flags.append("range_dash")
        values = list(atom.numbers)
    else:
        explicit = bound.op
        operator = explicit or noun.op_hint or spec.default_operator
        if explicit is None and noun.op_hint is None and spec.implied_operator_flag:
            flags.append("operator_implied")
        if explicit is not None:
            strategy = _operator_strategy(bound.op_word or "")
        elif noun.op_hint is not None:
            strategy = STRATEGY_ADJECTIVE
        else:
            strategy = STRATEGY_BARE
        # Roof angle bez kierunku to wartość stała: obie granice równe tej samej liczbie.
        operators = [OP_MIN, OP_MAX] if (family == FAMILY_ROOF and operator == OP_EXACT) else [operator]
        values = [atom.numbers[0]] * len(operators)

    end = atom.end
    start = bound.op_start if bound.op_start is not None else atom.start
    raw_value = " ".join(text[start:end].split())
    results: list[QuantityMatch] = []
    for operator, number in zip(operators, values):
        parameter = spec.names.get(operator)
        if parameter is None:
            continue
        raw_number = atom.raw_numbers[0 if operator != OP_MAX or atom.kind == "single" else 1]
        normalized: NormalizedQuantity | None = normalize_quantity(
            family,
            number,
            unit_kind=atom.unit_kind,
            raw_number=raw_number,
            parenthesized_percent=atom.parenthesized_percent,
            degree_symbol=atom.degree_symbol,
        )
        if normalized is None:
            continue
        results.append(
            QuantityMatch(
                parameter=parameter,
                family=family,
                value=normalized.value,
                unit=spec.unit,
                raw_value=raw_value,
                start=start,
                end=end,
                operator=(OP_RANGE if atom.kind == "range" else operator),
                strategy=strategy,
                flags=tuple(dict.fromkeys([*flags, *normalized.flags])),
                noun=noun.text if noun.text else None,
                noun_start=noun.start,
                noun_end=noun.end,
                clause_start=clause_start,
                clause_end=bound.clause.end,
                qualifiers=(
                    *((Qualifier(*pre_span, "pre"),) if pre_span[1] > pre_span[0] else ()),
                    *((Qualifier(*post_span, "post"),) if post_span[1] > post_span[0] else ()),
                    *extra_qualifiers,
                    *(Qualifier(h_start, h_end, "header") for h_start, h_end in header_spans),
                ),
                scope_symbols=scope.symbols if scope is not None else (),
                scope_kind=scope.kind if scope is not None else None,
            )
        )
    return results


def _ancestors(clauses: list[Clause], clause: Clause) -> list[Clause]:
    chain: list[Clause] = []
    parent = clause.parent
    while parent is not None:
        chain.append(clauses[parent])
        parent = clauses[parent].parent
    return chain


def _effective_scope(clauses: list[Clause], clause: Clause) -> tuple[ClauseScope | None, Clause | None]:
    for candidate in (clause, *_ancestors(clauses, clause)):
        if candidate.scope is not None:
            return candidate.scope, candidate
    return None, None


def _header_end(masked: str, clause: Clause, first_atom_start: int | None) -> int:
    colon = masked.find(":", clause.start, clause.end)
    candidates = [clause.end]
    if colon != -1:
        candidates.append(colon + 1)
    if first_atom_start is not None:
        candidates.append(first_atom_start)
    return min(candidates)


def extract_quantities(
    text: str,
    *,
    target_symbol: str | None = None,
    families: Collection[str] | None = None,
) -> QuantityExtraction:
    """Wydobywa ilości z tekstu ustaleń; ``target_symbol`` ogranicza wartości do tej strefy.

    Bez ``target_symbol`` wartości z klauzul ograniczonych do symboli (``w terenie X``) zostają
    z zapisanym ``scope_symbols``; z nim — wartości przypisane do INNYCH stref trafiają do
    ``dropped`` (``other_zone``). ``families`` zawęża wynik do wybranych rodzin parametrów.
    """
    masked = mask_text(text)
    clauses = split_clauses(text)
    result = QuantityExtraction()
    wanted = set(families) if families is not None else None

    per_clause_nouns: dict[int, list[NounHit]] = {}
    per_clause_atoms: dict[int, list[Atom]] = {}
    for clause in clauses:
        per_clause_nouns[clause.index] = find_nouns(masked, clause.start, clause.end, None)
        per_clause_atoms[clause.index] = find_atoms(masked, clause.start, clause.end)
    scoped: list[Clause] = []
    for clause in clauses:
        first_atom = per_clause_atoms[clause.index][0].start if per_clause_atoms[clause.index] else None
        scope = _detect_scope(text, clause.start, clause.end, first_atom)
        if scope is None:
            scope = _detect_symbol_prefix(text, clause, scoped + [clause])
        scoped.append(replace(clause, scope=scope))
    clauses = scoped

    all_matches: list[tuple[QuantityMatch, Clause]] = []
    for clause in clauses:
        for match in _extract_clause(text, masked, clauses, clause, per_clause_nouns, per_clause_atoms, result):
            all_matches.append((match, clause))

    kept = _apply_zone_scope(clauses, all_matches, target_symbol, result)
    for match in kept:
        if wanted is None or match.family in wanted:
            result.matches.append(match)
    order = {name: index for index, name in enumerate(PARAMETER_ORDER)}
    result.matches.sort(key=lambda m: (order.get(m.parameter, len(order)), m.start, m.end))
    return result


def _inherited_noun(
    clauses: list[Clause],
    clause: Clause,
    nouns: dict[int, list[NounHit]],
    atoms_by_clause: dict[int, list[Atom]],
    text: str,
) -> tuple[NounHit | None, Clause | None]:
    candidates = _ancestors(clauses, clause)
    preamble = clauses[0] if clauses and clauses[0].rank == -1 and clause.rank >= 0 else None
    if preamble is not None and not candidates and text[preamble.start : preamble.end].rstrip().endswith(":"):
        # Nagłówek bez znacznika listy (``wysokość zabudowy:`` w pierwszej linii) też dziedziczy dalej.
        candidates = [preamble]
    for ancestor in candidates:
        own = nouns[ancestor.index]
        if not own:
            continue
        atoms = atoms_by_clause[ancestor.index]
        first_atom = atoms[0].start if atoms else None
        header = [hit for hit in own if first_atom is None or hit.start < first_atom]
        if header:
            hit = header[-1]
            return (
                NounHit(hit.family, clause.start, clause.start, hit.text, hit.op_hint, inherited=True),
                ancestor,
            )
    return None, None


def _extract_clause(
    text: str,
    masked: str,
    clauses: list[Clause],
    clause: Clause,
    per_clause_nouns: dict[int, list[NounHit]],
    per_clause_atoms: dict[int, list[Atom]],
    result: QuantityExtraction,
) -> list[QuantityMatch]:
    own_nouns = per_clause_nouns[clause.index]
    frames = _find_frames(masked, clause.start, clause.end)
    atoms = [
        atom
        for atom in per_clause_atoms[clause.index]
        if not any(frame.start <= atom.start < frame.end for frame in frames)
    ]
    if not own_nouns and not frames and not atoms:
        return []

    inherited: NounHit | None = None
    inherited_from: Clause | None = None
    needs_inheritance = bool(atoms) and (not own_nouns or atoms[0].start < own_nouns[0].start)
    if needs_inheritance:
        inherited, inherited_from = _inherited_noun(clauses, clause, per_clause_nouns, per_clause_atoms, text)

    scope, _scope_clause = _effective_scope(clauses, clause)
    # Nagłówki wpływające na wartość: klauzula, z której odziedziczono rzeczownik, i bezpośredni
    # rodzic (``dla budynków gospodarczych i garażowych:``). Wyższych przodków (opis całego § albo
    # przeznaczenia terenu) nie bierzemy: to cel strefy, nie warunek wartości.
    header_clauses: list[Clause] = []
    if inherited_from is not None:
        header_clauses.append(inherited_from)
    if clause.parent is not None and clauses[clause.parent] not in header_clauses:
        header_clauses.append(clauses[clause.parent])
    header_spans: tuple[tuple[int, int], ...] = tuple(
        span
        for span in (
            _clean_span(text, *_cap_pre(text, header.start, _header_end(masked, header, None)))
            for header in header_clauses
        )
        if span[1] > span[0]
    )

    events: list[tuple[int, str, object]] = []
    for hit in own_nouns:
        events.append((hit.start, "noun", hit))
    for atom in atoms:
        events.append((atom.start, "atom", atom))
    for frame in frames:
        events.append((frame.start, "frame", frame))
    events.sort(key=lambda item: (item[0], {"noun": 0, "frame": 1, "atom": 2}[item[1]]))

    first_atom = atoms[0].start if atoms else clause.end
    delta_context = DELTA_CONTEXT.search(masked, clause.start, _header_end(masked, clause, None)) is not None or any(
        DELTA_CONTEXT.search(masked, ancestor.start, _header_end(masked, ancestor, None)) is not None
        for ancestor in _ancestors(clauses, clause)
    )

    current: NounHit | None = inherited
    anchor_end = clause.start
    bound_count = 0
    skipped_since_anchor = 0
    bounds: list[_Bound] = []
    pre_floor = clause.start
    emitted: list[QuantityMatch] = []

    for _position, kind, payload in events:
        if kind == "noun":
            current = payload  # type: ignore[assignment]
            anchor_end = current.end  # type: ignore[union-attr]
            bound_count = 0
            skipped_since_anchor = 0
            pre_floor = anchor_end
            continue
        if kind == "frame":
            frame: Frame = payload  # type: ignore[assignment]
            if frame.location_only:
                result.dropped.append(DroppedQuantity(frame.start, frame.end, "location_distance", text[frame.start : frame.end]))
                continue
            emitted.append(
                QuantityMatch(
                    parameter=frame.parameter,
                    family=frame.family,
                    value=frame.value,
                    unit=frame.unit,
                    raw_value=" ".join(text[frame.start : frame.end].split()),
                    start=frame.start,
                    end=frame.end,
                    operator=frame.operator,
                    strategy=frame.strategy,
                    flags=frame.flags,
                    noun=None,
                    noun_start=None,
                    noun_end=None,
                    clause_start=clause.start,
                    clause_end=clause.end,
                    qualifiers=(
                        *(
                            (Qualifier(lead[0], lead[1], "lead"),)
                            if (lead := _clean_span(text, *_cap_pre(text, clause.start, frame.start)))[1] > lead[0]
                            else ()
                        ),
                        *(Qualifier(start, end, "header") for start, end in header_spans),
                    ),
                    scope_symbols=scope.symbols if scope is not None else (),
                    scope_kind=scope.kind if scope is not None else None,
                    per_text=frame.per_text,
                )
            )
            continue

        atom: Atom = payload  # type: ignore[assignment]
        if delta_context:
            result.dropped.append(DroppedQuantity(atom.start, atom.end, "delta_context", text[atom.start : atom.end]))
            continue
        if current is not None:
            gap = masked[anchor_end : atom.start]
            limit = MAX_GAP_FIRST if bound_count == 0 else MAX_GAP_NEXT
            if len(gap) > limit or _has_sentence_barrier(gap):
                current = None  # rzeczownik za daleko albo w poprzednim zdaniu
        if atom.unit_kind == UNIT_STOREY and (
            current is None or current.family not in (FAMILY_HEIGHT, FAMILY_STOREYS, FAMILY_IGNORE)
        ):
            # ``do trzech kondygnacji``, ``jednokondygnacyjne`` mają jednostkę w sobie: liczba kondygnacji
            # jest parametrem bez osobnego rzeczownika, o ile jest operator albo to przymiotnik złożony.
            storey_op = _operator_before(masked, atom.start, clause.start)
            if (storey_op is not None and storey_op[0] in {OP_MAX, OP_MIN, OP_EXACT}) or atom.unit_text == "":
                if not UNDERGROUND_FOLLOWING.match(masked, atom.end):
                    bounds.append(
                        _Bound(
                            atom=atom,
                            noun=NounHit(FAMILY_STOREYS, atom.start, atom.start, "", None),
                            clause=clause,
                            family=FAMILY_STOREYS,
                            op=storey_op[0] if storey_op else None,
                            op_start=storey_op[1] if storey_op else None,
                            op_word=storey_op[2] if storey_op else None,
                            pre_floor=clause.start,
                            gap_connector=True,
                            extra_flags=() if storey_op is not None else ("noun_implied",),
                        )
                    )
            continue
        if current is None:
            continue
        gap = masked[anchor_end : atom.start]
        if current.family == FAMILY_IGNORE:
            result.dropped.append(DroppedQuantity(atom.start, atom.end, "excluded_noun", text[atom.start : atom.end]))
            anchor_end = atom.end
            bound_count += 1
            continue
        family = _resolve_family(current.family, atom)
        if family is None:
            skipped_since_anchor += 1
            continue
        if atom.unit_kind == UNIT_METER and METER_TRAILING_STOP.match(masked, atom.end):
            skipped_since_anchor += 1
            continue
        if OTHER_MEASURE_BEFORE.search(masked[max(clause.start, atom.start - 40) : atom.start]):
            skipped_since_anchor += 1
            continue
        if atom.unit_kind == UNIT_STOREY and UNDERGROUND_FOLLOWING.match(masked, atom.end):
            skipped_since_anchor += 1
            continue
        op_found = _operator_before(masked, atom.start, max(pre_floor, anchor_end if bound_count else pre_floor))
        if op_found is not None and op_found[0] == OP_GT and family == FAMILY_RETAIL:
            # ``zakaz obiektów handlowych o powierzchni sprzedaży powyżej 2000 m²``: próg zakazu to limit.
            op_found = (OP_MAX, op_found[1], op_found[2])
        if op_found is not None and op_found[0] in {OP_GT, OP_LT, OP_DELTA}:
            reason = "delta" if op_found[0] == OP_DELTA else "threshold"
            result.dropped.append(DroppedQuantity(atom.start, atom.end, reason, text[atom.start : atom.end]))
            skipped_since_anchor += 1
            continue
        connector = bool(_CONNECTOR_ONLY.match(gap)) if bound_count else True
        if atom.unit_kind == UNIT_NONE and atom.kind == "single":
            if skipped_since_anchor and op_found is None:
                continue
            if bound_count and op_found is None and not connector:
                continue
        elif atom.unit_kind == UNIT_NONE and atom.kind == "range" and skipped_since_anchor and op_found is None:
            continue
        if atom.kind == "range":
            op_found = None
        bound = _Bound(
            atom=atom,
            noun=current,
            clause=clause,
            family=family,
            op=op_found[0] if op_found else None,
            op_start=op_found[1] if op_found else None,
            op_word=op_found[2] if op_found else None,
            pre_floor=anchor_end,
            gap_connector=connector,
        )
        bounds.append(bound)
        bound_count += 1
        anchor_end = atom.end
        pre_floor = atom.end

    emitted.extend(
        _matches_for_bounds(text, bounds, clause, header_spans, scope, [position for position, _k, _p in events])
    )
    return emitted


def _matches_for_bounds(
    text: str,
    bounds: list[_Bound],
    clause: Clause,
    header_spans: tuple[tuple[int, int], ...],
    scope: ClauseScope | None,
    event_starts: list[int],
) -> list[QuantityMatch]:
    matches: list[QuantityMatch] = []
    first_pre: tuple[int, int] = (0, 0)
    lead_span: tuple[int, int] = (0, 0)
    for index, bound in enumerate(bounds):
        atom_start = bound.op_start if bound.op_start is not None else bound.atom.start
        previous = bounds[index - 1] if index else None
        following = bounds[index + 1] if index + 1 < len(bounds) else None

        pre_start = previous.atom.end if previous is not None else (bound.noun.end if not bound.noun.inherited else clause.start)
        pre_end = atom_start
        if previous is not None and pre_end > pre_start:
            between = text[pre_start:pre_end]
            conjunction = _BETWEEN_CONJUNCTION.search(between)
            if conjunction is not None:
                pre_start = pre_start + conjunction.end()
            elif not re.search(r"[:–—\-]\s*$", between):
                pre_start = pre_end  # bez separatora przed wartością tekst należy do poprzedniej wartości
            else:
                comma = between.find(",")
                if comma != -1:
                    pre_start = pre_start + comma + 1
        pre_span = _clean_span(text, *_cap_pre(text, pre_start, pre_end))
        if index == 0:
            first_pre = pre_span
            if not bound.noun.inherited and bound.noun.start > clause.start:
                lead_span = _clean_span(text, *_cap_pre(text, clause.start, bound.noun.start))

        post_start = bound.atom.end
        post_end = min([clause.end, *(e for e in event_starts if e >= post_start)]) if following is None else clause.end
        if _POST_INTRODUCER.match(text, post_start) is None:
            post_end = post_start  # tekst po wartości nie jest jej warunkiem
        elif following is not None:
            next_start = following.op_start if following.op_start is not None else following.atom.start
            between = text[post_start:next_start]
            conjunction = _BETWEEN_CONJUNCTION.search(between)
            if conjunction is not None:
                post_end = post_start + conjunction.start()
            elif re.search(r"[:–—\-]\s*$", between):
                comma = between.find(",")
                post_end = post_start + (comma if comma != -1 else 0)
            else:
                post_end = next_start
        post_span = _cap_post(text, *_clean_span(text, post_start, post_end))

        # Człon przed rzeczownikiem (``dla budynków mieszkalnych: wysokość …``) i tekst przed pierwszą
        # wartością dotyczą całej klauzuli, więc kolejne wartości dostają je jako kwalifikatory „clause”.
        shared = (first_pre, lead_span) if index else ()
        extra = tuple(
            Qualifier(span[0], span[1], "clause") for span in shared if span[1] > span[0]
        ) + (
            (Qualifier(lead_span[0], lead_span[1], "lead"),) if index == 0 and lead_span[1] > lead_span[0] else ()
        )
        matches.extend(
            _build_matches(
                text,
                bound,
                clause_start=clause.start,
                pre_span=pre_span,
                post_span=post_span,
                header_spans=header_spans,
                scope=scope,
                extra_qualifiers=extra,
            )
        )
    return matches


# --- zakres strefy ----------------------------------------------------------------------


def _symbol_key(symbol: str) -> str:
    """Klucz porównania symboli: bez odstępów i kropek, wielkości liter oraz pomyłek OCR.

    Porównanie służy do ODRZUCANIA wartości innych stref, więc ma być wyrozumiałe: pomyłka OCR
    (``AIMN`` zamiast ``A1MN``) nie może usunąć wartości z własnej sekcji strefy.
    """
    key = re.sub(r"[\s.]", "", symbol).upper()
    return key.translate(str.maketrans({"I": "1", "L": "1", "O": "0"}))


_TRAILING_NUMBER: Final[re.Pattern[str]] = re.compile(r"^(?P<prefix>\D.*?)(?P<num>\d+)$")
_LEADING_NUMBER: Final[re.Pattern[str]] = re.compile(r"^(?P<num>\d+)(?P<suffix>[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż].*)$")


def _range_contains(first: str, last: str, target: str) -> bool | None:
    """Czy przedział symboli (``MN.2 – MN.11``, ``1MN-U – 5MN-U``) obejmuje cel; ``None`` = nie wiadomo."""
    a, b, t = (re.sub(r"\s", "", item).upper() for item in (first, last, target))
    ma, mb, mt = (_TRAILING_NUMBER.match(item) for item in (a, b, t))
    if ma and mb and ma["prefix"] == mb["prefix"]:
        if mt and mt["prefix"] == ma["prefix"]:
            return int(ma["num"]) <= int(mt["num"]) <= int(mb["num"])
        return False
    la, lb, lt = (_LEADING_NUMBER.match(item) for item in (a, b, t))
    if la and lb and la["suffix"] == lb["suffix"]:
        if lt and lt["suffix"] == la["suffix"]:
            return int(la["num"]) <= int(lt["num"]) <= int(lb["num"])
        return False
    return None


def _scope_mentions(scope: ClauseScope, target: str) -> bool | None:
    """Czy zakres klauzuli obejmuje strefę docelową: ``True``/``False``; ``None`` = nie da się rozstrzygnąć."""
    key = _symbol_key(target)
    if key in {_symbol_key(symbol) for symbol in scope.symbols}:
        return True
    verdicts = [_range_contains(first, last, target) for first, last in scope.ranges]
    if any(verdict is True for verdict in verdicts):
        return True
    if any(verdict is None for verdict in verdicts):
        return None
    return False


def _apply_zone_scope(
    clauses: list[Clause],
    matches: list[tuple[QuantityMatch, Clause]],
    target_symbol: str | None,
    result: QuantityExtraction,
) -> list[QuantityMatch]:
    """Odrzuca wartości przypisane do innych stref; „pozostałe tereny” rozstrzyga po rodzeństwie."""
    if target_symbol is None:
        return [match for match, _clause in matches]
    # Rodzeństwo = klauzule o tym samym rodzicu; „pozostałych terenach” obowiązuje, gdy żadne
    # z rodzeństwa nie wymienia strefy docelowej (a żadne nie jest nierozstrzygalne).
    sibling_mentions: dict[int | None, list[bool | None]] = {}
    for clause in clauses:
        scope = clause.scope
        if scope is not None and scope.kind == "restrict":
            sibling_mentions.setdefault(clause.parent, []).append(_scope_mentions(scope, target_symbol))
    kept: list[QuantityMatch] = []
    for match, clause in matches:
        scope, scope_clause = _effective_scope(clauses, clause)
        if scope is None:
            kept.append(match)
            continue
        if scope.kind == "restrict":
            verdict = _scope_mentions(scope, target_symbol)
            if verdict is False:
                result.dropped.append(DroppedQuantity(match.start, match.end, "other_zone", match.raw_value))
            else:
                kept.append(match)  # True albo nierozstrzygalny przedział: nie odrzucamy
            continue
        parent = scope_clause.parent if scope_clause is not None else None
        mentions = sibling_mentions.get(parent, [])
        if any(verdict is True for verdict in mentions):
            result.dropped.append(DroppedQuantity(match.start, match.end, "other_zone", match.raw_value))
        else:
            kept.append(match)
    return kept


def find_quantities(
    text: str,
    *,
    target_symbol: str | None = None,
    families: Collection[str] | None = None,
) -> list[QuantityMatch]:
    """Skrót: lista wartości bez informacji o odrzuconych."""
    return extract_quantities(text, target_symbol=target_symbol, families=families).matches


__all__ = [
    "BASE_CONFIDENCE",
    "ENGINE_VERSION",
    "Atom",
    "Clause",
    "ClauseScope",
    "DroppedQuantity",
    "NounHit",
    "Qualifier",
    "QuantityExtraction",
    "QuantityMatch",
    "extract_quantities",
    "find_atoms",
    "find_nouns",
    "find_quantities",
    "mask_text",
    "split_clauses",
]
