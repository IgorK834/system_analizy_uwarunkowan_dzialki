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


# --- Ekstrakcja modelem językowym (PV3-10/11, ADR-001, ADR-012) ----------------------------------------


import ast  # noqa: E402

_PLANNING = default_modules_root() / "planning"
_LLM_INFRA = _PLANNING / "infrastructure" / "llm"
_PORT_MODULE = "app.modules.planning.application.ports"
_PLANNING_DOMAIN = "app.modules.planning.domain"


def _class_names(path: Path) -> set[str]:
    return {node.name for node in ast.walk(ast.parse(path.read_text("utf-8"))) if isinstance(node, ast.ClassDef)}


def _app_imports(path: Path) -> set[str]:
    return {usage.module for usage in extract_imports(path.read_text("utf-8")) if usage.module.split(".")[0] == "app"}


def _external_imports(path: Path) -> set[str]:
    return {usage.module.split(".")[0] for usage in extract_imports(path.read_text("utf-8")) if usage.module.split(".")[0] != "app"}


# Repozytorium cache wywołań (PV3-13) i rejestr zużycia (PV3-15) są adapterami bazy: każdy zna dodatkowo
# wyłącznie własny model ORM.
_REPOSITORY_EXTRA = ("app.models.mpzp_llm_extraction",)
_ORM_EXTRA = {"repository.py": _REPOSITORY_EXTRA, "budget.py": ("app.models.mpzp_llm_usage",)}


def _adapter_violations(source: str, extra: tuple[str, ...] = ()) -> list[str]:
    """Adapter modelu językowego zna tylko port i własny pakiet — nie domenę MPZP, serwisy ani ustawienia."""
    allowed = (_PORT_MODULE, "app.modules.planning.infrastructure.llm", *extra)
    return [
        usage.module
        for usage in extract_imports(source)
        if usage.module.split(".")[0] == "app" and not any(usage.module == a or usage.module.startswith(a + ".") for a in allowed)
    ]


def test_the_extraction_port_is_in_the_application_layer() -> None:
    ports = _PLANNING / "application" / "ports.py"
    assert "StructuredExtractionProvider" in _class_names(ports)
    assert classify_path(ports, default_modules_root()) == ("planning", "application")
    # Protokół nie jest definiowany w żadnym adapterze ani w domenie.
    for path in list((_PLANNING / "infrastructure").rglob("*.py")) + list((_PLANNING / "domain").rglob("*.py")):
        assert "StructuredExtractionProvider" not in _class_names(path), path


def test_the_adapters_are_in_the_infrastructure_layer_and_implement_the_port() -> None:
    for name, class_name in (("gemini_provider.py", "GeminiStructuredExtractionProvider"), ("fake_provider.py", "ReplayStructuredExtractionProvider")):
        path = _LLM_INFRA / name
        assert path.is_file() and classify_path(path, default_modules_root()) == ("planning", "infrastructure")
        assert class_name in _class_names(path)
        assert _PORT_MODULE in _app_imports(path)  # adapter zależy od portu, nie odwrotnie


def test_the_adapters_do_not_know_the_mpzp_domain() -> None:
    files = sorted(p for p in _LLM_INFRA.glob("*.py") if p.name != "__init__.py")
    assert {p.name for p in files} >= {"gemini_provider.py", "fake_provider.py", "json_schema.py", "resilience.py"}
    for path in files:
        extra = _ORM_EXTRA.get(path.name, ())
        assert _adapter_violations(path.read_text("utf-8"), extra) == [], path
        assert not any(name.startswith(_PLANNING_DOMAIN) for name in _app_imports(path)), path


def test_the_adapter_rule_detects_a_domain_import() -> None:
    bad = (
        "from app.modules.planning.domain.rules import PlanningRuleCandidate\n"
        "from app.core.settings import settings\n"
        "from app.modules.planning.application.ports import StructuredExtractionRequest\n"
        "from app.modules.planning.infrastructure.llm.resilience import RetryPolicy\n"
    )
    assert _adapter_violations(bad) == ["app.modules.planning.domain.rules", "app.core.settings"]


def test_application_and_domain_of_the_extraction_do_not_use_http_or_settings() -> None:
    for path in (_PLANNING / "application" / "llm_extraction.py", _PLANNING / "application" / "ports.py",
                 _PLANNING / "domain" / "extraction_contract.py"):
        assert _external_imports(path).isdisjoint(FORBIDDEN_DOMAIN_IMPORTS | {"requests", "aiohttp", "google"}), path
        imports = _app_imports(path)
        assert not any(".infrastructure" in name or name.startswith("app.core") or name.startswith("app.services") for name in imports), path
    contract_imports = _app_imports(_PLANNING / "domain" / "extraction_contract.py")
    assert not any(name.startswith("app.modules.planning.application") for name in contract_imports)


def test_the_composition_root_is_the_only_place_that_wires_the_provider() -> None:
    wiring = [
        path for path in default_modules_root().rglob("*.py")
        if any(name.startswith("app.modules.planning.infrastructure.llm") for name in _app_imports(path))
        and "infrastructure/llm" not in str(path)
    ]
    assert [p.name for p in wiring] == ["composition.py"]
    api_imports = set()
    for path in (_PLANNING / "api").rglob("*.py"):
        api_imports |= _app_imports(path)
    assert not any("llm" in name for name in api_imports)  # API nie omija warstwy application



def test_the_llm_cache_repository_knows_only_the_port_and_its_orm_model() -> None:
    path = _LLM_INFRA / "repository.py"
    assert "SqlAlchemyLlmExtractionCache" in _class_names(path)
    assert _PORT_MODULE in _app_imports(path)
    assert _app_imports(path) <= {_PORT_MODULE, *_REPOSITORY_EXTRA}
    assert _adapter_violations("from app.models.analysis import Analysis\n", _REPOSITORY_EXTRA) == ["app.models.analysis"]


def test_the_verifier_and_the_pipeline_respect_the_layers() -> None:
    domain = _PLANNING / "domain" / "candidate_verifier.py"
    assert _external_imports(domain).isdisjoint(FORBIDDEN_DOMAIN_IMPORTS | {"requests", "aiohttp", "google"})
    assert all(name.startswith((_PLANNING_DOMAIN, "app.shared")) for name in _app_imports(domain))
    for name in ("llm_pipeline.py", "llm_metrics.py", "llm_monitoring.py", "llm_drift.py"):
        path = _PLANNING / "application" / name
        assert _external_imports(path).isdisjoint(FORBIDDEN_DOMAIN_IMPORTS | {"requests", "aiohttp", "google"}), path
        assert not any(".infrastructure" in item or item.startswith(("app.core", "app.services", "app.models"))
                       for item in _app_imports(path)), path


def test_the_usage_ledger_adapter_knows_only_the_port_and_its_orm_model() -> None:
    path = _LLM_INFRA / "budget.py"
    assert {"BudgetedStructuredExtractionProvider", "SqlAlchemyUsageLedger"} <= _class_names(path)
    assert _app_imports(path) <= {_PORT_MODULE, *_ORM_EXTRA["budget.py"]}


def test_the_model_pin_adapter_is_standard_library_only() -> None:
    """Przypięcie (PV3-19) to plik danych i czysta logika: bez domeny, ustawień, serwisów i sieci."""
    path = _LLM_INFRA / "pin.py"
    assert "ModelPin" in _class_names(path)
    assert _app_imports(path) == set()
    assert _external_imports(path).isdisjoint({"httpx", "requests", "aiohttp", "sqlalchemy", "pydantic"})
