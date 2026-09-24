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
from app.shared.planning_status import (
    NO_GEOMETRY_IS_NOT_NO_PLAN_PL,
    LegalStatus,
    canonical_legal_status,
)

# Data jest punktem odniesienia opisu okresu przejściowego reformy i pozostaje
# w jednym miejscu, aby aktualizacja po zmianie prawa nie wymagała modyfikacji
# rozgałęzień domenowych ani testów opartych na przypadkowym litera­le tekstowym.
PLANNING_REFORM_REGISTER_DUTY_DATE: Final[date] = date(2026, 1, 1)
PLANNING_REFORM_REGISTER_DUTY_DATE_LABEL: Final[str] = "1 stycznia 2026 r."

LEGAL_INFORMATION_DISCLAIMER: Final[str] = (
    "Analiza ma charakter informacyjny i nie stanowi decyzji urzędowej ani "
    "administracyjnej, ani porady prawnej."
)

PogScenarioStatus = LegalStatus


class MpzpScenarioInput(Protocol):
    """Minimalny kontrakt MPZP do czasu wprowadzenia kategorii w Task 4.5."""

    primary_use: str | None


class PogScenarioInput(Protocol):
    """Minimalny kontrakt wyniku POG potrzebny do wyboru scenariusza."""

    legal_status: str
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
    działki, OUZ i stref POG w EPSG:2180. Ocena zgodności z MPZP jest
    wykonywana wyłącznie dla aktu ``binding`` potwierdzonego urzędowo — przez
    ``check_mpzp_pog_compatibility``. Wynik ``unknown`` lub ``uncertain`` nie
    jest dowodem konfliktu i ustawia jedynie ``conflict_uncertain``.

    ``project`` i ``in_progress`` są prawidłowym scenariuszem okresu
    przejściowego, opisanym bez języka obowiązywania. ``superseded`` i
    ``unknown`` nie są interpretowane jako brak planu.
    """
    status = canonical_legal_status(pog_result.legal_status if pog_result else None)
    if status in {"project", "in_progress"}:
        transition = (
            "Dostępny jest projekt POG; projekt nie jest aktem wiążącym."
            if status == "project"
            else "Procedura sporządzania POG jest w toku; ustalenia nie są wiążące."
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
                    "Akt POG jest projektem albo w trakcie sporządzania; sprawdź aktualne dokumenty gminy.",
                )
            ],
        )

    if status != "binding" or pog_result is None:
        detail = (
            "Akt POG jest nieaktualny; ocena zgodności z MPZP wymaga aktu, który go zastąpił."
            if status == "superseded"
            else "Nie udało się potwierdzić statusu prawnego POG w źródle urzędowym. "
            f"{NO_GEOMETRY_IS_NOT_NO_PLAN_PL}"
        )
        return PogScenarioResult(
            status=status,
            conflict=False,
            conflict_uncertain=True,
            compatibility=None,
            ouz_status=ouz_status,
            manual_review_required=True,
            message=_with_disclaimer(
                f"{detail} Nie przeprowadzono oceny zgodności z MPZP."
            ),
            legal_disclaimer=LEGAL_INFORMATION_DISCLAIMER,
            warnings=[
                _warning(
                    "POG_SCENARIO_UNKNOWN",
                    "Brak potwierdzonego statusu POG wymaga ręcznej weryfikacji w Rejestrze Urbanistycznym.",
                    severity="error",
                )
            ],
        )

    mpzp_function = _mpzp_function(mpzp_result)
    pog_zone_type = pog_result.planning_zone
    if mpzp_function is None or not pog_zone_type:
        return PogScenarioResult(
            status="binding",
            conflict=False,
            conflict_uncertain=True,
            compatibility=None,
            ouz_status=ouz_status,
            manual_review_required=True,
            message=_with_disclaimer(
                "POG obowiązuje, lecz brakuje znormalizowanej funkcji MPZP albo dominującej strefy POG do oceny zgodności."
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
        status="binding",
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
