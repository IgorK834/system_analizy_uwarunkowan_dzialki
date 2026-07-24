"""Test architektoniczny modularnego monolitu (Faza 10.2).

Weryfikuje reguły zależności warstw (domain/application/infrastructure/api) i
izolację modułów, korzystając z deterministycznej analizy AST
(app/core/architecture.py). Sprawdza realne drzewo modułów oraz kontrolowane
błędne i poprawne fixtures.
"""

from __future__ import annotations

from pathlib import Path

from app.core.architecture import (
    FORBIDDEN_DOMAIN_IMPORTS,
    LAYERS,
    Violation,
    check_architecture,
    classify_path,
    collect_module_files,
    default_modules_root,
    extract_imports,
    find_forbidden_domain_imports,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "architecture"
_BAD_ROOT = _FIXTURES / "bad"
_GOOD_ROOT = _FIXTURES / "good"

_REQUIRED_MODULES = {
    "parcels",
    "planning",
    "documents",
    "imports",
    "analysis",
    "provenance",
    "reporting",
    "identity",
}


def _rules(violations: list[Violation]) -> set[str]:
    return {violation.rule for violation in violations}


def _imported_names(violations: list[Violation]) -> set[str]:
    return {violation.imported for violation in violations}


# --- Realne drzewo modułów: brak naruszeń, kompletna struktura ---------------


def test_real_modules_have_no_architecture_violations() -> None:
    violations = check_architecture(default_modules_root())
    assert violations == [], "\n".join(str(v) for v in violations)


def test_all_required_modules_and_layers_exist() -> None:
    root = default_modules_root()
    for module in _REQUIRED_MODULES:
        module_dir = root / module
        assert (module_dir / "__init__.py").is_file(), module
        for layer in LAYERS:
            layer_init = module_dir / layer / "__init__.py"
            assert layer_init.is_file(), f"{module}/{layer}"


def test_shared_package_is_framework_free() -> None:
    # app.shared jest importowalny z domain, więc nie może wciągać FastAPI/ORM/httpx.
    shared_dir = Path(default_modules_root()).parent / "shared"
    for path in shared_dir.rglob("*.py"):
        imports = {usage.module for usage in extract_imports(path.read_text("utf-8"))}
        tops = {name.split(".")[0] for name in imports}
        assert tops.isdisjoint(FORBIDDEN_DOMAIN_IMPORTS), f"{path}: {tops}"


# --- Wykrywanie zabronionych importów w domain -------------------------------


def test_detects_fastapi_import_in_domain() -> None:
    violations = find_forbidden_domain_imports(_BAD_ROOT)
    assert "fastapi" in _imported_names(violations)


def test_detects_sqlalchemy_or_geoalchemy2_import_in_domain() -> None:
    names = _imported_names(find_forbidden_domain_imports(_BAD_ROOT))
    assert "sqlalchemy" in names
    assert "geoalchemy2" in names


def test_detects_httpx_import_in_domain() -> None:
    violations = find_forbidden_domain_imports(_BAD_ROOT)
    assert "httpx" in _imported_names(violations)


def test_detects_domain_illegal_app_dependency() -> None:
    violations = check_architecture(_BAD_ROOT)
    assert "domain_illegal_dependency" in _rules(violations)


def test_detects_api_bypassing_application() -> None:
    violations = check_architecture(_BAD_ROOT)
    assert "api_bypasses_application" in _rules(violations)


def test_detects_application_using_infrastructure() -> None:
    violations = check_architecture(_BAD_ROOT)
    assert "application_illegal_dependency" in _rules(violations)


def test_detects_cross_module_private_import() -> None:
    violations = check_architecture(_BAD_ROOT)
    assert "cross_module_private" in _rules(violations)


# --- Poprawne fixtures: import shared i protokołu application dozwolony -------


def test_good_fixtures_have_no_violations() -> None:
    violations = check_architecture(_GOOD_ROOT)
    assert violations == [], "\n".join(str(v) for v in violations)


# --- Testy jednostkowe analizatora (pokrycie) --------------------------------


def test_extract_imports_handles_import_and_from() -> None:
    source = (
        "import os\n"
        "import a.b.c\n"
        "from x.y import z\n"
        "from . import sibling\n"
        "from __future__ import annotations\n"
    )
    modules = {usage.module for usage in extract_imports(source)}
    assert "os" in modules
    assert "a.b.c" in modules
    assert "x.y" in modules
    assert "__future__" in modules
    # Import względny (level>0) jest pomijany.
    assert "sibling" not in modules


def test_classify_path_identifies_module_and_layer() -> None:
    root = default_modules_root()
    path = root / "parcels" / "domain" / "models.py"
    assert classify_path(path, root) == ("parcels", "domain")


def test_classify_path_returns_none_for_module_init() -> None:
    root = default_modules_root()
    assert classify_path(root / "parcels" / "__init__.py", root) is None


def test_classify_path_returns_none_outside_root() -> None:
    root = default_modules_root()
    assert classify_path(Path("/tmp/other/file.py"), root) is None


def test_collect_module_files_returns_layered_files_only() -> None:
    files = collect_module_files(default_modules_root())
    assert files, "spodziewano się plików warstw modułów"
    assert all(file.layer in LAYERS for file in files)


def test_violation_str_is_readable() -> None:
    violation = check_architecture(_BAD_ROOT)[0]
    text = str(violation)
    assert violation.rule in text
    assert str(violation.path) in text
