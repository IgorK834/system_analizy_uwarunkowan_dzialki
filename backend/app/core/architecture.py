"""Deterministyczna analiza importów wymuszająca granice modularnego monolitu.

Moduł statycznie (przez AST) sprawdza reguły zależności między warstwami
``domain`` / ``application`` / ``infrastructure`` / ``api`` oraz między modułami
(patrz docs/adr/ADR-001-modular-monolith.md). Jest używany przez test
architektoniczny (tests/test_architecture.py) na realnym drzewie modułów oraz na
kontrolowanych błędnych fixtures.

Analiza jest deterministyczna i nie wykonuje analizowanego kodu — pliki są
wyłącznie parsowane, dzięki czemu bezpiecznie sprawdza także moduły z celowo
zabronionymi importami.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# Warstwy modułu w kolejności od najbardziej wewnętrznej.
LAYERS: Final[tuple[str, ...]] = ("domain", "application", "infrastructure", "api")

# Biblioteki zewnętrzne zakazane w warstwie domenowej. Domain ma pozostać czysty
# — bez frameworka webowego, ORM/GIS ani klienta HTTP.
FORBIDDEN_DOMAIN_IMPORTS: Final[frozenset[str]] = frozenset(
    {
        "fastapi",
        "starlette",
        "sqlalchemy",
        "geoalchemy2",
        "httpx",
    }
)

_SHARED_PACKAGE: Final[str] = "app.shared"
_MODULES_PACKAGE: Final[str] = "app.modules"


@dataclass(frozen=True)
class ImportUsage:
    """Pojedynczy import wykryty w pliku (pełna, kropkowana nazwa modułu)."""

    module: str
    lineno: int


@dataclass(frozen=True)
class ModuleFile:
    """Plik przypisany do modułu i warstwy wraz z wykrytymi importami."""

    path: Path
    module: str
    layer: str
    imports: tuple[ImportUsage, ...]


@dataclass(frozen=True)
class Violation:
    """Naruszenie reguły architektonicznej."""

    rule: str
    module: str
    layer: str
    imported: str
    path: Path
    lineno: int

    def __str__(self) -> str:
        return (
            f"[{self.rule}] {self.module}/{self.layer}: import {self.imported!r} "
            f"({self.path}:{self.lineno})"
        )


def default_modules_root() -> Path:
    """Zwraca katalog ``app/modules`` względem tego pliku."""
    return Path(__file__).resolve().parent.parent / "modules"


def extract_imports(source: str, filename: str = "<unknown>") -> list[ImportUsage]:
    """Wyciąga pełne nazwy importowanych modułów z kodu źródłowego.

    Obsługuje ``import a.b.c`` (zapisuje ``a.b.c``) oraz ``from a.b import c``
    (zapisuje ``a.b``). Importy względne (``from . import x``) są pomijane, bo
    dotyczą wnętrza tego samego pakietu.
    """
    tree = ast.parse(source, filename=filename)
    usages: list[ImportUsage] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                usages.append(ImportUsage(module=alias.name, lineno=node.lineno))
        elif isinstance(node, ast.ImportFrom):
            # level > 0 oznacza import względny — pomijamy (wnętrze pakietu).
            if node.level == 0 and node.module:
                usages.append(ImportUsage(module=node.module, lineno=node.lineno))
    return usages


def classify_path(path: Path, root: Path) -> tuple[str, str] | None:
    """Zwraca (moduł, warstwa) dla pliku pod ``root/<moduł>/<warstwa>/...``.

    Zwraca None, gdy pliku nie da się przypisać do warstwy (np. ``__init__.py``
    samego pakietu modułu).
    """
    try:
        relative = path.relative_to(root)
    except ValueError:
        return None
    parts = relative.parts
    if len(parts) >= 2 and parts[1] in LAYERS:
        return parts[0], parts[1]
    return None


def collect_module_files(root: Path) -> list[ModuleFile]:
    """Zbiera pliki ``.py`` przypisane do warstw modułów pod ``root``."""
    module_files: list[ModuleFile] = []
    for path in sorted(root.rglob("*.py")):
        classified = classify_path(path, root)
        if classified is None:
            continue
        module, layer = classified
        imports = tuple(extract_imports(path.read_text(encoding="utf-8"), str(path)))
        module_files.append(
            ModuleFile(path=path, module=module, layer=layer, imports=imports)
        )
    return module_files


def _top_level(module: str) -> str:
    return module.split(".", maxsplit=1)[0]


def _starts_with_package(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


def _is_app_import(name: str) -> bool:
    return _starts_with_package(name, "app")


def _has_private_component(name: str) -> bool:
    # Prywatny element to komponent ścieżki zaczynający się od "_"
    # (z pominięciem dunderów typu __init__, które nie są elementem publicznego API,
    # ale też nie są importowane wprost).
    return any(part.startswith("_") and part != "" for part in name.split("."))


def _cross_module_violations(file: ModuleFile, imported: str) -> list[Violation]:
    """Reguły izolacji między modułami (import prywatnych implementacji)."""
    violations: list[Violation] = []
    if not _starts_with_package(imported, _MODULES_PACKAGE):
        return violations

    parts = imported.split(".")
    # app.modules.<other_module>.<layer>...
    if len(parts) < 3:
        return violations
    other_module = parts[2]
    if other_module == file.module:
        return violations  # własny moduł — reguły wewnątrzmodułowe sprawdzamy osobno

    other_layer = parts[3] if len(parts) > 3 else ""
    if other_layer == "infrastructure":
        violations.append(
            _violation("cross_module_infrastructure", file, imported)
        )
    if _has_private_component(imported):
        violations.append(_violation("cross_module_private", file, imported))
    return violations


def _violation(rule: str, file: ModuleFile, imported: str) -> Violation:
    lineno = next(
        (usage.lineno for usage in file.imports if usage.module == imported), 0
    )
    return Violation(
        rule=rule,
        module=file.module,
        layer=file.layer,
        imported=imported,
        path=file.path,
        lineno=lineno,
    )


def _domain_violations(file: ModuleFile, imported: str) -> list[Violation]:
    violations: list[Violation] = []
    if _top_level(imported) in FORBIDDEN_DOMAIN_IMPORTS:
        violations.append(_violation("domain_forbidden_framework", file, imported))
    if _is_app_import(imported):
        allowed = _starts_with_package(
            imported, _SHARED_PACKAGE
        ) or _starts_with_package(imported, f"{_MODULES_PACKAGE}.{file.module}.domain")
        if not allowed:
            violations.append(_violation("domain_illegal_dependency", file, imported))
    return violations


def _application_violations(file: ModuleFile, imported: str) -> list[Violation]:
    if not _is_app_import(imported):
        return []
    allowed = (
        _starts_with_package(imported, _SHARED_PACKAGE)
        or _starts_with_package(imported, f"{_MODULES_PACKAGE}.{file.module}.domain")
        or _starts_with_package(
            imported, f"{_MODULES_PACKAGE}.{file.module}.application"
        )
    )
    if not allowed:
        return [_violation("application_illegal_dependency", file, imported)]
    return []


def _api_violations(file: ModuleFile, imported: str) -> list[Violation]:
    # API nie może omijać application i sięgać do infrastruktury.
    if _starts_with_package(imported, _MODULES_PACKAGE) and ".infrastructure" in (
        "." + imported
    ):
        parts = imported.split(".")
        layer = parts[3] if len(parts) > 3 else ""
        if layer == "infrastructure":
            return [_violation("api_bypasses_application", file, imported)]
    return []


def check_architecture(root: Path | None = None) -> list[Violation]:
    """Zwraca listę wszystkich naruszeń reguł architektonicznych pod ``root``."""
    active_root = root if root is not None else default_modules_root()
    violations: list[Violation] = []
    for file in collect_module_files(active_root):
        for usage in file.imports:
            imported = usage.module
            if file.layer == "domain":
                violations.extend(_domain_violations(file, imported))
            elif file.layer == "application":
                violations.extend(_application_violations(file, imported))
            elif file.layer == "api":
                violations.extend(_api_violations(file, imported))
            violations.extend(_cross_module_violations(file, imported))
    return violations


def find_forbidden_domain_imports(root: Path | None = None) -> list[Violation]:
    """Zwraca wyłącznie naruszenia zakazanych importów w warstwie domenowej."""
    return [
        violation
        for violation in check_architecture(root)
        if violation.rule == "domain_forbidden_framework"
    ]
