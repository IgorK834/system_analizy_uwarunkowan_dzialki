"""Wykaz aktów POG z Rejestru Urbanistycznego i plan synchronizacji (ADR-026) na zamrożonych odpowiedziach RU."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from app.modules.imports.domain.ru_catalog import (
    ListingPage,
    RegistryToken,
    RuCatalogError,
    RuListing,
    assemble_listing,
    parse_content_disposition_filename,
    parse_listing_page,
    parse_listing_row,
    plan_sync,
    plan_withdrawals,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ru_national"


def _pages() -> list[ListingPage]:
    return [
        parse_listing_page(json.loads((FIXTURES / f"listing_current_page_{number}.json").read_text(encoding="utf-8")))
        for number in range(1, 6)
    ]


@pytest.fixture(scope="module")
def listing() -> RuListing:
    return assemble_listing(_pages())


def test_frozen_listing_is_complete_and_has_the_published_statuses(listing: RuListing) -> None:
    # Zamrożone 2026-10-13: 465 bieżących wersji w 462 gminach; trzy gminy mają dwa akty.
    assert listing.complete and listing.declared_total == len(listing.acts) == 465
    assert len({act.terc for act in listing.acts}) == 462
    assert Counter(act.legal_status for act in listing.acts) == {"binding": 327, "in_progress": 113, "project": 25}
    assert Counter(act.status_text for act in listing.acts) == {
        "prawnie wiążący lub realizowany": 327,
        "w opracowaniu": 113,
        "w trakcie przyjmowania": 25,
    }
    assert all(act.plan_type == "MUNICIPAL_GENERAL_PLAN" for act in listing.acts)
    bielsko = next(act for act in listing.acts if act.terc == "246101")
    assert (bielsko.commune, bielsko.legal_status, bielsko.teryt6) == ("Bielsko-Biała", "binding", "246101")


def test_listing_is_incomplete_when_a_page_or_a_row_is_missing_or_ids_repeat() -> None:
    pages = _pages()
    assert assemble_listing(pages[:-1]).incomplete_reason == "pages_missing"
    truncated = ListingPage(pages[0].acts[:-1], pages[0].total_elements, pages[0].total_pages)
    short = assemble_listing([truncated, *pages[1:]])
    assert (short.complete, short.incomplete_reason) == (False, "row_count_mismatch")
    duplicated = assemble_listing([pages[0], pages[0], *pages[2:]])
    assert duplicated.incomplete_reason == "duplicate_published_act_id"
    assert assemble_listing([]).incomplete_reason == "no_pages"


@pytest.mark.parametrize(
    "row",
    [
        {"tercCode": "246101", "planType": "MUNICIPAL_GENERAL_PLAN"},
        {"publishedActId": 5, "tercCode": "24", "planType": "MUNICIPAL_GENERAL_PLAN"},
        {"publishedActId": 5, "tercCode": "246101", "planType": "LOCAL_PLAN"},
        {"publishedActId": -1, "tercCode": "246101", "planType": "MUNICIPAL_GENERAL_PLAN"},
        {"publishedActId": 5, "tercCode": "246101", "planType": "MUNICIPAL_GENERAL_PLAN", "validFrom": "wczoraj"},
    ],
)
def test_malformed_listing_rows_are_rejected(row: dict[str, object]) -> None:
    with pytest.raises(RuCatalogError):
        parse_listing_row(row)


def test_page_without_page_info_is_rejected() -> None:
    with pytest.raises(RuCatalogError):
        parse_listing_page({"publishedActs": []})


def test_unknown_status_label_is_unknown_never_binding() -> None:
    row = {
        "publishedActId": 7, "tercCode": "246101", "planType": "MUNICIPAL_GENERAL_PLAN",
        "spatialPlanningActStatus": "uchwalony", "validFrom": None, "validTo": None,
    }
    assert parse_listing_row(row).legal_status == "unknown"


def test_content_disposition_filename_is_extracted() -> None:
    header = (
        'attachment; filename="PL.ZIPPZP.10169_241203-POG_1POG_20260102T120826.gml"; '
        "filename*=UTF-8''PL.ZIPPZP.10169_241203-POG_1POG_20260102T120826.gml"
    )
    assert parse_content_disposition_filename(header) == "PL.ZIPPZP.10169_241203-POG_1POG_20260102T120826.gml"
    assert parse_content_disposition_filename('attachment; filename="../../etc/passwd"') == "passwd"
    assert parse_content_disposition_filename(None) is None


def _token(act: int, teryt: str, ru_id: int | None) -> RegistryToken:
    return RegistryToken(act, f"ns/{teryt}-POG/1POG", teryt, ru_id, entry_id=act)


def test_incremental_plan_downloads_only_acts_without_a_registry_token(listing: RuListing) -> None:
    by_id = {act.published_act_id: act for act in listing.acts}
    first, second = sorted(by_id)[:2]
    registry = [_token(1, by_id[first].terc, first), _token(2, by_id[second].terc, second)]

    plan = plan_sync(listing, registry)

    assert len(plan.to_import) == 463 and len(plan.unchanged) == 2
    assert {act.published_act_id for act in plan.unchanged} == {first, second}


def test_full_plan_ignores_tokens_and_filters_apply_before_the_limit(listing: RuListing) -> None:
    tokens = [_token(index, act.terc, act.published_act_id) for index, act in enumerate(listing.acts, 1)]
    assert plan_sync(listing, tokens).to_import == ()
    full = plan_sync(listing, tokens, full=True, terc_filter=["246101", "2264011"], limit=1)
    assert [act.terc for act in full.to_import] == ["226401"]  # TERC posortowane, limit po filtrze
    assert full.skipped_by_filter == 463  # akt Sopotu i akt Bielska-Białej przechodzą filtr


def test_withdrawal_only_for_tokened_entries_missing_from_a_complete_listing(listing: RuListing) -> None:
    current = listing.acts[0]
    registry = [
        _token(1, current.terc, current.published_act_id),  # nadal bieżący
        _token(2, "999999", 9_999_001),  # token RU, którego nie ma na liście → kandydat
        _token(3, "888888", None),  # import spoza RU — nigdy nie jest dezaktywowany
    ]
    plan = plan_withdrawals(listing, registry)
    assert [entry.act_id for entry in plan.to_withdraw] == [2] and plan.blocked is None


def test_incomplete_listing_never_withdraws_anything(listing: RuListing) -> None:
    broken = RuListing(listing.acts[:10], 465, 5, 1, False, "pages_missing")
    plan = plan_withdrawals(broken, [_token(2, "999999", 9_999_001)])
    assert plan.to_withdraw == () and plan.blocked == "listing_incomplete:pages_missing"


def test_mass_withdrawal_needs_explicit_consent(listing: RuListing) -> None:
    registry = [_token(index, f"{index:06d}", 9_000_000 + index) for index in range(1, 41)]
    blocked = plan_withdrawals(listing, registry)
    assert blocked.to_withdraw == () and blocked.blocked == "mass_withdrawal:40_of_40"
    allowed = plan_withdrawals(listing, registry, allow_mass_withdrawal=True)
    assert len(allowed.to_withdraw) == 40
    small = plan_withdrawals(listing, registry[:5])  # do 5 wycofań naraz bez zgody
    assert len(small.to_withdraw) == 5


def test_withdrawal_respects_terc_filter(listing: RuListing) -> None:
    registry = [_token(1, "111111", 9_000_001), _token(2, "222222", 9_000_002)]
    plan = plan_withdrawals(listing, registry, terc_filter=["222222"])
    assert [entry.act_id for entry in plan.to_withdraw] == [2]
