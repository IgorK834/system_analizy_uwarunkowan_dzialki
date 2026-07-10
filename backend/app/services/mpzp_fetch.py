"""Bezpieczne pobieranie dokumentów MPZP z niezaufanych stron BIP.

Obsługiwane są cztery warianty: bezpośredni PDF, ZIP zawierający PDF, HTML
z linkiem do PDF oraz HTML z treścią uchwały bezpośrednio na stronie. ZIP jest
rozpakowywany wyłącznie w pamięci przez ``zipfile.ZipFile`` i ``io.BytesIO`` —
nigdy na dysk.

Ochrona SSRF sprawdza schemat i wszystkie adresy IP hosta przed każdym żądaniem,
także na każdym hopie ręcznie obsługiwanego przekierowania. Nie eliminuje jednak
w 100% ryzyka DNS rebinding (TOCTOU): między walidacją DNS a połączeniem httpx
adres może teoretycznie się zmienić. Pełna eliminacja wymagałaby transportu
przypinającego zweryfikowany IP do socketu i wykracza poza zakres tego modułu.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import logging
import socket
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.schemas.analyze import SourceMetadata

logger = logging.getLogger(__name__)

MPZP_FETCH_TIMEOUT_S: Final[float] = 30.0
MPZP_MAX_REDIRECTS: Final[int] = 5
MPZP_MAX_DOCUMENT_SIZE_BYTES: Final[int] = 50 * 1024 * 1024

_ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})
_ALLOWED_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/pdf",
        "application/zip",
        "application/x-zip-compressed",
        "application/octet-stream",
        "text/html",
    }
)
_REDIRECT_STATUS_CODES: Final[frozenset[int]] = frozenset(
    {301, 302, 303, 307, 308}
)
_PDF_NAME_KEYWORDS: Final[tuple[str, ...]] = (
    "uchwala",
    "uchwały",
    "tekst",
    "plan",
    "mpzp",
)
_PDF_LINK_TEXT_KEYWORDS: Final[tuple[str, ...]] = (
    "uchwała",
    "uchwały",
    "tekst planu",
    "plan miejscowy",
    "pobierz",
    "pdf",
)
_ZIP_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {"application/zip", "application/x-zip-compressed"}
)


class MpzpDocumentError(Exception):
    """Bazowy wyjątek modułu — nie jest podnoszony bezpośrednio."""


class MpzpDocumentSecurityError(MpzpDocumentError):
    """Pobranie zostało zablokowane ze względów bezpieczeństwa."""


class MpzpDocumentFetchError(MpzpDocumentError):
    """Legalne żądanie nie powiodło się lub zwróciło uszkodzone dane."""


class MpzpDocumentNotFoundError(MpzpDocumentError):
    """Pobranie się udało, ale nie znaleziono oczekiwanego dokumentu PDF."""


@dataclass(frozen=True)
class DocumentBlob:
    content: bytes
    media_type: str
    filename: str | None
    source_metadata: SourceMetadata


async def fetch_mpzp_document(uchwala_url: str) -> DocumentBlob:
    """Pobiera dokument MPZP z niezaufanego URL pochodzącego z discovery.

    Funkcja nie ufa wejściowemu URL. Wynikiem jest docelowy PDF — bezpośredni,
    z ZIP albo z linku HTML — lub surowy HTML z ``manual_review_required=True``,
    gdy strona zawiera treść uchwały bez linku PDF.

    ``MpzpDocumentSecurityError`` oznacza świadome zablokowanie żądania,
    natomiast ``MpzpDocumentFetchError`` niedostępność albo uszkodzenie danych.
    Wyższe warstwy nie powinny łączyć tych dwóch kategorii błędów.
    """
    async with httpx.AsyncClient(
        timeout=MPZP_FETCH_TIMEOUT_S, follow_redirects=False
    ) as client:
        return await _fetch_document_via_client(
            client, uchwala_url, allow_html_link_follow=True
        )


async def _fetch_document_via_client(
    client: httpx.AsyncClient,
    url: str,
    allow_html_link_follow: bool,
) -> DocumentBlob:
    content, content_type, final_url = await _fetch_with_redirect_validation(
        client, url
    )
    fetched_at = datetime.now(timezone.utc)

    _validate_declared_binary_format(content, content_type, final_url)

    if _looks_like_pdf(content, content_type, final_url):
        return DocumentBlob(
            content=content,
            media_type="application/pdf",
            filename=_filename_from_url(final_url) or "document.pdf",
            source_metadata=_build_source_metadata(
                final_url, fetched_at, "application/pdf", manual_review=False
            ),
        )

    if _looks_like_zip(content, content_type, final_url):
        pdf_bytes, pdf_name = _extract_best_pdf_from_zip(content)
        return DocumentBlob(
            content=pdf_bytes,
            media_type="application/pdf",
            filename=pdf_name,
            source_metadata=_build_source_metadata(
                final_url, fetched_at, "application/pdf", manual_review=False
            ),
        )

    if _looks_like_html(content, content_type):
        if allow_html_link_follow:
            pdf_link = _find_pdf_link_in_html(content, final_url)
            if pdf_link is not None:
                # Śledzimy tylko jeden skok HTML→dokument, żeby strony HTML
                # wskazujące cyklicznie na inne strony nie powodowały rekurencji.
                return await _fetch_document_via_client(
                    client, pdf_link, allow_html_link_follow=False
                )
        return DocumentBlob(
            content=content,
            media_type="text/html",
            filename=_filename_from_url(final_url) or "document.html",
            source_metadata=_build_source_metadata(
                final_url, fetched_at, "text/html", manual_review=True
            ),
        )

    raise MpzpDocumentSecurityError(
        f"Nierozpoznany typ zawartości dokumentu: {content_type!r}."
    )


def _build_source_metadata(
    url: str,
    fetched_at: datetime,
    media_type: str,
    manual_review: bool,
) -> SourceMetadata:
    return SourceMetadata(
        source_name="MPZP_BIP",
        source_url=url,
        fetched_at=fetched_at,
        response_status=200,
        confidence=0.5 if manual_review else 0.9,
        manual_review_required=manual_review,
    )


async def _fetch_with_redirect_validation(
    client: httpx.AsyncClient, url: str
) -> tuple[bytes, str, str]:
    """Pobiera zasób, walidując URL przed każdym ręcznie śledzonym hopem."""
    current_url = url
    for _ in range(MPZP_MAX_REDIRECTS + 1):
        await _ensure_url_is_safe(current_url)
        try:
            async with client.stream("GET", current_url) as response:
                if response.status_code in _REDIRECT_STATUS_CODES:
                    location = response.headers.get("location")
                    if not location:
                        raise MpzpDocumentFetchError(
                            "Serwer zwrócił przekierowanie bez nagłówka Location."
                        )
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()
                content_type = (
                    response.headers.get("content-type", "")
                    .split(";", maxsplit=1)[0]
                    .strip()
                    .lower()
                )
                if content_type not in _ALLOWED_CONTENT_TYPES:
                    raise MpzpDocumentSecurityError(
                        f"Niedozwolony Content-Type odpowiedzi: {content_type!r}."
                    )

                declared_length = response.headers.get("content-length")
                if declared_length:
                    try:
                        declared_size = int(declared_length)
                    except ValueError as exc:
                        raise MpzpDocumentSecurityError(
                            "Serwer zwrócił nieprawidłowy nagłówek Content-Length."
                        ) from exc
                    if declared_size > MPZP_MAX_DOCUMENT_SIZE_BYTES:
                        raise MpzpDocumentSecurityError(
                            "Serwer zadeklarował rozmiar dokumentu przekraczający "
                            "dozwolony limit."
                        )

                content = await _read_body_with_size_limit(response)
                return content, content_type, current_url
        except httpx.TimeoutException as exc:
            raise MpzpDocumentFetchError(
                f"Przekroczono limit czasu podczas pobierania dokumentu: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise MpzpDocumentFetchError(
                f"Serwer zwrócił błąd HTTP: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise MpzpDocumentFetchError(
                f"Błąd sieciowy podczas pobierania dokumentu: {exc}"
            ) from exc

    raise MpzpDocumentSecurityError(
        f"Przekroczono limit {MPZP_MAX_REDIRECTS} przekierowań podczas "
        "pobierania dokumentu."
    )


async def _read_body_with_size_limit(response: httpx.Response) -> bytes:
    """Wymusza limit podczas streamingu i przerywa po jego przekroczeniu."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MPZP_MAX_DOCUMENT_SIZE_BYTES:
            raise MpzpDocumentSecurityError(
                "Dokument przekracza maksymalny dozwolony rozmiar "
                f"{MPZP_MAX_DOCUMENT_SIZE_BYTES} bajtów."
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _ensure_url_is_safe(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise MpzpDocumentSecurityError(
            f"Niedozwolony schemat URL: {parsed.scheme!r}. Dozwolone: http, https."
        )
    hostname = parsed.hostname
    if not hostname:
        raise MpzpDocumentSecurityError("URL nie zawiera prawidłowej nazwy hosta.")
    await _ensure_host_is_public(hostname)


async def _ensure_host_is_public(hostname: str) -> None:
    """Rozwiązuje DNS poza event loop i blokuje wszystkie niepubliczne adresy."""
    loop = asyncio.get_event_loop()
    try:
        addr_infos = await loop.run_in_executor(
            None, socket.getaddrinfo, hostname, None
        )
    except socket.gaierror as exc:
        raise MpzpDocumentSecurityError(
            f"Nie udało się rozwiązać nazwy hosta {hostname!r}: {exc}"
        ) from exc

    if not addr_infos:
        raise MpzpDocumentSecurityError(
            f"Nazwa hosta {hostname!r} nie wskazuje na żaden adres IP."
        )

    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise MpzpDocumentSecurityError(
                f"Host {hostname!r} wskazuje na adres prywatny/lokalny ({ip}) — "
                "zablokowano ze względów bezpieczeństwa."
            )


def _validate_declared_binary_format(
    content: bytes, content_type: str, url: str
) -> None:
    """Blokuje sprzeczność deklarowanego PDF/ZIP z sygnaturą bajtową."""
    lower_url = url.lower()
    declared_pdf = content_type == "application/pdf" or lower_url.endswith(".pdf")
    declared_zip = content_type in _ZIP_CONTENT_TYPES or lower_url.endswith(".zip")

    if declared_pdf and not content.startswith(b"%PDF"):
        raise MpzpDocumentSecurityError(
            "Deklarowany typ PDF nie zgadza się z rzeczywistą zawartością pliku "
            "(brak sygnatury %PDF)."
        )
    if declared_zip and not content.startswith(b"PK\x03\x04"):
        raise MpzpDocumentSecurityError(
            "Deklarowany typ ZIP nie zgadza się z rzeczywistą zawartością pliku."
        )


def _looks_like_pdf(content: bytes, content_type: str, url: str) -> bool:
    magic_match = content.startswith(b"%PDF")
    declared_pdf = content_type == "application/pdf" or url.lower().endswith(".pdf")
    if declared_pdf and not magic_match:
        raise MpzpDocumentSecurityError(
            "Deklarowany typ PDF nie zgadza się z rzeczywistą zawartością pliku "
            "(brak sygnatury %PDF)."
        )
    return magic_match


def _looks_like_zip(content: bytes, content_type: str, url: str) -> bool:
    magic_match = content.startswith(b"PK\x03\x04")
    declared_zip = content_type in _ZIP_CONTENT_TYPES or url.lower().endswith(".zip")
    if declared_zip and not magic_match:
        raise MpzpDocumentSecurityError(
            "Deklarowany typ ZIP nie zgadza się z rzeczywistą zawartością pliku."
        )
    return magic_match


def _looks_like_html(content: bytes, content_type: str) -> bool:
    if content_type == "text/html":
        return True
    stripped = content.lstrip()[:20].lower()
    return stripped.startswith(b"<!doctype html") or stripped.startswith(b"<html")


def _find_pdf_link_in_html(html_bytes: bytes, base_url: str) -> str | None:
    """Wybiera najlepiej opisany link PDF znaleziony przez BeautifulSoup."""
    soup = BeautifulSoup(html_bytes, "html.parser")
    candidates: list[tuple[int, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href or not href.lower().split("?", maxsplit=1)[0].endswith(".pdf"):
            continue
        absolute_url = urljoin(base_url, href)
        link_text = (anchor.get_text() or "").strip().lower()
        haystack = f"{link_text} {href.lower()}"
        score = sum(
            1 for keyword in _PDF_LINK_TEXT_KEYWORDS if keyword in haystack
        )
        candidates.append((score, absolute_url))

    if not candidates:
        return None
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    return candidates[0][1]


def _extract_best_pdf_from_zip(content: bytes) -> tuple[bytes, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            candidates: list[tuple[str, zipfile.ZipInfo]] = []
            for info in archive.infolist():
                safe_name = _safe_zip_member_path(info.filename)
                if safe_name is None:
                    logger.warning(
                        "Wpis ZIP z podejrzaną ścieżką odrzucony (Zip Slip): %s",
                        info.filename,
                    )
                    continue
                if not safe_name.lower().endswith(".pdf"):
                    continue
                candidates.append((safe_name, info))

            if not candidates:
                raise MpzpDocumentNotFoundError(
                    "Archiwum ZIP nie zawiera żadnego bezpiecznego pliku PDF."
                )

            best_name, best_info = _pick_best_pdf_candidate(candidates)
            if best_info.file_size > MPZP_MAX_DOCUMENT_SIZE_BYTES:
                raise MpzpDocumentSecurityError(
                    "Plik PDF wewnątrz archiwum przekracza maksymalny dozwolony "
                    "rozmiar."
                )
            pdf_bytes = archive.read(best_info)
            if len(pdf_bytes) > MPZP_MAX_DOCUMENT_SIZE_BYTES:
                raise MpzpDocumentSecurityError(
                    "Rozpakowany plik PDF przekracza maksymalny dozwolony rozmiar."
                )
            if not pdf_bytes.startswith(b"%PDF"):
                raise MpzpDocumentSecurityError(
                    "Plik z rozszerzeniem PDF w archiwum nie ma sygnatury %PDF."
                )
            return pdf_bytes, best_name
    except zipfile.BadZipFile as exc:
        raise MpzpDocumentFetchError(
            f"Nie udało się odczytać archiwum ZIP: {exc}"
        ) from exc


def _safe_zip_member_path(member_name: str) -> str | None:
    """Odrzuca ścieżki absolutne i segmenty ``..`` jako ochronę Zip Slip."""
    normalized = member_name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith(".."):
        return None
    if ".." in normalized.split("/"):
        return None
    return normalized


def _pick_best_pdf_candidate(
    candidates: list[tuple[str, zipfile.ZipInfo]],
) -> tuple[str, zipfile.ZipInfo]:
    """Wybiera PDF po słowach kluczowych, a potem po rozmiarze."""

    def score(item: tuple[str, zipfile.ZipInfo]) -> tuple[int, int]:
        name, info = item
        lowered = name.lower()
        keyword_score = sum(
            1 for keyword in _PDF_NAME_KEYWORDS if keyword in lowered
        )
        return keyword_score, info.file_size

    return max(candidates, key=score)


def _filename_from_url(url: str) -> str | None:
    path = urlparse(url).path
    name = path.rsplit("/", maxsplit=1)[-1]
    return name or None
