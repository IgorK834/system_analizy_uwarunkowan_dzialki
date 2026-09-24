"""Współdzielony, publiczny typ pochodzenia danych (provenance).

Każdy wynik pochodzący z danych zewnętrznych lub zaimportowanych musi nieść ślad
pochodzenia: identyfikator źródła (spójny z katalogiem), identyfikator wydania
danych oraz moment pobrania. Typ używa wyłącznie biblioteki standardowej.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Provenance:
    """Minimalny, niezmienny ślad pochodzenia danych domenowych."""

    source_id: str
    fetched_at: datetime | None = None
    data_release_id: int | None = None
    source_artifact_id: int | None = None
    content_hash: str | None = None
    request_url: str | None = None
    operation: str | None = None
    complete: bool | None = None
    error_code: str | None = None

    def with_release(self, data_release_id: int) -> "Provenance":
        """Zwraca kopię provenance powiązaną z konkretnym wydaniem danych."""
        return Provenance(
            source_id=self.source_id,
            fetched_at=self.fetched_at,
            data_release_id=data_release_id,
            source_artifact_id=self.source_artifact_id,
            content_hash=self.content_hash,
            request_url=self.request_url,
            operation=self.operation,
            complete=self.complete,
            error_code=self.error_code,
        )


# --- Provenance aktu planistycznego (BK-107) ----------------------------------

_MAX_URL_LENGTH = 2000
_BLOCKED_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".lan")


def is_verified_https_url(url: str | None) -> bool:
    """Czy URL może zostać wyświetlony jako klikalny link do źródła.

    Akceptowany jest wyłącznie bezwzględny ``https://`` z nazwą hosta (nie
    adresem IP ani hostem lokalnym), bez danych logowania, znaków sterujących
    i niestandardowego portu. Weryfikacja jest składniowa: nie wykonuje
    żądania, więc nie potwierdza dostępności dokumentu.
    """
    if not url or len(url) > _MAX_URL_LENGTH or url != url.strip():
        return False
    if any(ord(char) < 33 or ord(char) == 127 for char in url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").casefold()
    if parts.scheme != "https" or not host or "." not in host:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    if port not in (None, 443):
        return False
    if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES):
        return False
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return True
    return False


@dataclass(frozen=True)
class CatalogRecordProvenance:
    """Rekord metadanych CSW zamrożony w chwili importu."""

    record_id: str
    resource_identifier: str | None = None
    title: str | None = None
    publication_date: date | None = None
    revision_date: date | None = None
    creation_date: date | None = None
    date_stamp: date | None = None
    metadata_url: str | None = None
    references: tuple[str, ...] = ()
    record_sha256: str | None = None
    response_sha256: str | None = None
    fetched_at: datetime | None = None


@dataclass(frozen=True)
class FormalDocumentProvenance:
    """Dokument formalny powiązany z aktem po identyfikatorze i wersji."""

    document_identifier: str
    document_version: str | None = None
    publication_id: str | None = None
    title: str | None = None
    short_name: str | None = None
    identification_number: str | None = None
    relation: str | None = None
    document_date: date | None = None
    effective_date: date | None = None
    repeal_date: date | None = None
    link: str | None = None
    link_verified: bool = False
    record_sha256: str | None = None
    resolution_status: str = "resolved"
    resolution_note: str | None = None


@dataclass(frozen=True)
class ActProvenance:
    """Łańcuch akt/wersja → metadane CSW → dokumenty dla jednego wyniku."""

    act_identifier: str
    act_version: str | None = None
    publication_id: str | None = None
    version_started_at: datetime | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    title: str | None = None
    gml_url: str | None = None
    source_reference: str | None = None
    metadata: tuple[CatalogRecordProvenance, ...] = ()
    formal_documents: tuple[FormalDocumentProvenance, ...] = field(default_factory=tuple)
