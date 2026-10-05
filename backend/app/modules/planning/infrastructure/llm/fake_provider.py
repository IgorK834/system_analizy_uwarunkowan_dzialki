"""Dostawca odtwarzający zapisane odpowiedzi (fixtures) — bez sieci i bez klucza (PV3-10).

Czyta katalog ``<klucz>.json`` w formacie ``ReplayStore`` ewaluatora (``scripts/mpzp_eval_engines``):
klucz to SHA-256 z (dostawca, model, wersja i skrót instrukcji, wersja schematu, temperatura,
skrót wiadomości z danymi). Zmiana instrukcji, schematu, modelu albo tekstu daje inny klucz, więc
brak zapisu kończy się ``replay_miss`` — nigdy cichym użyciem odpowiedzi na inne wejście ani
próbą połączenia. Odpowiedź przechodzi tę samą walidację schematu co odpowiedź z sieci.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from app.modules.planning.application.ports import (
    StructuredExtractionError,
    StructuredExtractionErrorCode,
    StructuredExtractionRequest,
    StructuredExtractionResult,
)
from app.modules.planning.infrastructure.llm.json_schema import validate_json_schema

PROVIDER_NAME: Final[str] = "fake"
# Pod tą nazwą zapisano odpowiedzi, które odtwarzamy (klucz zawiera nazwę dostawcy nagrania).
RECORDED_PROVIDER: Final[str] = "gemini"
_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ReplayStructuredExtractionProvider:
    """Port ``StructuredExtractionProvider`` oparty o katalog zapisanych odpowiedzi."""

    provider_name = PROVIDER_NAME

    def __init__(self, directory: Path | str, *, model: str, recorded_provider: str = RECORDED_PROVIDER) -> None:
        self.directory = Path(directory)
        self.model = model
        self.recorded_provider = recorded_provider
        self.calls = 0

    def cache_key(self, request: StructuredExtractionRequest) -> str:
        return _sha256(
            canonical_json(
                {
                    "provider": self.recorded_provider,
                    "model": self.model,
                    "prompt_version": request.prompt_version,
                    "prompt_sha256": request.prompt_sha256,
                    "schema_version": request.schema_version,
                    "temperature": request.temperature,
                    "input_sha256": request.input_sha256,
                }
            )
        )

    def has_response(self, request: StructuredExtractionRequest) -> bool:
        return (self.directory / f"{self.cache_key(request)}.json").is_file()

    async def extract_structured(self, request: StructuredExtractionRequest) -> StructuredExtractionResult:
        self.calls += 1
        key = self.cache_key(request)
        path = self.directory / f"{key}.json"
        if not _KEY_PATTERN.fullmatch(key) or not path.is_file():
            raise StructuredExtractionError(StructuredExtractionErrorCode.REPLAY_MISS, detail=key[:16])
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            response: Mapping[str, Any] = record["response"]
            content = response["content"]
        except (OSError, ValueError, KeyError, TypeError):
            raise StructuredExtractionError(StructuredExtractionErrorCode.REPLAY_INTEGRITY, detail=key[:16]) from None
        digest = _sha256(canonical_json(content))
        if record.get("cache_key") != key or record.get("response_sha256") != digest:
            raise StructuredExtractionError(StructuredExtractionErrorCode.REPLAY_INTEGRITY, detail=key[:16])
        violations = validate_json_schema(content, request.response_schema)
        if violations:
            raise StructuredExtractionError(
                StructuredExtractionErrorCode.SCHEMA_VIOLATION,
                violations=tuple(str(item) for item in violations),
            )
        returned = response.get("model_returned")
        returned_model = returned if isinstance(returned, str) and returned else None
        return StructuredExtractionResult(
            content=content,
            provider=PROVIDER_NAME,
            model_requested=self.model,
            model_returned=returned_model,
            model_mismatch=returned_model is not None and returned_model != self.model,
            response_sha256=digest,
            request_sha256=request.request_sha256,
            input_sha256=request.input_sha256,
            finish_reason=str(response.get("finish_reason") or "STOP"),
            input_tokens=_count(response.get("input_tokens")),
            output_tokens=_count(response.get("output_tokens")),
            thinking_tokens=None,
            latency_ms=0.0,
            attempts=1,
        )


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
