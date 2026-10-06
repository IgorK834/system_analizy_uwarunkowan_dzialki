"""Przypięcie modelu i promptu (PV3-19): zmiana bez ponownej ewaluacji i wpisu w ADR jest wykrywana.

Przypięcie (``model_pin.json``) zapisuje parę (model, prompt), która została oceniona biegiem ``--live`` na
zbiorze złotym. Testy sprawdzają: zgodność przypięcia z ustawieniami, kodem, Compose, ``.env.example``, złotymi
odpowiedziami i ADR; wykrycie zmiany ``model_id``, ``prompt_version``, treści promptu i schematu; zapis ewaluacji
z manifestu biegu (tylko ``hybrid`` + ``live`` + dokładnie ta para) i jego integralność. Bez sieci i bez klucza.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from app.core.settings import Settings
from app.modules.planning.domain import extraction_contract as contract
from app.modules.planning.infrastructure.llm import pin as model_pin
from scripts import check_llm_pin as script
from scripts import evaluate_mpzp_parser as ev
from tests.parcel_fixtures_config import find_repo_root

REPO = find_repo_root()
PIN = model_pin.load_pin()
IDENTITY = {
    "model_id": PIN.model_id,
    "prompt_version": PIN.prompt_version,
    "prompt_sha256": PIN.prompt_sha256,
    "schema_version": PIN.schema_version,
    "schema_sha256": PIN.schema_sha256,
}


def verify(**overrides: Any) -> tuple[str, ...]:
    args: dict[str, Any] = {**IDENTITY, "thinking_level": PIN.thinking_level}
    args.update(overrides)
    return model_pin.verify_pin(PIN, **args)


# --- przypięcie zgadza się z kodem ---------------------------------------------------------------------


def test_the_committed_pin_matches_the_code_the_settings_and_the_docs() -> None:
    assert verify(
        prompt_version=contract.PROMPT_VERSION, prompt_sha256=contract.prompt_sha256(),
        schema_version=contract.SCHEMA_VERSION, schema_sha256=contract.SCHEMA_SHA256,
        model_id=Settings.model_fields["mpzp_llm_model"].default,
    ) == ()
    assert script.check(REPO, require_evaluation=False) == []


def test_the_pin_file_is_canonical_json_and_has_no_secrets() -> None:
    text = model_pin.PIN_PATH.read_text(encoding="utf-8")
    assert text == model_pin.render_pin(model_pin.parse_pin(text))  # stabilne diffy: klucze posortowane
    assert "AIza" not in text and "api_key" not in text.lower()
    assert PIN.provider == "gemini" and PIN.thinking_level == Settings.model_fields["mpzp_llm_thinking_level"].default


def test_the_golden_replay_is_keyed_by_the_pinned_model() -> None:
    from scripts import build_llm_replay_fixtures as golden

    assert golden.MODEL == PIN.model_id


def test_the_pending_evaluation_is_visible_and_blocks_the_release_gate() -> None:
    assert PIN.evaluation_recorded is False
    assert script.check(REPO, require_evaluation=True) == [model_pin.EVALUATION_NOT_RECORDED]
    assert script.main(["check", "--require-evaluation"], repo_root=REPO) == 1
    assert script.main(["check"], repo_root=REPO) == 0


# --- wykrywanie zmian -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"model_id": "gemini-3.9-flash"}, model_pin.MODEL_ID_DIFFERS),
        ({"prompt_version": "mpzp-extraction/2"}, model_pin.PROMPT_VERSION_DIFFERS),
        ({"prompt_sha256": "0" * 64}, model_pin.PROMPT_SHA_DIFFERS),
        ({"schema_version": "mpzp-extraction-schema/2"}, model_pin.SCHEMA_VERSION_DIFFERS),
        ({"schema_sha256": "f" * 64}, model_pin.SCHEMA_SHA_DIFFERS),
        ({"thinking_level": "high"}, model_pin.THINKING_LEVEL_DIFFERS),
    ],
)
def test_a_change_of_model_prompt_or_schema_is_detected(overrides: dict[str, Any], code: str) -> None:
    assert verify(**overrides) == (code,)


def test_a_one_character_prompt_edit_changes_the_hash_and_so_breaks_the_pin() -> None:
    edited = contract.prompt_sha256()[:-1] + ("0" if contract.prompt_sha256()[-1] != "0" else "1")
    assert verify(prompt_sha256=edited) == (model_pin.PROMPT_SHA_DIFFERS,)
    # Bez podniesienia wersji promptu sama zmiana treści jest już wykryta (hash); wersja jest drugą barierą.
    assert verify(prompt_sha256=edited, prompt_version="mpzp-extraction/2") == (
        model_pin.PROMPT_VERSION_DIFFERS, model_pin.PROMPT_SHA_DIFFERS,
    )


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    """Minimalna kopia repozytorium z plikami, które kontrola porównuje z przypięciem."""
    root = tmp_path / "repo"
    (root / "docs" / "adr").mkdir(parents=True)
    for name in ("docker-compose.yml", ".env.example"):
        shutil.copy(REPO / name, root / name)
    shutil.copy(REPO / script.ADR_PATH, root / script.ADR_PATH)
    return root


def test_a_repository_copy_is_consistent(repo_copy: Path) -> None:
    assert script.check(repo_copy, require_evaluation=False) == []


def test_a_model_changed_in_compose_or_env_example_without_the_pin_is_detected(repo_copy: Path) -> None:
    compose = repo_copy / "docker-compose.yml"
    compose.write_text(compose.read_text().replace(f":-{PIN.model_id}}}", ":-gemini-3.9-flash}"), encoding="utf-8")
    env = repo_copy / ".env.example"
    env.write_text(env.read_text().replace(f"MPZP_LLM_MODEL={PIN.model_id}", "MPZP_LLM_MODEL=gemini-3.9-flash"), encoding="utf-8")
    problems = script.check(repo_copy, require_evaluation=False)
    assert problems == [script.COMPOSE_MODEL_DIFFERS, script.ENV_EXAMPLE_MODEL_DIFFERS]


def test_a_prompt_version_changed_in_compose_or_env_example_without_the_pin_is_detected(repo_copy: Path) -> None:
    compose = repo_copy / "docker-compose.yml"
    compose.write_text(compose.read_text().replace(f":-{PIN.prompt_version}}}", ":-mpzp-extraction/2}"), encoding="utf-8")
    env = repo_copy / ".env.example"
    env.write_text(env.read_text().replace(f"PROMPT_VERSION={PIN.prompt_version}", "PROMPT_VERSION=mpzp-extraction/2"), encoding="utf-8")
    assert script.check(repo_copy, require_evaluation=False) == [script.COMPOSE_PROMPT_DIFFERS, script.ENV_EXAMPLE_PROMPT_DIFFERS]


def test_the_adr_change_log_must_name_the_pinned_pair(repo_copy: Path) -> None:
    adr = repo_copy / script.ADR_PATH
    original = adr.read_text(encoding="utf-8")
    adr.write_text(original.replace(PIN.prompt_sha256[:12], "000000000000"), encoding="utf-8")
    assert script.check(repo_copy, require_evaluation=False) == [model_pin.ADR_LOG_ENTRY_MISSING]
    adr.write_text(original.split(model_pin.ADR_LOG_HEADING)[0], encoding="utf-8")
    assert script.check(repo_copy, require_evaluation=False) == [model_pin.ADR_LOG_ENTRY_MISSING]
    adr.unlink()
    assert script.check(repo_copy, require_evaluation=False) == [model_pin.ADR_LOG_ENTRY_MISSING]


def test_a_changed_pin_in_the_file_is_detected_against_the_code(tmp_path: Path, repo_copy: Path) -> None:
    changed = json.loads(model_pin.PIN_PATH.read_text(encoding="utf-8"))
    changed["model_id"] = "gemini-3.9-flash"
    path = tmp_path / "pin.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    problems = script.check(repo_copy, require_evaluation=False, pin_path=path)
    assert model_pin.MODEL_ID_DIFFERS in problems and script.COMPOSE_MODEL_DIFFERS in problems
    assert script.main(["check"], repo_root=repo_copy, pin_path=path) == 1


# --- zapis ewaluacji z biegu --live ---------------------------------------------------------------------


def run_manifest(**overrides: Any) -> dict[str, Any]:
    llm = {"mode": "live", "recorded": 13, "replayed": 0, "responses_in_store": 13, **IDENTITY}
    llm.update(overrides.pop("llm", {}))
    manifest = {
        "engine": "hybrid", "engine_version": "mpzp-parser/3.0-det+llm", "manifest_sha256": "a" * 64,
        "corpus_sha256": "b" * 64, "annotations_sha256": "c" * 64, "llm": llm,
    }
    manifest.update(overrides)
    return manifest


def write_manifest(root: Path, manifest: dict[str, Any], name: str = "live-run") -> Path:
    path = root / "docs" / "evaluation" / "results" / name / "hybrid" / "run_manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def pin_copy(tmp_path: Path) -> Path:
    path = tmp_path / "model_pin.json"
    shutil.copy(model_pin.PIN_PATH, path)
    return path


def test_a_live_run_of_the_pinned_pair_is_recorded_and_unlocks_the_release_gate(repo_copy: Path, pin_copy: Path) -> None:
    manifest_path = write_manifest(repo_copy, run_manifest())
    assert script.main(
        ["record-evaluation", "--run-manifest", str(manifest_path)], repo_root=repo_copy, pin_path=pin_copy
    ) == 0
    recorded = model_pin.load_pin(pin_copy)
    assert recorded.evaluation_recorded
    assert recorded.evaluation["run_manifest"] == "docs/evaluation/results/live-run/hybrid/run_manifest.json"
    assert recorded.evaluation["run_manifest_sha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert recorded.evaluation["responses_recorded"] == 13 and recorded.evaluation["mode"] == "live"
    assert script.check(repo_copy, require_evaluation=True, pin_path=pin_copy) == []
    assert script.main(["check", "--require-evaluation"], repo_root=repo_copy, pin_path=pin_copy) == 0


def test_a_tampered_evaluation_artifact_is_detected(repo_copy: Path, pin_copy: Path) -> None:
    manifest_path = write_manifest(repo_copy, run_manifest())
    script.record_evaluation(manifest_path, repo_copy, pin_path=pin_copy, recorded_at="2026-10-06T00:00:00Z")
    manifest_path.write_text(manifest_path.read_text() + " ", encoding="utf-8")
    assert script.check(repo_copy, require_evaluation=True, pin_path=pin_copy) == [model_pin.EVALUATION_ARTIFACT_CHANGED]
    manifest_path.unlink()
    assert script.check(repo_copy, require_evaluation=True, pin_path=pin_copy) == [model_pin.EVALUATION_ARTIFACT_MISSING]


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"llm": {"mode": "replay"}}, model_pin.EVALUATION_NOT_LIVE),
        ({"llm": {"recorded": 0}}, model_pin.EVALUATION_NOTHING_RECORDED),
        ({"engine": "v3"}, model_pin.EVALUATION_NOT_HYBRID),
        ({"llm": {"model_id": "gemini-3.9-flash"}}, model_pin.EVALUATION_FOR_OTHER_PIN),
        ({"llm": {"prompt_sha256": "0" * 64}}, model_pin.EVALUATION_FOR_OTHER_PIN),
        ({"llm": {"schema_sha256": "0" * 64}}, model_pin.EVALUATION_FOR_OTHER_PIN),
    ],
)
def test_an_evaluation_of_another_pair_or_not_live_is_refused(
    repo_copy: Path, pin_copy: Path, overrides: dict[str, Any], code: str
) -> None:
    manifest_path = write_manifest(repo_copy, run_manifest(**overrides))
    before = pin_copy.read_text(encoding="utf-8")
    assert script.main(["record-evaluation", "--run-manifest", str(manifest_path)], repo_root=repo_copy, pin_path=pin_copy) == 2
    assert pin_copy.read_text(encoding="utf-8") == before  # odmowa niczego nie zapisuje
    with pytest.raises(model_pin.PinError, match=code):
        script.record_evaluation(manifest_path, repo_copy, pin_path=pin_copy)


def test_a_manifest_outside_the_repository_or_unreadable_is_refused(repo_copy: Path, pin_copy: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere.json"
    outside.write_text(json.dumps(run_manifest()), encoding="utf-8")
    assert script.main(["record-evaluation", "--run-manifest", str(outside)], repo_root=repo_copy, pin_path=pin_copy) == 2
    assert script.main(["record-evaluation", "--run-manifest", str(tmp_path / "none.json")], repo_root=repo_copy, pin_path=pin_copy) == 2
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert script.main(["check"], repo_root=repo_copy, pin_path=broken) == 2


def test_the_pin_parser_rejects_foreign_or_broken_files() -> None:
    for text in ("", "[]", '{"schema": "x"}', json.dumps({**PIN.as_dict(), "evaluation": {"status": "maybe"}}),
                 json.dumps({k: v for k, v in PIN.as_dict().items() if k != "model_id"})):
        with pytest.raises(model_pin.PinError):
            model_pin.parse_pin(text)
    with pytest.raises(model_pin.PinError):
        model_pin.load_pin(Path("/nonexistent/model_pin.json"))


# --- ewaluator zapisuje, co oceniono ---------------------------------------------------------------------


def test_the_evaluator_records_the_model_and_prompt_it_evaluated() -> None:
    engine = ev.HybridEngine(object())
    identity = ev._llm_identity(engine)
    assert identity == IDENTITY  # domyślnie dokładnie przypięta para
    other = ev._llm_identity(ev.HybridEngine(object(), "gemini-3.9-flash"))
    assert other["model_id"] == "gemini-3.9-flash" and other["prompt_sha256"] == PIN.prompt_sha256


def test_the_evaluator_accepts_a_model_option() -> None:
    args = ev.build_parser().parse_args(["--engine", "hybrid", "--model", "gemini-3.9-flash"])
    assert args.model == "gemini-3.9-flash"
    assert ev.build_parser().parse_args([]).model is None


def test_the_run_manifest_block_names_the_evaluated_pair_and_feeds_record_evaluation(tmp_path: Path) -> None:
    from types import SimpleNamespace

    gateway = SimpleNamespace(
        mode="live", store=SimpleNamespace(digest=lambda: "d" * 64, keys=lambda: ["k"] * 13), replayed=0, recorded=13
    )
    info = ev._llm_run_info(gateway, ev.HybridEngine(gateway))
    assert info["mode"] == "live" and info["recorded"] == 13 and info["responses_in_store"] == 13
    assert {key: info[key] for key in IDENTITY} == IDENTITY
    manifest = {"engine": "hybrid", "manifest_sha256": "a" * 64, "corpus_sha256": "b" * 64,
                "annotations_sha256": "c" * 64, "llm": info}
    record = model_pin.build_evaluation_record(PIN, manifest, b"{}", "docs/x/run_manifest.json", "2026-10-06T00:00:00Z")
    assert record["status"] == "recorded" and record["mode"] == "live" and record["responses_recorded"] == 13


def test_a_full_hybrid_run_offline_stops_where_the_golden_replay_has_no_response() -> None:
    """Złote odpowiedzi pokrywają 13 bloków: pełny korpus wymaga nagrań na żywo i nie jest po cichu uzupełniany."""
    from scripts import build_llm_replay_fixtures as golden

    code = ev.main(["--engine", "hybrid", "--llm-replay", str(golden.DEFAULT_OUTPUT_DIR), "--output-dir", "/nonexistent-out"])
    assert code == 1
