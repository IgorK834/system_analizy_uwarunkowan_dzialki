"""Jeden, bezpieczny domyślnie limiter zapytań w oknie stałym (in-process, AU-006).

Chroni publiczne endpointy przed nadużyciem. Liczba śledzonych kluczy jest
ograniczona (eksmisja najstarszego), aby uniknąć nieograniczonej kardynalności.
Zwraca informację o dozwoleniu oraz sugerowany czas Retry-After.

Wszystkie routery używają tej samej funkcji klucza (``client_key``) i zależności
``rate_limit``; każda polityka (analiza, kafle, geokodowanie, …) ma własny licznik
i próg z ``settings``. Klucz klienta to adres połączenia (``request.client``).
``X-Forwarded-For`` jest brany pod uwagę wyłącznie, gdy włączono
``RATE_LIMIT_TRUST_FORWARDED_FOR`` **i** adres połączenia należy do listy
``RATE_LIMIT_TRUSTED_PROXIES``; wtedy klientem jest wpis liczony od końca o
``TRUSTED_PROXY_COUNT`` — wcześniejsze wpisy mógł podać sam klient.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache

from fastapi import HTTPException, Query, Request

from app.core.settings import settings


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int


class FixedWindowRateLimiter:
    """Limiter w oknie stałym: max ``limit`` żądań na ``window_seconds`` na klucz."""

    def __init__(
        self, limit: int, window_seconds: float, max_tracked_keys: int = 10_000
    ) -> None:
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("limit i window_seconds muszą być dodatnie.")
        self._limit = limit
        self._window = window_seconds
        self._max_tracked_keys = max_tracked_keys
        # klucz -> (początek_okna, licznik)
        self._windows: OrderedDict[str, tuple[float, int]] = OrderedDict()

    def check(self, key: str, now: float | None = None) -> RateLimitDecision:
        current = now if now is not None else time.monotonic()
        window_start, count = self._windows.get(key, (current, 0))

        if current - window_start >= self._window:
            # Nowe okno.
            window_start, count = current, 0

        if count >= self._limit:
            retry_after = max(1, int(self._window - (current - window_start)) + 1)
            self._windows[key] = (window_start, count)
            self._windows.move_to_end(key)
            return RateLimitDecision(allowed=False, retry_after_seconds=retry_after)

        self._windows[key] = (window_start, count + 1)
        self._windows.move_to_end(key)
        self._evict_if_needed()
        return RateLimitDecision(allowed=True, retry_after_seconds=0)

    def _evict_if_needed(self) -> None:
        while len(self._windows) > self._max_tracked_keys:
            self._windows.popitem(last=False)

    def reset(self) -> None:
        self._windows.clear()


_LIMITERS: list[FixedWindowRateLimiter] = []
_WINDOW_SECONDS = 60.0
_IPV6_BUCKET_PREFIX = 64
logger = logging.getLogger(__name__)

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_trusted_proxies(raw: str) -> tuple[IpNetwork, ...]:
    """Lista adresów/sieci CIDR rozdzielona przecinkami; błędny wpis to ``ValueError``."""
    networks: list[IpNetwork] = []
    for entry in raw.split(","):
        item = entry.strip()
        if item:
            networks.append(ipaddress.ip_network(item, strict=False))
    return tuple(networks)


@lru_cache(maxsize=16)
def _trusted_networks(raw: str) -> tuple[IpNetwork, ...]:
    try:
        return parse_trusted_proxies(raw)
    except ValueError:
        # Błędna konfiguracja nigdy nie rozszerza zaufania (walidator ustawień
        # odrzuca ją już przy starcie; tu zostaje fail-closed).
        return ()


def _parse_ip(raw: str) -> IpAddress | None:
    value = raw.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _bucket(raw: str) -> str:
    """Klucz licznika: IPv4 w całości, IPv6 do prefiksu /64 (rotacja w obrębie /64 nic nie daje)."""
    address = _parse_ip(raw)
    if address is None:
        return raw.strip() or "unknown"
    if isinstance(address, ipaddress.IPv6Address):
        network = ipaddress.ip_network(f"{address}/{_IPV6_BUCKET_PREFIX}", strict=False)
        return f"{network.network_address}/{_IPV6_BUCKET_PREFIX}"
    return str(address)


def _is_trusted_proxy(peer: str) -> bool:
    address = _parse_ip(peer)
    if address is None:
        return False
    return any(
        address.version == network.version and address in network
        for network in _trusted_networks(settings.rate_limit_trusted_proxies)
    )


def _forwarded_client(request: Request) -> str | None:
    """Klient wg ``X-Forwarded-For``: wpis liczony od końca o ``TRUSTED_PROXY_COUNT``.

    Każdy zaufany proxy dopisuje adres swojego nadawcy, więc przy N proxy przed
    aplikacją klient jest N-tym wpisem od końca (a bezpośrednim peerem jest N-ty
    proxy). Za krótki łańcuch albo wpis niebędący adresem IP oznacza, że nagłówka
    nie da się uczciwie zinterpretować — wtedy używamy adresu połączenia.
    """
    # Puste elementy zostają na swoich pozycjach (nie są pomijane): pusty wpis na miejscu klienta
    # oznacza nagłówek, którego proxy nie dopisało, więc jest nieprawidłowy, a nie przesuwa indeksów.
    entries = [
        part.strip()
        for header in request.headers.getlist("x-forwarded-for")
        for part in header.split(",")
    ]
    count = settings.trusted_proxy_count
    if count < 1 or len(entries) < count:
        return None
    candidate = entries[-count]
    return candidate if _parse_ip(candidate) is not None else None


@lru_cache(maxsize=1)
def _warn_forwarded_for_without_proxies() -> None:
    logger.warning(
        "RATE_LIMIT_TRUST_FORWARDED_FOR=true, ale RATE_LIMIT_TRUSTED_PROXIES jest puste: "
        "X-Forwarded-For jest ignorowany, a limit liczony po adresie połączenia."
    )


def client_key(request: Request) -> str:
    """Klucz klienta dla wszystkich limiterów: adres połączenia albo klient z zaufanego proxy."""
    client = request.client
    peer = client.host if client is not None else None
    if settings.rate_limit_trust_forwarded_for:
        if not _trusted_networks(settings.rate_limit_trusted_proxies):
            _warn_forwarded_for_without_proxies()
        elif peer is not None and _is_trusted_proxy(peer):
            forwarded = _forwarded_client(request)
            if forwarded is not None:
                return _bucket(forwarded)
    return _bucket(peer) if peer is not None else "unknown"


def _enforce(limiter: FixedWindowRateLimiter, request: Request) -> None:
    if not settings.rate_limit_enabled:
        return
    decision = limiter.check(client_key(request))
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail="Przekroczono limit zapytań. Spróbuj ponownie za chwilę.",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )


def rate_limit(limit: int) -> Callable[[Request], Awaitable[None]]:
    """Zależność FastAPI: ``limit`` żądań na minutę na klienta (429 po przekroczeniu).

    Zależność jest ``async``, więc limiter działa wyłącznie w wątku pętli
    zdarzeń — ``FixedWindowRateLimiter`` nie jest thread-safe. Limiter jest
    dostępny jako atrybut ``limiter`` zależności (testy ustawiają próg i sprawdzają
    pokrycie tras).
    """
    limiter = FixedWindowRateLimiter(limit=limit, window_seconds=_WINDOW_SECONDS)
    _LIMITERS.append(limiter)

    async def dependency(request: Request) -> None:
        _enforce(limiter, request)

    dependency.limiter = limiter  # type: ignore[attr-defined]
    return dependency


def rate_limit_refresh(limit: int) -> Callable[[Request, bool], Awaitable[None]]:
    """Jak ``rate_limit``, ale liczy tylko żądania z ``force_refresh=true``."""
    limiter = FixedWindowRateLimiter(limit=limit, window_seconds=_WINDOW_SECONDS)
    _LIMITERS.append(limiter)

    async def dependency(
        request: Request,
        force_refresh: bool = Query(False, include_in_schema=False),
    ) -> None:
        if force_refresh:
            _enforce(limiter, request)

    dependency.limiter = limiter  # type: ignore[attr-defined]
    return dependency


def reset_all_rate_limiters() -> None:
    """Czyści liczniki wszystkich limiterów utworzonych przez ``rate_limit*``."""
    for limiter in _LIMITERS:
        limiter.reset()
