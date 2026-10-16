"""Wykaz aktów POG opublikowanych w Rejestrze Urbanistycznym i plan synchronizacji (ADR-026).

Czysta logika (bez HTTP i bazy): odczyt jednej strony odpowiedzi ``POST /api/public/published/query``, kompletność
listy, rozpoznanie statusu aktu z etykiety słownika RU oraz plan synchronizacji — które akty pobrać, które pominąć
jako niezmienione i które dezaktywować, bo przestały być bieżącą wersją w RU.

Zasady bezpieczeństwa (dezaktywacja):

* lista niekompletna (strona brakująca, liczba wierszy ≠ zadeklarowanej) **nie służy do dezaktywacji** — brak aktu
  na niepełnej liście nie jest dowodem jego wycofania;
* dezaktywacja masowa (> ``max_withdraw_fraction`` aktywnych aktów z tokenem RU) wymaga jawnej zgody operatora;
* akt bez tokenu RU (zaimportowany ścieżką WFS/lokalną) nigdy nie jest dezaktywowany przez synchronizację — nie ma
  podstawy porównania z listą;
* dezaktywacja jest planowana po imporcie, żeby nowa wersja aktu (nowy ``publishedActId``) zastąpiła starą zamiast
  ją wycofać.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from app.shared.planning_status import normalize_official_legal_status

PLAN_TYPE_POG: Final[str] = "MUNICIPAL_GENERAL_PLAN"
_TERC = re.compile(r"^\d{6,7}$")


class RuCatalogError(ValueError):
    """Odpowiedź wykazu RU nie spełnia oczekiwanego kontraktu."""


@dataclass(frozen=True)
class RuPublishedAct:
    """Wiersz wykazu RU: jedna opublikowana wersja aktu POG (``publishedActId`` zmienia się z każdą wersją)."""

    published_act_id: int
    terc: str
    plan_type: str
    title: str | None
    voivodeship: str | None
    district: str | None
    commune: str | None
    status_text: str | None
    legal_status: str
    publish_date: date | None
    valid_from: date | None
    valid_to: date | None

    @property
    def teryt6(self) -> str:
        return self.terc[:6]


@dataclass(frozen=True)
class RuListing:
    """Wynik pobrania wykazu: wiersze, deklarowana liczność i kompletność."""

    acts: tuple[RuPublishedAct, ...]
    declared_total: int | None
    pages_expected: int | None
    pages_fetched: int
    complete: bool
    incomplete_reason: str | None = None


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise RuCatalogError(f"Niepoprawna data w wykazie RU: {value!r}.") from exc


def _text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:limit] if text else None


def parse_listing_row(row: Mapping[str, Any]) -> RuPublishedAct:
    try:
        published_act_id = int(row["publishedActId"])
        terc = str(row["tercCode"]).strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise RuCatalogError(f"Wiersz wykazu RU bez publishedActId/tercCode: {sorted(row)}.") from exc
    if published_act_id <= 0 or not _TERC.match(terc):
        raise RuCatalogError(f"Niepoprawny identyfikator albo TERC w wykazie RU: {published_act_id}/{terc!r}.")
    plan_type = str(row.get("planType") or "")
    if plan_type != PLAN_TYPE_POG:
        raise RuCatalogError(f"Wykaz zawiera akt innego rodzaju niż POG: {plan_type!r}.")
    status_text = _text(row.get("spatialPlanningActStatus"), 80)
    return RuPublishedAct(
        published_act_id=published_act_id,
        terc=terc,
        plan_type=plan_type,
        title=_text(row.get("title"), 300),
        voivodeship=_text(row.get("voivodeship"), 40),
        district=_text(row.get("district"), 80),
        commune=_text(row.get("commune"), 120),
        status_text=status_text,
        # Etykieta słownika RU jest urzędowym oznaczeniem statusu (ADR-002); nierozpoznana daje ``unknown``.
        legal_status=normalize_official_legal_status(status_text),
        publish_date=_date(row.get("publishDate")),
        valid_from=_date(row.get("validFrom")),
        valid_to=_date(row.get("validTo")),
    )


@dataclass(frozen=True)
class ListingPage:
    acts: tuple[RuPublishedAct, ...]
    total_elements: int
    total_pages: int


def parse_listing_page(payload: Mapping[str, Any]) -> ListingPage:
    rows = payload.get("publishedActs")
    info = payload.get("pageInfo")
    if not isinstance(rows, list) or not isinstance(info, Mapping):
        raise RuCatalogError("Odpowiedź wykazu RU nie ma publishedActs/pageInfo.")
    try:
        total_elements = int(info["totalElements"])
        total_pages = int(info["totalPages"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuCatalogError("pageInfo wykazu RU nie ma totalElements/totalPages.") from exc
    return ListingPage(tuple(parse_listing_row(row) for row in rows), total_elements, total_pages)


def assemble_listing(pages: Sequence[ListingPage]) -> RuListing:
    """Składa strony w wykaz i ocenia jego kompletność (liczba wierszy, brak duplikatów identyfikatorów)."""
    if not pages:
        return RuListing((), None, None, 0, False, "no_pages")
    acts = tuple(act for page in pages for act in page.acts)
    declared = pages[0].total_elements
    expected = pages[0].total_pages
    ids = [act.published_act_id for act in acts]
    reason: str | None = None
    if len(set(ids)) != len(ids):
        reason = "duplicate_published_act_id"
    elif len(pages) != expected:
        reason = "pages_missing"
    elif len(acts) != declared:
        reason = "row_count_mismatch"
    return RuListing(acts, declared, expected, len(pages), reason is None, reason)


@dataclass(frozen=True)
class RegistryToken:
    """Aktywny wpis rejestru widziany przez planer: akt, jego TERYT i token RU (``None`` — import spoza RU)."""

    act_id: int
    act_identifier: str
    teryt: str | None
    ru_published_act_id: int | None
    entry_id: int


@dataclass(frozen=True)
class SyncPlan:
    to_import: tuple[RuPublishedAct, ...]
    unchanged: tuple[RuPublishedAct, ...]
    skipped_by_filter: int = 0


def plan_sync(
    listing: RuListing,
    registry: Iterable[RegistryToken],
    *,
    full: bool = False,
    terc_filter: Iterable[str] | None = None,
    limit: int | None = None,
) -> SyncPlan:
    """Plan pobierania: akt jest pobierany, gdy jego ``publishedActId`` nie jest tokenem żadnego aktywnego wpisu rejestru.

    Tryb pełny (``full``) pobiera wszystkie akty z filtra bez względu na tokeny.
    """
    entries = list(registry)
    selected_terc = {value[:6] for value in terc_filter} if terc_filter else None
    active_tokens = {entry.ru_published_act_id for entry in entries if entry.ru_published_act_id is not None}
    to_import: list[RuPublishedAct] = []
    unchanged: list[RuPublishedAct] = []
    skipped = 0
    for act in sorted(listing.acts, key=lambda item: (item.terc, item.published_act_id)):
        if selected_terc is not None and act.teryt6 not in selected_terc:
            skipped += 1
            continue
        if full or act.published_act_id not in active_tokens:
            to_import.append(act)
        else:
            unchanged.append(act)
    if limit is not None:
        to_import = to_import[: max(limit, 0)]
    return SyncPlan(tuple(to_import), tuple(unchanged), skipped)


@dataclass(frozen=True)
class WithdrawalPlan:
    to_withdraw: tuple[RegistryToken, ...]
    blocked: str | None = None


def plan_withdrawals(
    listing: RuListing,
    registry: Iterable[RegistryToken],
    *,
    terc_filter: Iterable[str] | None = None,
    max_withdraw_fraction: float = 0.1,
    allow_mass_withdrawal: bool = False,
) -> WithdrawalPlan:
    """Wpisy z tokenem RU, którego nie ma już na kompletnej liście bieżących wersji — kandydaci do dezaktywacji.

    Wołać PO imporcie: nowa wersja aktu zastępuje starą (wpis ze starym tokenem jest już zamknięty), więc zostają
    wyłącznie akty, które RU rzeczywiście wycofał albo zastąpił wersją niebędącą już bieżącą. Wpisy bez tokenu RU
    (import WFS/lokalny) nie są ruszane. Lista niekompletna albo zbyt wiele wycofań naraz blokuje dezaktywację.
    """
    entries = list(registry)
    selected_terc = {value[:6] for value in terc_filter} if terc_filter else None
    current_ids = {act.published_act_id for act in listing.acts}
    candidates = tuple(
        entry
        for entry in entries
        if entry.ru_published_act_id is not None
        and entry.ru_published_act_id not in current_ids
        and (selected_terc is None or (entry.teryt or "")[:6] in selected_terc)
    )
    if not candidates:
        return WithdrawalPlan(())
    if not listing.complete:
        return WithdrawalPlan((), f"listing_incomplete:{listing.incomplete_reason}")
    tokened = sum(1 for entry in entries if entry.ru_published_act_id is not None)
    if not allow_mass_withdrawal and tokened and len(candidates) > max(5, int(tokened * max_withdraw_fraction)):
        return WithdrawalPlan((), f"mass_withdrawal:{len(candidates)}_of_{tokened}")
    return WithdrawalPlan(candidates)


def parse_content_disposition_filename(value: str | None) -> str | None:
    """Nazwa pliku z nagłówka ``Content-Disposition`` (``filename="…"`` albo ``filename*=UTF-8''…``)."""
    if not value:
        return None
    match = re.search(r"filename\*=(?:UTF-8|utf-8)''([^;]+)", value) or re.search(r'filename="?([^";]+)"?', value)
    if match is None:
        return None
    name = match.group(1).strip().rsplit("/", 1)[-1]
    return name or None
