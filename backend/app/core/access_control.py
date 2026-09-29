"""Kontrola dostępu: tokeny analiz (odczyt) i klucze administracyjne (zapis).

Identyfikatory analiz są kolejnymi liczbami, więc sam ``analysis_id`` nie może
wystarczać do pobrania raportu ani dokumentu — inaczej każdy mógłby wyliczyć
cudze analizy. Token analizy to HMAC-SHA256 identyfikatora, wyliczany bez
dodatkowego stanu w bazie: dostaje go klient, który sam uruchomił lub odczytał
analizę (pole ``access_token`` odpowiedzi).

Endpointy administracyjne (akceptacja rastrów) wymagają klucza z nagłówka
``X-Admin-Key``. Klucz jest przypisany do operatora, więc ślad audytu nie
opiera się na polu wpisanym przez wywołującego. Brak skonfigurowanych kluczy
wyłącza te endpointy (fail closed).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Path, Query

from app.core.settings import settings

logger = logging.getLogger(__name__)

_TOKEN_CONTEXT = b"analysis-access:v1:"


@lru_cache(maxsize=1)
def _signing_key() -> bytes:
    configured = settings.access_token_secret.strip()
    if configured:
        return configured.encode("utf-8")
    # Bez sekretu tokeny są ważne tylko do restartu procesu (i tylko w jednym
    # workerze). To bezpieczne, ale niewygodne — produkcja ustawia sekret.
    logger.warning(
        "ACCESS_TOKEN_SECRET nie jest ustawiony; użyto losowego klucza procesu "
        "— tokeny dostępu do raportów przestaną działać po restarcie."
    )
    return secrets.token_bytes(32)


def make_analysis_token(analysis_id: int) -> str:
    """Token dostępu do zasobów analizy o podanym identyfikatorze."""
    digest = hmac.new(
        _signing_key(), _TOKEN_CONTEXT + str(analysis_id).encode("ascii"), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_analysis_token(analysis_id: int, token: str | None) -> bool:
    if not token:
        return False
    # Porównanie na bajtach: ``compare_digest`` na ``str`` rzuca TypeError dla
    # znaków spoza ASCII, co dawałoby 500 zamiast 403.
    return hmac.compare_digest(
        make_analysis_token(analysis_id).encode("ascii"), token.encode("utf-8", errors="replace")
    )


def require_analysis_token(
    analysis_id: Annotated[int, Path(gt=0)],
    access_token: Annotated[
        str | None,
        Query(description="Token dostępu z pola ``access_token`` odpowiedzi analizy."),
    ] = None,
) -> None:
    """Zależność: 403, gdy token nie pasuje do identyfikatora analizy.

    Sprawdzenie poprzedza odczyt bazy, więc odpowiedź nie zdradza, czy analiza o
    danym identyfikatorze istnieje.
    """
    if not verify_analysis_token(analysis_id, access_token):
        raise HTTPException(status_code=403, detail="Brak dostępu do tej analizy.")


def _admin_keys() -> dict[str, str]:
    """Mapa operator → klucz z ``ADMIN_API_KEYS`` (``operator:klucz,operator2:klucz2``)."""
    keys: dict[str, str] = {}
    for entry in settings.admin_api_keys.split(","):
        operator, separator, key = entry.strip().partition(":")
        if separator and operator.strip() and key.strip():
            keys[operator.strip()] = key.strip()
    return keys


def require_admin_operator(
    x_admin_key: Annotated[str | None, Header()] = None,
) -> str:
    """Zależność: zwraca operatora uwierzytelnionego kluczem ``X-Admin-Key``."""
    keys = _admin_keys()
    if not keys:
        raise HTTPException(
            status_code=403,
            detail="Endpointy administracyjne są wyłączone (brak ADMIN_API_KEYS).",
        )
    matched: str | None = None
    presented = (x_admin_key or "").encode("utf-8", errors="replace")
    # Porównujemy ze wszystkimi kluczami w stałym czasie, bez wcześniejszego wyjścia.
    for operator, key in keys.items():
        if hmac.compare_digest(presented, key.encode("utf-8")):
            matched = operator
    if matched is None:
        raise HTTPException(
            status_code=401,
            detail="Nieprawidłowy lub brakujący klucz administracyjny.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return matched


AdminOperator = Annotated[str, Depends(require_admin_operator)]
