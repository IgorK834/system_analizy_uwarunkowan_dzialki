"""Deterministyczny zapis pakietu audytowego do ZIP i strumieniowanie (BK-505).

Archiwum jest budowane do pliku tymczasowego (w pamięci do progu, potem na
dysku), bo hash całej paczki musi być znany przed wysłaniem nagłówków
(``X-Audit-Package-SHA256``, ``Content-Length``). Odpowiedź jest potem
strumieniowana fragmentami z tego pliku — bez ładowania całości do pamięci
odpowiedzi.

Determinizm: wpisy w kolejności nazw, stały znacznik czasu (1980-01-01), stała
metoda i poziom kompresji, stałe atrybuty pliku i system tworzący. Te same
pliki wejściowe w tym samym środowisku dają identyczne bajty archiwum; sumy w
manifeście (SHA-256 treści) nie zależą od środowiska w ogóle.
"""

from __future__ import annotations

import hashlib
import tempfile
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import BinaryIO, Final

from app.modules.reporting.domain.audit_package import (
    FIXED_ZIP_TIMESTAMP,
    AuditLimits,
    PackageFile,
    safe_entry_name,
)

CHUNK_BYTES: Final[int] = 64 * 1024
# Do tego progu archiwum żyje w pamięci; powyżej trafia na dysk.
SPOOL_MAX_BYTES: Final[int] = 8 * 1024 * 1024
_COMPRESS_LEVEL: Final[int] = 6
_FILE_MODE: Final[int] = 0o644 << 16
_CREATE_SYSTEM_UNIX: Final[int] = 3


@dataclass
class ArchiveResult:
    """Zbudowane archiwum: uchwyt do odczytu, rozmiar i SHA-256 całej paczki."""

    file: BinaryIO
    size: int
    sha256: str

    def close(self) -> None:
        self.file.close()


def write_deterministic_zip(
    files: Sequence[PackageFile], limits: AuditLimits | None = None
) -> ArchiveResult:
    """Zapisuje pliki do ZIP w posortowanej kolejności; zwraca uchwyt, rozmiar i hash."""
    ordered = sorted(files, key=lambda item: item.name)
    names = [safe_entry_name(item.name) for item in ordered]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("Nazwy wpisów pakietu muszą być unikalne bez względu na wielkość liter.")
    if limits is not None:
        limits.enforce(ordered)

    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX_BYTES)  # noqa: SIM115
    try:
        with zipfile.ZipFile(spool, mode="w", allowZip64=True) as archive:
            for item in ordered:
                info = zipfile.ZipInfo(item.name, date_time=FIXED_ZIP_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = _FILE_MODE
                info.create_system = _CREATE_SYSTEM_UNIX
                archive.writestr(info, item.data, compress_type=zipfile.ZIP_DEFLATED,
                                 compresslevel=_COMPRESS_LEVEL)
        size = spool.seek(0, 2)
        spool.seek(0)
        digest = hashlib.sha256()
        for chunk in iter(lambda: spool.read(CHUNK_BYTES), b""):
            digest.update(chunk)
        spool.seek(0)
    except BaseException:
        spool.close()
        raise
    return ArchiveResult(spool, size, digest.hexdigest())


def iter_archive(result: ArchiveResult, chunk_size: int = CHUNK_BYTES) -> Iterator[bytes]:
    """Strumieniuje archiwum fragmentami i zawsze zamyka plik (także po rozłączeniu)."""
    try:
        while True:
            chunk = result.file.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        result.close()
