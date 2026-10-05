"""Skaner sekretów w repozytorium (PV3-16, ADR-014) — bez zależności, uruchamiany w CI przed budową obrazów.

Szuka w plikach tekstowych postaci sekretów, które nie mogą trafić do repozytorium: kluczy API Google
(``AIza`` + 35 znaków), kluczy prywatnych PEM, kluczy AWS, tokenów GitHub i Slack, oraz plików ``.env``
(dozwolony jest tylko ``.env.example`` z pustymi wartościami). Atrapy w testach (``AIzaSyTEST…``) są
krótsze niż prawdziwy klucz, więc nie pasują. Kod wyjścia 1 = znaleziono; komunikat podaje plik, wiersz
i rodzaj sekretu, nigdy jego wartości.

    python3 backend/scripts/secret_scan.py [KATALOG]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|github_pat_[A-Za-z0-9_]{60,}")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("gemini_key_assignment", re.compile(r"GEMINI_API_KEY\s*=\s*['\"]?AIza")),
)
SKIPPED_DIRS = frozenset(
    {".git", "node_modules", ".next", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage",
     ".venv", "venv", "results"}
)
MAX_BYTES = 2_000_000


def _git_files(root: Path) -> list[Path] | None:
    """Pliki objęte repozytorium (śledzone i nieignorowane); ``None`` poza repozytorium git."""
    if not (root / ".git").exists():
        return None
    try:
        output = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            check=True, capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return sorted(root / name for name in output.decode("utf-8", errors="ignore").split("\0") if name)


def iter_files(root: Path) -> Iterator[Path]:
    tracked = _git_files(root)
    if tracked is not None:
        yield from (path for path in tracked if path.is_file() and not path.is_symlink()
                    and not any(part in SKIPPED_DIRS for part in path.relative_to(root).parts[:-1]))
        return
    for path in sorted(root.rglob("*")):
        if any(part in SKIPPED_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        if path.is_file() and not path.is_symlink():
            yield path


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for path in iter_files(root):
        relative = path.relative_to(root)
        if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
            findings.append(f"{relative}: env_file (plik środowiska nie może być w repozytorium)")
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:4096]:
            continue  # plik binarny
        text = data.decode("utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), start=1):
            for kind, pattern in PATTERNS:
                if pattern.search(line):
                    findings.append(f"{relative}:{number}: {kind}")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("root", nargs="?", default=".", type=Path)
    args = parser.parse_args(argv)
    findings = scan(args.root.resolve())
    for finding in findings:
        print(finding)
    print(f"secret scan: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
