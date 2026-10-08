"""Bramka mypy z zapisaną bazą znanych zgłoszeń (AU-010).

Uruchamia ``mypy`` w zakresie z ``pyproject.toml`` (``app/modules``) i porównuje zgłoszenia z
``mypy-baseline.txt``. Numery linii są pomijane (przesunięcia kodu nie unieważniają bazy), liczą się
trójki (plik, komunikat, kod). Kod wyjścia: 0 — brak nowych zgłoszeń, 1 — są nowe, 2 — błąd narzędzia.
Zgłoszenia usunięte z kodu są wypisywane jako „do usunięcia z bazy”, ale nie psują przebiegu.

    python scripts/check_mypy_baseline.py            # sprawdzenie (CI)
    python scripts/check_mypy_baseline.py --update   # przepisanie bazy po świadomej zmianie
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
BASELINE = BACKEND_DIR / "mypy-baseline.txt"
_LINE = re.compile(r"^(?P<path>[^:]+):\d+(?::\d+)?: error: (?P<message>.*?)(?:  \[(?P<code>[a-z0-9-]+)\])?$")


def run_mypy() -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--no-error-summary", "--show-error-codes", "--no-pretty"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(2)
    return normalize(result.stdout.splitlines())


def normalize(lines: list[str]) -> list[str]:
    entries: list[str] = []
    for raw in lines:
        match = _LINE.match(raw.strip())
        if match:
            entries.append(f"{match['path']}: {match['message']} [{match['code'] or '-'}]")
    return sorted(entries)


def read_baseline() -> Counter[str]:
    if not BASELINE.exists():
        return Counter()
    return Counter(
        line for line in BASELINE.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true", help="przepisz bazę bieżącymi zgłoszeniami")
    args = parser.parse_args(argv)

    current = run_mypy()
    if args.update:
        header = (
            "# Znane zgłoszenia mypy (bez numerów linii) dla zakresu z pyproject.toml. Nie dopisuj ręcznie:\n"
            "# `python scripts/check_mypy_baseline.py --update`. Cel: pusta lista.\n"
        )
        BASELINE.write_text(header + "\n".join(current) + ("\n" if current else ""), encoding="utf-8")
        print(f"Zapisano {len(current)} zgłoszeń w {BASELINE.name}.")
        return 0

    known = read_baseline()
    seen = Counter(current)
    new = seen - known
    fixed = known - seen
    for entry, count in sorted(fixed.items()):
        print(f"do usunięcia z bazy ({count}×): {entry}")
    if new:
        print("NOWE zgłoszenia mypy (nie ma ich w mypy-baseline.txt):")
        for entry, count in sorted(new.items()):
            print(f"  {count}× {entry}")
        return 1
    print(f"mypy: brak nowych zgłoszeń ({sum(known.values())} znanych w bazie, {len(current)} bieżących).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
