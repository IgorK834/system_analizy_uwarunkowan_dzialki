"""Domena pakietu audytowego analizy (BK-505, ADR-011).

Czysta logika: bezpieczne nazwy wpisów, opis pliku pakietu, limity, manifest
z SHA-256 każdego pliku i opis pominiętych artefaktów. Nie zna ZIP-a, bazy ani
HTTP — zapis archiwum jest w ``infrastructure.archive``, a składanie treści w
``application.audit_export``.

Manifest opisuje każdy plik pakietu poza samym sobą (nazwa, bajty, SHA-256).
Hash całej paczki nie może być wewnątrz archiwum, więc jest podawany poza nim
(nagłówek odpowiedzi). Serializacja jest kanoniczna (posortowane klucze i wpisy,
bez czasu eksportu), dzięki czemu ten sam snapshot w tej samej wersji eksportera
daje identyczne bajty.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

AUDIT_PACKAGE_SCHEMA_VERSION: Final[str] = "audit-package/1"
# Podbijana przy każdej zmianie treści lub układu pakietu — determinizm dotyczy
# tej samej wersji eksportera.
AUDIT_EXPORTER_VERSION: Final[str] = "audit-exporter/1.0.0"
MANIFEST_NAME: Final[str] = "manifest.json"
README_NAME: Final[str] = "README.md"

# Stały znacznik czasu wpisów ZIP (najwcześniejszy dozwolony przez format) —
# czas eksportu nie może wpływać na bajty archiwum.
FIXED_ZIP_TIMESTAMP: Final[tuple[int, int, int, int, int, int]] = (1980, 1, 1, 0, 0, 0)

REASON_REDISTRIBUTION_FORBIDDEN: Final[str] = "redistribution_forbidden"
REASON_REDISTRIBUTION_UNCONFIRMED: Final[str] = "redistribution_unconfirmed"
REASON_RAW_NOT_ALLOWED: Final[str] = "raw_redistribution_not_allowed"

OMISSION_REASON_LABELS_PL: Final[dict[str, str]] = {
    REASON_REDISTRIBUTION_FORBIDDEN: "katalog źródeł zabrania redystrybucji danych źródła",
    REASON_REDISTRIBUTION_UNCONFIRMED: (
        "katalog źródeł nie potwierdza zgody na redystrybucję (traktowane jak zakaz)"
    ),
    REASON_RAW_NOT_ALLOWED: (
        "katalog źródeł dopuszcza wyłącznie dane pochodne — surowych danych źródła "
        "nie kopiujemy"
    ),
}

_ENTRY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}(/[A-Za-z0-9][A-Za-z0-9._-]{0,63}){0,3}$")
_MAX_NAME_LENGTH: Final[int] = 128


class UnsafeEntryNameError(ValueError):
    """Nazwa wpisu mogłaby wyjść poza katalog rozpakowania albo jest niejednoznaczna."""


class AuditExportLimitError(Exception):
    """Pakiet przekroczyłby jawny limit liczby plików lub rozmiaru."""


def safe_entry_name(name: str) -> str:
    """Zwraca nazwę wpisu albo zgłasza ``UnsafeEntryNameError``.

    Dozwolone są względne ścieżki POSIX o co najwyżej czterech segmentach z
    liter ASCII, cyfr, ``.``, ``_`` i ``-``. Zakazane: ścieżki absolutne, ``..``,
    ukośnik wsteczny, puste segmenty, segmenty zaczynające się od kropki i nazwy
    kończące się kropką (Windows) — Zip Slip nie jest możliwy nawet po
    rozpakowaniu przez dowolne narzędzie.
    """
    if (
        not name
        or len(name) > _MAX_NAME_LENGTH
        or _ENTRY_NAME.fullmatch(name) is None
        or name.endswith(".")
        or any(part in {".", ".."} for part in name.split("/"))
        or name.casefold() == MANIFEST_NAME.casefold() and name != MANIFEST_NAME
    ):
        raise UnsafeEntryNameError(f"Niebezpieczna nazwa wpisu pakietu: {name!r}.")
    return name


def sanitize_component(value: str | None, *, fallback: str = "x", limit: int = 48) -> str:
    """Bezpieczny segment nazwy z dowolnego tekstu (np. identyfikatora źródła)."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", (value or "").casefold()).strip("_")
    return (cleaned or fallback)[:limit].strip("_") or fallback


@dataclass(frozen=True)
class PackageFile:
    """Jeden plik pakietu: bezpieczna nazwa, zawartość i typ mediów."""

    name: str
    data: bytes
    media_type: str

    def __post_init__(self) -> None:
        safe_entry_name(self.name)

    @property
    def sha256(self) -> str:
        return sha256_hex(self.data)


@dataclass(frozen=True)
class OmittedArtifact:
    """Artefakt pominięty w pakiecie: tylko referencja, hash i powód.

    ``sha256`` i ``bytes`` opisują treść, której NIE ma w paczce (kanoniczny JSON
    warstwy albo bloku danych) — pozwala ją później porównać z materiałem
    uzyskanym legalnie, bez ujawniania treści.
    """

    name: str
    kind: str
    source_ids: tuple[str, ...]
    reason: str
    policy: Mapping[str, str]
    sha256: str
    bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "source_ids": list(self.source_ids),
            "reason": self.reason,
            "reason_label": OMISSION_REASON_LABELS_PL[self.reason],
            "redistribution": dict(sorted(self.policy.items())),
            "sha256": self.sha256,
            "bytes": self.bytes,
        }


@dataclass(frozen=True)
class AuditLimits:
    """Jawne limity pakietu (liczba plików, rozmiar pliku, rozmiar łączny)."""

    max_files: int
    max_file_bytes: int
    max_total_bytes: int

    def enforce(self, files: Sequence[PackageFile]) -> None:
        if len(files) > self.max_files:
            raise AuditExportLimitError(
                f"Pakiet audytowy ma {len(files)} plików; limit to {self.max_files}."
            )
        total = 0
        for file in files:
            if len(file.data) > self.max_file_bytes:
                raise AuditExportLimitError(
                    f"Plik {file.name} ({len(file.data)} B) przekracza limit "
                    f"{self.max_file_bytes} B."
                )
            total += len(file.data)
        if total > self.max_total_bytes:
            raise AuditExportLimitError(
                f"Pakiet audytowy ({total} B) przekracza limit {self.max_total_bytes} B."
            )


@dataclass(frozen=True)
class AuditPackage:
    """Gotowa treść pakietu: pliki (z manifestem) i pominięte artefakty."""

    files: tuple[PackageFile, ...]
    omitted: tuple[OmittedArtifact, ...] = field(default_factory=tuple)

    def by_name(self, name: str) -> PackageFile:
        return next(file for file in self.files if file.name == name)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render_json(payload: Any) -> bytes:
    """Kanoniczny, czytelny JSON: UTF-8, posortowane klucze, wcięcia, końcowy \\n."""
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def canonical_bytes(payload: Any) -> bytes:
    """Kanoniczny zapis do hashowania pominiętych artefaktów (bez wcięć)."""
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def build_manifest(
    files: Sequence[PackageFile],
    omitted: Sequence[OmittedArtifact],
    context: Mapping[str, Any],
    limits: AuditLimits,
) -> PackageFile:
    """Manifest z SHA-256 każdego pliku poza samym manifestem.

    ``context`` niesie wyłącznie dane niezmienne dla snapshotu (identyfikator,
    czas analizy, hash macierzy jakości…). Czasu eksportu tu nie ma.
    """
    names = [file.name for file in files]
    if len(set(names)) != len(names):
        raise ValueError("Nazwy plików pakietu muszą być unikalne.")
    if MANIFEST_NAME in names:
        raise ValueError("Manifest nie może opisywać samego siebie.")
    payload = {
        "schema_version": AUDIT_PACKAGE_SCHEMA_VERSION,
        "exporter_version": AUDIT_EXPORTER_VERSION,
        **dict(context),
        "hash_algorithm": "SHA-256",
        "package_hash_note": (
            "Hash całej paczki ZIP nie znajduje się w archiwum; jest podawany poza nim "
            "(nagłówek odpowiedzi X-Audit-Package-SHA256)."
        ),
        "limits": {
            "max_files": limits.max_files,
            "max_file_bytes": limits.max_file_bytes,
            "max_total_bytes": limits.max_total_bytes,
        },
        "files": [
            {
                "name": file.name,
                "bytes": len(file.data),
                "sha256": file.sha256,
                "media_type": file.media_type,
            }
            for file in sorted(files, key=lambda item: item.name)
        ],
        "omitted_artifacts": [
            item.to_dict() for item in sorted(omitted, key=lambda item: item.name)
        ],
    }
    return PackageFile(MANIFEST_NAME, render_json(payload), "application/json")


def verify_manifest(
    manifest: Mapping[str, Any], read: Callable[[str], bytes | None]
) -> list[str]:
    """Sprawdza pliki względem manifestu; zwraca listę problemów (pusta = OK).

    ``read`` zwraca bajty pliku o danej nazwie albo ``None``, gdy go brak.
    Wykrywa zmianę pojedynczego bajtu (SHA-256 i rozmiar), brakujące pliki oraz
    pliki spoza manifestu, jeżeli ``read`` udostępnia listę przez ``names`` —
    to drugie robi wywołujący (np. porównując nazwy z archiwum).
    """
    problems: list[str] = []
    for entry in manifest.get("files", []):
        name = entry["name"]
        data = read(name)
        if data is None:
            problems.append(f"brak pliku: {name}")
            continue
        if len(data) != entry["bytes"]:
            problems.append(f"zły rozmiar: {name} ({len(data)} ≠ {entry['bytes']})")
        if sha256_hex(data) != entry["sha256"]:
            problems.append(f"zła suma SHA-256: {name}")
    return problems
