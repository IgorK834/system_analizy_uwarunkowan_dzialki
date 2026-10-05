"""Kontrakt ekstrakcji parametrów MPZP modelem językowym (PV3-11, ADR-012/ADR-013).

Kontrakt jest zbudowany tak, żeby KAŻDEGO kandydata dało się zweryfikować bez ufania
modelowi: dosłowny cytat dowodu (``evidence_quote``), surowa wartość zapisana w tekście
(``raw_value`` musi leżeć w cytacie), cytat zakresu (``scope_quote``), jawne „nie znaleziono”
(``not_found``). Liczba (``value``) jest opcjonalna i ignorowana, gdy nie zgadza się z liczbą
wyliczoną deterministycznie z ``raw_value``.

Moduł nie wysyła niczego i nie zna dostawcy modelu: definiuje schemat odpowiedzi, treść
instrukcji (plik ``prompts/mpzp_extraction_v1.md``), szablon wiadomości z danymi i walidację
odpowiedzi z kodami przyczyn. ``PROMPT_VERSION`` i ``SCHEMA_VERSION`` to stałe; skróty
``PROMPT_SHA256`` i ``SCHEMA_SHA256`` wchodzą do provenance i do klucza cache odpowiedzi,
więc zmiana instrukcji albo schematu bez podniesienia wersji jest wykrywana przez testy
(``test_llm_extraction_contract``), a po podniesieniu wersji unieważnia zapisane odpowiedzi.

Wynik modelu NIGDY nie jest źródłem prawdy: kandydat ma status ``ai_candidate`` (nie ma tu
żadnego stanu „zweryfikowany”) i przechodzi weryfikację cytatu względem tekstu źródłowego
(Task 20.12) zanim trafi do wyniku analizy.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.modules.planning.domain.quantity_normalization import (
    QuantityNormalizationError,
    normalize_annotation_value,
)
from app.shared.zone_symbol import canonicalize_zone_symbol, same_zone_symbol

# Podniesienie którejkolwiek z wersji jest obowiązkowe przy zmianie treści instrukcji
# (``prompts/mpzp_extraction_v1.md``, ``USER_TEMPLATE``) albo schematu; przypięte skróty
# w ``tests/test_llm_extraction_contract.py`` wykrywają zmianę bez podniesienia wersji.
PROMPT_VERSION: Final[str] = "mpzp-extraction/1"
SCHEMA_VERSION: Final[str] = "mpzp-extraction-schema/1"

# Kandydat modelu to kandydat do ręcznej weryfikacji, nigdy „verified” (ReviewStatus w rules).
CANDIDATE_REVIEW_STATUS: Final[str] = "ai_candidate"

ParameterName = Literal[
    "max_building_height_m",
    "min_intensity",
    "max_intensity",
    "max_building_coverage_percent",
    "min_biologically_active_percent",
    "roof_angle_min_deg",
    "roof_angle_max_deg",
    "max_storeys",
    "setback_m",
]
OperatorName = Literal["max", "min", "range_lower", "range_upper", "exact"]
UnitName = Literal["m", "percent", "deg", "ratio", "count", "other"]
ApplicabilityName = Literal["zone_section", "general_clause", "residual_clause", "unresolved"]
ConditionKind = Literal["building_type", "roof_type", "subzone", "location", "other"]

PARAMETERS: Final[tuple[str, ...]] = get_args(ParameterName)
OPERATORS: Final[tuple[str, ...]] = get_args(OperatorName)
UNITS: Final[tuple[str, ...]] = get_args(UnitName)
APPLICABILITY: Final[tuple[str, ...]] = get_args(ApplicabilityName)
CONDITION_KINDS: Final[tuple[str, ...]] = get_args(ConditionKind)


@dataclass(frozen=True)
class ParameterSpec:
    """Definicja parametru katalogu BK-603 w kontrakcie: jednostka i dozwolone operatory."""

    unit: str
    operators: tuple[str, ...]


# Zgodne z ``catalog`` korpusu BK-603 (``mpzp_evaluation/manifest.json``): test pilnuje zgodności.
CATALOG: Final[Mapping[str, ParameterSpec]] = {
    "max_building_height_m": ParameterSpec("m", ("max",)),
    "min_intensity": ParameterSpec("ratio", ("min", "range_lower")),
    "max_intensity": ParameterSpec("ratio", ("max", "range_upper")),
    "max_building_coverage_percent": ParameterSpec("percent", ("max",)),
    "min_biologically_active_percent": ParameterSpec("percent", ("min",)),
    "roof_angle_min_deg": ParameterSpec("deg", ("min", "range_lower")),
    "roof_angle_max_deg": ParameterSpec("deg", ("max", "range_upper")),
    "max_storeys": ParameterSpec("count", ("max",)),
    "setback_m": ParameterSpec("m", ("exact",)),
}

# Ustawowa lista wskaźników (kontekst instrukcji): wskaźnik → parametr katalogu albo ``None``,
# gdy tego kontraktu nie obejmuje. Interpretacja „4 ustawowych” jest w ADR-013.
STATUTORY_INDICATORS: Final[tuple[tuple[str, str | None], ...]] = (
    ("intensity_min", "min_intensity"),
    ("intensity_max", "max_intensity"),
    ("building_coverage_max", "max_building_coverage_percent"),
    ("biologically_active_min", "min_biologically_active_percent"),
    ("building_height_max", "max_building_height_m"),
    ("roof_angle_min", "roof_angle_min_deg"),
    ("roof_angle_max", "roof_angle_max_deg"),
    ("storeys_max", "max_storeys"),
    ("building_line_distance", "setback_m"),
    ("parking_minimum", None),
    ("plot_area_minimum", None),
    ("retail_sales_area_maximum", None),
    ("building_coverage_min", None),
)

# --- schemat odpowiedzi (jedyny kontrakt wyjścia) ---------------------------------------


def _build_response_schema() -> dict[str, Any]:
    condition = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(CONDITION_KINDS)},
            "label": {"type": "string"},
            "quote": {"type": "string"},
        },
        "required": ["kind", "label", "quote"],
    }
    candidate = {
        "type": "object",
        "properties": {
            "zone_symbol": {"type": "string"},
            "parameter": {"type": "string", "enum": list(PARAMETERS)},
            "operator": {"type": "string", "enum": list(OPERATORS)},
            "raw_value": {"type": "string"},
            "value": {"type": ["number", "null"]},
            "unit": {"type": "string", "enum": list(UNITS)},
            "applicability": {"type": "string", "enum": list(APPLICABILITY)},
            "conditions": {"type": "array", "items": condition},
            "evidence_quote": {"type": "string"},
            "scope_quote": {"type": "string"},
        },
        "required": [
            "zone_symbol",
            "parameter",
            "operator",
            "raw_value",
            "value",
            "unit",
            "applicability",
            "conditions",
            "evidence_quote",
            "scope_quote",
        ],
    }
    not_found = {
        "type": "object",
        "properties": {
            "zone_symbol": {"type": "string"},
            "parameter": {"type": "string", "enum": list(PARAMETERS)},
        },
        "required": ["zone_symbol", "parameter"],
    }
    return {
        "type": "object",
        "properties": {
            "candidates": {"type": "array", "items": candidate},
            "not_found": {"type": "array", "items": not_found},
        },
        "required": ["candidates", "not_found"],
    }


RESPONSE_SCHEMA: Final[Mapping[str, Any]] = _build_response_schema()


def canonical_json(value: object) -> str:
    """Zapis kanoniczny używany do skrótów (zgodny z ``scripts/mpzp_eval_engines.canonical_json``)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


SCHEMA_SHA256: Final[str] = sha256_text(canonical_json(RESPONSE_SCHEMA))

# --- instrukcja i wiadomość z danymi ------------------------------------------------------

PROMPT_FILE: Final[Path] = Path(__file__).with_name("prompts") / "mpzp_extraction_v1.md"
DOCUMENT_BEGIN: Final[str] = "=====BEGIN DOCUMENT TEXT====="
DOCUMENT_END: Final[str] = "=====END DOCUMENT TEXT====="
# Wiadomość z danymi: nagłówek (symbole, ścieżka) i tekst między znacznikami. Instrukcja
# systemowa jest osobnym polem żądania, więc tekst dokumentu nie może jej zmienić.
USER_TEMPLATE: Final[str] = (
    "Zone symbols: {symbols}\n"
    "Heading path: {path}\n"
    "{part}"
    f"\n{DOCUMENT_BEGIN}\n"
    "{text}"
    f"\n{DOCUMENT_END}"
)
CONTEXT_GAP_MARKER: Final[str] = "[…]"


class PromptError(RuntimeError):
    """Plik instrukcji nie istnieje albo jest pusty."""


@lru_cache(maxsize=1)
def system_instruction() -> str:
    try:
        text = PROMPT_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptError(f"Nie można wczytać instrukcji {PROMPT_FILE.name}.") from exc
    text = text.replace("\r\n", "\n").strip("\n") + "\n"
    if len(text) < 200:
        raise PromptError("Instrukcja ekstrakcji jest pusta lub ucięta.")
    return text


def prompt_sha256() -> str:
    """Skrót wszystkiego, co instruuje model: instrukcja systemowa i szablon wiadomości."""
    return sha256_text(f"{system_instruction()}\n---\n{USER_TEMPLATE}")


class DocumentTextError(ValueError):
    """Tekst bloku nie nadaje się do wysłania (np. zawiera znacznik końca danych)."""


def render_user_text(
    *,
    symbols: Sequence[str],
    path: str,
    text: str,
    part: tuple[int, int] | None = None,
) -> str:
    """Wiadomość z danymi: symbole, ścieżka nagłówków i dosłowny tekst bloku.

    Zawiera wyłącznie tekst publicznego aktu i symbole stref; nie ma w niej identyfikatora
    działki, analizy ani użytkownika (ADR-012 pkt 4). Tekst zawierający znacznik danych jest
    odrzucany, bo mógłby zamknąć sekcję danych i podszyć się pod instrukcję.
    """
    if DOCUMENT_BEGIN in text or DOCUMENT_END in text:
        raise DocumentTextError("Tekst dokumentu zawiera znacznik sekcji danych.")
    cleaned_symbols = [canonicalize_zone_symbol(symbol) for symbol in symbols]
    if not cleaned_symbols or not all(cleaned_symbols):
        raise DocumentTextError("Brak symbolu strefy do ekstrakcji.")
    heading = " ".join(path.split()) or "(none)"
    part_line = f"Part: {part[0]} of {part[1]} (the fragment continues the same plan text)\n" if part else ""
    rendered = USER_TEMPLATE.format(symbols=", ".join(cleaned_symbols), path=heading, part=part_line, text=text)
    validate_user_text(rendered)
    return rendered


# Dozwolone pola wiadomości z danymi (PV3-16): nagłówek z symbolami stref, ścieżką nagłówków i numerem
# części oraz tekst bloku między znacznikami. Instrukcja systemowa (definicje katalogu) jest stała.
ALLOWED_HEADER_LINES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"Zone symbols: [^\n]{1,2000}"),
    re.compile(r"Heading path: [^\n]{1,2000}"),
    re.compile(r"Part: \d{1,3} of \d{1,3} \(the fragment continues the same plan text\)"),
)


def validate_user_text(user_text: str) -> None:
    """Wiadomość z danymi zawiera WYŁĄCZNIE dozwolone pola (lista w ``ALLOWED_HEADER_LINES``) i tekst bloku.

    Chroni przed dopisaniem do żądania czegokolwiek spoza kontraktu (np. identyfikatora działki,
    adresu, danych użytkownika, tokenu) — także przez przyszłą zmianę kodu: żądanie, które nie
    pasuje, nie jest wysyłane (``DocumentTextError``).
    """
    head, separator, rest = user_text.partition(f"\n{DOCUMENT_BEGIN}\n")
    body, end_separator, tail = rest.rpartition(f"\n{DOCUMENT_END}")
    if not separator or not end_separator or tail or DOCUMENT_BEGIN in body or DOCUMENT_END in body:
        raise DocumentTextError("Wiadomość z danymi nie ma postaci: nagłówek, znaczniki, tekst bloku.")
    # Szablon kończy nagłówek pustym wierszem przed znacznikiem danych (``USER_TEMPLATE``).
    if not head.endswith("\n"):
        raise DocumentTextError("Nagłówek wiadomości z danymi ma niedozwolony układ.")
    lines = head[:-1].split("\n")
    if not lines or not all(any(p.fullmatch(line) for p in ALLOWED_HEADER_LINES) for line in lines):
        raise DocumentTextError("Wiadomość z danymi zawiera pole spoza listy dozwolonych.")
    kinds = [next(i for i, p in enumerate(ALLOWED_HEADER_LINES) if p.fullmatch(line)) for line in lines]
    if kinds[:2] != [0, 1] or len(kinds) != len(set(kinds)):
        raise DocumentTextError("Nagłówek wiadomości z danymi ma niedozwolony układ.")


def extraction_cache_key(
    *,
    provider: str,
    model: str,
    temperature: float,
    user_text: str,
    prompt_version: str = PROMPT_VERSION,
    prompt_digest: str | None = None,
    schema_version: str = SCHEMA_VERSION,
) -> str:
    """Klucz odpowiedzi modelu: ten sam co w odtwarzaniu ewaluatora (``LlmRequest.cache_key``).

    Obejmuje dostawcę, model, wersje i skrót instrukcji, wersję schematu, temperaturę i skrót
    wysłanej wiadomości z danymi — zmiana któregokolwiek elementu daje inny klucz, więc zapisana
    odpowiedź przestaje pasować (nie ma cichego użycia odpowiedzi na inny prompt).
    """
    return sha256_text(
        canonical_json(
            {
                "provider": provider,
                "model": model,
                "prompt_version": prompt_version,
                "prompt_sha256": prompt_digest if prompt_digest is not None else prompt_sha256(),
                "schema_version": schema_version,
                "temperature": temperature,
                "input_sha256": sha256_text(user_text),
            }
        )
    )


# --- tekst i cytaty -----------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """Zwinięcie białych znaków (jak ``normalize_text`` ewaluatora i spike’u)."""
    return re.sub(r"\s+", " ", text).strip()


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """``normalize_text`` oraz indeks w ``text`` dla każdego znaku tekstu znormalizowanego."""
    chars: list[str] = []
    origin: list[int] = []
    pending_space = False
    space_index = 0
    for index, char in enumerate(text):
        if char.isspace():
            pending_space = bool(chars)
            if pending_space:
                space_index = index
            continue
        if pending_space:
            chars.append(" ")
            origin.append(space_index)
            pending_space = False
        chars.append(char)
        origin.append(index)
    return "".join(chars), origin


def locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """Zakres ``[start, end)`` pierwszego dosłownego wystąpienia cytatu w tekście (po zwinięciu
    białych znaków) albo ``None``. Nie poprawia literówek, myślników ani łamań wyrazów."""
    needle = normalize_text(quote)
    if not needle:
        return None
    haystack, origin = normalize_with_map(text)
    start = haystack.find(needle)
    if start < 0:
        return None
    return origin[start], origin[start + len(needle) - 1] + 1


def quote_contains(quote: str, fragment: str) -> bool:
    needle = normalize_text(fragment)
    return bool(needle) and needle in normalize_text(quote)


# --- kandydaci ------------------------------------------------------------------------------


class LlmCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: ConditionKind
    label: str
    quote: str


class LlmCandidate(BaseModel):
    """Jeden kandydat z odpowiedzi modelu — po walidacji schematu, przed weryfikacją cytatu."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    zone_symbol: str
    parameter: ParameterName
    operator: OperatorName
    raw_value: str
    value: float | None
    unit: UnitName
    applicability: ApplicabilityName
    conditions: list[LlmCondition] = Field(default_factory=list)
    evidence_quote: str
    scope_quote: str


class LlmNotFound(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    zone_symbol: str
    parameter: ParameterName


class ExtractionPayload(BaseModel):
    """Cała odpowiedź: dokładnie to, co opisuje ``RESPONSE_SCHEMA``."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    candidates: list[LlmCandidate]
    not_found: list[LlmNotFound]


# Kody przyczyn: odpowiedź (całość) albo pojedynczy kandydat.
CODE_NOT_OBJECT: Final[str] = "payload_not_object"
CODE_SCHEMA_VIOLATION: Final[str] = "schema_violation"
CODE_UNKNOWN_SYMBOL: Final[str] = "unknown_symbol"
CODE_OPERATOR_NOT_ALLOWED: Final[str] = "operator_not_allowed"
CODE_EMPTY_RAW_VALUE: Final[str] = "empty_raw_value"
CODE_EMPTY_EVIDENCE: Final[str] = "empty_evidence_quote"
CODE_RAW_NOT_IN_EVIDENCE: Final[str] = "raw_value_not_in_evidence"
CODE_RAW_NOT_NUMERIC: Final[str] = "raw_value_not_numeric"
CODE_RANGE_INCOMPLETE: Final[str] = "range_incomplete"
CODE_MISSING_SCOPE_QUOTE: Final[str] = "missing_scope_quote"
CODE_EMPTY_CONDITION_QUOTE: Final[str] = "empty_condition_quote"
CANDIDATE_REJECTION_CODES: Final[tuple[str, ...]] = (
    CODE_UNKNOWN_SYMBOL,
    CODE_OPERATOR_NOT_ALLOWED,
    CODE_EMPTY_RAW_VALUE,
    CODE_EMPTY_EVIDENCE,
    CODE_RAW_NOT_IN_EVIDENCE,
    CODE_RAW_NOT_NUMERIC,
    CODE_RANGE_INCOMPLETE,
    CODE_MISSING_SCOPE_QUOTE,
    CODE_EMPTY_CONDITION_QUOTE,
)


@dataclass(frozen=True)
class ContractViolation:
    """Miejsce i rodzaj naruszenia schematu — bez wartości z odpowiedzi (mogłyby zawierać tekst)."""

    path: str
    kind: str


class ExtractionContractError(ValueError):
    """Odpowiedź niezgodna ze schematem; ``code`` to powód odrzucenia całej odpowiedzi."""

    def __init__(self, code: str, violations: tuple[ContractViolation, ...] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.violations = violations


@dataclass(frozen=True)
class RejectedCandidate:
    """Kandydat odrzucony przez kontrakt; zapisujemy kod i adres, nie treść."""

    index: int
    code: str
    parameter: str
    zone_symbol: str


@dataclass(frozen=True)
class CandidateRecord:
    """Kandydat po sprawdzeniu kontraktu: symbol z listy żądanych i liczba wyliczona z ``raw_value``.

    ``derived_value`` pochodzi wyłącznie z ``raw_value`` (reguła ``identity`` albo zakres);
    ``value_ignored`` oznacza, że liczba z odpowiedzi modelu była niezgodna i nie jest używana.
    ``evidence_span`` / ``scope_span`` to zakresy w tekście bloku albo ``None``, gdy cytatu nie
    ma w tekście (taki kandydat nie przejdzie weryfikacji z Task 20.12).
    """

    candidate: LlmCandidate
    zone_symbol: str
    derived_value: float
    value_ignored: bool
    evidence_span: tuple[int, int] | None = None
    scope_span: tuple[int, int] | None = None
    chunk_index: int = 0
    review_status: str = CANDIDATE_REVIEW_STATUS

    @property
    def quote_located(self) -> bool:
        return self.evidence_span is not None

    @property
    def dedupe_key(self) -> tuple[Any, ...]:
        """Ten sam symbol, parametr, operator, wartość i ten sam cytat w bloku = ten sam kandydat."""
        quote = self.evidence_span if self.evidence_span is not None else normalize_text(self.candidate.evidence_quote)
        return (
            self.zone_symbol,
            self.candidate.parameter,
            self.candidate.operator,
            normalize_text(self.candidate.raw_value),
            quote,
        )


@dataclass(frozen=True)
class ParsedExtraction:
    candidates: tuple[CandidateRecord, ...]
    not_found: tuple[tuple[str, str], ...]
    rejected: tuple[RejectedCandidate, ...]


_VALUE_TOLERANCE: Final[float] = 1e-9


def derive_value(operator: str, raw_value: str) -> float | None:
    """Liczba wyliczona deterministycznie z ``raw_value``; ``None``, gdy w zapisie nie ma liczby.

    ``range_upper`` bierze drugą liczbę zakresu, pozostałe operatory pierwszą; ułamek→procent
    i inne przeliczenia należą do weryfikacji (Task 20.12), nie do kontraktu.
    """
    rule = {"range_lower": "range_lower", "range_upper": "range_upper"}.get(operator, "identity")
    try:
        return normalize_annotation_value(rule, raw_value)
    except QuantityNormalizationError:
        return None


def _violations(error: ValidationError) -> tuple[ContractViolation, ...]:
    items = []
    for item in error.errors(include_input=False, include_url=False, include_context=False)[:20]:
        path = "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in item["loc"])
        items.append(ContractViolation(path=path, kind=str(item["type"])))
    return tuple(items)


def _resolve_symbol(symbol: str, requested: Sequence[str]) -> str | None:
    for candidate in requested:
        if same_zone_symbol(symbol, candidate):
            return candidate
    return None


def _check_candidate(index: int, item: LlmCandidate, requested: Sequence[str]) -> CandidateRecord | RejectedCandidate:
    def reject(code: str) -> RejectedCandidate:
        return RejectedCandidate(index=index, code=code, parameter=item.parameter, zone_symbol=item.zone_symbol[:60])

    symbol = _resolve_symbol(item.zone_symbol, requested)
    if symbol is None:
        return reject(CODE_UNKNOWN_SYMBOL)
    if item.operator not in CATALOG[item.parameter].operators:
        return reject(CODE_OPERATOR_NOT_ALLOWED)
    if not normalize_text(item.raw_value):
        return reject(CODE_EMPTY_RAW_VALUE)
    if not normalize_text(item.evidence_quote):
        return reject(CODE_EMPTY_EVIDENCE)
    if not quote_contains(item.evidence_quote, item.raw_value):
        return reject(CODE_RAW_NOT_IN_EVIDENCE)
    if item.applicability != "unresolved" and not normalize_text(item.scope_quote):
        return reject(CODE_MISSING_SCOPE_QUOTE)
    if any(not normalize_text(condition.quote) for condition in item.conditions):
        return reject(CODE_EMPTY_CONDITION_QUOTE)
    derived = derive_value(item.operator, item.raw_value)
    if derived is None or not math.isfinite(derived):
        # range_upper bez drugiej liczby to niepełny zakres, a nie brak liczby w zapisie.
        incomplete = item.operator == "range_upper" and derive_value("range_lower", item.raw_value) is not None
        return reject(CODE_RANGE_INCOMPLETE if incomplete else CODE_RAW_NOT_NUMERIC)
    if item.value is None:
        ignored = False
    else:
        ignored = not math.isclose(item.value, derived, rel_tol=_VALUE_TOLERANCE, abs_tol=_VALUE_TOLERANCE)
    return CandidateRecord(candidate=item.model_copy(update={"zone_symbol": symbol}), zone_symbol=symbol,
                           derived_value=derived, value_ignored=ignored)


def parse_payload(payload: object, requested_symbols: Sequence[str]) -> ParsedExtraction:
    """Waliduje odpowiedź względem schematu i kontraktu.

    Niezgodność ze schematem odrzuca CAŁĄ odpowiedź (``ExtractionContractError`` z kodem i
    miejscami naruszeń). Kandydat poprawny składniowo, ale łamiący kontrakt (symbol spoza listy,
    operator niedozwolony dla parametru, ``raw_value`` poza cytatem, brak liczby…), jest odrzucany
    pojedynczo z kodem (``RejectedCandidate``); reszta odpowiedzi zostaje.
    """
    if not isinstance(payload, Mapping):
        raise ExtractionContractError(CODE_NOT_OBJECT)
    try:
        parsed = ExtractionPayload.model_validate(payload)
    except ValidationError as exc:
        raise ExtractionContractError(CODE_SCHEMA_VIOLATION, _violations(exc)) from None
    records: list[CandidateRecord] = []
    rejected: list[RejectedCandidate] = []
    for index, item in enumerate(parsed.candidates):
        outcome = _check_candidate(index, item, requested_symbols)
        if isinstance(outcome, RejectedCandidate):
            rejected.append(outcome)
        else:
            records.append(outcome)
    not_found: list[tuple[str, str]] = []
    for entry in parsed.not_found:
        symbol = _resolve_symbol(entry.zone_symbol, requested_symbols)
        if symbol is not None and (symbol, entry.parameter) not in not_found:
            not_found.append((symbol, entry.parameter))
    return ParsedExtraction(candidates=tuple(records), not_found=tuple(not_found), rejected=tuple(rejected))
