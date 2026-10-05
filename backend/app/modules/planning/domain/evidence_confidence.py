"""Pewność wartości parametru MPZP wyprowadzona z cech dowodu i skalibrowana na danych (PV3-09).

Zamiast stałych mnożników (0,85 · 0,6 · 0,95…) pewność jest PRAWDOPODOBIEŃSTWEM POPRAWNOŚCI
wartości: ``p = sigmoid(w · x + b)``, gdzie ``x`` to cechy dowodu dające się sprawdzić
deterministycznie, a wagi ``w`` i ``b`` pochodzą z artefaktu kalibracji
(``app/core/mpzp_confidence_calibration.json``) dopasowanego na PODZIALE KALIBRACYJNYM i
zweryfikowanego na osobnym zbiorze (nigdy na zbiorze końcowym).

Cechy: metoda ekstrakcji tekstu i jakość OCR strony, strategia i pewność zakresu strefy, weryfikacja
cytatu względem tekstu stron, strategia dopasowania silnika i flagi przeróbek zapisu, liczba
kandydatów, konflikt, typ wartości (bezwarunkowa/warunkowa), wieloznaczność sekcji i symbole
odkryte w tekście. **Samoocena modelu językowego NIE jest cechą**: ``ConfidenceFeatures`` nie ma
takiego pola, a wartość z modelu (``origin="llm"``) nigdy nie dostaje pasma ``high`` ani
zgody na pominięcie ręcznej weryfikacji, dopóki nie istnieje kalibracja jej własnego silnika.

Pasma ``low``/``medium``/``high`` i próg ``manual_review_required`` są odczytywane z artefaktu:
progi wynikają z POMIARU na danych kalibracyjnych (najniższa pewność, powyżej której zmierzony
odsetek błędów mieści się w przyjętej tolerancji), a tolerancje (5% dla ``high``, 10% dla
braku ręcznej weryfikacji) to jawne parametry polityki zapisane w artefakcie obok wyniku pomiaru.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal

CALIBRATION_ARTIFACT_PATH: Final[Path] = (
    Path(__file__).resolve().parents[3] / "core" / "mpzp_confidence_calibration.json"
)
ARTIFACT_SCHEMA_VERSION: Final[str] = "mpzp-confidence-calibration/1"

Band = Literal["low", "medium", "high"]
BANDS: Final[tuple[Band, ...]] = ("low", "medium", "high")
ORIGIN_DETERMINISTIC: Final[str] = "deterministic"
ORIGIN_LLM: Final[str] = "llm"

PROBABILITY_FLOOR: Final[float] = 0.01
PROBABILITY_CEILING: Final[float] = 0.99

# Cechy, które nie mogą pojawić się w modelu: pewność z modelu językowego zależy wyłącznie od cech
# weryfikowalnych (cytat, zakres, metoda, konflikt), nie od tego, jak pewny siebie jest model.
FORBIDDEN_FEATURE_FRAGMENTS: Final[tuple[str, ...]] = ("self", "model_confidence", "llm_score", "logprob")

_FLAG_GROUPS: Final[Mapping[str, frozenset[str]]] = {
    "flag_conversion": frozenset({"ratio_to_percent", "percent_to_ratio", "hectare_to_m2", "double_notation_mismatch"}),
    "flag_ocr_artifact": frozenset({"degree_artifact", "degree_letter", "degree_ocr"}),
    "flag_implied": frozenset({"operator_implied", "unit_implied", "noun_implied", "implicit_percent"}),
    "flag_number_word": frozenset({"number_word"}),
    "flag_inherited": frozenset({"inherited_noun"}),
    "flag_approximate": frozenset({"approximate"}),
}
_STRATEGY_GROUPS: Final[Mapping[str, frozenset[str]]] = {
    "strategy_range": frozenset({"range_from_to", "range_dash"}),
    "strategy_bare": frozenset({"bare", "exact_word"}),
    "strategy_frame": frozenset({"frame_setback", "frame_parking", "frame_value_first"}),
}

FEATURE_NAMES: Final[tuple[str, ...]] = (
    "ocr",
    "ocr_noise",
    "html",
    "scope_fallback",
    "scope_general",
    "scope_uncertainty",
    "scope_legacy",
    "quote_unverified",
    "strategy_range",
    "strategy_bare",
    "strategy_frame",
    "flag_conversion",
    "flag_ocr_artifact",
    "flag_implied",
    "flag_number_word",
    "flag_inherited",
    "flag_approximate",
    "multi_candidates",
    "conflict",
    "conditional",
    "zone_ambiguous",
    "symbols_inferred",
)


# Wiedza a priori o kierunku cech (polityka, nie dane): każda z poniższych cech jest czynnikiem RYZYKA,
# więc jej waga nie może być dodatnia — szum w małym zbiorze nie ma prawa „nagradzać” np. niezweryfikowanego
# cytatu. Wagi startowe (``FEATURE_PRIORS``) są środkiem regularyzacji: dane odsuwają wagę od wartości
# startowej tylko wtedy, gdy ją wyraźnie uzasadniają. Pozostałe cechy (``html``, ``strategy_range``,
# ``flag_inherited``) opisują strukturę zapisu i mogą mieć dowolny znak.
NON_POSITIVE_FEATURES: Final[frozenset[str]] = frozenset(
    {
        "ocr",
        "ocr_noise",
        "scope_fallback",
        "scope_general",
        "scope_uncertainty",
        "scope_legacy",
        "quote_unverified",
        "strategy_bare",
        "strategy_frame",
        "flag_conversion",
        "flag_ocr_artifact",
        "flag_implied",
        "flag_number_word",
        "flag_approximate",
        "multi_candidates",
        "conflict",
        "conditional",
        "zone_ambiguous",
        "symbols_inferred",
    }
)
FEATURE_PRIORS: Final[Mapping[str, float]] = {
    "ocr": -0.7,
    "ocr_noise": -1.5,
    "html": -0.2,
    "scope_fallback": -1.0,
    "scope_general": -0.7,
    "scope_uncertainty": -1.0,
    "scope_legacy": -0.3,
    "quote_unverified": -2.0,
    "strategy_range": 0.0,
    "strategy_bare": -0.4,
    "strategy_frame": -0.2,
    "flag_conversion": -0.3,
    "flag_ocr_artifact": -0.7,
    "flag_implied": -0.5,
    "flag_number_word": -0.2,
    "flag_inherited": 0.0,
    "flag_approximate": -0.7,
    "multi_candidates": -0.5,
    "conflict": -1.0,
    "conditional": -0.3,
    "zone_ambiguous": -0.5,
    "symbols_inferred": -1.0,
}


@dataclass(frozen=True)
class ConfidenceFeatures:
    """Cechy dowodu jednej wartości; wszystkie sprawdzalne bez udziału modelu językowego."""

    extraction_method: str | None = None  # pdf_text | html | ocr | None (nieznana)
    ocr_quality: float | None = None  # jakość strony 0–1; sensowna tylko dla OCR
    scope_strategy: int | None = None  # 0–6 (PV3-06); None = tryb dotychczasowy
    scope_confidence: float | None = None
    scope_kind: str | None = None  # zone_section | general_clause | residual_clause | fallback | None
    quote_verified: bool = True  # fragment dowodowy znaleziony w tekście strony
    strategy: str | None = None  # strategia dopasowania silnika ilości
    flags: tuple[str, ...] = ()
    candidate_count: int = 1  # kandydatury tego parametru w strefie z innymi wartościami + 1
    value_kind: str = "unconditional"
    zone_ambiguous: bool = False
    symbols_inferred: bool = False
    origin: str = ORIGIN_DETERMINISTIC

    def as_dict(self) -> dict[str, Any]:
        return {
            "extraction_method": self.extraction_method,
            "ocr_quality": self.ocr_quality,
            "scope_strategy": self.scope_strategy,
            "scope_confidence": self.scope_confidence,
            "scope_kind": self.scope_kind,
            "quote_verified": self.quote_verified,
            "strategy": self.strategy,
            "flags": list(self.flags),
            "candidate_count": self.candidate_count,
            "value_kind": self.value_kind,
            "zone_ambiguous": self.zone_ambiguous,
            "symbols_inferred": self.symbols_inferred,
            "origin": self.origin,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ConfidenceFeatures:
        known = {name for name in cls.__dataclass_fields__}
        unknown = set(payload) - known
        if unknown:
            raise ValueError(f"Nieznane cechy pewności: {sorted(unknown)}")
        data = dict(payload)
        data["flags"] = tuple(data.get("flags", ()))
        return cls(**data)


def feature_vector(features: ConfidenceFeatures) -> list[float]:
    """Wektor cech w kolejności ``FEATURE_NAMES``; wartości w zakresie 0–1 (``multi`` w skali log)."""
    flags = set(features.flags)
    is_ocr = features.extraction_method == "ocr"
    scope_kind = features.scope_kind
    values: dict[str, float] = {
        "ocr": float(is_ocr),
        "ocr_noise": (1.0 - min(1.0, max(0.0, features.ocr_quality))) if is_ocr and features.ocr_quality is not None else 0.0,
        "html": float(features.extraction_method == "html"),
        "scope_fallback": float(scope_kind == "fallback" or features.scope_strategy == 0),
        "scope_general": float(scope_kind in {"general_clause", "residual_clause"}),
        "scope_uncertainty": (
            1.0 - min(1.0, max(0.0, features.scope_confidence)) if features.scope_confidence is not None else 0.0
        ),
        "scope_legacy": float(scope_kind is None),
        "quote_unverified": float(not features.quote_verified),
        "multi_candidates": min(1.0, math.log(max(1, features.candidate_count)) / math.log(4)),
        "conflict": float(features.value_kind == "conflict"),
        "conditional": float(features.value_kind == "conditional"),
        "zone_ambiguous": float(features.zone_ambiguous),
        "symbols_inferred": float(features.symbols_inferred),
    }
    for name, strategies in _STRATEGY_GROUPS.items():
        values[name] = float(features.strategy in strategies)
    for name, members in _FLAG_GROUPS.items():
        values[name] = float(bool(flags & members))
    return [values[name] for name in FEATURE_NAMES]


# --- artefakt kalibracji ------------------------------------------------------------------


class CalibrationError(ValueError):
    """Artefakt kalibracji jest niespójny, nieaktualny albo niepełny."""


@dataclass(frozen=True)
class CalibrationArtifact:
    """Wersjonowany artefakt kalibracji (wagi, pasma, próg ręcznej weryfikacji, skrót danych)."""

    payload: Mapping[str, Any]
    weights: tuple[float, ...]
    intercept: float
    band_thresholds: Mapping[str, float]  # medium: dolna granica, high: dolna granica
    review_threshold: float
    uncalibrated_cap: float

    @property
    def version(self) -> str:
        return str(self.payload["calibration_version"])

    @property
    def data_sha256(self) -> str:
        return str(self.payload["data"]["sha256"])

    @property
    def engine_versions(self) -> tuple[str, ...]:
        return tuple(self.payload["engine"]["versions"])

    @property
    def calibration_id(self) -> str:
        return f"{self.version}+{self.data_sha256[:12]}"


def validate_payload(payload: Mapping[str, Any]) -> CalibrationArtifact:
    if payload.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise CalibrationError("Nieznana wersja schematu artefaktu kalibracji.")
    model = payload.get("model") or {}
    names = tuple(model.get("feature_names", ()))
    if names != FEATURE_NAMES:
        raise CalibrationError("Cechy artefaktu nie zgadzają się z cechami kodu — wymagana rekalibracja.")
    if any(fragment in name for name in names for fragment in FORBIDDEN_FEATURE_FRAGMENTS):
        raise CalibrationError("Samoocena modelu językowego nie może być cechą pewności.")
    weights = tuple(float(value) for value in model.get("weights", ()))
    if len(weights) != len(FEATURE_NAMES):
        raise CalibrationError("Liczba wag nie zgadza się z liczbą cech.")
    bands = payload.get("bands") or {}
    thresholds = {"medium": float(bands["medium"]["lower"]), "high": float(bands["high"]["lower"])}
    if not 0.0 <= thresholds["medium"] <= thresholds["high"] <= 1.0:
        raise CalibrationError("Progi pasm muszą być niemalejące w zakresie 0–1.")
    review = float((payload.get("manual_review") or {})["threshold"])
    if not 0.0 <= review <= 1.0:
        raise CalibrationError("Próg ręcznej weryfikacji poza zakresem 0–1.")
    if not str((payload.get("data") or {}).get("sha256", "")).strip():
        raise CalibrationError("Artefakt bez skrótu danych kalibracyjnych nie jest odtwarzalny.")
    return CalibrationArtifact(
        payload=payload,
        weights=weights,
        intercept=float(model["intercept"]),
        band_thresholds=thresholds,
        review_threshold=review,
        uncalibrated_cap=float(payload.get("uncalibrated_cap", thresholds["high"])),
    )


def load_artifact(path: Path | None = None) -> CalibrationArtifact:
    """Wczytuje i waliduje artefakt; domyślnie ten dostarczany z aplikacją."""
    target = path or CALIBRATION_ARTIFACT_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"Nie można wczytać artefaktu kalibracji {target}: {exc}") from exc
    return validate_payload(payload)


@lru_cache(maxsize=1)
def default_artifact() -> CalibrationArtifact:
    return load_artifact()


_OVERRIDES: list[CalibrationArtifact] = []


@contextmanager
def override_artifact(artifact: CalibrationArtifact) -> Iterator[CalibrationArtifact]:
    """Tymczasowo podmienia artefakt (testy i kalibracja, która zbiera cechy bez wcześniejszych wag)."""
    _OVERRIDES.append(artifact)
    try:
        yield artifact
    finally:
        _OVERRIDES.pop()


def current_artifact() -> CalibrationArtifact:
    return _OVERRIDES[-1] if _OVERRIDES else default_artifact()


def neutral_artifact() -> CalibrationArtifact:
    """Artefakt zerowy (p = 0,5 dla każdej wartości); służy wyłącznie do zebrania cech przed kalibracją."""
    payload = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "calibration_version": "neutral",
        "engine": {"versions": []},
        "data": {"sha256": "0" * 64},
    }
    return CalibrationArtifact(
        payload=payload,
        weights=tuple(0.0 for _ in FEATURE_NAMES),
        intercept=0.0,
        band_thresholds={"medium": 0.5, "high": 1.0},
        review_threshold=0.5,
        uncalibrated_cap=0.5,
    )


# --- ocena ---------------------------------------------------------------------------------


def sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


def probability(features: ConfidenceFeatures, artifact: CalibrationArtifact | None = None) -> float:
    """Prawdopodobieństwo poprawności wartości z cech (obcięte do ``[0,01; 0,99]``)."""
    artifact = artifact or current_artifact()
    score = artifact.intercept + sum(w * x for w, x in zip(artifact.weights, feature_vector(features)))
    return min(PROBABILITY_CEILING, max(PROBABILITY_FLOOR, sigmoid(score)))


def band_for(p: float, artifact: CalibrationArtifact | None = None) -> Band:
    artifact = artifact or current_artifact()
    if p >= artifact.band_thresholds["high"]:
        return "high"
    if p >= artifact.band_thresholds["medium"]:
        return "medium"
    return "low"


@dataclass(frozen=True)
class ScoredConfidence:
    """Wynik oceny: pewność, pasmo, flaga ręcznej weryfikacji i skąd pochodzi liczba."""

    confidence: float
    band: Band
    manual_review_required: bool
    calibrated: bool
    calibration_id: str
    features: ConfidenceFeatures = field(repr=False, compare=False, default_factory=ConfidenceFeatures)


def score(features: ConfidenceFeatures, artifact: CalibrationArtifact | None = None) -> ScoredConfidence:
    """Pewność, pasmo i potrzeba ręcznej weryfikacji dla cech dowodu.

    Wartość z modelu językowego nie ma skalibrowanego silnika, więc jest ograniczona od góry
    (``uncalibrated_cap``, poniżej pasma ``high``) i zawsze wymaga ręcznej weryfikacji.
    """
    artifact = artifact or current_artifact()
    p = probability(features, artifact)
    if features.origin != ORIGIN_DETERMINISTIC:
        # Brak kalibracji dla tego silnika: pewność ograniczona poniżej pasma ``high``, weryfikacja ręczna zawsze.
        p = min(p, artifact.uncalibrated_cap, artifact.band_thresholds["high"] - 1e-6)
        return ScoredConfidence(p, band_for(p, artifact), True, False, artifact.calibration_id, features)
    return ScoredConfidence(p, band_for(p, artifact), p < artifact.review_threshold, True, artifact.calibration_id, features)


def uncalibrated_text_confidence(artifact: CalibrationArtifact | None = None) -> float:
    """Pewność zapisu opisowego (przeznaczenie, zakazy…): brak kalibracji, więc nigdy powyżej ``uncalibrated_cap``."""
    artifact = artifact or current_artifact()
    return round(min(0.84, artifact.uncalibrated_cap), 4)


# --- dopasowanie modelu ---------------------------------------------------------------------


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Rozwiązuje układ liniowy eliminacją Gaussa z wyborem elementu głównego."""
    size = len(vector)
    augmented = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise CalibrationError("Układ równań kalibracji jest osobliwy.")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        for row in range(column + 1, size):
            factor = augmented[row][column] / augmented[column][column]
            for index in range(column, size + 1):
                augmented[row][index] -= factor * augmented[column][index]
    result = [0.0] * size
    for row in range(size - 1, -1, -1):
        tail = sum(augmented[row][index] * result[index] for index in range(row + 1, size))
        result[row] = (augmented[row][size] - tail) / augmented[row][row]
    return result


def _newton(
    rows: Sequence[Sequence[float]],
    labels: Sequence[int],
    l2: float,
    prior: Sequence[float],
    frozen: set[int],
    iterations: int,
) -> list[float]:
    dimension = len(rows[0])
    beta = [0.0] + [0.0 if index in frozen else prior[index] for index in range(dimension)]
    positives = sum(labels)
    if 0 < positives < len(labels):
        rate = positives / len(labels)
        beta[0] = math.log(rate / (1 - rate))
    free = [0] + [index + 1 for index in range(dimension) if index not in frozen]
    for _ in range(iterations):
        gradient = [0.0] * (dimension + 1)
        hessian = [[0.0] * (dimension + 1) for _ in range(dimension + 1)]
        for features, label in zip(rows, labels):
            vector = [1.0, *features]
            p = sigmoid(sum(b * x for b, x in zip(beta, vector)))
            error = p - label
            weight = p * (1 - p)
            for i in free:
                gradient[i] += error * vector[i]
                if vector[i] == 0.0:
                    continue
                for j in free:
                    if j >= i:
                        hessian[i][j] += weight * vector[i] * vector[j]
        for i in free[1:]:
            gradient[i] += l2 * (beta[i] - prior[i - 1])
            hessian[i][i] += l2
        hessian[0][0] += 1e-9
        for i in free:
            for j in free:
                if j < i:
                    hessian[i][j] = hessian[j][i]
        sub_hessian = [[hessian[i][j] for j in free] for i in free]
        step = _solve(sub_hessian, [gradient[i] for i in free])
        for position, i in enumerate(free):
            beta[i] -= step[position]
        if max(abs(value) for value in step) < 1e-9:
            break
    return beta


def fit_logistic(
    rows: Sequence[Sequence[float]],
    labels: Sequence[int],
    l2: float = 2.0,
    iterations: int = 100,
    prior: Sequence[float] | None = None,
    non_positive: Sequence[int] = (),
) -> tuple[list[float], float]:
    """Regresja logistyczna z regularyzacją L2 wokół wag a priori, metodą Newtona.

    ``prior`` to środek regularyzacji (domyślnie zera), ``non_positive`` — indeksy cech, których waga nie
    może być dodatnia (metoda zbioru aktywnego: cecha z dodatnią wagą jest zerowana i zamrażana, a model
    dopasowywany ponownie). Wyraz wolny nie jest regularyzowany. Deterministyczna: te same dane i parametry
    dają te same wagi. Zwraca ``(wagi, wyraz wolny)``.
    """
    if not rows or len(rows) != len(labels):
        raise CalibrationError("Brak danych kalibracyjnych albo różna liczba cech i etykiet.")
    dimension = len(rows[0])
    centre = list(prior) if prior is not None else [0.0] * dimension
    if len(centre) != dimension:
        raise CalibrationError("Wagi a priori mają inną długość niż wektor cech.")
    constrained = set(non_positive)
    frozen: set[int] = set()
    while True:
        beta = _newton(rows, labels, l2, centre, frozen, iterations)
        violating = {index for index in constrained - frozen if beta[index + 1] > 0.0}
        if not violating:
            break
        frozen |= violating
    for index in frozen:
        beta[index + 1] = 0.0
    return beta[1:], beta[0]


# --- niezawodność (krzywa, Brier, ECE) ---------------------------------------------------------


def reliability_table(
    predictions: Sequence[float], correct: Sequence[bool], bins: int = 10
) -> list[dict[str, Any]]:
    """Krzywa niezawodności: równe przedziały pewności, liczba wartości, średnia pewność i trafność."""
    table: list[dict[str, Any]] = []
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        members = [
            (p, ok) for p, ok in zip(predictions, correct)
            if low <= p < high or (index == bins - 1 and p == 1.0)
        ]
        n = len(members)
        table.append(
            {
                "lower": low,
                "upper": high,
                "n": n,
                "mean_confidence": sum(p for p, _ in members) / n if n else None,
                "accuracy": sum(ok for _, ok in members) / n if n else None,
                "errors": sum(not ok for _, ok in members),
            }
        )
    return table


def brier_score(predictions: Sequence[float], correct: Sequence[bool]) -> float | None:
    if not predictions:
        return None
    return sum((p - (1.0 if ok else 0.0)) ** 2 for p, ok in zip(predictions, correct)) / len(predictions)


def expected_calibration_error(
    predictions: Sequence[float], correct: Sequence[bool], bins: int = 10
) -> float | None:
    """ECE: średnia ważona liczbą wartości różnica między średnią pewnością a trafnością w przedziale."""
    n = len(predictions)
    if n == 0:
        return None
    total = 0.0
    for row in reliability_table(predictions, correct, bins):
        if row["n"]:
            total += row["n"] / n * abs(row["accuracy"] - row["mean_confidence"])
    return total


def band_error_table(
    predictions: Sequence[float], correct: Sequence[bool], artifact: CalibrationArtifact
) -> dict[str, dict[str, Any]]:
    """Zmierzony odsetek błędów w każdym paśmie artefaktu (``n`` i liczba błędów obok odsetka)."""
    result: dict[str, dict[str, Any]] = {}
    for name in BANDS:
        members = [ok for p, ok in zip(predictions, correct) if band_for(p, artifact) == name]
        errors = sum(not ok for ok in members)
        result[name] = {
            "n": len(members),
            "errors": errors,
            "error_rate": (errors / len(members)) if members else None,
        }
    return result


def is_monotone(table: Mapping[str, Mapping[str, Any]]) -> bool:
    """Czy odsetek błędów maleje od ``low`` do ``high`` (pasma bez wartości są pomijane)."""
    rates = [table[name]["error_rate"] for name in BANDS if table[name]["n"]]
    return all(earlier >= later for earlier, later in zip(rates, rates[1:]))


def isotonic_blocks(predictions: Sequence[float], correct: Sequence[bool]) -> list[dict[str, float]]:
    """Regresja izotoniczna (PAV) trafności względem pewności: bloki o niemalejącej trafności.

    Zwraca bloki rosnąco po pewności: ``{"lower": najniższa pewność w bloku, "n": liczba wartości,
    "accuracy": trafność bloku}``. Wartości o identycznej pewności tworzą jeden punkt.
    """
    points: dict[float, list[int]] = {}
    for p, ok in zip(predictions, correct):
        entry = points.setdefault(p, [0, 0])
        entry[0] += 1
        entry[1] += int(ok)
    blocks: list[dict[str, float]] = [
        {"lower": p, "n": counts[0], "correct": counts[1]} for p, counts in sorted(points.items())
    ]
    merged: list[dict[str, float]] = []
    for block in blocks:
        merged.append(dict(block))
        while len(merged) >= 2 and merged[-2]["correct"] / merged[-2]["n"] > merged[-1]["correct"] / merged[-1]["n"]:
            last = merged.pop()
            merged[-1]["n"] += last["n"]
            merged[-1]["correct"] += last["correct"]
    return [
        {"lower": block["lower"], "n": block["n"], "accuracy": block["correct"] / block["n"]} for block in merged
    ]


def choose_threshold(
    predictions: Sequence[float], correct: Sequence[bool], tolerance: float, minimum_n: int = 20
) -> float | None:
    """Najniższa pewność, od której (w górę) wygładzony odsetek błędów nie przekracza ``tolerance``.

    Wygładzanie to regresja izotoniczna: pojedyncza grupa wartości o wysokiej pewności nie może
    „rozcieńczyć” błędów w niższych przedziałach (skumulowany odsetek błędów ukryłby np. przedział,
    w którym co druga wartość jest błędna, wśród setek poprawnych). Obszar powyżej progu musi mieć
    co najmniej ``minimum_n`` wartości. ``None`` — żaden próg nie spełnia tolerancji.
    """
    blocks = isotonic_blocks(predictions, correct)
    best: float | None = None
    tail = 0
    for block in reversed(blocks):
        if block["accuracy"] < 1.0 - tolerance:
            break
        tail += int(block["n"])
        if tail >= minimum_n:
            best = block["lower"]
    return best


def data_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    """SHA-256 kanonicznego zapisu wierszy kalibracyjnych (cechy + etykieta), niezależny od kolejności."""
    canonical = sorted(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")) for row in rows)
    return hashlib.sha256("\n".join(canonical).encode("utf-8")).hexdigest()
