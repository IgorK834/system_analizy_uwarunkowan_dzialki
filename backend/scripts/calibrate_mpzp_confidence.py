"""Kalibracja pewności wartości parametrów MPZP (PV3-09) i sprawdzenie odtwarzalności artefaktu.

Dopasowuje regresję logistyczną cech dowodu → prawdopodobieństwo poprawności wartości na PODZIALE
KALIBRACYJNYM zamrożonego korpusu BK-603 (domyślnie ``development``), wyznacza z pomiaru progi pasm
``low``/``medium``/``high`` oraz próg ``manual_review_required`` i zapisuje wersjonowany artefakt
``app/core/mpzp_confidence_calibration.json`` ze skrótem SHA-256 danych kalibracyjnych. Wynik na
OSOBNYM zbiorze (domyślnie ``final``) trafia do artefaktu jako ocena, nigdy do dopasowania.

Uruchomienie z katalogu ``backend/``::

    python3 scripts/calibrate_mpzp_confidence.py            # zapisuje artefakt
    python3 scripts/calibrate_mpzp_confidence.py --check    # sprawdza, że artefakt odtwarza się z danych

Rekalibrację trzeba wykonać po KAŻDEJ zmianie silnika ekstrakcji (``quantity_engine``, leksykon,
słownik warunków), parsera (``MPZP_PARSER_VERSION``), promptu albo modelu językowego: artefakt zapisuje
wersje, dla których powstał, a test ``test_mpzp_confidence_calibration`` zawodzi, gdy się rozjadą.
Narzędzie nie wywołuje sieci ani modelu językowego.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_IMPORT_CWD = Path.cwd()
try:
    os.chdir(BACKEND_DIR)  # ``app.core.settings`` czyta względne ``.env`` przy imporcie
    from app.modules.planning.domain import evidence_confidence as model  # noqa: E402
    from app.modules.planning.domain.quantity_engine import ENGINE_VERSION  # noqa: E402
    from app.modules.planning.domain.quantity_lexicon import LEXICON_VERSION  # noqa: E402
    from app.modules.planning.domain.value_conditions import CONDITIONS_VERSION  # noqa: E402
    from app.services.mpzp_parser import MPZP_PARSER_VERSION  # noqa: E402
    from app.services.mpzp_parser_blocks import MPZP_PARSER_VERSION_BLOCKS  # noqa: E402
finally:
    os.chdir(_IMPORT_CWD)

from scripts import evaluate_mpzp_parser as ev  # noqa: E402

CALIBRATION_VERSION = "mpzp-confidence/1.0"
ENGINES = ("legacy", "v3")
DEFAULT_CALIBRATION_SPLIT = "development"
DEFAULT_TEST_SPLIT = "final"
# Tolerancje polityki: dla pasma ``high`` (jawny cel z zadania) i dla wartości przyjmowanych bez
# ręcznej weryfikacji. Próg wynika z pomiaru, tolerancja jest decyzją produktową.
EPSILON_HIGH = 0.05
EPSILON_REVIEW = 0.10
MINIMUM_N = 20
L2_GRID = (0.5, 1.0, 2.0, 5.0, 10.0, 20.0)
ROUND = 10


def _engine(name: str) -> Any:
    return {"legacy": ev.LegacyEngine, "v3": ev.V3Engine}[name]()


def collect_rows(manifest: Mapping[str, Any], base_dir: Path) -> list[dict[str, Any]]:
    """Wartości zwrócone przez oba silniki na całym korpusie, z cechami dowodu i etykietą poprawności.

    Cechy nie zależą od artefaktu (artefakt nadaje tylko liczbę), więc zbieramy je z artefaktem zerowym.
    """
    rows: list[dict[str, Any]] = []
    with model.override_artifact(model.neutral_artifact()):
        for name in ENGINES:
            evaluation = ev.evaluate(manifest, base_dir, engine=_engine(name))
            for value in evaluation["values"]:
                if value.get("features") is None:
                    raise SystemExit(f"Silnik {name} nie raportuje cech dowodu — nie da się go skalibrować.")
                rows.append(
                    {
                        "engine": name,
                        "sample_id": value["sample_id"],
                        "zone": value["zone_symbol"],
                        "parameter": value["parameter"],
                        "value": value["value"],
                        "split": value["split"],
                        "features": value["features"],
                        "correct": bool(value["correct"]),
                    }
                )
    return rows


def _vectors(rows: Sequence[Mapping[str, Any]]) -> tuple[list[list[float]], list[int]]:
    vectors = [model.feature_vector(model.ConfidenceFeatures.from_dict(row["features"])) for row in rows]
    return vectors, [int(row["correct"]) for row in rows]


def _fit(rows: Sequence[Mapping[str, Any]], l2: float) -> tuple[list[float], float]:
    vectors, labels = _vectors(rows)
    prior = [model.FEATURE_PRIORS[name] for name in model.FEATURE_NAMES]
    non_positive = [index for index, name in enumerate(model.FEATURE_NAMES) if name in model.NON_POSITIVE_FEATURES]
    return model.fit_logistic(vectors, labels, l2=l2, prior=prior, non_positive=non_positive)


def _log_loss(probabilities: Sequence[float], labels: Sequence[int]) -> float:
    total = 0.0
    for p, label in zip(probabilities, labels):
        p = min(0.999999, max(0.000001, p))
        total -= math.log(p) if label else math.log(1 - p)
    return total / len(labels)


def choose_l2(rows: Sequence[Mapping[str, Any]]) -> tuple[float, dict[str, float]]:
    """Siła regularyzacji z walidacji krzyżowej po próbkach (leave-one-sample-out) na danych kalibracyjnych."""
    samples = sorted({row["sample_id"] for row in rows})
    scores: dict[str, float] = {}
    for l2 in L2_GRID:
        predictions: list[float] = []
        labels: list[int] = []
        for held_out in samples:
            train = [row for row in rows if row["sample_id"] != held_out]
            test = [row for row in rows if row["sample_id"] == held_out]
            if not test or not train:
                continue
            weights, intercept = _fit(train, l2)
            x_test, y_test = _vectors(test)
            predictions.extend(model.sigmoid(intercept + sum(w * x for w, x in zip(weights, vector))) for vector in x_test)
            labels.extend(y_test)
        scores[str(l2)] = round(_log_loss(predictions, labels), ROUND)
    best = min(L2_GRID, key=lambda l2: (scores[str(l2)], l2))
    return best, scores


def _predict(weights: Sequence[float], intercept: float, rows: Sequence[Mapping[str, Any]]) -> list[float]:
    vectors, _ = _vectors(rows)
    return [
        min(model.PROBABILITY_CEILING, max(model.PROBABILITY_FLOOR, model.sigmoid(intercept + sum(w * x for w, x in zip(weights, vector)))))
        for vector in vectors
    ]


def _evaluation(
    split: str, rows: Sequence[Mapping[str, Any]], weights: Sequence[float], intercept: float, artifact: model.CalibrationArtifact
) -> dict[str, Any]:
    predictions = _predict(weights, intercept, rows)
    correct = [bool(row["correct"]) for row in rows]
    table = model.band_error_table(predictions, correct, artifact)
    accepted = [ok for p, ok in zip(predictions, correct) if p >= artifact.review_threshold]
    return {
        "split": split,
        "n": len(rows),
        "errors": sum(not ok for ok in correct),
        "brier_score": _r(model.brier_score(predictions, correct)),
        "expected_calibration_error": _r(model.expected_calibration_error(predictions, correct)),
        "reliability_curve": [
            {k: (_r(v) if isinstance(v, float) else v) for k, v in row.items()}
            for row in model.reliability_table(predictions, correct)
        ],
        "bands": {name: {**band, "error_rate": _r(band["error_rate"])} for name, band in table.items()},
        "bands_monotone": model.is_monotone(table),
        "without_manual_review": {
            "n": len(accepted),
            "errors": sum(not ok for ok in accepted),
            "error_rate": _r(sum(not ok for ok in accepted) / len(accepted)) if accepted else None,
        },
    }


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, ROUND)


def build_artifact(
    manifest: Mapping[str, Any],
    corpus_sha256: str,
    rows: Sequence[Mapping[str, Any]],
    calibration_split: str = DEFAULT_CALIBRATION_SPLIT,
    test_split: str = DEFAULT_TEST_SPLIT,
) -> dict[str, Any]:
    """Artefakt kalibracji z wierszy zebranych przez ``collect_rows`` (czysta funkcja: dane → artefakt)."""
    if calibration_split == test_split:
        raise SystemExit("Podział kalibracyjny i testowy muszą być różne: kalibracja nie może widzieć zbioru oceny.")
    calibration = [row for row in rows if row["split"] == calibration_split]
    test = [row for row in rows if row["split"] == test_split]
    if not calibration or not test:
        raise SystemExit("Brak wartości w podziale kalibracyjnym albo testowym.")
    training_rows = [
        {key: row[key] for key in ("engine", "sample_id", "zone", "parameter", "value", "features", "correct")}
        for row in calibration
    ]
    digest = model.data_digest(training_rows)
    l2, l2_scores = choose_l2(calibration)
    weights, intercept = _fit(calibration, l2)
    weights = [round(w, ROUND) for w in weights]
    intercept = round(intercept, ROUND)
    predictions = _predict(weights, intercept, calibration)
    correct = [bool(row["correct"]) for row in calibration]
    high = model.choose_threshold(predictions, correct, EPSILON_HIGH, MINIMUM_N)
    review = model.choose_threshold(predictions, correct, EPSILON_REVIEW, MINIMUM_N)
    review_threshold = 1.0 if review is None else round(review, ROUND)
    high_threshold = 1.0 if high is None else max(round(high, ROUND), review_threshold)
    cap = round(min(0.84, max(review_threshold, high_threshold - 0.01)), 4)
    payload: dict[str, Any] = {
        "schema_version": model.ARTIFACT_SCHEMA_VERSION,
        "calibration_version": CALIBRATION_VERSION,
        "engine": {
            "versions": sorted({MPZP_PARSER_VERSION, MPZP_PARSER_VERSION_BLOCKS}),
            "quantity_engine": ENGINE_VERSION,
            "lexicon": LEXICON_VERSION,
            "conditions": CONDITIONS_VERSION,
            "language_model": None,
            "prompt": None,
            "note": (
                "Silnik deterministyczny (legacy i bloki). Brak ścieżki modelu językowego: wartość z modelu nie ma "
                "tu kalibracji, więc dostaje pewność ograniczoną od góry i zawsze ręczną weryfikację."
            ),
        },
        "data": {
            "corpus_id": manifest.get("corpus_id"),
            "corpus_sha256": corpus_sha256,
            "annotations_sha256": ev.annotations_sha256(manifest),
            "calibration_split": calibration_split,
            "test_split": test_split,
            "engines": list(ENGINES),
            "n": len(calibration),
            "errors": sum(not ok for ok in correct),
            "sha256": digest,
            "note": (
                "Wiersze: wartości zwrócone przez silniki na podziale kalibracyjnym (cechy dowodu + etykieta: "
                "wartość należy do adnotacji strefy). Skrót to SHA-256 kanonicznego zapisu wierszy."
            ),
        },
        "model": {
            "type": "logistic_regression_l2",
            "l2": l2,
            "l2_cross_validation_log_loss": l2_scores,
            "feature_names": list(model.FEATURE_NAMES),
            "prior_weights": [model.FEATURE_PRIORS[name] for name in model.FEATURE_NAMES],
            "non_positive_features": sorted(model.NON_POSITIVE_FEATURES),
            "weights": weights,
            "intercept": intercept,
            "probability_floor": model.PROBABILITY_FLOOR,
            "probability_ceiling": model.PROBABILITY_CEILING,
        },
        "policy": {
            "epsilon_high": EPSILON_HIGH,
            "epsilon_review": EPSILON_REVIEW,
            "minimum_n": MINIMUM_N,
            "meaning": (
                "Próg to najniższa pewność, od której (w górę) odsetek błędów wygładzony regresją izotoniczną na "
                "danych kalibracyjnych nie przekracza tolerancji, przy co najmniej minimum_n wartości. Wygładzanie "
                "nie pozwala wysokiej pewności rozcieńczyć błędów w niższych przedziałach. Tolerancje (epsilon_*) "
                "są decyzją polityki, progi — wynikiem pomiaru."
            ),
        },
        "bands": {
            "low": {"lower": 0.0},
            "medium": {"lower": review_threshold},
            "high": {"lower": high_threshold},
        },
        "manual_review": {
            "threshold": review_threshold,
            "rule": "manual_review_required = (pewność < threshold) lub konflikt lub naruszenie zakresu domenowego",
        },
        "uncalibrated_cap": cap,
    }
    draft = model.validate_payload({**payload, "measurements": {}})
    payload["measurements"] = {
        "calibration": _evaluation(calibration_split, calibration, weights, intercept, draft),
        "held_out": _evaluation(test_split, test, weights, intercept, draft),
        "independence_note": (
            "Podział testowy NIE jest niezależny od projektu leksykonu: sformułowania z całego korpusu BK-603 "
            "były znane przy jego budowie, a anotacje zrobił asystent AI. Niezależną ocenę da dopiero nowy zbiór "
            "końcowy z drugim anotatorem (PV3-02)."
        ),
    }
    return payload


def render(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _comparable(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "digest": payload["data"]["sha256"],
        "n": payload["data"]["n"],
        "weights": payload["model"]["weights"],
        "intercept": payload["model"]["intercept"],
        "l2": payload["model"]["l2"],
        "bands": payload["bands"],
        "review": payload["manual_review"]["threshold"],
        "cap": payload["uncalibrated_cap"],
        "versions": payload["engine"]["versions"],
        "lexicon": payload["engine"]["lexicon"],
        "conditions": payload["engine"]["conditions"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kalibracja pewności wartości parametrów MPZP (PV3-09).")
    parser.add_argument("--corpus", type=Path, default=ev.DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=model.CALIBRATION_ARTIFACT_PATH)
    parser.add_argument("--calibration-split", default=DEFAULT_CALIBRATION_SPLIT)
    parser.add_argument("--test-split", default=DEFAULT_TEST_SPLIT)
    parser.add_argument("--check", action="store_true", help="nie zapisuje; sprawdza, że artefakt odtwarza się z danych")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest, corpus_sha = ev.load_corpus(args.corpus)
    rows = collect_rows(manifest, args.corpus.parent)
    payload = build_artifact(manifest, corpus_sha, rows, args.calibration_split, args.test_split)
    if args.check:
        try:
            stored = json.loads(args.output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Nie można wczytać artefaktu {args.output}: {exc}", file=sys.stderr)
            return 2
        left, right = _comparable(stored), _comparable(payload)
        if left != right:
            differing = sorted(key for key in left if left[key] != right[key])
            print(f"Artefakt NIE odtwarza się z danych; różnią się: {', '.join(differing)}", file=sys.stderr)
            return 1
        print(f"Artefakt odtwarza się z danych: {stored['calibration_version']}, SHA-256 danych {payload['data']['sha256']}")
        return 0
    args.output.write_text(render(payload), encoding="utf-8")
    held_out = payload["measurements"]["held_out"]
    print(
        f"Zapisano {args.output}: n={payload['data']['n']} (błędów {payload['data']['errors']}), l2={payload['model']['l2']}, "
        f"progi medium={payload['bands']['medium']['lower']}, high={payload['bands']['high']['lower']}; "
        f"zbiór oceny `{held_out['split']}`: n={held_out['n']}, ECE={held_out['expected_calibration_error']}, "
        f"Brier={held_out['brier_score']}, monotoniczne={held_out['bands_monotone']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
