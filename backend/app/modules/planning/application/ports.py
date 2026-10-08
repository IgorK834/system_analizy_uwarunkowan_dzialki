"""Port zapisu ustrukturyzowanych reguł planistycznych."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.modules.planning.domain.kimpzp_discovery import KimpzpPointResult
from app.modules.planning.domain.rules import PlanningRuleCandidate


class PlanningRuleRepository(Protocol):
    def replace_for_legal_units(
        self,
        legal_unit_ids: list[int],
        rules: list[PlanningRuleCandidate],
    ) -> list[PlanningRuleCandidate]:
        """Zastępuje reguły wyłącznie wskazanych jednostek prawnych."""


# --- ekstrakcja strukturalna modelem językowym (PV3-10) -----------------------------------
#
# Port zna wyłącznie kontrakt „instrukcja + tekst + schemat JSON → obiekt JSON”. Nie zna
# domeny MPZP (parametrów, stref, cytatów): to wiedza usługi aplikacyjnej
# (``llm_extraction``) i kontraktu domenowego (``extraction_contract``). Adapter leży w
# ``infrastructure/llm`` i nie importuje domeny planowania.


class StructuredExtractionErrorCode(StrEnum):
    """Powód niepowodzenia wywołania; ``retryable`` w błędzie mówi, czy adapter ponawiał."""

    CONFIGURATION = "configuration"
    REQUEST_TOO_LARGE = "request_too_large"
    CIRCUIT_OPEN = "circuit_open"
    BAD_REQUEST = "bad_request"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    MODEL_NOT_FOUND = "model_not_found"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    NETWORK = "network"
    UNEXPECTED_REDIRECT = "unexpected_redirect"
    BAD_RESPONSE = "bad_response"
    RESPONSE_TOO_LARGE = "response_too_large"
    INVALID_JSON = "invalid_json"
    SCHEMA_VIOLATION = "schema_violation"
    TRUNCATED = "truncated"
    BLOCKED = "blocked"
    REPLAY_MISS = "replay_miss"
    REPLAY_INTEGRITY = "replay_integrity"
    BUDGET_EXHAUSTED = "budget_exhausted"
    # PV3-15: limity i bezpieczna degradacja (żadne z nich nie wysyła żądania).
    DOCUMENT_BUDGET_EXHAUSTED = "document_budget_exhausted"
    REQUEST_TOKEN_LIMIT = "request_token_limit"
    DAILY_LIMIT = "daily_limit_reached"
    MONTHLY_LIMIT = "monthly_limit_reached"
    CONCURRENCY_LIMIT = "concurrency_limit"
    LOCAL_RATE_LIMIT = "local_rate_limit"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    USAGE_LEDGER_UNAVAILABLE = "usage_ledger_unavailable"
    # PV3-19: model, prompt albo schemat różnią się od przypiętej, ocenionej wersji (``model_pin.json``).
    PIN_MISMATCH = "pin_mismatch"


_SAFE_MESSAGES: dict[StructuredExtractionErrorCode, str] = {
    StructuredExtractionErrorCode.CONFIGURATION: "Niepoprawna konfiguracja dostawcy ekstrakcji.",
    StructuredExtractionErrorCode.REQUEST_TOO_LARGE: "Żądanie przekracza dopuszczalny rozmiar.",
    StructuredExtractionErrorCode.CIRCUIT_OPEN: "Wyłącznik awaryjny dostawcy jest otwarty; żądania wstrzymano.",
    StructuredExtractionErrorCode.BAD_REQUEST: "Dostawca odrzucił żądanie jako niepoprawne.",
    StructuredExtractionErrorCode.UNAUTHORIZED: "Dostawca odrzucił uwierzytelnienie (klucz).",
    StructuredExtractionErrorCode.FORBIDDEN: "Dostawca odmówił dostępu do zasobu.",
    StructuredExtractionErrorCode.MODEL_NOT_FOUND: "Dostawca nie zna skonfigurowanego modelu.",
    StructuredExtractionErrorCode.RATE_LIMITED: "Dostawca ograniczył liczbę żądań.",
    StructuredExtractionErrorCode.SERVER_ERROR: "Dostawca zgłosił błąd po swojej stronie.",
    StructuredExtractionErrorCode.TIMEOUT: "Przekroczono limit czasu odpowiedzi dostawcy.",
    StructuredExtractionErrorCode.NETWORK: "Błąd połączenia z dostawcą.",
    StructuredExtractionErrorCode.UNEXPECTED_REDIRECT: "Dostawca odpowiedział przekierowaniem; nie podążono za nim.",
    StructuredExtractionErrorCode.BAD_RESPONSE: "Nieoczekiwana odpowiedź dostawcy.",
    StructuredExtractionErrorCode.RESPONSE_TOO_LARGE: "Odpowiedź dostawcy przekracza dopuszczalny rozmiar.",
    StructuredExtractionErrorCode.INVALID_JSON: "Odpowiedź modelu nie jest poprawnym JSON.",
    StructuredExtractionErrorCode.SCHEMA_VIOLATION: "Odpowiedź modelu jest niezgodna ze schematem.",
    StructuredExtractionErrorCode.TRUNCATED: "Odpowiedź modelu została ucięta (limit tokenów).",
    StructuredExtractionErrorCode.BLOCKED: "Model zakończył odpowiedź bez treści (blokada lub inny powód).",
    StructuredExtractionErrorCode.REPLAY_MISS: "Brak zapisanej odpowiedzi dla tego żądania.",
    StructuredExtractionErrorCode.REPLAY_INTEGRITY: "Zapisana odpowiedź nie zgadza się ze skrótem.",
    StructuredExtractionErrorCode.BUDGET_EXHAUSTED: "Wyczerpano budżet wywołań modelu dla analizy; żądania nie wysłano.",
    StructuredExtractionErrorCode.DOCUMENT_BUDGET_EXHAUSTED: "Wyczerpano budżet modelu dla dokumentu; żądania nie wysłano.",
    StructuredExtractionErrorCode.REQUEST_TOKEN_LIMIT: "Żądanie przekracza limit tokenów wejścia; nie wysłano go.",
    StructuredExtractionErrorCode.DAILY_LIMIT: "Osiągnięto dobowy limit zużycia modelu; ścieżka modelu jest wstrzymana.",
    StructuredExtractionErrorCode.MONTHLY_LIMIT: "Osiągnięto miesięczny limit zużycia modelu; ścieżka modelu jest wstrzymana.",
    StructuredExtractionErrorCode.CONCURRENCY_LIMIT: "Osiągnięto limit równoległych wywołań modelu.",
    StructuredExtractionErrorCode.LOCAL_RATE_LIMIT: "Przekroczono lokalny limit częstotliwości wywołań modelu.",
    StructuredExtractionErrorCode.DEADLINE_EXCEEDED: "Przekroczono budżet czasu analizy dla ścieżki modelu.",
    StructuredExtractionErrorCode.USAGE_LEDGER_UNAVAILABLE: "Rejestr zużycia modelu jest niedostępny; żądania nie wysłano.",
    StructuredExtractionErrorCode.PIN_MISMATCH: "Model lub prompt różni się od przypiętej, ocenionej wersji; ścieżka modelu wstrzymana.",
}

# Dozwolone w opisie błędu wyłącznie krótkie, ustalone znaczniki (HTTP, status dostawcy, powód
# zakończenia, ścieżki schematu); nigdy treść żądania, odpowiedzi ani nagłówki.
_SAFE_DETAIL = re.compile(r"[A-Za-z0-9_.$\[\]:-]{0,120}")


class StructuredExtractionError(Exception):
    """Błąd wywołania modelu. Komunikat jest złożony z ustalonego tekstu i bezpiecznego znacznika.

    Nie niesie klucza API, nagłówków, treści żądania ani treści odpowiedzi — wyjątek może
    trafić do logów, a odpowiedź modelu zawiera dosłowne cytaty z dokumentu.
    """

    def __init__(
        self,
        code: StructuredExtractionErrorCode,
        *,
        detail: str = "",
        retryable: bool = False,
        status: int | None = None,
        attempts: int = 1,
        retry_after: float | None = None,
        violations: tuple[str, ...] = (),
    ) -> None:
        safe_detail = detail if _SAFE_DETAIL.fullmatch(detail) else ""
        message = f"{code.value}: {_SAFE_MESSAGES[code]}" + (f" ({safe_detail})" if safe_detail else "")
        super().__init__(message)
        self.code = code
        self.detail = safe_detail
        self.retryable = retryable
        self.status = status
        self.attempts = attempts
        self.retry_after = retry_after
        self.violations = tuple(item for item in violations if _SAFE_DETAIL.fullmatch(item))[:20]


@dataclass(frozen=True)
class StructuredExtractionRequest:
    """Żądanie: instrukcja systemowa (osobno), wiadomość z danymi i schemat odpowiedzi.

    ``prompt_version``, ``prompt_sha256`` i ``schema_version`` są etykietami provenance — adapter
    ich nie wysyła. Wiadomość z danymi zawiera wyłącznie tekst publicznego aktu (ADR-012 pkt 4).
    """

    system_instruction: str
    user_text: str
    response_schema: Mapping[str, Any]
    temperature: float = 0.0
    max_output_tokens: int | None = None
    prompt_version: str = ""
    prompt_sha256: str = ""
    schema_version: str = ""

    @property
    def input_sha256(self) -> str:
        return hashlib.sha256(self.user_text.encode("utf-8")).hexdigest()

    @property
    def request_sha256(self) -> str:
        canonical = json.dumps(
            {
                "system": self.system_instruction,
                "user": self.user_text,
                "schema": self.response_schema,
                "temperature": self.temperature,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StructuredExtractionResult:
    """Odpowiedź zgodna ze schematem żądania wraz z provenance wywołania (bez treści żądania)."""

    content: Mapping[str, Any]
    provider: str
    model_requested: str
    model_returned: str | None
    model_mismatch: bool
    response_sha256: str
    request_sha256: str
    input_sha256: str
    finish_reason: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    latency_ms: float = 0.0
    attempts: int = 1


@runtime_checkable
class StructuredExtractionProvider(Protocol):
    """Dostawca odpowiedzi strukturalnych (model językowy albo odtwarzanie z fixtures)."""

    provider_name: str
    model: str

    async def extract_structured(self, request: StructuredExtractionRequest) -> StructuredExtractionResult:
        """Jedno żądanie → obiekt JSON zgodny z ``request.response_schema`` albo ``StructuredExtractionError``."""


# --- cache i provenance wywołań modelu (PV3-13) -------------------------------------------------
#
# Rekord nie zawiera treści żądania (tekstu dokumentu, instrukcji, wiadomości z danymi) ani
# identyfikatora działki, analizy czy użytkownika — tylko skróty wejścia i wyjście modelu.

LlmExtractionStatus = str  # ``ok`` | ``rejected_schema`` | ``error``
LLM_EXTRACTION_STATUSES: tuple[str, ...] = ("ok", "rejected_schema", "error")


@dataclass(frozen=True)
class LlmExtractionRecord:
    """Zapis wywołań modelu dla jednego bloku strefy (klucz ``cache_key``)."""

    cache_key: str
    document_sha256: str
    block_sha256: str
    prompt_version: str
    schema_version: str
    model_id: str
    params_hash: str
    status: LlmExtractionStatus
    response: Mapping[str, Any] | None = None
    response_sha256: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    cost_estimate_usd: float | None = None
    error_code: str | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in LLM_EXTRACTION_STATUSES:
            raise ValueError(f"Nieznany status zapisu ekstrakcji: {self.status!r}.")


class LlmExtractionCache(Protocol):
    """Trwały cache odpowiedzi modelu; implementacja w ``infrastructure/llm/repository.py``."""

    def get(self, cache_key: str) -> LlmExtractionRecord | None:
        """Zapis w okresie retencji albo ``None`` (zapis przeterminowany nie jest trafieniem)."""

    def save(self, record: LlmExtractionRecord) -> LlmExtractionRecord:
        """Idempotentny zapis: ``ok`` w retencji nie jest nadpisywany; inny status albo zapis przeterminowany — tak."""

    def purge(self, *, older_than: datetime) -> int:
        """Usuwa zapisy starsze niż ``older_than``; zwraca liczbę usuniętych."""


# --- punktowe rozpoznanie aktów MPZP w KIMPZP (AU-004) -------------------------------------


@runtime_checkable
class KimpzpFeatureInfoParser(Protocol):
    """Zamienia surową odpowiedź GetFeatureInfo KIMPZP na wynik domenowy punktu.

    Implementacja (``infrastructure/kimpzp_feature_info.py``) nie podnosi wyjątków dla
    żadnego wejścia tekstowego: nierozpoznana odpowiedź ma status ``unknown``.
    """

    def __call__(self, text: str) -> KimpzpPointResult:
        """Wynik jednego punktu: status źródła, akty obecne w punkcie i ich zmiany."""
