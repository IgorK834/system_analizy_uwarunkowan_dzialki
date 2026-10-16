"""Pobieranie paczki powiatowej BDOT10k z dozwolonego hosta (bezpieczny, ograniczony rozmiarem odczyt strumieniowy).

Adres musi być ``https`` i wskazywać host z allowlisty wynikającej z katalogu źródeł; przekierowania nie są
śledzone (przekierowanie poza allowlistę byłoby ominięciem kontroli). Rozmiar jest ograniczony zarówno nagłówkiem
``Content-Length``, jak i licznikiem odczytanych bajtów. Plik trafia do katalogu przekazanego przez wołającego (osobny
katalog na pobranie), a SHA-256 jest liczony w trakcie zapisu.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

CHUNK_BYTES = 1024 * 1024


class Bdot10kDownloadError(RuntimeError):
    """Pobranie paczki się nie powiodło albo naruszyło limity (host, rozmiar, status)."""


@dataclass(frozen=True)
class DownloadedPackage:
    path: Path
    sha256: str
    size_bytes: int
    url: str


def package_url(template: str, teryt_powiat: str) -> str:
    """Adres paczki z szablonu katalogu: ``{woj}`` to dwie pierwsze cyfry TERYT, ``{powiat}`` — cztery."""
    return template.replace("{woj}", teryt_powiat[:2]).replace("{powiat}", teryt_powiat)


def allowed_hosts_from(urls: Iterable[str]) -> frozenset[str]:
    return frozenset(host for host in (urlsplit(url.replace("{woj}", "00").replace("{powiat}", "0000")).hostname for url in urls) if host)


def download_package(
    url: str,
    destination_dir: Path,
    *,
    allowed_hosts: frozenset[str],
    max_bytes: int,
    timeout_seconds: float,
    client: httpx.Client | None = None,
) -> DownloadedPackage:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in allowed_hosts:
        raise Bdot10kDownloadError(
            f"Adres paczki musi być https i wskazywać host z listy dozwolonych ({', '.join(sorted(allowed_hosts))})."
        )
    name = Path(parts.path).name
    if not name.lower().endswith(".zip"):
        raise Bdot10kDownloadError("Adres paczki nie wskazuje pliku ZIP.")
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / name
    owned = client is None
    active = client or httpx.Client(
        timeout=httpx.Timeout(timeout_seconds, connect=10.0), follow_redirects=False
    )
    digest = hashlib.sha256()
    size = 0
    try:
        with active.stream("GET", url) as response:
            if response.status_code != 200:
                raise Bdot10kDownloadError(
                    f"Serwer paczki zwrócił HTTP {response.status_code} (oczekiwano 200)."
                )
            declared = response.headers.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > max_bytes:
                raise Bdot10kDownloadError(
                    f"Paczka ma {declared} B, a limit to {max_bytes} B."
                )
            with open(target, "wb") as handle:
                for chunk in response.iter_bytes(CHUNK_BYTES):
                    size += len(chunk)
                    if size > max_bytes:
                        raise Bdot10kDownloadError(
                            f"Paczka przekroczyła limit {max_bytes} B w trakcie pobierania."
                        )
                    digest.update(chunk)
                    handle.write(chunk)
    except httpx.HTTPError as exc:
        # Komunikat wyjątku httpx zawiera adres — w błędzie zostaje sam typ.
        raise Bdot10kDownloadError(f"Błąd połączenia przy pobieraniu paczki ({type(exc).__name__}).") from exc
    except Bdot10kDownloadError:
        if target.exists():
            os.unlink(target)
        raise
    finally:
        if owned:
            active.close()
    if size == 0:
        os.unlink(target)
        raise Bdot10kDownloadError("Serwer zwrócił pustą paczkę.")
    return DownloadedPackage(path=target, sha256=digest.hexdigest(), size_bytes=size, url=url)
