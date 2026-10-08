"""Lista kluczy podpisu tokenów dostępu z identyfikatorem ``kid`` (AU-012).

Format ``ACCESS_TOKEN_SECRETS``::

    kid1:sekret1,kid2:sekret2|2026-12-01

- pierwszy wpis jest kluczem **aktywnym**: tylko nim podpisujemy nowe tokeny;
- kolejne wpisy są kluczami **wycofywanymi**: służą wyłącznie do weryfikacji tokenów wydanych
  wcześniej. Przyrostek ``|RRRR-MM-DD`` (albo ``|RRRR-MM-DDTGG:MM:SS+00:00``) kończy okres
  przejściowy — dla samej daty klucz działa do końca tego dnia (UTC); bez przyrostka klucz działa,
  dopóki operator nie usunie wpisu z listy;
- ``kid`` to 1–32 znaków ``[A-Za-z0-9_-]`` (trafia do tokenu jako osobny segment), sekret nie może
  zawierać ``|`` ani być pusty. Komunikaty błędów nigdy nie powtarzają sekretu.

Moduł nie importuje ``settings``, żeby walidator ustawień mógł z niego korzystać bez cyklu importów.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

KID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
LEGACY_KID: Final[str] = "default"
"""``kid`` nadawany pojedynczemu ``ACCESS_TOKEN_SECRET`` (zgodność wstecz konfiguracji)."""
EPHEMERAL_KID: Final[str] = "ephemeral"
"""``kid`` losowego klucza procesu, gdy nie skonfigurowano żadnego sekretu."""


@dataclass(frozen=True)
class SigningKey:
    kid: str
    secret: bytes
    valid_until: int | None = None
    """Koniec okresu przejściowego (sekundy UNIX, wyłącznie); ``None`` = bez daty końca."""

    def is_usable_at(self, now: float) -> bool:
        return self.valid_until is None or now < self.valid_until


def _parse_until(raw: str, position: int) -> int:
    text = raw.strip()
    try:
        if len(text) == 10:  # sama data: do końca dnia UTC
            day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC)
            return int((day + timedelta(days=1)).timestamp())
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"ACCESS_TOKEN_SECRETS: wpis {position}: koniec okresu przejściowego musi mieć postać "
            "RRRR-MM-DD albo ISO 8601 z godziną."
        ) from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp())


def parse_token_secrets(raw: str) -> tuple[SigningKey, ...]:
    """Parsuje ``ACCESS_TOKEN_SECRETS``; pusty tekst daje pustą krotkę. Błąd = ``ValueError``."""
    keys: list[SigningKey] = []
    seen: set[str] = set()
    for position, entry in enumerate((part.strip() for part in raw.split(",")), start=1):
        if not entry:
            continue
        kid, separator, rest = entry.partition(":")
        kid = kid.strip()
        if not separator or not KID_PATTERN.fullmatch(kid):
            raise ValueError(
                f"ACCESS_TOKEN_SECRETS: wpis {position} musi mieć postać kid:sekret, "
                "gdzie kid to 1–32 znaków [A-Za-z0-9_-]."
            )
        if kid in seen:
            raise ValueError(f"ACCESS_TOKEN_SECRETS: powtórzony kid {kid!r}.")
        secret, pipe, until_raw = rest.partition("|")
        secret = secret.strip()
        if not secret or "|" in until_raw:
            raise ValueError(
                f"ACCESS_TOKEN_SECRETS: wpis {position} ({kid}) ma pusty sekret albo więcej niż jeden '|'."
            )
        valid_until = _parse_until(until_raw, position) if pipe else None
        if valid_until is not None and not keys:
            raise ValueError(
                "ACCESS_TOKEN_SECRETS: pierwszy wpis to klucz aktywny i nie może mieć daty końca; "
                "daty końca mają tylko klucze wycofywane."
            )
        seen.add(kid)
        keys.append(SigningKey(kid=kid, secret=secret.encode("utf-8"), valid_until=valid_until))
    return tuple(keys)
