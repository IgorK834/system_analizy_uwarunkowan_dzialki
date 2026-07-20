"""Jawna tabela zgodności znormalizowanych funkcji MPZP ze strefami POG.

Reguły są zapisane w Pythonie zamiast JSON, ponieważ każdy wpis wymaga
czytelnego uzasadnienia domenowego i kontroli typów enum. Brak wpisu oznacza
brak zdefiniowanej reguły, nigdy domyślny konflikt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Literal

from app.schemas.source import WarningMessage

CompatibilityValue = Literal["compatible", "incompatible", "uncertain", "unknown"]

EXPLICIT_COMPATIBILITY_CONFIDENCE: Final[float] = 0.9
EXPLICIT_INCOMPATIBILITY_CONFIDENCE: Final[float] = 0.85
CONDITIONAL_COMPATIBILITY_CONFIDENCE: Final[float] = 0.55
UNKNOWN_COMPATIBILITY_CONFIDENCE: Final[float] = 0.0


class MpzpFunction(StrEnum):
    """Minimalny zamknięty katalog funkcji MPZP do czasu ukończenia Task 4.5."""

    SINGLE_FAMILY_HOUSING = "single_family_housing"
    MULTI_FAMILY_HOUSING = "multi_family_housing"
    SERVICES = "services"
    PRODUCTION = "production"
    GREENERY = "greenery"
    AGRICULTURE = "agriculture"


class PogPlanningZoneType(StrEnum):
    """Ustawowe typy stref planistycznych POG oraz jawny wariant nieznany."""

    MULTIFUNCTIONAL_MULTI_FAMILY = "SW"
    MULTIFUNCTIONAL_SINGLE_FAMILY = "SJ"
    MULTIFUNCTIONAL_FARMSTEAD = "SZ"
    SERVICES = "SU"
    LARGE_FORMAT_RETAIL = "SH"
    ECONOMIC = "SP"
    AGRICULTURAL_PRODUCTION = "SR"
    INFRASTRUCTURE = "SI"
    GREENERY_AND_RECREATION = "SN"
    CEMETERY = "SC"
    MINING = "SG"
    OPEN = "SO"
    TRANSPORT = "SK"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CompatibilityRule:
    """Pojedynczy jawny wpis tabeli zgodności wraz z uzasadnieniem."""

    result: Literal["compatible", "incompatible", "uncertain"]
    reasoning: str
    confidence: float


@dataclass(frozen=True)
class PlanningCompatibilityResult:
    """Wynik deterministycznego sprawdzenia pary funkcja MPZP–strefa POG."""

    result: CompatibilityValue
    reasoning: str
    confidence: float
    warnings: list[WarningMessage] = field(default_factory=list)


# To nie jest automatyczna wykładnia prawa ani heurystyka nazw. Każda para jest
# wpisana jawnie. Wpisy ``uncertain`` obejmują funkcje, których dopuszczalność
# zależy od profilu usług i szczegółowych ustaleń obu aktów planistycznych.
PLANNING_COMPATIBILITY_RULES: Final[
    dict[tuple[MpzpFunction, PogPlanningZoneType], CompatibilityRule]
] = {
    (
        MpzpFunction.SINGLE_FAMILY_HOUSING,
        PogPlanningZoneType.MULTIFUNCTIONAL_SINGLE_FAMILY,
    ): CompatibilityRule(
        result="compatible",
        reasoning=(
            "Zabudowa mieszkaniowa jednorodzinna odpowiada podstawowemu profilowi "
            "strefy wielofunkcyjnej z zabudową jednorodzinną."
        ),
        confidence=EXPLICIT_COMPATIBILITY_CONFIDENCE,
    ),
    (
        MpzpFunction.MULTI_FAMILY_HOUSING,
        PogPlanningZoneType.MULTIFUNCTIONAL_MULTI_FAMILY,
    ): CompatibilityRule(
        result="compatible",
        reasoning=(
            "Zabudowa mieszkaniowa wielorodzinna odpowiada podstawowemu profilowi "
            "strefy wielofunkcyjnej z zabudową wielorodzinną."
        ),
        confidence=EXPLICIT_COMPATIBILITY_CONFIDENCE,
    ),
    (
        MpzpFunction.SERVICES,
        PogPlanningZoneType.SERVICES,
    ): CompatibilityRule(
        result="compatible",
        reasoning="Funkcja usługowa MPZP odpowiada podstawowemu profilowi strefy usługowej POG.",
        confidence=EXPLICIT_COMPATIBILITY_CONFIDENCE,
    ),
    (
        MpzpFunction.SERVICES,
        PogPlanningZoneType.MULTIFUNCTIONAL_SINGLE_FAMILY,
    ): CompatibilityRule(
        result="uncertain",
        reasoning=(
            "Usługi w strefie mieszkaniowej mogą być dopuszczalne warunkowo; "
            "wymagane jest sprawdzenie profilu usług i szczegółowych ustaleń aktów."
        ),
        confidence=CONDITIONAL_COMPATIBILITY_CONFIDENCE,
    ),
    (
        MpzpFunction.SERVICES,
        PogPlanningZoneType.MULTIFUNCTIONAL_MULTI_FAMILY,
    ): CompatibilityRule(
        result="uncertain",
        reasoning=(
            "Usługi w strefie mieszkaniowej mogą być funkcją uzupełniającą, ale "
            "zgodność zależy od ich charakteru i szczegółowych ustaleń planów."
        ),
        confidence=CONDITIONAL_COMPATIBILITY_CONFIDENCE,
    ),
    (
        MpzpFunction.PRODUCTION,
        PogPlanningZoneType.ECONOMIC,
    ): CompatibilityRule(
        result="compatible",
        reasoning="Funkcja produkcyjna odpowiada podstawowemu profilowi strefy gospodarczej POG.",
        confidence=EXPLICIT_COMPATIBILITY_CONFIDENCE,
    ),
    (
        MpzpFunction.PRODUCTION,
        PogPlanningZoneType.GREENERY_AND_RECREATION,
    ): CompatibilityRule(
        result="incompatible",
        reasoning=(
            "Zabudowa produkcyjna narusza podstawową funkcję zieleni i rekreacji "
            "przypisaną tej strefie POG."
        ),
        confidence=EXPLICIT_INCOMPATIBILITY_CONFIDENCE,
    ),
    (
        MpzpFunction.PRODUCTION,
        PogPlanningZoneType.OPEN,
    ): CompatibilityRule(
        result="incompatible",
        reasoning=(
            "Zabudowa produkcyjna jest sprzeczna z podstawowym, otwartym "
            "charakterem tej strefy POG."
        ),
        confidence=EXPLICIT_INCOMPATIBILITY_CONFIDENCE,
    ),
    (
        MpzpFunction.GREENERY,
        PogPlanningZoneType.GREENERY_AND_RECREATION,
    ): CompatibilityRule(
        result="compatible",
        reasoning="Funkcja zieleni odpowiada podstawowemu profilowi strefy zieleni i rekreacji.",
        confidence=EXPLICIT_COMPATIBILITY_CONFIDENCE,
    ),
    (
        MpzpFunction.GREENERY,
        PogPlanningZoneType.OPEN,
    ): CompatibilityRule(
        result="compatible",
        reasoning="Funkcja zieleni zachowuje niezabudowany charakter strefy otwartej.",
        confidence=EXPLICIT_COMPATIBILITY_CONFIDENCE,
    ),
    (
        MpzpFunction.AGRICULTURE,
        PogPlanningZoneType.AGRICULTURAL_PRODUCTION,
    ): CompatibilityRule(
        result="compatible",
        reasoning="Funkcja rolnicza odpowiada podstawowemu profilowi strefy produkcji rolniczej.",
        confidence=EXPLICIT_COMPATIBILITY_CONFIDENCE,
    ),
}


def check_mpzp_pog_compatibility(
    mpzp_function: MpzpFunction | str,
    pog_zone_type: PogPlanningZoneType | str,
) -> PlanningCompatibilityResult:
    """Sprawdza jawną zgodność funkcji MPZP ze strefą POG.

    Funkcja nie wykonuje obliczeń geometrycznych; zakłada, że funkcję MPZP
    i dominującą strefę POG wyznaczono wcześniej na podstawie przecięć
    powierzchniowych w EPSG:2180. Relacja MPZP–POG nie jest prostym
    nadpisaniem: wynik ``compatible`` lub ``incompatible`` opisuje wyłącznie
    wpis w jawnej tabeli, a ``uncertain`` wymaga analizy szczegółowych ustaleń.

    Nierozpoznana wartość albo para bez wpisu zwraca ``unknown`` z ostrzeżeniem,
    nigdy domyślny konflikt. Każdy wynik ma charakter informacyjny i może
    wymagać ręcznej analizy planistyczno-prawnej.
    """
    normalized_function = _as_mpzp_function(mpzp_function)
    normalized_zone = _as_pog_zone_type(pog_zone_type)
    if normalized_function is None or normalized_zone is None:
        return _unknown_result(
            "Nierozpoznana kategoria funkcji MPZP lub typ strefy POG."
        )

    rule = PLANNING_COMPATIBILITY_RULES.get((normalized_function, normalized_zone))
    if rule is None:
        return _unknown_result(
            "Tabela nie zawiera zweryfikowanej reguły dla tej kombinacji funkcji MPZP i strefy POG."
        )
    return PlanningCompatibilityResult(
        result=rule.result,
        reasoning=rule.reasoning,
        confidence=rule.confidence,
    )


def _as_mpzp_function(value: MpzpFunction | str) -> MpzpFunction | None:
    try:
        return value if isinstance(value, MpzpFunction) else MpzpFunction(value)
    except ValueError:
        return None


def _as_pog_zone_type(
    value: PogPlanningZoneType | str,
) -> PogPlanningZoneType | None:
    try:
        return (
            value
            if isinstance(value, PogPlanningZoneType)
            else PogPlanningZoneType(value)
        )
    except ValueError:
        return None


def _unknown_result(reasoning: str) -> PlanningCompatibilityResult:
    return PlanningCompatibilityResult(
        result="unknown",
        reasoning=reasoning,
        confidence=UNKNOWN_COMPATIBILITY_CONFIDENCE,
        warnings=[
            WarningMessage(
                code="MPZP_POG_COMPATIBILITY_UNKNOWN",
                message=(
                    "Nie można automatycznie ustalić zgodności MPZP z POG; "
                    "wynik wymaga ręcznej weryfikacji."
                ),
                severity="warning",
                source_name="planning_compatibility",
            )
        ],
    )
