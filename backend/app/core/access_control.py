"""Kontrola dostępu: tokeny analiz (odczyt) i klucze administracyjne (zapis).

Identyfikatory analiz są kolejnymi liczbami, więc sam ``analysis_id`` nie może
wystarczać do pobrania raportu ani dokumentu — inaczej każdy mógłby wyliczyć
cudze analizy. Token analizy (AU-012) ma postać ``v2.<exp>.<kid>.<sig>``:

- ``exp`` — koniec ważności (sekundy UNIX); po nim token daje 403;
- ``kid`` — identyfikator klucza z ``ACCESS_TOKEN_SECRETS`` (rotacja bez unieważniania linków:
  nowe tokeny podpisuje pierwszy klucz, stare weryfikują także klucze wycofywane);
- ``sig`` — HMAC-SHA256 po ``analysis_id|exp`` (base64url bez dopełnienia).

Token jest wyliczany bez dodatkowego stanu w bazie: dostaje go klient, który sam uruchomił lub
odczytał analizę (pole ``access_token`` odpowiedzi), albo który wydał go ``POST /analyze/{id}/links``.
Token może przyjść w nagłówku ``X-Analysis-Token`` (zalecane — nie trafia do logów ani ``Referer``)
albo w parametrze ``access_token`` (linki do pobrania; wartość w logach dostępu jest maskowana).

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
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Final, Literal

from fastapi import Depends, Header, HTTPException, Path, Query

from app.core.access_keys import (
    EPHEMERAL_KID,
    KID_PATTERN,
    LEGACY_KID,
    SigningKey,
    parse_token_secrets,
)
from app.core.settings import settings

logger = logging.getLogger(__name__)

TOKEN_VERSION: Final[str] = "v2"
_TOKEN_CONTEXT: Final[bytes] = b"analysis-access:v2:"
_SIGNATURE_LENGTH: Final[int] = 43  # base64url(SHA-256) bez dopełnienia
_MAX_EXP_DIGITS: Final[int] = 12

LinkPurpose = Literal["download", "share"]
"""``download`` — bezpośrednie pobranie (krótko); ``share`` — link „Udostępnij” (długo)."""


def _now() -> float:
    """Bieżący czas UNIX; osobna funkcja, żeby testy mogły przesuwać zegar."""
    return time.time()


@lru_cache(maxsize=1)
def _keyring() -> tuple[SigningKey, ...]:
    """Klucze podpisu: pierwszy jest aktywny, reszta tylko weryfikuje."""
    keys = parse_token_secrets(settings.access_token_secrets)
    if keys:
        return keys
    legacy = settings.access_token_secret.strip()
    if legacy:
        return (SigningKey(kid=LEGACY_KID, secret=legacy.encode("utf-8")),)
    # Bez sekretu tokeny są ważne tylko do restartu procesu (i tylko w jednym
    # workerze). To bezpieczne, ale niewygodne — produkcja ustawia sekret.
    logger.warning(
        "ACCESS_TOKEN_SECRETS nie jest ustawiony; użyto losowego klucza procesu "
        "— tokeny dostępu do raportów przestaną działać po restarcie."
    )
    return (SigningKey(kid=EPHEMERAL_KID, secret=secrets.token_bytes(32)),)


def seconds_until(expires_at: int) -> int:
    """Pozostałe sekundy do ``expires_at`` (nie mniej niż 0)."""
    return max(0, expires_at - int(_now()))


def reset_key_cache() -> None:
    """Wymusza ponowne odczytanie kluczy z ustawień (testy, zmiana konfiguracji w procesie)."""
    _keyring.cache_clear()


def _signing_key() -> SigningKey:
    """Klucz aktywny — jedyny, którym podpisujemy nowe tokeny."""
    return _keyring()[0]


def _signature(key: SigningKey, analysis_id: int, exp: int) -> str:
    payload = _TOKEN_CONTEXT + f"{analysis_id}|{exp}".encode("ascii")
    digest = hmac.new(key.secret, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def make_analysis_token(
    analysis_id: int,
    *,
    ttl_seconds: int | None = None,
    expires_at: int | None = None,
) -> str:
    """Token dostępu do zasobów analizy; domyślnie ważny ``ACCESS_TOKEN_TTL_SECONDS``.

    ``expires_at`` (sekundy UNIX) ma pierwszeństwo przed ``ttl_seconds`` — używa go endpoint linków,
    żeby wydany token nie przeżył tokenu, na podstawie którego powstał.
    """
    if expires_at is None:
        ttl = settings.access_token_ttl_seconds if ttl_seconds is None else ttl_seconds
        expires_at = int(_now()) + ttl
    key = _signing_key()
    return f"{TOKEN_VERSION}.{expires_at}.{key.kid}.{_signature(key, analysis_id, expires_at)}"


@dataclass(frozen=True)
class TokenVerdict:
    """Wynik weryfikacji tokenu; ``reason`` służy diagnostyce (nie trafia do odpowiedzi HTTP)."""

    valid: bool
    expires_at: int | None
    reason: str
    """``ok`` | ``missing`` | ``malformed`` | ``unknown_kid`` | ``key_retired`` | ``bad_signature`` | ``expired``."""


def _invalid(reason: str) -> TokenVerdict:
    return TokenVerdict(False, None, reason)


def check_analysis_token(analysis_id: int, token: str | None) -> TokenVerdict:
    """Sprawdza podpis, klucz (``kid``) i termin ważności tokenu dla danej analizy."""
    if not token:
        return _invalid("missing")
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != TOKEN_VERSION:
        return _invalid("malformed")
    _, exp_raw, kid, signature = parts
    if (
        not (exp_raw.isascii() and exp_raw.isdigit())
        or len(exp_raw) > _MAX_EXP_DIGITS
        or not KID_PATTERN.fullmatch(kid)
        or len(signature) != _SIGNATURE_LENGTH
    ):
        return _invalid("malformed")
    key = next((candidate for candidate in _keyring() if candidate.kid == kid), None)
    if key is None:
        return _invalid("unknown_kid")
    exp = int(exp_raw)
    # Porównanie na bajtach: ``compare_digest`` na ``str`` rzuca TypeError dla
    # znaków spoza ASCII, co dawałoby 500 zamiast 403.
    if not hmac.compare_digest(
        _signature(key, analysis_id, exp).encode("ascii"),
        signature.encode("utf-8", errors="replace"),
    ):
        return _invalid("bad_signature")
    now = _now()
    if not key.is_usable_at(now):
        return _invalid("key_retired")
    if exp <= now:
        return _invalid("expired")
    return TokenVerdict(True, exp, "ok")


def verify_analysis_token(analysis_id: int, token: str | None) -> bool:
    return check_analysis_token(analysis_id, token).valid


ANALYSIS_ACCESS_DENIED_DETAIL = "Brak dostępu do tej analizy."
"""Jedyna treść odpowiedzi 403: identyczna dla analizy istniejącej i nieistniejącej."""

ANALYSIS_TOKEN_HEADER = "X-Analysis-Token"


def ensure_analysis_access(analysis_id: int, *presented: str | None) -> int:
    """403, gdy żaden z przedstawionych tokenów nie pasuje do analizy; zwraca ``exp`` najdłuższego.

    Wszystkie kandydaty są weryfikowane bez wcześniejszego wyjścia, a treść błędu
    jest stała — brak tokenu, token błędny, wygasły i token innej analizy wyglądają tak samo.
    """
    best: int | None = None
    for token in presented:
        verdict = check_analysis_token(analysis_id, token)
        if verdict.valid and verdict.expires_at is not None:
            best = verdict.expires_at if best is None else max(best, verdict.expires_at)
        elif token:
            logger.info("Odrzucono token dostępu do analizy %s: %s", analysis_id, verdict.reason)
    if best is None:
        raise HTTPException(status_code=403, detail=ANALYSIS_ACCESS_DENIED_DETAIL)
    return best


def require_analysis_token(
    analysis_id: Annotated[int, Path(gt=0)],
    access_token: Annotated[
        str | None,
        Query(
            description=(
                "Token dostępu z pola ``access_token`` odpowiedzi analizy albo wydany przez "
                "``POST /analyze/{id}/links`` (wartość w logach dostępu jest maskowana)."
            ),
        ),
    ] = None,
    x_analysis_token: Annotated[
        str | None,
        Header(
            alias=ANALYSIS_TOKEN_HEADER,
            description="Token dostępu w nagłówku (zalecane: nie trafia do logów ani ``Referer``).",
        ),
    ] = None,
) -> int:
    """Zależność: 403, gdy token nie pasuje do identyfikatora analizy; zwraca jego ``exp``.

    Sprawdzenie poprzedza odczyt bazy, więc odpowiedź nie zdradza, czy analiza o
    danym identyfikatorze istnieje.
    """
    return ensure_analysis_access(analysis_id, access_token, x_analysis_token)


def issue_link_token(
    analysis_id: int, purpose: LinkPurpose, *, presented_expires_at: int
) -> tuple[str, int]:
    """Token do linku: ``download`` (domyślnie 15 min) albo ``share`` (domyślnie 30 dni).

    Termin nie przekracza ważności tokenu przedstawionego przy wydaniu, więc krótki token nie
    zamienia się w długi (brak eskalacji); zwraca token i jego ``exp``.
    """
    ttl = (
        settings.access_token_download_ttl_seconds
        if purpose == "download"
        else settings.access_token_ttl_seconds
    )
    expires_at = min(int(_now()) + ttl, presented_expires_at)
    return make_analysis_token(analysis_id, expires_at=expires_at), expires_at


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
