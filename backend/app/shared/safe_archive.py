"""Współdzielona, bezpieczna ekstrakcja archiwów ZIP z niezaufanych źródeł.

Moduł jest jedynym dozwolonym miejscem logiki rozpakowywania ZIP dla wszystkich
warstw (ADR-001: ``app.shared`` mogą importować nawet warstwy ``domain``).
Używa wyłącznie biblioteki standardowej, aby pozostać wolnym od zależności
frameworka/ORM/HTTP i bezpiecznym do importu z każdej warstwy.

Chroni przed trzema klasami ataków na archiwa pobierane z sieci (BIP, WFS,
gminne pliki APP/GML):

1. **Zip Slip** — wpis o ścieżce absolutnej lub z segmentem ``..`` mógłby
   nadpisać plik poza katalogiem docelowym. Każda ścieżka jest normalizowana i
   odrzucana, jeśli wychodzi poza katalog docelowy.
2. **Zip bomb (rozmiar)** — sumaryczny i pojedynczy rozmiar po dekompresji ma
   twardy limit; przekroczenie przerywa ekstrakcję zanim zapełni dysk.
3. **Zip bomb (współczynnik kompresji)** — wpis o skrajnie wysokim stosunku
   rozmiaru rozpakowanego do spakowanego jest odrzucany, nawet jeśli mieści się
   w limicie sumarycznym.

Wszystkie limity są jawne (``ArchiveLimits``), aby żaden odbiorca nie polegał na
magicznych liczbach ukrytych w kodzie (context.md pkt 12.10).
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# Domyślne, konserwatywne limity. 50 MB jest zgodne z limitem pojedynczego
# dokumentu MPZP (app/services/mpzp_fetch.MPZP_MAX_DOCUMENT_SIZE_BYTES) i
# wystarcza dla realnych paczek GML/APP pojedynczej gminy.
_MB = 1024 * 1024


class UnsafeArchiveError(Exception):
    """Archiwum zostało odrzucone ze względów bezpieczeństwa.

    Podnoszone dla Zip Slip, przekroczenia limitu rozmiaru/liczby wpisów oraz
    podejrzanie wysokiego współczynnika kompresji (zip bomb). Jest to
    kontrolowany błąd bezpieczeństwa — wyższe warstwy nie powinny mylić go z
    błędem dostępności (uszkodzone archiwum sygnalizuje ``BadArchiveError``).
    """


class BadArchiveError(Exception):
    """Zawartość nie jest poprawnym archiwum ZIP."""


@dataclass(frozen=True)
class ArchiveLimits:
    """Jawne limity bezpieczeństwa rozpakowywania archiwum.

    - ``max_entries`` — maksymalna liczba wpisów (plików i katalogów),
    - ``max_total_uncompressed_bytes`` — łączny rozmiar po dekompresji,
    - ``max_entry_uncompressed_bytes`` — rozmiar pojedynczego wpisu,
    - ``max_compression_ratio`` — maksymalny stosunek rozmiaru rozpakowanego do
      spakowanego pojedynczego wpisu; wpisy o rozmiarze poniżej
      ``compression_ratio_floor_bytes`` są zwolnione z tej kontroli, bo bardzo
      małe pliki naturalnie mają wysoki, nieszkodliwy współczynnik.
    """

    max_entries: int = 512
    max_total_uncompressed_bytes: int = 50 * _MB
    max_entry_uncompressed_bytes: int = 50 * _MB
    max_compression_ratio: float = 200.0
    compression_ratio_floor_bytes: int = 4 * 1024


DEFAULT_LIMITS = ArchiveLimits()


def safe_member_path(member_name: str) -> str | None:
    """Zwraca znormalizowaną, bezpieczną ścieżkę wpisu albo ``None``.

    Odrzuca ścieżki absolutne oraz zawierające segment ``..`` (ochrona Zip
    Slip). Backslashe są normalizowane do ``/`` (archiwa z Windows). Zwrócona
    wartość jest relatywną ścieżką POSIX bezpieczną do połączenia z katalogiem
    docelowym.
    """
    normalized = member_name.replace("\\", "/").strip()
    if not normalized:
        return None
    pure = PurePosixPath(normalized)
    if pure.is_absolute():
        return None
    parts = pure.parts
    if any(part == ".." for part in parts):
        return None
    # Pomijamy zbędne segmenty "." i puste, budując czystą ścieżkę relatywną.
    cleaned = [part for part in parts if part not in (".", "")]
    if not cleaned:
        return None
    return "/".join(cleaned)


def _iter_safe_infos(
    archive: zipfile.ZipFile,
    limits: ArchiveLimits,
    allowed_suffixes: tuple[str, ...] | None,
) -> Iterator[tuple[zipfile.ZipInfo, str]]:
    """Waliduje wpisy archiwum i zwraca pary (info, bezpieczna_ścieżka).

    Egzekwuje limity liczby wpisów, rozmiaru sumarycznego/pojedynczego oraz
    współczynnika kompresji. Katalogi i wpisy o rozszerzeniu spoza
    ``allowed_suffixes`` są pomijane (nie wliczają się do plików wynikowych, ale
    wliczają się do limitu liczby wpisów, aby archiwum z milionem pustych
    katalogów również zostało odrzucone).
    """
    infos = archive.infolist()
    if len(infos) > limits.max_entries:
        raise UnsafeArchiveError(
            f"Archiwum ma {len(infos)} wpisów, limit to {limits.max_entries}."
        )

    total = 0
    for info in infos:
        safe_name = safe_member_path(info.filename)
        if safe_name is None:
            raise UnsafeArchiveError(
                "Archiwum zawiera ścieżkę mogącą prowadzić do Zip Slip: "
                f"{info.filename!r}."
            )
        if info.is_dir():
            continue

        if info.file_size > limits.max_entry_uncompressed_bytes:
            raise UnsafeArchiveError(
                f"Wpis {safe_name!r} po dekompresji ma {info.file_size} B, "
                f"limit to {limits.max_entry_uncompressed_bytes} B."
            )
        total += info.file_size
        if total > limits.max_total_uncompressed_bytes:
            raise UnsafeArchiveError(
                "Rozpakowana zawartość archiwum przekracza limit "
                f"{limits.max_total_uncompressed_bytes} B."
            )
        # Współczynnik kompresji liczymy tylko dla wpisów powyżej progu — bardzo
        # małe pliki mają naturalnie wysoki, nieszkodliwy współczynnik.
        if (
            info.file_size >= limits.compression_ratio_floor_bytes
            and info.compress_size > 0
        ):
            ratio = info.file_size / info.compress_size
            if ratio > limits.max_compression_ratio:
                raise UnsafeArchiveError(
                    f"Wpis {safe_name!r} ma podejrzany współczynnik kompresji "
                    f"{ratio:.1f} (limit {limits.max_compression_ratio})."
                )

        if allowed_suffixes is not None and not safe_name.lower().endswith(
            allowed_suffixes
        ):
            continue
        yield info, safe_name


def _normalize_suffixes(
    allowed_suffixes: Iterable[str] | None,
) -> tuple[str, ...] | None:
    if allowed_suffixes is None:
        return None
    return tuple(suffix.lower() for suffix in allowed_suffixes)


def extract_zip(
    content: bytes,
    destination: str | Path,
    *,
    limits: ArchiveLimits = DEFAULT_LIMITS,
    allowed_suffixes: Iterable[str] | None = None,
) -> list[Path]:
    """Bezpiecznie rozpakowuje archiwum ZIP z pamięci do katalogu docelowego.

    Zwraca listę ścieżek wypakowanych plików (bez katalogów) w kolejności
    występowania w archiwum. Jeśli podano ``allowed_suffixes``, wypakowywane są
    wyłącznie pliki o tych rozszerzeniach (np. ``('.gml',)``), ale limity
    bezpieczeństwa są liczone dla całego archiwum.

    Podnosi ``UnsafeArchiveError`` przy naruszeniu bezpieczeństwa oraz
    ``BadArchiveError``, gdy dane nie są poprawnym ZIP.
    """
    destination_root = Path(destination).resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    suffixes = _normalize_suffixes(allowed_suffixes)
    extracted: list[Path] = []
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for info, safe_name in _iter_safe_infos(archive, limits, suffixes):
                target = (destination_root / safe_name).resolve()
                # Druga linia obrony: nawet po walidacji ścieżki upewniamy się,
                # że rozwiązany cel nie wyszedł poza katalog docelowy.
                if (
                    target != destination_root
                    and destination_root not in target.parents
                ):
                    raise UnsafeArchiveError(
                        f"Ścieżka wpisu {safe_name!r} wychodzi poza katalog docelowy."
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(target, "wb") as handle:
                    handle.write(source.read())
                extracted.append(target)
    except zipfile.BadZipFile as exc:
        raise BadArchiveError("Zawartość nie jest poprawnym archiwum ZIP.") from exc
    return extracted


def read_zip_members(
    content: bytes,
    *,
    limits: ArchiveLimits = DEFAULT_LIMITS,
    allowed_suffixes: Iterable[str] | None = None,
) -> list[tuple[str, bytes]]:
    """Zwraca bezpieczne wpisy archiwum jako pary (ścieżka, zawartość) w pamięci.

    Wariant dla odbiorców, którzy nie chcą zapisu na dysk. Egzekwuje te same
    limity co ``extract_zip``.
    """
    suffixes = _normalize_suffixes(allowed_suffixes)
    members: list[tuple[str, bytes]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for info, safe_name in _iter_safe_infos(archive, limits, suffixes):
                members.append((safe_name, archive.read(info)))
    except zipfile.BadZipFile as exc:
        raise BadArchiveError("Zawartość nie jest poprawnym archiwum ZIP.") from exc
    return members
