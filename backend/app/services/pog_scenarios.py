"""Budowanie czytelnych scenariuszy POG, OUZ i zgodności z MPZP."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Final, Literal, Protocol

from app.core.planning_compatibility import (
    MpzpFunction,
    PlanningCompatibilityResult,
    check_mpzp_pog_compatibility,
)
from app.schemas.source import WarningMessage
from app.services.ouz import OuzStatusResult

# Data jest punktem odniesienia opisu okresu przejściowego reformy i pozostaje
# w jednym miejscu, aby aktualizacja po zmianie prawa nie wymagała modyfikacji
# rozgałęzień domenowych ani testów opartych na przypadkowym litera­le tekstowym.
PLANNING_REFORM_REGISTER_DUTY_DATE: Final[date] = date(2026, 1, 1)
PLANNING_REFORM_REGISTER_DUTY_DATE_LABEL: Final[str] = "1 stycznia 2026 r."

LEGAL_INFORMATION_DISCLAIMER: Final[str] = (
    "Analiza ma charakter informacyjny i nie stanowi decyzji urzędowej ani "
    "administracyjnej, ani porady prawnej."
)

PogScenarioStatus = Literal["adopted", "not_available", "in_progress", "unknown"]


class MpzpScenarioInput(Protocol):
    """Minimalny kontrakt MPZP do czasu wprowadzenia kategorii w Task 4.5."""

    primary_use: str | None


class PogScenarioInput(Protocol):
    """Minimalny kontrakt wyniku POG potrzebny do wyboru scenariusza."""

    status: str
    planning_zone: str | None


@dataclass(frozen=True)
class PogScenarioResult:
    """Złożony scenariusz planistyczny z jawną niepewnością i klauzulą."""

    status: PogScenarioStatus
    conflict: bool
    conflict_uncertain: bool
    compatibility: PlanningCompatibilityResult | None
    ouz_status: OuzStatusResult
    manual_review_required: bool
    message: str
    legal_disclaimer: str
    warnings: list[WarningMessage] = field(default_factory=list)


def build_pog_scenario_result(
    mpzp_result: MpzpScenarioInput | None,
    pog_result: PogScenarioInput | None,
    ouz_status: OuzStatusResult,
) -> PogScenarioResult:
    """Buduje scenariusz POG/OUZ bez kategorycznej porady prawnej.

    Funkcja nie wykonuje geometrii; zakłada wcześniejsze obliczenie przecięć
    działki, OUZ i stref POG w EPSG:2180. Dla uchwalonego POG potencjalny
    konflikt jest ustawiany wyłącznie przez
    ``check_mpzp_pog_compatibility``. Wynik ``unknown`` lub ``uncertain`` nie
    jest dowodem konfliktu i ustawia jedynie ``conflict_uncertain``.

    Status ``not_available`` albo ``in_progress`` jest prawidłowym scenariuszem
    okresu przejściowego, nie błędem. Każdy komunikat zawiera klauzulę, że wynik
    jest informacyjny i wymaga sprawdzenia aktualnego stanu prawnego.
    """
    status = _normalized_pog_status(pog_result.status if pog_result else None)
    if status in {"not_available", "in_progress"}:
        transition = (
            "Gmina nie udostępnia jeszcze uchwalonego POG."
            if status == "not_available"
            else "Procedura sporządzania lub uchwalania POG jest w toku."
        )
        return PogScenarioResult(
            status=status,
            conflict=False,
            conflict_uncertain=True,
            compatibility=None,
            ouz_status=ouz_status,
            manual_review_required=True,
            message=_with_disclaimer(
                f"{transition} Trwa okres wdrażania reformy planowania; "
                f"obowiązki rejestrowe są odnoszone do daty "
                f"{PLANNING_REFORM_REGISTER_DUTY_DATE_LABEL}."
            ),
            legal_disclaimer=LEGAL_INFORMATION_DISCLAIMER,
            warnings=[
                _warning(
                    "POG_TRANSITIONAL_STATUS",
                    "Brak uchwalonego POG lub trwająca procedura wymaga sprawdzenia aktualnych dokumentów gminy.",
                )
            ],
        )

    if status == "unknown" or pog_result is None:
        return PogScenarioResult(
            status="unknown",
            conflict=False,
            conflict_uncertain=True,
            compatibility=None,
            ouz_status=ouz_status,
            manual_review_required=True,
            message=_with_disclaimer(
                "Nie udało się potwierdzić statusu POG ani przeprowadzić oceny zgodności z MPZP."
            ),
            legal_disclaimer=LEGAL_INFORMATION_DISCLAIMER,
            warnings=[
                _warning(
                    "POG_SCENARIO_UNKNOWN",
                    "Brak potwierdzonych danych POG wymaga ręcznej weryfikacji w źródłach gminy.",
                    severity="error",
                )
            ],
        )

    mpzp_function = _mpzp_function(mpzp_result)
    pog_zone_type = pog_result.planning_zone
    if mpzp_function is None or not pog_zone_type:
        return PogScenarioResult(
            status="adopted",
            conflict=False,
            conflict_uncertain=True,
            compatibility=None,
            ouz_status=ouz_status,
            manual_review_required=True,
            message=_with_disclaimer(
                "POG jest uchwalony, lecz brakuje znormalizowanej funkcji MPZP albo dominującej strefy POG do oceny zgodności."
            ),
            legal_disclaimer=LEGAL_INFORMATION_DISCLAIMER,
            warnings=[
                _warning(
                    "MPZP_POG_INPUT_INCOMPLETE",
                    "Ocena zgodności wymaga znormalizowanej funkcji MPZP i typu strefy POG.",
                )
            ],
        )

    compatibility = check_mpzp_pog_compatibility(mpzp_function, pog_zone_type)
    conflict = compatibility.result == "incompatible"
    conflict_uncertain = compatibility.result in {"unknown", "uncertain"}
    manual_review_required = (
        conflict or conflict_uncertain or ouz_status.manual_review_required
    )
    if conflict:
        summary = "Jawna tabela zgodności wskazuje potencjalną rozbieżność funkcji MPZP ze strefą POG."
    elif compatibility.result == "compatible":
        summary = "Jawna tabela zgodności nie wskazuje rozbieżności funkcji MPZP ze strefą POG."
    else:
        summary = "Tabela zgodności nie pozwala jednoznacznie ocenić relacji funkcji MPZP ze strefą POG."

    return PogScenarioResult(
        status="adopted",
        conflict=conflict,
        conflict_uncertain=conflict_uncertain,
        compatibility=compatibility,
        ouz_status=ouz_status,
        manual_review_required=manual_review_required,
        message=_with_disclaimer(f"{summary} {compatibility.reasoning}"),
        legal_disclaimer=LEGAL_INFORMATION_DISCLAIMER,
        warnings=list(compatibility.warnings),
    )


def _mpzp_function(mpzp_result: MpzpScenarioInput | None) -> MpzpFunction | None:
    if mpzp_result is None or mpzp_result.primary_use is None:
        return None
    try:
        # Tylko dokładna wartość enum jest akceptowana. Nie klasyfikujemy
        # dowolnych opisów primary_use przez słowa kluczowe, bo Task 4.5 nie
        # dostarczył jeszcze zweryfikowanego normalizatora funkcji MPZP.
        return MpzpFunction(mpzp_result.primary_use)
    except ValueError:
        return None


def _normalized_pog_status(value: str | None) -> PogScenarioStatus:
    if value == "adopted":
        return "adopted"
    if value == "not_available":
        return "not_available"
    if value == "in_progress":
        return "in_progress"
    return "unknown"


def _with_disclaimer(message: str) -> str:
    return f"{message} {LEGAL_INFORMATION_DISCLAIMER}"


def _warning(
    code: str,
    message: str,
    *,
    severity: Literal["info", "warning", "error"] = "warning",
) -> WarningMessage:
    return WarningMessage(
        code=code,
        message=message,
        severity=severity,
        source_name="pog",
    )
