"""Cykliczna kontrola dryfu ścieżki modelu: bieżące wyjścia kontra zamrożone odtworzenie (PV3-19).

Dostawca może zmienić zachowanie modelu pod tym samym identyfikatorem. Kontrola bierze zestaw
„kanarkowych” bloków stref, przepuszcza je przez TEN SAM potok (kontrakt → bramki G1–G8) dwa razy:

* z **zamrożonym odtworzeniem** (odpowiedzi zapisane pod kluczem modelu i instrukcji, bez sieci),
* z **dostawcą na żywo** (ręcznie albo z harmonogramu, poza CI),

i porównuje zbiory PRZYJĘTYCH wartości (strefa, parametr, operator, wartość, warunki). Dryf to udział
wartości, które występują tylko po jednej stronie, w sumie wszystkich: ``(tylko_zamrożone +
tylko_bieżące) / suma``. Osiągnięcie progu (przy dryfie > 0) — alarm. Porównanie dotyczy przyjętych wartości, nie
bajtów odpowiedzi (model nie jest bajtowo powtarzalny — ADR-012, pomiar).

Awaria dostawcy (limit, sieć, wyłącznik) nie jest dryfem: blok bez kompletnej odpowiedzi na żywo jest
pomijany i wymieniony w ``failures``; gdy nie porównano żadnego bloku, wynik to ``inconclusive``,
nigdy ``ok`` ani ``alarm``. Zamrożone odtworzenie jest złotym wzorcem z korpusu (nie nagraniem modelu),
więc kontrola mierzy także zgodność z adnotacjami na zestawie kanarkowym.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.modules.planning.application.llm_extraction import ExtractionLimits
from app.modules.planning.application.llm_monitoring import DriftStatus
from app.modules.planning.application.llm_pipeline import BudgetTracker, LlmBudget, MpzpLlmPipeline
from app.modules.planning.application.ports import StructuredExtractionProvider
from app.modules.planning.domain import extraction_contract as contract
from app.modules.planning.domain.candidate_verifier import AcceptedCandidate, DocumentContext
from app.modules.planning.domain.zone_blocks import ZoneBlock

DEFAULT_DRIFT_THRESHOLD: Final[float] = 0.20
ValueKey = tuple[str, str, str, float, tuple[tuple[str, str], ...]]


@dataclass(frozen=True)
class DriftCase:
    """Blok kanarkowy: identyfikator przypadku, blok strefy, skrót dokumentu i kontekst bramek."""

    case_id: str
    block: ZoneBlock
    document_sha256: str
    document: DocumentContext | None = None


@dataclass(frozen=True)
class CaseDrift:
    case_id: str
    block_id: str
    compared: bool
    frozen: int
    live: int
    matching: int
    only_frozen: tuple[str, ...] = ()
    only_live: tuple[str, ...] = ()
    failure: str | None = None


@dataclass(frozen=True)
class DriftReport:
    status: DriftStatus
    drift_rate: float | None
    threshold: float
    compared: int
    skipped: int
    frozen_values: int
    live_values: int
    matching: int
    only_frozen: int
    only_live: int
    cases: tuple[CaseDrift, ...]
    model_id: str
    prompt_version: str
    live_calls: int = 0

    @property
    def alarm(self) -> bool:
        return self.status == "alarm"


def value_key(item: AcceptedCandidate) -> ValueKey:
    conditions = tuple(sorted((c.kind, " ".join(c.quote.casefold().split())) for c in item.conditions))
    return item.zone_symbol, item.parameter, item.operator, round(float(item.value), 6), conditions


def _label(key: ValueKey) -> str:
    """Opis wartości do raportu: strefa, parametr, operator, wartość (bez cytatów)."""
    zone, parameter, operator, value, conditions = key
    suffix = f" [{len(conditions)} warunk.]" if conditions else ""
    return f"{zone}:{parameter}:{operator}={value:g}{suffix}"


def compare_values(frozen: Sequence[AcceptedCandidate], live: Sequence[AcceptedCandidate]) -> tuple[int, list[str], list[str]]:
    """(liczba wspólnych, tylko zamrożone, tylko bieżące) — porównanie zbiorów kluczy wartości."""
    left = {value_key(item) for item in frozen}
    right = {value_key(item) for item in live}
    return len(left & right), sorted(_label(k) for k in left - right), sorted(_label(k) for k in right - left)


def drift_rate(matching: int, only_frozen: int, only_live: int) -> float | None:
    union = matching + only_frozen + only_live
    return round((only_frozen + only_live) / union, 4) if union else None


def summarize(
    cases: Sequence[CaseDrift], *, threshold: float, model_id: str, prompt_version: str, live_calls: int
) -> DriftReport:
    compared = [case for case in cases if case.compared]
    matching = sum(case.matching for case in compared)
    only_frozen = sum(len(case.only_frozen) for case in compared)
    only_live = sum(len(case.only_live) for case in compared)
    rate = drift_rate(matching, only_frozen, only_live)
    if not compared:
        status: DriftStatus = "inconclusive"
    elif rate is None:
        status = "ok"  # nic po żadnej stronie: zgodne (brak wartości w obu wynikach)
    else:
        status = "alarm" if rate > 0 and rate >= threshold else "ok"  # osiągnięcie progu alarmuje (jak progi zdrowia)
    return DriftReport(
        status=status,
        drift_rate=rate,
        threshold=threshold,
        compared=len(compared),
        skipped=len(cases) - len(compared),
        frozen_values=sum(case.frozen for case in compared),
        live_values=sum(case.live for case in compared),
        matching=matching,
        only_frozen=only_frozen,
        only_live=only_live,
        cases=tuple(cases),
        model_id=model_id,
        prompt_version=prompt_version,
        live_calls=live_calls,
    )


async def run_drift_check(
    cases: Sequence[DriftCase],
    *,
    frozen: StructuredExtractionProvider,
    live: StructuredExtractionProvider,
    limits: ExtractionLimits | None = None,
    threshold: float = DEFAULT_DRIFT_THRESHOLD,
    max_requests: int = 50,
) -> DriftReport:
    """Porównuje przyjęte wartości z odtworzenia i z dostawcy na żywo dla bloków kanarkowych.

    ``frozen`` i ``live`` przechodzą przez ten sam ``MpzpLlmPipeline`` bez cache (kontrola ma zawsze
    wołać dostawcę na żywo). ``max_requests`` ogranicza liczbę żądań na żywo (koszt kontroli).
    """
    reference = MpzpLlmPipeline(frozen, limits=limits, cache=None, budget=LlmBudget(max_requests=10_000, max_input_tokens=10**9))
    # Jeden licznik na wszystkie bloki: ``max_requests`` ogranicza łączną liczbę żądań na żywo.
    live_budget = LlmBudget(max_requests=max_requests, max_input_tokens=10**9)
    current = MpzpLlmPipeline(live, limits=limits, cache=None, budget=live_budget, tracker=BudgetTracker(live_budget))
    results: list[CaseDrift] = []
    live_calls = 0
    for case in cases:
        before = await reference.run([case.block], document_sha256=case.document_sha256, document=case.document)
        after = await current.run([case.block], document_sha256=case.document_sha256, document=case.document)
        live_calls += after.calls
        failure = None
        if not before.available:
            failure = "frozen_unavailable:" + ",".join(before.unavailable_reasons)
        elif not after.available:
            failure = "live_unavailable:" + ",".join(after.unavailable_reasons)
        if failure is not None:
            results.append(CaseDrift(case.case_id, case.block.block_id, False, 0, 0, 0, failure=failure))
            continue
        matching, only_frozen, only_live = compare_values(before.report.accepted, after.report.accepted)
        results.append(
            CaseDrift(
                case.case_id,
                case.block.block_id,
                True,
                len(before.report.accepted),
                len(after.report.accepted),
                matching,
                tuple(only_frozen),
                tuple(only_live),
            )
        )
    return summarize(
        results,
        threshold=threshold,
        model_id=current.model_id,
        prompt_version=contract.PROMPT_VERSION,
        live_calls=live_calls,
    )

