"""Systemowa warstwa walidacji i pewność wartości parsera MPZP.

``validate_mpzp_result`` NIE zastępuje ustaleń numerycznych/opisowych ekstraktorów
(``mpzp_parser_numeric.py``, ``mpzp_parser_descriptive.py``), tylko wykrywa konflikty MIĘDZY
parametrami o tej samej nazwie w tej samej strefie, sprawdza zakresy domenowe i nadaje
``confidence``. Zakres detekcji konfliktów obejmuje też parametry dodane przez różne moduły
ekstraktorów (numeric i descriptive), jeśli kiedyś nazwa parametru się powtórzy między nimi —
walidacja nie zakłada, z którego modułu parametr pochodzi.

Od PV3-09 ``confidence`` nie jest iloczynem stałych mnożników, tylko PRAWDOPODOBIEŃSTWEM
POPRAWNOŚCI wartości wyprowadzonym z cech dowodu (metoda ekstrakcji i jakość OCR, strategia i pewność
zakresu, weryfikacja cytatu, strategia dopasowania i flagi przeróbek zapisu, liczba kandydatów,
konflikt, rodzaj wartości) przez model skalibrowany na danych (``evidence_confidence``,
``app/core/mpzp_confidence_calibration.json``). Pewność z ekstraktorów jest WSTĘPNA; ostateczną nadaje
ten etap. ``manual_review_required`` wynika dodatkowo ze zmierzonego progu artefaktu, a nie z liczby
przyjętej z góry. Wartości opisowe (przeznaczenie, zakazy…) nie mają kalibracji: ich pewność jest
ograniczona (``uncalibrated_cap``) i nie dostają pasma.

Od PV3-08 sprzeczność i wartość warunkowa są rozdzielone: kilka wartości tego samego parametru
z RÓŻNYMI warunkami (``dla dachu płaskiego`` / ``dla pozostałych``) to wartości warunkowe
(``value_kind="conditional"``), a sprzeczność (``"conflict"``, grupa konfliktu, ręczna
weryfikacja) powstaje dopiero, gdy ta sama przesłanka — ten sam zestaw warunków albo brak
warunków — ma więcej niż jedną wartość.

Komunikaty w ``MpzpParserWarning.message`` są po polsku i user-facing.
Techniczne detale (dokładny regex, wewnętrzne wyjątki, nazwy klas) idą
wyłącznie do ``logger.debug``, nigdy do ``message`` — użytkownik nietechniczny
nie powinien widzieć szczegółów implementacji.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Final

from app.modules.planning.domain import evidence_confidence
from app.modules.planning.domain.value_conditions import (
    VALUE_KIND_CONDITIONAL,
    VALUE_KIND_CONFLICT,
    VALUE_KIND_UNCONDITIONAL,
    condition_key,
)
from app.schemas.mpzp import (
    MpzpParameter,
    MpzpParseResult,
    MpzpParserWarning,
    MpzpZoneResult,
)

logger = logging.getLogger(__name__)

_PERCENT_NAME_SUFFIX: Final[str] = "_percent"
_POSITIVE_ONLY_PARAMETER_NAMES: Final[frozenset[str]] = frozenset(
    {"max_building_height_m", "max_intensity", "min_intensity"}
)


def validate_mpzp_result(result: MpzpParseResult) -> MpzpParseResult:
    """Wykrywa konflikty parametrów, sprawdza zakresy i nadaje skalibrowaną pewność.

    Pracuje na kopii wejścia i zwraca nowy ``MpzpParseResult`` — nie mutuje
    przekazanego obiektu, żeby wcześniejsze etapy pipeline'u mogły bezpiecznie
    zachować referencję do swojego wyniku.
    """
    context = _ScoringContext.from_result(result)
    ambiguous_zone_symbols = _ambiguous_zone_symbols(result.warnings)

    new_warnings: list[MpzpParserWarning] = list(result.warnings)
    conflict_flags: list[str] = list(result.conflict_flags)

    new_zones: list[MpzpZoneResult] = []
    for zone in result.zones:
        zone_ambiguous = zone.zone_symbol in ambiguous_zone_symbols
        parameters, zone_conflict_flags, zone_warnings = _validate_zone_parameters(
            zone.zone_symbol,
            zone.parameters,
            context=context,
            zone_ambiguous=zone_ambiguous,
        )
        conflict_flags.extend(zone_conflict_flags)
        new_warnings.extend(zone_warnings)
        new_zones.append(zone.model_copy(update={"parameters": parameters}))

    return result.model_copy(
        update={
            "zones": new_zones,
            "warnings": new_warnings,
            "conflict_flags": conflict_flags,
        }
    )


@dataclass(frozen=True)
class _ScoringContext:
    """Dane dokumentu potrzebne do cech pewności: metoda, jakość OCR stron, tekst stron, symbole."""

    extraction_method: str | None
    page_quality: dict[int, float]
    normalized_pages: tuple[str, ...]
    symbols_inferred: bool

    @classmethod
    def from_result(cls, result: MpzpParseResult) -> _ScoringContext:
        audit = result.document_audit
        method: str | None = None
        qualities: dict[int, float] = {}
        pages: tuple[str, ...] = ()
        if audit is not None:
            method = audit.extraction_method if audit.extraction_method != "unsupported" else None
            qualities = {page.page_number: page.quality for page in audit.pages if page.quality is not None}
            pages = tuple(" ".join(page.text.split()) for page in audit.pages)
        elif _has_ocr_warning(result.warnings):
            method = "ocr"  # wynik złożony bez audytu dokumentu, ale z ostrzeżeniem o OCR
        return cls(
            extraction_method=method,
            page_quality=qualities,
            normalized_pages=pages,
            symbols_inferred=any(w.code == "ZONE_SYMBOLS_INFERRED" for w in result.warnings),
        )

    def quote_verified(self, parameter: MpzpParameter) -> bool:
        """Czy fragment dowodowy leży w tekście stron (zakres znaków z trybu blokowego liczy się z góry)."""
        if parameter.char_start is not None:
            return True
        if not parameter.source_text:
            return False
        if not self.normalized_pages:
            return True  # nie ma tekstu stron do porównania (wynik złożony ręcznie)
        needle = " ".join(parameter.source_text.split())
        page = parameter.page_number
        if page is not None and 1 <= page <= len(self.normalized_pages) and needle in self.normalized_pages[page - 1]:
            return True
        return any(needle in text for text in self.normalized_pages)


def _has_ocr_warning(warnings: list[MpzpParserWarning]) -> bool:
    return any(
        "OCR" in warning.code or warning.severity == "error"
        for warning in warnings
        if warning.stage == "extract_text"
    )


def _ambiguous_zone_symbols(warnings: list[MpzpParserWarning]) -> set[str]:
    return {
        warning.zone_symbol
        for warning in warnings
        if warning.code in {"ZONE_SECTION_AMBIGUOUS", "ZONE_SCOPE_AMBIGUOUS"} and warning.zone_symbol
    }


def _validate_zone_parameters(
    zone_symbol: str,
    parameters: list[MpzpParameter],
    *,
    context: _ScoringContext,
    zone_ambiguous: bool,
) -> tuple[list[MpzpParameter], list[str], list[MpzpParserWarning]]:
    conflicting_keys = _conflicting_keys(parameters)
    candidates = _candidate_counts(parameters)

    conflict_flags: list[str] = []
    warnings: list[MpzpParserWarning] = []
    for name in sorted({key[0] for key in conflicting_keys}):
        keys = sorted(
            (key for key in conflicting_keys if key[0] == name),
            key=lambda key: (str(key[1]), sorted(key[2])),
        )
        for key in keys:
            distinct_values = sorted(
                {
                    param.normalized_value
                    for param in parameters
                    if _group_key(param) == key and param.normalized_value is not None
                },
                key=str,
            )
            scope_note = f" [{_describe_conditions(key[2])}]" if key[2] else ""
            conflict_flags.append(
                f"{zone_symbol}: {name}{scope_note} ma sprzeczne wartości "
                f"({', '.join(str(value) for value in distinct_values)})"
            )
        warnings.append(
            MpzpParserWarning(
                stage="validate_result",
                code="PARAMETER_CONFLICT",
                message=(
                    f"Parametr „{name}” w strefie {zone_symbol} ma kilka "
                    "różnych wartości znalezionych w dokumencie dla tej samej przesłanki "
                    "i wymaga ręcznej weryfikacji."
                ),
                zone_symbol=zone_symbol,
                parameter_name=name,
                page_number=None,
                severity="warning",
            )
        )

    validated_parameters: list[MpzpParameter] = []
    for parameter in parameters:
        key = _group_key(parameter)
        in_conflict = key in conflicting_keys
        update: dict[str, object] = {"value_kind": _value_kind(parameter, in_conflict)}
        if in_conflict:
            update["conflict_group_id"] = conflict_group_id(zone_symbol, parameter.name, key[2])
        parameter = parameter.model_copy(update=update)
        validated, domain_warning = _validate_single_parameter(
            zone_symbol,
            parameter,
            has_conflict=in_conflict,
            context=context,
            zone_ambiguous=zone_ambiguous,
            candidate_count=candidates.get(_group_key(parameter), 1),
        )
        validated_parameters.append(validated)
        if domain_warning is not None:
            warnings.append(domain_warning)

    return validated_parameters, conflict_flags, warnings


def conflict_group_id(
    zone_symbol: str, parameter_name: str, conditions: frozenset[tuple[str, str]] = frozenset()
) -> str:
    """Deterministyczne ID grupy sprzecznych kandydatur w obrębie dokumentu.

    Dokument i wersja aktu są dołączane przy mapowaniu do wyniku analizy, więc tu wystarcza strefa
    + parametr (+ zestaw warunków, gdy sprzeczność dotyczy wartości z tym samym warunkiem).
    """
    base = f"{zone_symbol}:{parameter_name}"
    if not conditions:
        return base
    # Etykiety warunków bywają długie, a kolumna ``conflict_group_id`` ma 300 znaków, więc zestaw
    # warunków wchodzi do identyfikatora jako krótki, deterministyczny skrót.
    digest = hashlib.sha256("|".join(f"{kind}:{label}" for kind, label in sorted(conditions)).encode()).hexdigest()
    return f"{base}#{digest[:10]}"


def _describe_conditions(conditions: frozenset[tuple[str, str]]) -> str:
    return "; ".join(label for _kind, label in sorted(conditions))


GroupKey = tuple[str, "str | None", frozenset]


def _group_key(parameter: MpzpParameter) -> GroupKey:
    """Przesłanka wartości: nazwa, zakres bloku i zestaw warunków (pusty = wartość bezwarunkowa)."""
    return (parameter.name, parameter.scope_kind, condition_key(parameter.conditions))


def _value_kind(parameter: MpzpParameter, in_conflict: bool) -> str:
    if in_conflict:
        return VALUE_KIND_CONFLICT
    return VALUE_KIND_CONDITIONAL if parameter.conditions else VALUE_KIND_UNCONDITIONAL


def _conflicting_keys(parameters: list[MpzpParameter]) -> set[GroupKey]:
    """Przesłanki, dla których liczbowy parametr ma więcej niż jedną wartość (PV3-08).

    Detekcja obejmuje WYŁĄCZNIE wartości liczbowe: parametry opisowe (``primary_use``,
    ``prohibition``, ``roof_geometry``…) są z natury listami współistniejących ustaleń. Wartości
    z różnymi warunkami należą do różnych przesłanek, więc ze sobą nie kolidują. Wartość z klauzuli
    ogólnej albo resztowej (inny ``scope_kind``) nie jest sprzeczna z wartością z sekcji strefy;
    w trybie dotychczasowym ``scope_kind`` jest ``None``.
    """
    values: dict[GroupKey, set[float]] = {}
    for parameter in parameters:
        if isinstance(parameter.normalized_value, (int, float)):
            values.setdefault(_group_key(parameter), set()).add(parameter.normalized_value)
    return {key for key, found in values.items() if len(found) > 1}


def _candidate_counts(parameters: list[MpzpParameter]) -> dict[GroupKey, int]:
    """Liczba różnych wartości liczbowych w tej samej przesłance (nazwa, zakres, warunki)."""
    values: dict[GroupKey, set[float]] = {}
    for parameter in parameters:
        if isinstance(parameter.normalized_value, (int, float)):
            values.setdefault(_group_key(parameter), set()).add(parameter.normalized_value)
    return {key: len(found) for key, found in values.items()}


def _confidence_features(
    parameter: MpzpParameter,
    *,
    context: _ScoringContext,
    zone_ambiguous: bool,
    candidate_count: int,
) -> evidence_confidence.ConfidenceFeatures:
    page = parameter.page_number
    return evidence_confidence.ConfidenceFeatures(
        extraction_method=parameter.extraction_method or context.extraction_method,
        ocr_quality=context.page_quality.get(page) if page is not None else None,
        scope_strategy=parameter.scope_strategy,
        scope_confidence=parameter.scope_confidence,
        scope_kind=parameter.scope_kind,
        quote_verified=context.quote_verified(parameter),
        strategy=parameter.extraction_strategy,
        flags=tuple(parameter.normalization_flags),
        candidate_count=candidate_count,
        value_kind=parameter.value_kind,
        zone_ambiguous=zone_ambiguous,
        symbols_inferred=context.symbols_inferred,
    )


def _validate_single_parameter(
    zone_symbol: str,
    parameter: MpzpParameter,
    *,
    has_conflict: bool,
    context: _ScoringContext,
    zone_ambiguous: bool,
    candidate_count: int = 1,
) -> tuple[MpzpParameter, MpzpParserWarning | None]:
    manual_review_required = parameter.manual_review_required
    features = _confidence_features(
        parameter, context=context, zone_ambiguous=zone_ambiguous, candidate_count=candidate_count
    )
    artifact = evidence_confidence.current_artifact()
    update: dict[str, object] = {"confidence_features": features.as_dict()}
    if isinstance(parameter.normalized_value, (int, float)):
        scored = evidence_confidence.score(features, artifact)
        update.update(
            confidence=scored.confidence,
            confidence_band=scored.band,
            confidence_calibration=scored.calibration_id,
        )
        # Próg ręcznej weryfikacji pochodzi z pomiaru (artefakt kalibracji), nie z liczby przyjętej z góry.
        if scored.manual_review_required:
            manual_review_required = True
    else:
        # Zapis opisowy nie ma kalibracji: pewność ograniczona od góry, bez pasma.
        update.update(
            confidence=min(parameter.confidence, evidence_confidence.probability(features, artifact), artifact.uncalibrated_cap),
            confidence_band=None,
            confidence_calibration=None,
        )

    if has_conflict:
        manual_review_required = True

    domain_warning = _check_domain_range(zone_symbol, parameter)
    if domain_warning is not None:
        manual_review_required = True

    update["manual_review_required"] = manual_review_required
    return parameter.model_copy(update=update), domain_warning


def _check_domain_range(
    zone_symbol: str, parameter: MpzpParameter
) -> MpzpParserWarning | None:
    value = parameter.normalized_value
    if not isinstance(value, (int, float)):
        return None

    violates_range = False
    if parameter.name.endswith(_PERCENT_NAME_SUFFIX) and not (0 <= value <= 100):
        violates_range = True
    elif parameter.name in _POSITIVE_ONLY_PARAMETER_NAMES and value <= 0:
        violates_range = True

    if not violates_range:
        return None

    logger.debug(
        "Naruszenie zakresu domenowego: strefa=%s parametr=%s wartość=%s",
        zone_symbol,
        parameter.name,
        value,
    )
    return MpzpParserWarning(
        stage="validate_result",
        code="DOMAIN_RANGE_VIOLATION",
        message=(
            f"Wartość parametru „{parameter.name}” w strefie {zone_symbol} "
            "jest poza dopuszczalnym zakresem i wymaga ręcznej weryfikacji."
        ),
        zone_symbol=zone_symbol,
        parameter_name=parameter.name,
        page_number=parameter.page_number,
        severity="warning",
    )
