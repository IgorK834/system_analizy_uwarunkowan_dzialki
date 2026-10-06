"""Przypięcie wersji modelu i promptu ścieżki modelu językowego (PV3-19, ADR-012).

``model_pin.json`` zapisuje parę (model, prompt), która została — albo ma zostać — oceniona na
zbiorze złotym biegiem ``--live`` ewaluatora. Przypięcie jest kontrolowane w trzech miejscach:

* **w czasie działania** (``verify_pin``): ustawienia i kod muszą odpowiadać przypięciu; rozbieżność
  zmienia komponent zdrowia na ``degraded`` i — domyślnie — wyłącza ścieżkę modelu (analiza zostaje
  deterministyczna z ostrzeżeniem), bo nieoceniony model nie ma zmierzonej jakości;
* **w CI i przed wdrożeniem** (``scripts/check_llm_pin.py``, ``tests/test_llm_pin.py``): zmiana
  ``model_id``, wersji lub skrótu promptu, wersji lub skrótu schematu bez aktualizacji przypięcia jest
  wykrywana; ``--require-evaluation`` dodatkowo żąda zapisu ponownej ewaluacji zgodnego z przypięciem;
* **w dzienniku zmian ADR-012**: każda para (model, prompt) ma wiersz z wynikiem oceny.

Moduł jest czystą biblioteką standardową (adapter nie zna domeny ani ustawień).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

PIN_PATH: Final[Path] = Path(__file__).with_name("model_pin.json")
PIN_SCHEMA: Final[str] = "mpzp-llm-pin/1"
ADR_LOG_HEADING: Final[str] = "## Dziennik zmian przypięcia modelu i promptu"

EVALUATION_PENDING: Final[str] = "pending"
EVALUATION_RECORDED: Final[str] = "recorded"

# Kody problemów (stałe, bez treści): używane w zdrowiu, logach, skrypcie kontrolnym i testach.
MODEL_ID_DIFFERS: Final[str] = "model_id_differs"
PROMPT_VERSION_DIFFERS: Final[str] = "prompt_version_differs"
PROMPT_SHA_DIFFERS: Final[str] = "prompt_sha256_differs"
SCHEMA_VERSION_DIFFERS: Final[str] = "schema_version_differs"
SCHEMA_SHA_DIFFERS: Final[str] = "schema_sha256_differs"
THINKING_LEVEL_DIFFERS: Final[str] = "thinking_level_differs"
EVALUATION_NOT_RECORDED: Final[str] = "evaluation_not_recorded"
EVALUATION_NOT_LIVE: Final[str] = "evaluation_not_live"
EVALUATION_NOT_HYBRID: Final[str] = "evaluation_not_hybrid"
EVALUATION_NOTHING_RECORDED: Final[str] = "evaluation_no_responses_recorded"
EVALUATION_FOR_OTHER_PIN: Final[str] = "evaluation_for_other_pin"
EVALUATION_ARTIFACT_MISSING: Final[str] = "evaluation_artifact_missing"
EVALUATION_ARTIFACT_CHANGED: Final[str] = "evaluation_artifact_changed"
ADR_LOG_ENTRY_MISSING: Final[str] = "adr_log_entry_missing"


class PinError(ValueError):
    """Przypięcie jest nieczytelne albo niepoprawne (kod w ``args[0]``)."""


@dataclass(frozen=True)
class ModelPin:
    provider: str
    model_id: str
    prompt_version: str
    prompt_sha256: str
    schema_version: str
    schema_sha256: str
    thinking_level: str
    adr_log: str
    evaluation: Mapping[str, Any]

    @property
    def evaluation_recorded(self) -> bool:
        return self.evaluation.get("status") == EVALUATION_RECORDED

    def as_dict(self) -> dict[str, Any]:
        return {
            "adr_log": self.adr_log,
            "evaluation": dict(self.evaluation),
            "model_id": self.model_id,
            "prompt_sha256": self.prompt_sha256,
            "prompt_version": self.prompt_version,
            "provider": self.provider,
            "schema": PIN_SCHEMA,
            "schema_sha256": self.schema_sha256,
            "schema_version": self.schema_version,
            "thinking_level": self.thinking_level,
        }


def parse_pin(text: str) -> ModelPin:
    try:
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("schema") != PIN_SCHEMA:
            raise PinError("pin_schema")
        evaluation = data["evaluation"]
        if not isinstance(evaluation, dict) or evaluation.get("status") not in (EVALUATION_PENDING, EVALUATION_RECORDED):
            raise PinError("pin_evaluation")
        return ModelPin(
            provider=str(data["provider"]),
            model_id=str(data["model_id"]),
            prompt_version=str(data["prompt_version"]),
            prompt_sha256=str(data["prompt_sha256"]),
            schema_version=str(data["schema_version"]),
            schema_sha256=str(data["schema_sha256"]),
            thinking_level=str(data["thinking_level"]),
            adr_log=str(data["adr_log"]),
            evaluation=evaluation,
        )
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise PinError("pin_unreadable") from exc


def load_pin(path: Path = PIN_PATH) -> ModelPin:
    try:
        return parse_pin(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PinError("pin_missing") from exc


def render_pin(pin: ModelPin) -> str:
    return json.dumps(pin.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def verify_pin(
    pin: ModelPin,
    *,
    model_id: str,
    prompt_version: str,
    prompt_sha256: str,
    schema_version: str,
    schema_sha256: str,
    thinking_level: str | None = None,
) -> tuple[str, ...]:
    """Rozbieżności między tym, co działa (ustawienia i kod), a przypięciem; pusta krotka = zgodne."""
    problems: list[str] = []
    if model_id != pin.model_id:
        problems.append(MODEL_ID_DIFFERS)
    if prompt_version != pin.prompt_version:
        problems.append(PROMPT_VERSION_DIFFERS)
    if prompt_sha256 != pin.prompt_sha256:
        problems.append(PROMPT_SHA_DIFFERS)
    if schema_version != pin.schema_version:
        problems.append(SCHEMA_VERSION_DIFFERS)
    if schema_sha256 != pin.schema_sha256:
        problems.append(SCHEMA_SHA_DIFFERS)
    if thinking_level is not None and thinking_level != pin.thinking_level:
        problems.append(THINKING_LEVEL_DIFFERS)
    return tuple(problems)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluation_problems(pin: ModelPin, repo_root: Path | None = None) -> tuple[str, ...]:
    """Czy zapisana ewaluacja dotyczy DOKŁADNIE tego przypięcia (model, prompt, schemat) i jest biegiem na żywo."""
    evaluation = pin.evaluation
    if not pin.evaluation_recorded:
        return (EVALUATION_NOT_RECORDED,)
    problems: list[str] = []
    if evaluation.get("engine") != "hybrid":
        problems.append(EVALUATION_NOT_HYBRID)
    if evaluation.get("mode") != "live":
        problems.append(EVALUATION_NOT_LIVE)
    if not isinstance(evaluation.get("responses_recorded"), int) or evaluation["responses_recorded"] < 1:
        problems.append(EVALUATION_NOTHING_RECORDED)
    for field in ("model_id", "prompt_version", "prompt_sha256", "schema_version", "schema_sha256"):
        if evaluation.get(field) != getattr(pin, field):
            problems.append(EVALUATION_FOR_OTHER_PIN)
            break
    if repo_root is not None:
        relative = evaluation.get("run_manifest")
        artifact = repo_root / str(relative) if relative else None
        if artifact is None or not artifact.is_file():
            problems.append(EVALUATION_ARTIFACT_MISSING)
        elif sha256_file(artifact) != evaluation.get("run_manifest_sha256"):
            problems.append(EVALUATION_ARTIFACT_CHANGED)
    return tuple(dict.fromkeys(problems))


def adr_log_problems(adr_text: str, pin: ModelPin) -> tuple[str, ...]:
    """Dziennik zmian ADR musi mieć wiersz tabeli z modelem, wersją promptu i skrótem promptu (12 znaków)."""
    if ADR_LOG_HEADING not in adr_text:
        return (ADR_LOG_ENTRY_MISSING,)
    section = adr_text.split(ADR_LOG_HEADING, 1)[1].split("\n## ", 1)[0]
    needles = (pin.model_id, pin.prompt_version, pin.prompt_sha256[:12])
    for line in section.splitlines():
        if line.lstrip().startswith("|") and all(needle in line for needle in needles):
            return ()
    return (ADR_LOG_ENTRY_MISSING,)


def build_evaluation_record(
    pin: ModelPin, run_manifest: Mapping[str, Any], manifest_bytes: bytes, relative_path: str, recorded_at: str
) -> dict[str, Any]:
    """Zapis ewaluacji z ``run_manifest.json`` biegu ``--live`` silnika ``hybrid``.

    Odrzuca manifest, który nie jest biegiem hybrydy na żywo z zapisanymi odpowiedziami albo nie
    nazywa DOKŁADNIE przypiętego modelu i promptu (``PinError`` z kodem).
    """
    llm = run_manifest.get("llm") if isinstance(run_manifest.get("llm"), Mapping) else {}
    record: dict[str, Any] = {
        "status": EVALUATION_RECORDED,
        "recorded_at": recorded_at,
        "run_manifest": relative_path,
        "run_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "manifest_sha256": run_manifest.get("manifest_sha256"),
        "corpus_sha256": run_manifest.get("corpus_sha256"),
        "annotations_sha256": run_manifest.get("annotations_sha256"),
        "engine": run_manifest.get("engine"),
        "mode": llm.get("mode"),
        "responses_recorded": llm.get("recorded"),
        "model_id": llm.get("model_id"),
        "prompt_version": llm.get("prompt_version"),
        "prompt_sha256": llm.get("prompt_sha256"),
        "schema_version": llm.get("schema_version"),
        "schema_sha256": llm.get("schema_sha256"),
    }
    candidate = ModelPin(
        pin.provider, pin.model_id, pin.prompt_version, pin.prompt_sha256, pin.schema_version, pin.schema_sha256,
        pin.thinking_level, pin.adr_log, record,
    )
    problems = evaluation_problems(candidate)
    if problems:
        raise PinError(",".join(problems))
    return record
