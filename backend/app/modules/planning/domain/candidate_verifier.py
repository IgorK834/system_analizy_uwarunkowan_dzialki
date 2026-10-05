"""Weryfikator kandydatów modelu językowego: bramki deterministyczne względem tekstu (PV3-12, ADR-012).

To JEDYNA droga, którą wartość z modelu może trafić do wyniku. Model jest źródłem kandydatów,
nie prawdy: kandydat przechodzi kolejno bramki G1–G8 i każda z nich sprawdza go wyłącznie
względem tekstu bloku strefy (``ZoneBlock``) i katalogu parametrów — nigdy względem tego, co
model „uważa”. Odrzucony kandydat niesie numer bramki i kod przyczyny; przyjęty ma status
``ai_candidate`` (nigdy ``verified``) i ``extraction_method = llm_verified``.

Kolejność bramek i kody odrzuceń są stałe (``GATE_CODES``):

- **G1** parametr należy do katalogu kontraktu (``extraction_contract.CATALOG``);
- **G2** operator jest dozwolony dla parametru;
- **G3** ``evidence_quote`` jest podciągiem tekstu bloku po normalizacji białych znaków, zaczyna
  się i kończy na granicy wyrazu, nie wygląda jak polecenie ani JSON, a w nim albo tuż przed nim
  (w bloku) stoi termin parametru; dla dokumentu z OCR dopuszczalna jest mała odległość edycyjna
  (bez zmiany cyfr) — z flagą ``quote_ocr_fuzzy`` i karą pewności; cytaty warunków też muszą
  leżeć w bloku;
- **G4** ``raw_value`` leży w cytacie i w dopasowanym tekście bloku, a liczby nie są ucięte
  (``1`` z ``1,5`` albo ``12`` z ``120``);
- **G5** wartość jest przeliczana deterministycznie z ``raw_value`` (ta sama normalizacja co w
  silniku ilości, ``quantity_normalization``); liczba modelu nie jest wymagana, a podana i różna
  od wyliczonej odrzuca kandydata (nigdy nie nadpisuje wyliczonej); reguła ``manual`` przypada
  wyłącznie zapisom z flagą artefaktu (np. ``300`` zamiast ``30°``);
- **G6** walidacja dziedzinowa (``rules.validate_planning_rule``: procent 0–100, dodatnie
  wysokości i intensywność, min ≤ max) — także między przyjętymi kandydatami tej samej strefy;
- **G7** zakres: symbol strefy występuje w ``scope_quote`` (leżącym w bloku) albo blok ma
  ``scope_confidence`` ≥ progu; w przeciwnym razie kandydat dostaje ``applicability=unresolved``
  i wartość NIE jest przypisywana; cytat nazywający wyłącznie inną strefę też jest odrzucany;
- **G8** deduplikacja (ta sama strefa, parametr, operator, wartość, warunki i miejsce cytatu).

Strona i zakres znaków pochodzą wyłącznie z dopasowania w bloku (``ZoneBlock.locate`` →
``DocumentStructureView.page_at``), nigdy z odpowiedzi modelu. Moduł jest czysty (bez sieci,
bazy i ustawień), deterministyczny i nie zapisuje treści kandydata w kodach odrzuceń.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final

from app.modules.planning.domain import evidence_confidence
from app.modules.planning.domain import extraction_contract as contract
from app.modules.planning.domain.extraction_contract import LlmCandidate
from app.modules.planning.domain.quantity_lexicon import (
    DEGREE_LETTER_PATTERN,
    FAMILY_BIO,
    FAMILY_COVERAGE,
    FAMILY_HEIGHT,
    FAMILY_INTENSITY,
    FAMILY_ROOF,
    FAMILY_SETBACK,
    FAMILY_STOREYS,
    PARAMETER_FAMILIES,
    UNIT_DEGREE,
    UNIT_NONE,
    UNIT_REGEXES,
)
from app.modules.planning.domain.quantity_normalization import normalize_quantity, number_word_value
from app.modules.planning.domain.rules import (
    PlanningRuleCandidate,
    PlanningRuleValidationError,
    numeric_rule_spec,
    validate_planning_rule,
)
from app.modules.planning.domain.zone_blocks import DocumentStructureView, ZoneBlock
from app.shared.numbers import NUMBER_PATTERN, parse_polish_number
from app.shared.zone_symbol import find_zone_symbol_mentions, same_zone_symbol

VERIFIER_VERSION: Final[str] = "llm-candidate-verifier/1"
EXTRACTION_METHOD_LLM_VERIFIED: Final[str] = "llm_verified"
REVIEW_STATUS: Final[str] = contract.CANDIDATE_REVIEW_STATUS  # "ai_candidate", nigdy "verified"

# --- bramki i kody (stałe; kolejność jest częścią kontraktu) --------------------------------

G1, G2, G3, G4, G5, G6, G7, G8 = "G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"
GATES: Final[tuple[str, ...]] = (G1, G2, G3, G4, G5, G6, G7, G8)

CODE_PARAMETER_NOT_IN_CATALOG: Final[str] = "parameter_not_in_catalog"
CODE_OPERATOR_NOT_ALLOWED: Final[str] = "operator_not_allowed"
CODE_QUOTE_EMPTY: Final[str] = "quote_empty"
CODE_QUOTE_SUSPICIOUS: Final[str] = "quote_suspicious"
CODE_QUOTE_NOT_IN_BLOCK: Final[str] = "quote_not_in_block"
CODE_QUOTE_TRUNCATED: Final[str] = "quote_truncated"
CODE_QUOTE_LACKS_TERM: Final[str] = "quote_lacks_parameter_term"
CODE_CONDITION_NOT_IN_BLOCK: Final[str] = "condition_quote_not_in_block"
CODE_RAW_EMPTY: Final[str] = "raw_value_empty"
CODE_RAW_NOT_IN_QUOTE: Final[str] = "raw_value_not_in_quote"
CODE_RAW_TRUNCATED: Final[str] = "raw_value_truncated"
CODE_RAW_NOT_NUMERIC: Final[str] = "raw_value_not_numeric"
CODE_RANGE_INCOMPLETE: Final[str] = "range_incomplete"
CODE_UNIT_MISMATCH: Final[str] = "unit_mismatch"
CODE_VALUE_MISMATCH: Final[str] = "value_mismatch"
CODE_DOMAIN_INVALID: Final[str] = "domain_invalid"
CODE_RANGE_INVERTED: Final[str] = "range_inverted"
CODE_MIN_EXCEEDS_MAX: Final[str] = "min_exceeds_max"
CODE_SYMBOL_NOT_IN_BLOCK: Final[str] = "symbol_not_in_block"
CODE_QUOTE_NAMES_OTHER_ZONE: Final[str] = "quote_names_other_zone"
CODE_SCOPE_UNRESOLVED: Final[str] = "scope_unresolved"
CODE_DUPLICATE: Final[str] = "duplicate"

GATE_CODES: Final[Mapping[str, tuple[str, ...]]] = {
    G1: (CODE_PARAMETER_NOT_IN_CATALOG,),
    G2: (CODE_OPERATOR_NOT_ALLOWED,),
    G3: (
        CODE_QUOTE_EMPTY,
        CODE_QUOTE_SUSPICIOUS,
        CODE_QUOTE_NOT_IN_BLOCK,
        CODE_QUOTE_TRUNCATED,
        CODE_QUOTE_LACKS_TERM,
        CODE_CONDITION_NOT_IN_BLOCK,
    ),
    G4: (CODE_RAW_EMPTY, CODE_RAW_NOT_IN_QUOTE, CODE_RAW_TRUNCATED),
    G5: (CODE_RAW_NOT_NUMERIC, CODE_RANGE_INCOMPLETE, CODE_UNIT_MISMATCH, CODE_VALUE_MISMATCH),
    G6: (CODE_DOMAIN_INVALID, CODE_RANGE_INVERTED, CODE_MIN_EXCEEDS_MAX),
    G7: (CODE_SYMBOL_NOT_IN_BLOCK, CODE_QUOTE_NAMES_OTHER_ZONE, CODE_SCOPE_UNRESOLVED),
    G8: (CODE_DUPLICATE,),
}
CODE_GATE: Final[Mapping[str, str]] = {code: gate for gate, codes in GATE_CODES.items() for code in codes}

# Flagi zapisu (oprócz flag normalizacji z ``quantity_normalization``).
FLAG_QUOTE_OCR_FUZZY: Final[str] = "quote_ocr_fuzzy"
FLAG_UNIT_IMPLIED: Final[str] = "unit_implied"
FLAG_NUMBER_WORD: Final[str] = "number_word"
FLAG_QUOTE_REPEATED: Final[str] = "quote_repeated_in_block"
# Reguła ``manual`` (decyzja człowieka) wyłącznie dla zapisów z flagą artefaktu albo nietypowej konwersji.
ARTIFACT_FLAGS: Final[frozenset[str]] = frozenset(
    {"degree_artifact", "degree_letter", "degree_ocr", "double_notation_mismatch", "percent_to_ratio"}
)

_PARAMETER_FAMILY: Final[Mapping[str, str]] = {
    "max_building_height_m": FAMILY_HEIGHT,
    "min_intensity": FAMILY_INTENSITY,
    "max_intensity": FAMILY_INTENSITY,
    "max_building_coverage_percent": FAMILY_COVERAGE,
    "min_biologically_active_percent": FAMILY_BIO,
    "roof_angle_min_deg": FAMILY_ROOF,
    "roof_angle_max_deg": FAMILY_ROOF,
    "max_storeys": FAMILY_STOREYS,
    "setback_m": FAMILY_SETBACK,
}
# Jednostka wyniku zgodna z parserem MPZP (``MpzpParameter.unit``).
PARAMETER_UNIT: Final[Mapping[str, str | None]] = {
    "max_building_height_m": "m",
    "min_intensity": None,
    "max_intensity": None,
    "max_building_coverage_percent": "percent",
    "min_biologically_active_percent": "percent",
    "roof_angle_min_deg": "deg",
    "roof_angle_max_deg": "deg",
    "max_storeys": None,
    "setback_m": "m",
}
# Termin parametru, który musi stać w cytacie albo tuż przed nim w bloku (np. nagłówek listy
# „wysokość zabudowy:” nad punktem „a) dla budynków mieszkalnych – 9 m”). To bramka przeciw
# cytatom bez związku z parametrem (np. tekstowi udającemu JSON albo polecenie).
_TERMS: Final[Mapping[str, re.Pattern[str]]] = {
    FAMILY_HEIGHT: re.compile(r"wysoko\w*|wys\.", re.IGNORECASE),
    FAMILY_STOREYS: re.compile(r"kondygnac\w*", re.IGNORECASE),
    FAMILY_COVERAGE: re.compile(r"zabudow\w*", re.IGNORECASE),
    FAMILY_BIO: re.compile(r"biologicznie|biol\.|czynn\w*", re.IGNORECASE),
    FAMILY_INTENSITY: re.compile(r"intensywno\w*", re.IGNORECASE),
    FAMILY_ROOF: re.compile(r"dach\w*|połac\w*|nachyle\w*|pochyle\w*|spad\w*|k[ąa]t\w*", re.IGNORECASE),
    FAMILY_SETBACK: re.compile(r"odległo\w*|odsuni\w*|lini\w*|granic\w*|krawędzi\w*", re.IGNORECASE),
}
TERM_LOOKBACK_CHARS: Final[int] = 400
# Tekst dokumentu udający polecenie albo strukturę JSON nie jest dowodem wartości planu.
_SUSPICIOUS_QUOTE: Final[re.Pattern[str]] = re.compile(
    r"[{}\[\]]|`{3}|\"\s*:|\b(?:ignore|disregard|instruction\w*|prompt\w*|assistant|json|system\s+message|"
    r"zignoruj\w*|instrukcj\w*|polecen\w+\s+(?:systemow|dla\s+modelu))\b",
    re.IGNORECASE,
)
_WORD_CHAR: Final[re.Pattern[str]] = re.compile(r"[0-9A-Za-zĄąĆćĘęŁłŃńÓóŚśŹźŻż]")
_NUMBER_RE: Final[re.Pattern[str]] = re.compile(NUMBER_PATTERN)
_PARENTHESIZED_PERCENT: Final[re.Pattern[str]] = re.compile(r"\(\s*(" + NUMBER_PATTERN + r")\s*%\s*\)")
_DIGITS: Final[re.Pattern[str]] = re.compile(r"\d")
_VALUE_TOLERANCE: Final[float] = 1e-6
# Pary parametrów, dla których min ≤ max w tej samej strefie i przy tych samych warunkach.
MIN_MAX_PAIRS: Final[tuple[tuple[str, str], ...]] = (
    ("min_intensity", "max_intensity"),
    ("roof_angle_min_deg", "roof_angle_max_deg"),
)


@dataclass(frozen=True)
class VerifierPolicy:
    """Progi weryfikacji (jawna polityka, nie dane).

    ``scope_confidence_threshold`` — minimalna pewność zakresu bloku, przy której cytat w bloku
    wystarcza bez symbolu w ``scope_quote`` (ten sam próg, poniżej którego tryb blokowy wymusza
    ręczną weryfikację). Tolerancja OCR: najwyżej ``ocr_max_edits`` edycji i nie więcej niż
    ``ocr_max_edit_ratio`` długości cytatu, cytat co najmniej ``ocr_min_quote_chars`` znaków,
    cyfry bez zmian; dopasowanie przybliżone mnoży pewność przez ``ocr_fuzzy_penalty``.
    """

    scope_confidence_threshold: float = 0.6
    ocr_max_edits: int = 6
    ocr_max_edit_ratio: float = 0.08
    ocr_min_quote_chars: int = 24
    ocr_fuzzy_penalty: float = 0.8
    term_lookback_chars: int = TERM_LOOKBACK_CHARS

    def __post_init__(self) -> None:
        if not 0.0 <= self.scope_confidence_threshold <= 1.0:
            raise ValueError("Próg pewności zakresu musi mieścić się w 0–1.")
        if self.ocr_max_edits < 0 or not 0.0 <= self.ocr_max_edit_ratio <= 0.2:
            raise ValueError("Tolerancja OCR musi być nieujemna i nie większa niż 20% cytatu.")
        if not 0.0 < self.ocr_fuzzy_penalty <= 1.0:
            raise ValueError("Kara za dopasowanie przybliżone musi mieścić się w (0, 1].")


@dataclass(frozen=True)
class CandidateProvenance:
    """Skąd pochodzi kandydat: model, prompt, schemat i skrót odpowiedzi (bez treści)."""

    model_id: str
    prompt_version: str
    schema_version: str
    response_sha256: str | None = None
    provider: str | None = None


@dataclass(frozen=True)
class DocumentContext:
    """Cechy dokumentu potrzebne bramkom: widok drzewa (strony), metoda tekstu, symbole planu."""

    view: DocumentStructureView | None = None
    extraction_method: str | None = None  # pdf_text | html | ocr
    ocr_quality: float | None = None
    zone_symbols: tuple[str, ...] = ()

    @property
    def is_ocr(self) -> bool:
        return self.extraction_method == "ocr"


@dataclass(frozen=True)
class VerifiedCondition:
    kind: str
    label: str
    quote: str


@dataclass(frozen=True)
class AcceptedCandidate:
    """Kandydat po bramkach G1–G8. Wartość i źródło są wyliczone z tekstu, nie z modelu."""

    zone_symbol: str
    parameter: str
    operator: str
    raw_value: str
    value: float
    unit: str | None
    normalization_rule: str
    flags: tuple[str, ...]
    conditions: tuple[VerifiedCondition, ...]
    applicability: str
    block_id: str
    scope_kind: str
    scope_confidence: float
    scope_strategy: int
    evidence_text: str  # dosłowny tekst bloku pod cytatem (nie cytat modelu)
    evidence_span: tuple[int, int]  # w tekście bloku
    value_span: tuple[int, int]  # ``raw_value`` w tekście bloku
    evidence_doc_spans: tuple[tuple[int, int], ...]  # w znormalizowanym tekście dokumentu
    value_doc_spans: tuple[tuple[int, int], ...]
    page_number: int | None
    quote_match: str  # exact | ocr_fuzzy
    quote_edit_distance: int
    confidence: float
    confidence_band: str
    confidence_calibration: str
    confidence_features: Mapping[str, Any]
    candidate_index: int
    provenance: CandidateProvenance | None = None
    review_status: str = REVIEW_STATUS
    extraction_method: str = EXTRACTION_METHOD_LLM_VERIFIED
    manual_review_required: bool = True
    verifier_version: str = VERIFIER_VERSION

    def __post_init__(self) -> None:
        # Twarda gwarancja kontraktu: wartość z modelu nigdy nie jest „verified”.
        if self.review_status != REVIEW_STATUS or self.extraction_method != EXTRACTION_METHOD_LLM_VERIFIED:
            raise ValueError("Kandydat modelu musi mieć status ai_candidate i metodę llm_verified.")
        if not self.manual_review_required:
            raise ValueError("Kandydat modelu zawsze wymaga ręcznej weryfikacji.")

    @property
    def condition_key(self) -> frozenset[tuple[str, str]]:
        return frozenset((c.kind, " ".join(c.quote.casefold().split())) for c in self.conditions)

    @property
    def dedupe_key(self) -> tuple[Any, ...]:
        return (
            self.zone_symbol,
            self.parameter,
            self.operator,
            round(self.value, 9),
            self.condition_key,
            self.block_id,
            self.evidence_span,
        )


@dataclass(frozen=True)
class RejectedLlmCandidate:
    """Odrzucenie z numerem bramki i kodem; bez treści cytatu i wartości (mogą być tekstem modelu)."""

    candidate_index: int
    gate: str
    code: str
    zone_symbol: str
    parameter: str
    applicability: str | None = None  # ``unresolved`` dla G7

    def __post_init__(self) -> None:
        if CODE_GATE.get(self.code) != self.gate:
            raise ValueError(f"Kod {self.code!r} nie należy do bramki {self.gate!r}.")


@dataclass(frozen=True)
class VerificationReport:
    """Wynik weryfikacji kandydatów jednego bloku albo dokumentu."""

    accepted: tuple[AcceptedCandidate, ...]
    rejected: tuple[RejectedLlmCandidate, ...]
    verifier_version: str = VERIFIER_VERSION

    @property
    def rejection_counts(self) -> dict[str, int]:
        """Liczniki odrzuceń per bramka (``G1``–``G8``) — do metryk."""
        counts = Counter(item.gate for item in self.rejected)
        return {gate: counts.get(gate, 0) for gate in GATES}

    @property
    def rejection_codes(self) -> dict[str, int]:
        return dict(sorted(Counter(item.code for item in self.rejected).items()))

    @property
    def unresolved(self) -> tuple[RejectedLlmCandidate, ...]:
        return tuple(item for item in self.rejected if item.code == CODE_SCOPE_UNRESOLVED)

    def merged(self, other: VerificationReport) -> VerificationReport:
        return VerificationReport(accepted=self.accepted + other.accepted, rejected=self.rejected + other.rejected)


class _Rejected(Exception):
    def __init__(self, gate: str, code: str, applicability: str | None = None) -> None:
        super().__init__(code)
        self.gate = gate
        self.code = code
        self.applicability = applicability


# --- dopasowanie cytatu ------------------------------------------------------------------------


@dataclass(frozen=True)
class QuoteMatch:
    start: int  # w tekście bloku
    end: int
    distance: int
    occurrences: int


def _normalized_span(origin: list[int], start: int, end: int) -> tuple[int, int]:
    """Zakres w znormalizowanym tekście → zakres w tekście oryginalnym."""
    return origin[start], (origin[end - 1] + 1) if end > start else origin[start]


def locate_exact(text: str, quote: str) -> QuoteMatch | None:
    needle = contract.normalize_text(quote)
    if not needle:
        return None
    haystack, origin = contract.normalize_with_map(text)
    first = haystack.find(needle)
    if first < 0:
        return None
    occurrences, position = 0, first
    while position >= 0:
        occurrences += 1
        position = haystack.find(needle, position + 1)
    start, end = _normalized_span(origin, first, first + len(needle))
    return QuoteMatch(start=start, end=end, distance=0, occurrences=occurrences)


def _edit_distance_window(window: str, needle: str, limit: int) -> tuple[int, int, int] | None:
    """Najlepsze dopasowanie ``needle`` jako podciągu ``window`` (algorytm Sellersa).

    Zwraca ``(odległość, początek, koniec)`` w ``window`` albo ``None``, gdy odległość > ``limit``.
    Przy równej odległości wybierane jest dopasowanie najwcześniej kończące się, a potem najdłuższe.
    """
    m = len(needle)
    # previous[i] = (koszt, początek) dla needle[:i] kończącego się w bieżącej pozycji okna.
    previous = [(i, 0) for i in range(m + 1)]
    best: tuple[int, int, int] | None = None
    for j, char in enumerate(window, start=1):
        current = [(0, j)]
        for i in range(1, m + 1):
            substitution = previous[i - 1][0] + (needle[i - 1] != char)
            deletion = previous[i][0] + 1  # znak okna nadmiarowy
            insertion = current[i - 1][0] + 1  # brak znaku cytatu w oknie
            cost = min(substitution, deletion, insertion)
            if cost == substitution:
                origin = previous[i - 1][1]
            elif cost == deletion:
                origin = previous[i][1]
            else:
                origin = current[i - 1][1]
            current.append((cost, origin))
        cost, start = current[m]
        if cost <= limit and (best is None or cost < best[0]):
            best = (cost, start, j)
        previous = current
    return best


def locate_fuzzy(text: str, quote: str, max_edits: int) -> QuoteMatch | None:
    """Dopasowanie przybliżone (OCR): najwyżej ``max_edits`` edycji, bez zmiany cyfr.

    Zasada szufladkowa zawęża poszukiwanie: przy ``k`` edycjach co najmniej jeden z ``k + 1``
    rozłącznych kawałków cytatu występuje w tekście dosłownie, więc odległość liczona jest tylko
    w oknach wokół takich wystąpień. Wynik jest deterministyczny.
    """
    needle = contract.normalize_text(quote)
    if not needle or max_edits <= 0:
        return None
    haystack, origin = contract.normalize_with_map(text)
    pieces = max_edits + 1
    size = len(needle) // pieces
    if size < 3:
        return None
    windows: set[tuple[int, int]] = set()
    for index in range(pieces):
        offset = index * size
        piece = needle[offset : offset + size]
        position = haystack.find(piece)
        while position >= 0:
            low = max(0, position - offset - max_edits)
            high = min(len(haystack), position - offset + len(needle) + max_edits)
            windows.add((low, high))
            position = haystack.find(piece, position + 1)
    best: tuple[int, int, int] | None = None
    for low, high in sorted(windows):
        found = _edit_distance_window(haystack[low:high], needle, max_edits)
        if found is None:
            continue
        distance, start, end = found[0], low + found[1], low + found[2]
        if best is None or (distance, start) < (best[0], best[1]):
            best = (distance, start, end)
    if best is None:
        return None
    distance, start, end = best
    matched = haystack[start:end]
    if _DIGITS.findall(matched) != _DIGITS.findall(needle):
        return None  # OCR może przekręcić literę, ale liczba musi zgadzać się co do cyfry
    block_start, block_end = _normalized_span(origin, start, end)
    return QuoteMatch(start=block_start, end=block_end, distance=distance, occurrences=1)


def _cut_mid_word(text: str, start: int, end: int) -> bool:
    def word(position: int) -> bool:
        return 0 <= position < len(text) and bool(_WORD_CHAR.match(text[position]))

    return (word(start - 1) and word(start)) or (word(end - 1) and word(end))


# --- wartość -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DerivedValue:
    value: float
    rule: str
    flags: tuple[str, ...]
    numbers: tuple[float, ...]


def _unit_at(text: str, position: int) -> tuple[str, str | None]:
    """Rodzaj jednostki zaczynającej się (po odstępach) w ``position`` i dosłowny znak stopnia."""
    index = position
    while index < len(text) and text[index] in "  \t":
        index += 1
    for kind, pattern in UNIT_REGEXES:
        match = pattern.match(text, index)
        if match:
            symbol = match.group(0) if kind == UNIT_DEGREE and len(match.group(0)) == 1 else None
            return kind, symbol
    if DEGREE_LETTER_PATTERN.match(text, index):
        return UNIT_DEGREE, "o"
    return UNIT_NONE, None


def derive_value(parameter: str, operator: str, raw_value: str, tail: str = "") -> DerivedValue:
    """Liczba z ``raw_value`` przeliczona tym samym kodem co w silniku ilości.

    ``tail`` to tekst bloku tuż za ``raw_value`` (jednostka ucięta z zapisu modelu). Wyjątek
    ``_Rejected`` niesie bramkę G5/G6. ``range_upper`` bierze drugą liczbę zapisu, pozostałe
    operatory pierwszą; brak cyfr w liczbie kondygnacji pozwala na liczebnik słowny.
    """
    family = _PARAMETER_FAMILY[parameter]
    spec = PARAMETER_FAMILIES[family]
    matches = list(_NUMBER_RE.finditer(raw_value))
    flags: list[str] = []
    if not matches:
        word = number_word_value(raw_value) if family == FAMILY_STOREYS else None
        if word is None:
            raise _Rejected(G5, CODE_RAW_NOT_NUMERIC)
        normalized = normalize_quantity(family, word, unit_kind=UNIT_NONE)
        if normalized is None:
            raise _Rejected(G6, CODE_DOMAIN_INVALID)
        return DerivedValue(normalized.value, "word_number", (FLAG_NUMBER_WORD, *normalized.flags), (word,))
    numbers = tuple(parse_polish_number(match.group(0)) for match in matches)
    if operator == "range_upper":
        if len(numbers) < 2:
            raise _Rejected(G5, CODE_RANGE_INCOMPLETE)
        chosen = matches[1]
    else:
        chosen = matches[0]
    if operator in {"range_lower", "range_upper"} and len(numbers) >= 2 and numbers[0] > numbers[1]:
        raise _Rejected(G6, CODE_RANGE_INVERTED)
    unit_kind, degree_symbol = _unit_at(raw_value, chosen.end())
    if unit_kind == UNIT_NONE and operator in {"range_lower", "range_upper"} and len(matches) >= 2:
        unit_kind, degree_symbol = _unit_at(raw_value, matches[-1].end())
    if unit_kind == UNIT_NONE and chosen is matches[-1]:
        unit_kind, degree_symbol = _unit_at(tail, 0) if tail else (UNIT_NONE, None)
    if unit_kind != UNIT_NONE and unit_kind not in spec.accepted_units:
        raise _Rejected(G5, CODE_UNIT_MISMATCH)
    if unit_kind == UNIT_NONE and UNIT_NONE not in spec.accepted_units:
        flags.append(FLAG_UNIT_IMPLIED)
    parenthesized = _PARENTHESIZED_PERCENT.search(raw_value, chosen.end())
    normalized = normalize_quantity(
        family,
        parse_polish_number(chosen.group(0)),
        unit_kind=unit_kind,
        raw_number=chosen.group(0),
        parenthesized_percent=parse_polish_number(parenthesized.group(1)) if parenthesized else None,
        degree_symbol=degree_symbol,
    )
    if normalized is None:
        raise _Rejected(G6, CODE_DOMAIN_INVALID)
    flags.extend(normalized.flags)
    ordered = tuple(dict.fromkeys(flags))
    if set(ordered) & ARTIFACT_FLAGS:
        rule = "manual"
    elif operator in {"range_lower", "range_upper"}:
        rule = operator
    elif "ratio_to_percent" in ordered:
        rule = "ratio_to_percent"
    else:
        rule = "identity"
    return DerivedValue(normalized.value, rule, ordered, numbers)


def _domain_rule(parameter: str, operator: str, value: float, source_text: str, raw: str) -> PlanningRuleCandidate:
    code, rule_operator, unit = numeric_rule_spec(parameter)
    return PlanningRuleCandidate(
        legal_unit_id=0,
        code=code,
        operator=rule_operator,
        parser_version=VERIFIER_VERSION,
        confidence=0.5,
        source_text=source_text,
        raw_value=raw,
        value=value,
        unit=unit,
        review_status="ai_candidate",
        extraction_method=EXTRACTION_METHOD_LLM_VERIFIED,
    )


# --- weryfikacja ---------------------------------------------------------------------------------


def _resolve_symbol(symbol: str, block: ZoneBlock) -> str | None:
    return next((candidate for candidate in block.symbols if same_zone_symbol(symbol, candidate)), None)


def _mentions(text: str, symbol: str, ocr: bool) -> bool:
    return bool(find_zone_symbol_mentions(text, symbol)) or (
        ocr and bool(find_zone_symbol_mentions(text, symbol, ocr_tolerant=True))
    )


def _locate_in_text(text: str, start: int, end: int, fragment: str) -> tuple[int, int] | None:
    """Pierwsze wystąpienie ``fragment`` (po zwinięciu białych znaków) w ``text[start:end]``."""
    found = contract.locate_quote(text[start:end], fragment)
    return (start + found[0], start + found[1]) if found else None


def _raw_truncated(text: str, start: int, end: int) -> bool:
    """Czy liczba w tekście bloku wychodzi poza zakres ``raw_value`` (ucięta wartość)."""
    low, high = max(0, start - 24), min(len(text), end + 24)
    for match in _NUMBER_RE.finditer(text, low, high):
        a, b = match.start(), match.end()
        if a < end and start < b and (a < start or b > end):
            return True
    return False


def _page(block: ZoneBlock, doc_spans: Sequence[tuple[int, int]], view: DocumentStructureView | None) -> int | None:
    if doc_spans and view is not None:
        return view.page_at(doc_spans[0][0])
    return block.pages[0] if len(block.pages) == 1 else None


def verify_candidate(
    candidate: LlmCandidate,
    block: ZoneBlock,
    *,
    index: int = 0,
    document: DocumentContext | None = None,
    policy: VerifierPolicy | None = None,
    provenance: CandidateProvenance | None = None,
) -> AcceptedCandidate | RejectedLlmCandidate:
    """Przepuszcza jednego kandydata przez bramki G1–G7 (G6 parami i G8 — w ``verify_candidates``)."""
    active = policy or VerifierPolicy()
    context = document or DocumentContext()
    try:
        return _verify(candidate, block, index, context, active, provenance)
    except _Rejected as rejection:
        return RejectedLlmCandidate(
            candidate_index=index,
            gate=rejection.gate,
            code=rejection.code,
            zone_symbol=candidate.zone_symbol[:60],
            parameter=str(candidate.parameter)[:60],
            applicability=rejection.applicability,
        )


def _verify(
    candidate: LlmCandidate,
    block: ZoneBlock,
    index: int,
    context: DocumentContext,
    policy: VerifierPolicy,
    provenance: CandidateProvenance | None,
) -> AcceptedCandidate:
    text = block.text
    # G1, G2 — katalog i operator.
    spec = contract.CATALOG.get(candidate.parameter)
    if spec is None or candidate.parameter not in _PARAMETER_FAMILY:
        raise _Rejected(G1, CODE_PARAMETER_NOT_IN_CATALOG)
    if candidate.operator not in spec.operators:
        raise _Rejected(G2, CODE_OPERATOR_NOT_ALLOWED)

    # G3 — cytat dowodu w bloku.
    if not contract.normalize_text(candidate.evidence_quote):
        raise _Rejected(G3, CODE_QUOTE_EMPTY)
    match = locate_exact(text, candidate.evidence_quote)
    if match is None and context.is_ocr:
        needle_length = len(contract.normalize_text(candidate.evidence_quote))
        if needle_length >= policy.ocr_min_quote_chars:
            budget = min(policy.ocr_max_edits, int(needle_length * policy.ocr_max_edit_ratio))
            match = locate_fuzzy(text, candidate.evidence_quote, budget)
    if match is None:
        raise _Rejected(G3, CODE_QUOTE_NOT_IN_BLOCK)
    matched_text = text[match.start : match.end]
    if _SUSPICIOUS_QUOTE.search(matched_text) or _SUSPICIOUS_QUOTE.search(candidate.evidence_quote):
        raise _Rejected(G3, CODE_QUOTE_SUSPICIOUS)
    if _cut_mid_word(text, match.start, match.end):
        raise _Rejected(G3, CODE_QUOTE_TRUNCATED)
    family = _PARAMETER_FAMILY[candidate.parameter]
    context_start = max(0, match.start - policy.term_lookback_chars)
    if not _TERMS[family].search(text, context_start, match.end):
        raise _Rejected(G3, CODE_QUOTE_LACKS_TERM)
    conditions: list[VerifiedCondition] = []
    for condition in candidate.conditions:
        located = contract.locate_quote(text, condition.quote) if contract.normalize_text(condition.quote) else None
        if located is None:
            raise _Rejected(G3, CODE_CONDITION_NOT_IN_BLOCK)
        quote_text = " ".join(text[located[0] : located[1]].split())
        conditions.append(VerifiedCondition(kind=condition.kind, label=quote_text[:120], quote=quote_text))

    # G4 — surowa wartość w cytacie i w dopasowanym tekście bloku, bez uciętych liczb.
    if not contract.normalize_text(candidate.raw_value):
        raise _Rejected(G4, CODE_RAW_EMPTY)
    if not contract.quote_contains(candidate.evidence_quote, candidate.raw_value):
        raise _Rejected(G4, CODE_RAW_NOT_IN_QUOTE)
    value_span = _locate_in_text(text, match.start, match.end, candidate.raw_value)
    if value_span is None:
        raise _Rejected(G4, CODE_RAW_NOT_IN_QUOTE)
    if _raw_truncated(text, *value_span):
        raise _Rejected(G4, CODE_RAW_TRUNCATED)

    # G5 — wartość przeliczona z zapisu w BLOKU (nie z cytatu modelu); liczba modelu nie nadpisuje.
    raw_in_block = " ".join(text[value_span[0] : value_span[1]].split())
    tail = text[value_span[1] : min(len(text), value_span[1] + 16)]
    tail = re.split(r"\d", tail, maxsplit=1)[0]
    derived = derive_value(candidate.parameter, candidate.operator, raw_in_block, tail)
    if candidate.value is not None and (
        not math.isfinite(candidate.value)
        or not math.isclose(candidate.value, derived.value, rel_tol=_VALUE_TOLERANCE, abs_tol=_VALUE_TOLERANCE)
    ):
        raise _Rejected(G5, CODE_VALUE_MISMATCH)

    # G6 — walidacja dziedzinowa (ta sama co dla reguł planistycznych).
    try:
        validate_planning_rule(
            _domain_rule(candidate.parameter, candidate.operator, derived.value, matched_text, raw_in_block)
        )
    except PlanningRuleValidationError:
        raise _Rejected(G6, CODE_DOMAIN_INVALID) from None

    # G7 — zakres strefy.
    symbol = _resolve_symbol(candidate.zone_symbol, block)
    if symbol is None:
        raise _Rejected(G7, CODE_SYMBOL_NOT_IN_BLOCK)
    others = [s for s in dict.fromkeys((*block.symbols, *context.zone_symbols)) if not same_zone_symbol(s, symbol)]
    if any(_mentions(matched_text, other, context.is_ocr) for other in others) and not _mentions(
        matched_text, symbol, context.is_ocr
    ):
        raise _Rejected(G7, CODE_QUOTE_NAMES_OTHER_ZONE)
    scope_span = (
        contract.locate_quote(text, candidate.scope_quote) if contract.normalize_text(candidate.scope_quote) else None
    )
    symbol_in_scope = scope_span is not None and _mentions(text[scope_span[0] : scope_span[1]], symbol, context.is_ocr)
    confident_block = block.scope_kind != "fallback" and block.scope_confidence >= policy.scope_confidence_threshold
    if not (symbol_in_scope or confident_block):
        raise _Rejected(G7, CODE_SCOPE_UNRESOLVED, applicability="unresolved")
    if block.scope_kind != "fallback":
        applicability = block.scope_kind
    elif candidate.applicability in {"zone_section", "general_clause", "residual_clause"}:
        applicability = candidate.applicability
    else:
        raise _Rejected(G7, CODE_SCOPE_UNRESOLVED, applicability="unresolved")

    # Źródło: wyłącznie z dopasowania w bloku.
    evidence_doc = block.locate(match.start, match.end)
    value_doc = block.locate(*value_span)
    flags = list(derived.flags)
    if match.distance:
        flags.append(FLAG_QUOTE_OCR_FUZZY)
    if match.occurrences > 1:
        flags.append(FLAG_QUOTE_REPEATED)
    features = evidence_confidence.ConfidenceFeatures(
        extraction_method=context.extraction_method,
        ocr_quality=context.ocr_quality,
        scope_strategy=block.strategy,
        scope_confidence=block.scope_confidence,
        scope_kind=block.scope_kind,
        quote_verified=True,
        flags=tuple(flags),
        value_kind="conditional" if conditions else "unconditional",
        origin=evidence_confidence.ORIGIN_LLM,
    )
    scored = evidence_confidence.score(features)
    confidence = scored.confidence * (policy.ocr_fuzzy_penalty if match.distance else 1.0)
    return AcceptedCandidate(
        zone_symbol=symbol,
        parameter=candidate.parameter,
        operator=candidate.operator,
        raw_value=raw_in_block,
        value=derived.value,
        unit=PARAMETER_UNIT[candidate.parameter],
        normalization_rule=derived.rule,
        flags=tuple(dict.fromkeys(flags)),
        conditions=tuple(conditions),
        applicability=applicability,
        block_id=block.block_id,
        scope_kind=block.scope_kind,
        scope_confidence=block.scope_confidence,
        scope_strategy=block.strategy,
        evidence_text=" ".join(matched_text.split()),
        evidence_span=(match.start, match.end),
        value_span=value_span,
        evidence_doc_spans=evidence_doc,
        value_doc_spans=value_doc,
        page_number=_page(block, value_doc or evidence_doc, context.view),
        quote_match="ocr_fuzzy" if match.distance else "exact",
        quote_edit_distance=match.distance,
        confidence=round(confidence, 4),
        confidence_band=evidence_confidence.band_for(confidence),
        confidence_calibration=scored.calibration_id,
        confidence_features=features.as_dict(),
        candidate_index=index,
        provenance=provenance,
    )


def _pair_violations(accepted: Sequence[AcceptedCandidate]) -> set[int]:
    """Indeksy kandydatów łamiących min ≤ max z innym kandydatem tej samej strefy i warunków (G6)."""
    broken: set[int] = set()
    for low_name, high_name in MIN_MAX_PAIRS:
        lows = [(i, item) for i, item in enumerate(accepted) if item.parameter == low_name]
        highs = [(i, item) for i, item in enumerate(accepted) if item.parameter == high_name]
        for i, low in lows:
            for j, high in highs:
                same_scope = low.zone_symbol == high.zone_symbol and low.condition_key == high.condition_key
                if same_scope and low.value > high.value:
                    broken.update((i, j))
    return broken


def verify_candidates(
    candidates: Iterable[LlmCandidate],
    block: ZoneBlock,
    *,
    document: DocumentContext | None = None,
    policy: VerifierPolicy | None = None,
    provenance: CandidateProvenance | None = None,
    start_index: int = 0,
) -> VerificationReport:
    """Weryfikuje kandydatów bloku: G1–G7 per kandydat, G6 parami, G8 deduplikacja."""
    accepted: list[AcceptedCandidate] = []
    rejected: list[RejectedLlmCandidate] = []
    for offset, candidate in enumerate(candidates):
        outcome = verify_candidate(
            candidate, block, index=start_index + offset, document=document, policy=policy, provenance=provenance
        )
        (accepted if isinstance(outcome, AcceptedCandidate) else rejected).append(outcome)  # type: ignore[arg-type]
    broken = _pair_violations(accepted)
    kept: list[AcceptedCandidate] = []
    for position, item in enumerate(accepted):
        if position in broken:
            rejected.append(_reject(item, G6, CODE_MIN_EXCEEDS_MAX))
        else:
            kept.append(item)
    seen: set[tuple[Any, ...]] = set()
    unique: list[AcceptedCandidate] = []
    for item in kept:
        if item.dedupe_key in seen:
            rejected.append(_reject(item, G8, CODE_DUPLICATE))
            continue
        seen.add(item.dedupe_key)
        unique.append(item)
    rejected.sort(key=lambda item: item.candidate_index)
    return VerificationReport(accepted=tuple(unique), rejected=tuple(rejected))


def _reject(item: AcceptedCandidate, gate: str, code: str) -> RejectedLlmCandidate:
    return RejectedLlmCandidate(
        candidate_index=item.candidate_index, gate=gate, code=code, zone_symbol=item.zone_symbol, parameter=item.parameter
    )


def deduplicate_across_blocks(report: VerificationReport) -> VerificationReport:
    """G8 dla kandydatów z kilku bloków strefy: ta sama wartość i warunki w tym samym miejscu dokumentu."""
    seen: set[tuple[Any, ...]] = set()
    unique: list[AcceptedCandidate] = []
    duplicates: list[RejectedLlmCandidate] = []
    for item in report.accepted:
        key = (item.zone_symbol, item.parameter, item.operator, round(item.value, 9), item.condition_key,
               item.evidence_doc_spans)
        if key in seen:
            duplicates.append(_reject(item, G8, CODE_DUPLICATE))
            continue
        seen.add(key)
        unique.append(item)
    return VerificationReport(accepted=tuple(unique), rejected=report.rejected + tuple(duplicates))


def with_provenance(report: VerificationReport, provenance: CandidateProvenance) -> VerificationReport:
    return replace(report, accepted=tuple(replace(item, provenance=provenance) for item in report.accepted))


__all__ = [
    "AcceptedCandidate",
    "ARTIFACT_FLAGS",
    "CODE_GATE",
    "CandidateProvenance",
    "DocumentContext",
    "EXTRACTION_METHOD_LLM_VERIFIED",
    "GATES",
    "GATE_CODES",
    "RejectedLlmCandidate",
    "VERIFIER_VERSION",
    "VerificationReport",
    "VerifierPolicy",
    "deduplicate_across_blocks",
    "derive_value",
    "locate_exact",
    "locate_fuzzy",
    "verify_candidate",
    "verify_candidates",
    "with_provenance",
]
