"""Systemowa warstwa walidacji i confidence scoring wyniku parsera MPZP.

``validate_mpzp_result`` NIE zastępuje ustaleń numerycznych/opisowych
ekstraktorów (``mpzp_parser_numeric.py``, ``mpzp_parser_descriptive.py``),
tylko dokłada dodatkową, systemową warstwę kar do ``confidence`` i wykrywa
konflikty MIĘDZY parametrami o tej samej nazwie w tej samej strefie. Zakres
tej detekcji obejmuje też parametry dodane przez różne moduły ekstraktorów
(numeric i descriptive), jeśli kiedyś nazwa parametru się powtórzy między
nimi — walidacja nie zakłada, z którego modułu parametr pochodzi.

Komunikaty w ``MpzpParserWarning.message`` są po polsku i user-facing.
Techniczne detale (dokładny regex, wewnętrzne wyjątki, nazwy klas) idą
wyłącznie do ``logger.debug``, nigdy do ``message`` — użytkownik nietechniczny
nie powinien widzieć szczegółów implementacji.

Progi confidence 0.5-0.8 ("wynik częściowy") i >=0.8 ("wynik pewny") są
semantyką dokumentacyjną tego modułu, nie osobnym polem schematu: poniżej 0.5
parametr jest zawsze wymuszony na ``manual_review_required=True``, powyżej
tego progu istniejące pole ``confidence`` już wystarczająco komunikuje poziom
zaufania.
"""

from __future__ import annotations

import logging
from typing import Final

from app.schemas.mpzp import (
    MpzpParameter,
    MpzpParseResult,
    MpzpParserWarning,
    MpzpZoneResult,
)

logger = logging.getLogger(__name__)

# Kara globalna OCR (ADR-005): tekst odczytany przez OCR — albo etap
# extract_text zgłosił problem z OCR — zawsze obniża confidence względem
# równoważnego tekstowego PDF. Stosowana raz na dokument, nie per ostrzeżenie.
_OCR_WARNING_CONFIDENCE_PENALTY: Final[float] = 0.85
OCR_CONFIDENCE_PENALTY: Final[float] = _OCR_WARNING_CONFIDENCE_PENALTY
# Kara dla całej strefy, gdy segmentacja zgłosiła wieloznaczność lokalizacji
# sekcji tej strefy (ZONE_SECTION_AMBIGUOUS) — nie wiadomo z pewnością, czy
# parametry pochodzą z właściwego fragmentu uchwały.
_AMBIGUOUS_ZONE_CONFIDENCE_PENALTY: Final[float] = 0.85
# Kara za brak numeru strony — słabszy sygnał niepewności niż brak
# source_text, więc kara jest łagodna.
_MISSING_PAGE_NUMBER_CONFIDENCE_PENALTY: Final[float] = 0.95
# Parametr bez source_text nie może mieć wysokiego confidence — to jest
# twardy sufit, nie mnożnik, bo brak dowodu źródłowego jest fundamentalnym
# ograniczeniem niezależnie od tego, jak wysoko ekstraktor sam siebie ocenił.
_MISSING_SOURCE_TEXT_CONFIDENCE_CEILING: Final[float] = 0.4
# Poniżej tego progu wynik jest na tyle niepewny, że wymusza ręczną
# weryfikację niezależnie od tego, co ustalił ekstraktor.
_MANUAL_REVIEW_CONFIDENCE_THRESHOLD: Final[float] = 0.5

_PERCENT_NAME_SUFFIX: Final[str] = "_percent"
_POSITIVE_ONLY_PARAMETER_NAMES: Final[frozenset[str]] = frozenset(
    {"max_building_height_m", "max_intensity", "min_intensity"}
)


def validate_mpzp_result(result: MpzpParseResult) -> MpzpParseResult:
    """Dokłada systemowe kary confidence i wykrywa konflikty parametrów.

    Pracuje na kopii wejścia i zwraca nowy ``MpzpParseResult`` — nie mutuje
    przekazanego obiektu, żeby wcześniejsze etapy pipeline'u mogły bezpiecznie
    zachować referencję do swojego wyniku.
    """
    ocr_warning_present = _has_ocr_warning(result.warnings) or (
        result.document_audit is not None
        and result.document_audit.extraction_method == "ocr"
    )
    ambiguous_zone_symbols = _ambiguous_zone_symbols(result.warnings)

    new_warnings: list[MpzpParserWarning] = list(result.warnings)
    conflict_flags: list[str] = list(result.conflict_flags)

    new_zones: list[MpzpZoneResult] = []
    for zone in result.zones:
        zone_ambiguous = zone.zone_symbol in ambiguous_zone_symbols
        parameters, zone_conflict_flags, zone_warnings = _validate_zone_parameters(
            zone.zone_symbol,
            zone.parameters,
            ocr_warning_present=ocr_warning_present,
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
        if warning.code == "ZONE_SECTION_AMBIGUOUS" and warning.zone_symbol
    }


def _validate_zone_parameters(
    zone_symbol: str,
    parameters: list[MpzpParameter],
    *,
    ocr_warning_present: bool,
    zone_ambiguous: bool,
) -> tuple[list[MpzpParameter], list[str], list[MpzpParserWarning]]:
    conflicting_names = _find_conflicting_parameter_names(parameters)

    conflict_flags: list[str] = []
    warnings: list[MpzpParserWarning] = []
    for name in sorted(conflicting_names):
        distinct_values = sorted(
            {
                param.normalized_value
                for param in parameters
                if param.name == name and param.normalized_value is not None
            },
            key=str,
        )
        conflict_flags.append(
            f"{zone_symbol}: {name} ma sprzeczne wartości "
            f"({', '.join(str(value) for value in distinct_values)})"
        )
        warnings.append(
            MpzpParserWarning(
                stage="validate_result",
                code="PARAMETER_CONFLICT",
                message=(
                    f"Parametr „{name}” w strefie {zone_symbol} ma kilka "
                    "różnych wartości znalezionych w dokumencie i wymaga "
                    "ręcznej weryfikacji."
                ),
                zone_symbol=zone_symbol,
                parameter_name=name,
                page_number=None,
                severity="warning",
            )
        )

    validated_parameters: list[MpzpParameter] = []
    for parameter in parameters:
        if parameter.name in conflicting_names:
            parameter = parameter.model_copy(
                update={"conflict_group_id": conflict_group_id(zone_symbol, parameter.name)}
            )
        validated, domain_warning = _validate_single_parameter(
            zone_symbol,
            parameter,
            has_conflict=parameter.name in conflicting_names,
            ocr_warning_present=ocr_warning_present,
            zone_ambiguous=zone_ambiguous,
        )
        validated_parameters.append(validated)
        if domain_warning is not None:
            warnings.append(domain_warning)

    return validated_parameters, conflict_flags, warnings


def conflict_group_id(zone_symbol: str, parameter_name: str) -> str:
    """Deterministyczne ID grupy sprzecznych kandydatur w obrębie dokumentu.

    Dokument i wersja aktu są dołączane przy mapowaniu do wyniku analizy, więc
    tu wystarcza para strefa + parametr.
    """
    return f"{zone_symbol}:{parameter_name}"


def _find_conflicting_parameter_names(
    parameters: list[MpzpParameter],
) -> set[str]:
    """Wykrywa nazwy parametrów o sprzecznych wartościach W TEJ SAMEJ STREFIE.

    Detekcja obejmuje WYŁĄCZNIE wartości liczbowe. Parametry opisowe
    (``primary_use``, ``prohibition``, ``permission``, ``roof_geometry`` itd.)
    są z natury listami — kilka wpisów tej samej nazwy to współistniejące
    ustalenia uchwały, nie sprzeczność jednej wartości (patrz dokumentacja
    ``mpzp_parser_descriptive.py``). Liczbowy parametr planistyczny (np.
    wysokość zabudowy) ma z definicji jedną obowiązującą wartość dla danej
    strefy, więc więcej niż jedna dystynktywna wartość jest realnym
    konfliktem wymagającym ręcznej weryfikacji.
    """
    values_by_name: dict[str, set[float]] = {}
    for parameter in parameters:
        if not isinstance(parameter.normalized_value, (int, float)):
            continue
        values_by_name.setdefault(parameter.name, set()).add(
            parameter.normalized_value
        )
    return {name for name, values in values_by_name.items() if len(values) > 1}


def _validate_single_parameter(
    zone_symbol: str,
    parameter: MpzpParameter,
    *,
    has_conflict: bool,
    ocr_warning_present: bool,
    zone_ambiguous: bool,
) -> tuple[MpzpParameter, MpzpParserWarning | None]:
    confidence = parameter.confidence
    manual_review_required = parameter.manual_review_required

    if ocr_warning_present:
        confidence *= _OCR_WARNING_CONFIDENCE_PENALTY
    if zone_ambiguous:
        confidence *= _AMBIGUOUS_ZONE_CONFIDENCE_PENALTY
    if parameter.page_number is None:
        confidence *= _MISSING_PAGE_NUMBER_CONFIDENCE_PENALTY

    if not parameter.source_text:
        # Twardy sufit, nie mnożnik: brak dowodu źródłowego nie może
        # zostać "odrobiony" wysoką pewnością samego ekstraktora.
        confidence = min(confidence, _MISSING_SOURCE_TEXT_CONFIDENCE_CEILING)

    if has_conflict:
        manual_review_required = True

    domain_warning = _check_domain_range(zone_symbol, parameter)
    if domain_warning is not None:
        manual_review_required = True

    if confidence < _MANUAL_REVIEW_CONFIDENCE_THRESHOLD:
        manual_review_required = True

    validated_parameter = parameter.model_copy(
        update={
            "confidence": confidence,
            "manual_review_required": manual_review_required,
        }
    )
    return validated_parameter, domain_warning


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
