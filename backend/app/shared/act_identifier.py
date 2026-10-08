"""Identyfikator aktu planistycznego tworzony z danych zewnętrznych (AU-001).

Dokument uchwały bez znanego identyfikatora planu dostaje identyfikator ``mpzp-document:<url>``, a
adres dokumentu pochodzi ze źródła, nad którym aplikacja nie ma kontroli. ``planning_acts.act_identifier``
to ``VARCHAR(200)`` z unikalnością, więc przycięcie zmieniłoby tożsamość aktu, a nadmiar kończył analizę
błędem zapisu. Zamiast tego zbyt długi identyfikator jest skracany deterministycznie do postaci
``<początek>#<skrót SHA-256>``: czytelny prefiks zostaje, a unikalność zapewnia skrót całej wartości.

Wartości mieszczące się w limicie nie zmieniają się, więc zapisane wcześniej akty zachowują identyfikatory.
Ta sama funkcja obsługuje wszystkie miejsca, które tworzą identyfikator (wstrzymanie analizy, wznowienie
i analiza best-effort) — muszą dawać tę samą wartość dla tego samego dokumentu.
"""

from __future__ import annotations

import hashlib
from typing import Final

ACT_IDENTIFIER_MAX_LENGTH: Final[int] = 200  # ``planning_acts.act_identifier``
_DIGEST_LENGTH: Final[int] = 24
_DOCUMENT_PREFIX: Final[str] = "mpzp-document:"


def bounded_act_identifier(plan_id: str | None, document_url: str | None) -> str:
    """``plan_id`` albo ``mpzp-document:<url>``, skrócony do ``ACT_IDENTIFIER_MAX_LENGTH`` znaków."""
    identifier = plan_id or f"{_DOCUMENT_PREFIX}{document_url}"
    if len(identifier) <= ACT_IDENTIFIER_MAX_LENGTH:
        return identifier
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]
    head = ACT_IDENTIFIER_MAX_LENGTH - _DIGEST_LENGTH - 1
    return f"{identifier[:head]}#{digest}"
