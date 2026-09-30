#!/usr/bin/env python3
"""Offline'owa weryfikacja pakietu audytowego analizy (BK-505).

Sprawdza pakiet (plik ``.zip`` albo rozpakowany katalog) względem
``manifest.json``: obecność, rozmiar i SHA-256 każdego pliku, brak plików spoza
manifestu oraz bezpieczne nazwy wpisów. Używa wyłącznie biblioteki standardowej
Pythona 3 — nie wymaga sieci ani reszty repozytorium.

Użycie::

    python3 verify_audit_package.py analiza_123_pakiet_audytowy.zip
    python3 verify_audit_package.py rozpakowany_katalog/
    python3 verify_audit_package.py paczka.zip --package-sha256 <hash z nagłówka>

Kod wyjścia: 0 — pakiet zgodny; 1 — wykryto niezgodność (każda wypisana);
2 — błąd użycia albo nieczytelny pakiet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

MANIFEST_NAME = "manifest.json"
SUPPORTED_SCHEMA = "audit-package/1"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}(/[A-Za-z0-9][A-Za-z0-9._-]{0,63}){0,3}$")


def verify(
    names: list[str],
    read: Callable[[str], bytes],
) -> list[str]:
    """Zwraca listę problemów (pusta = pakiet zgodny z manifestem)."""
    problems: list[str] = []
    for name in names:
        if _SAFE_NAME.fullmatch(name) is None or ".." in PurePosixPath(name).parts:
            problems.append(f"niebezpieczna nazwa wpisu: {name!r}")
    if MANIFEST_NAME not in names:
        return [*problems, f"brak {MANIFEST_NAME}"]
    try:
        manifest = json.loads(read(MANIFEST_NAME).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return [*problems, f"manifest nieczytelny: {exc}"]
    if manifest.get("schema_version") != SUPPORTED_SCHEMA:
        problems.append(f"nieobsługiwany schemat manifestu: {manifest.get('schema_version')!r}")

    listed = {entry["name"]: entry for entry in manifest.get("files", [])}
    for name, entry in sorted(listed.items()):
        if name not in names:
            problems.append(f"brak pliku z manifestu: {name}")
            continue
        data = read(name)
        if len(data) != entry["bytes"]:
            problems.append(f"zły rozmiar {name}: {len(data)} ≠ {entry['bytes']}")
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            problems.append(f"zła suma SHA-256: {name}")
    for name in sorted(set(names) - set(listed) - {MANIFEST_NAME}):
        problems.append(f"plik spoza manifestu: {name}")
    return problems


def _load(target: Path) -> tuple[list[str], Callable[[str], bytes], Callable[[], None]]:
    if target.is_dir():
        names = sorted(
            path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()
        )
        return names, lambda name: (target / name).read_bytes(), lambda: None
    archive = zipfile.ZipFile(target)
    names = sorted(info.filename for info in archive.infolist() if not info.is_dir())
    return names, archive.read, archive.close


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Weryfikacja pakietu audytowego analizy.")
    parser.add_argument("package", type=Path, help="plik .zip albo rozpakowany katalog")
    parser.add_argument("--package-sha256", help="oczekiwany SHA-256 całego pliku .zip (z nagłówka)")
    args = parser.parse_args(argv)

    if not args.package.exists():
        print(f"BŁĄD: nie znaleziono {args.package}", file=sys.stderr)
        return 2
    problems: list[str] = []
    if args.package_sha256:
        if args.package.is_dir():
            print("BŁĄD: --package-sha256 dotyczy pliku .zip", file=sys.stderr)
            return 2
        actual = hashlib.sha256(args.package.read_bytes()).hexdigest()
        if actual != args.package_sha256.lower():
            problems.append(f"zła suma SHA-256 całej paczki: {actual} ≠ {args.package_sha256}")
    try:
        names, read, close = _load(args.package)
    except (zipfile.BadZipFile, OSError) as exc:
        print(f"BŁĄD: nie można odczytać pakietu: {exc}", file=sys.stderr)
        return 2
    try:
        problems.extend(verify(names, read))
    finally:
        close()

    if problems:
        print(f"NIEZGODNY ({len(problems)} problem/ów):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"OK: {len(names)} plików zgodnych z {MANIFEST_NAME}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
