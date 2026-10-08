"""Linki lokalne w README i ``docs/current_state.md`` wskazują pliki śledzone przez git (AU-009, R1/B14).

Po ``git clone`` działają tylko pliki objęte repozytorium. Dokumenty, które README i ``current_state.md``
cytują jako dowody (ADR, wyniki ewaluacji, raporty postępu), były kiedyś wykluczone przez ``.gitignore`` —
linki do nich były martwe poza maszyną autora. Test sprawdza ``git ls-files``; bez git (np. archiwum
źródeł bez katalogu ``.git``) wraca do sprawdzenia istnienia pliku, co nie wykryje plików ignorowanych.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest

from tests.docker_compose_config import find_repo_root

DOCUMENTS = ("README.md", "docs/current_state.md")
# [tekst](cel) i [tekst](cel "tytuł"); cel bez spacji i nawiasów.
_LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_REFERENCE = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)", re.M)
_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#)", re.I)


@pytest.fixture(scope="module")
def repo() -> Path:
    return find_repo_root()


def _git_tracked(repo: Path) -> set[str] | None:
    try:
        result = subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", str(repo), "ls-files", "-z"],
            capture_output=True,
            check=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return {item.decode("utf-8") for item in result.stdout.split(b"\0") if item}


def _local_targets(document: Path) -> list[str]:
    text = document.read_text(encoding="utf-8")
    # Bloki kodu mogą zawierać przykłady z nawiasami — pomijamy je.
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    targets = [*_LINK.findall(text), *_REFERENCE.findall(text)]
    return [target for target in targets if not _EXTERNAL.match(target)]


def _normalize(target: str, document: Path, repo: Path) -> str:
    path = unquote(target.split("#", 1)[0].split("?", 1)[0])
    # Odnośnik do linii (`plik.py:42`) wskazuje plik.
    path = re.sub(r":\d+(?::\d+)?$", "", path)
    resolved = (document.parent / path).resolve()
    return resolved.relative_to(repo.resolve()).as_posix()


def _is_tracked(relative: str, tracked: set[str]) -> bool:
    if relative in tracked:
        return True
    prefix = relative.rstrip("/") + "/"
    return any(item.startswith(prefix) for item in tracked)  # katalog z plikami śledzonymi


def test_readme_links_resolve(repo: Path) -> None:
    tracked = _git_tracked(repo)
    broken: list[str] = []
    checked = 0
    for name in DOCUMENTS:
        document = repo / name
        for target in _local_targets(document):
            try:
                relative = _normalize(target, document, repo)
            except ValueError:
                broken.append(f"{name}: {target} (poza repozytorium)")
                continue
            checked += 1
            exists = (repo / relative).exists()
            if not exists or (tracked is not None and not _is_tracked(relative, tracked)):
                broken.append(f"{name}: {target} → {relative}")

    assert checked > 20, "test nie znalazł linków lokalnych — zmienił się format dokumentów?"
    assert broken == [], "linki lokalne bez pliku śledzonego przez git:\n" + "\n".join(broken)


def test_documents_cited_as_evidence_are_not_ignored(repo: Path) -> None:
    gitignore = (repo / ".gitignore").read_text(encoding="utf-8").splitlines()
    patterns = {line.strip() for line in gitignore if line.strip() and not line.startswith("#")}

    for ignored in ("docs/evaluation", "docs/adr", "docs/progress", "backlog.md", "docs/cureent_state.md"):
        assert ignored not in patterns and ignored.rstrip("/") + "/" not in patterns, ignored
    # Prywatne notatki robocze zostają poza repozytorium.
    assert {"context.md", "ANALIZA_ARCHITEKTURY_I_PLAN.md"} <= patterns


def test_removed_scratch_script_is_gone(repo: Path) -> None:
    assert not (repo / "backend" / "_imp.py").exists()


def test_large_binary_artifacts_are_tracked_by_lfs_or_stay_under_the_limit(repo: Path) -> None:
    """Artefakty w ``docs/evaluation/results`` ≤ 5 MB albo objęte regułą LFS w ``.gitattributes``."""
    limit = 5 * 1024 * 1024
    attributes = (repo / ".gitattributes").read_text(encoding="utf-8") if (repo / ".gitattributes").exists() else ""
    lfs_patterns = [line.split()[0] for line in attributes.splitlines() if "filter=lfs" in line]
    results = repo / "docs" / "evaluation" / "results"

    oversized = [
        path.relative_to(repo).as_posix()
        for path in results.rglob("*")
        if path.is_file()
        and path.stat().st_size > limit
        and not any(path.match(pattern) for pattern in lfs_patterns)
    ]

    assert oversized == []
