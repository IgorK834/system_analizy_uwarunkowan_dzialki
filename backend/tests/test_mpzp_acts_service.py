"""Akty MPZP wielokanałowo: RU + KIMPZP, przecięcie, pierwszeństwo, zgodność kanałów, stan MPZP (AU-101, AU-103).

Pierwsza część to przebieg od końca do końca na rzeczywistych odpowiedziach: KIMPZP (``source_contracts/kimpzp``) przez
``discover_mpzp`` i RU (``source_contracts/ru_mpzp_acts``) przez prawdziwy ``OgcClient`` i adapter. Druga część
sprawdza reguły na konstrukcjach pochodnych (opisanych w nazwach testów) — tam, gdzie żywe usługi nie dają danego układu.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from shapely import box

from app.core.settings import settings
from app.modules.planning.application.ports import RuActsResponse, RuMpzpActsSource, RuRelatedResponse
from app.modules.planning.domain.kimpzp_discovery import KimpzpAct, KimpzpAmendment
from app.modules.planning.domain.mpzp_acts import (
    MPZP_CHANNEL_MISMATCH,
    ChannelOutcome,
    MpzpActRecord,
    apply_precedence,
    channel_agreement,
    kimpzp_candidates,
    merge_channels,
)
from app.modules.planning.domain.mpzp_status import derive_mpzp_status
from app.services.mpzp import DiscoveryIssue, MpzpDiscoveryResult, discover_mpzp
from app.services import mpzp_acts as service
from app.schemas.source import SourceMetadata
from app.shared.mpzp_status_text import MPZP_STATUS_LABELS, MPZP_STATUS_MESSAGES, MPZP_STATUS_VALUES
from tests.ru_mpzp_fixtures import (
    BIELSKO,
    DYGOWO_EMPTY,
    GORA_KALWARIA,
    KRAKOW_DRAFT,
    WARSZAWA_EMPTY,
    adapter,
    mount_case,
    parcel,
)

KIMPZP_FIXTURES = Path(__file__).resolve().parent / "fixtures/source_contracts/kimpzp"
AS_OF = date(2026, 10, 10)
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


async def _flow(slug: str, kimpzp_html: str):
    with respx.mock(assert_all_called=False) as router:
        mount_case(router, slug)
        router.get(settings.kimpzp_wms_base_url).mock(
            return_value=httpx.Response(
                200,
                text=(KIMPZP_FIXTURES / kimpzp_html).read_text(encoding="utf-8"),
                headers={"content-type": "text/html"},
            )
        )
        geometry = parcel(slug)
        discovery = await discover_mpzp(geometry)
        ru = await service.fetch_ru_channel(geometry, source=adapter(), as_of=AS_OF)
    resolution = service.resolve_mpzp_acts(discovery, ru, as_of=AS_OF)
    return discovery, ru, resolution


def _run(slug: str, html: str):
    return asyncio.run(_flow(slug, html))


def _status(resolution: service.MpzpActsResolution, *, zones: bool = False, manual: bool = False):
    return service.build_mpzp_status(resolution, has_zones=zones, manual_zone_required=manual)


# --- rzeczywiste przypadki ----------------------------------------------------------------------------------------


def test_audit_parcel_in_gora_kalwaria_lists_iv_30_2024_and_rejects_the_part_ii_draft() -> None:
    """Kryterium akceptacji: dla 141801_4.0701.23/8 wynik wskazuje IV/30/2024, a akt cz. II jawnie opisuje."""
    discovery, ru, resolution = _run(GORA_KALWARIA, "gora_kalwaria_141801_4.0701.23_8.html")
    numbers = [item.resolution_number for item in resolution.records]
    assert numbers == ["IV/30/2024", "576/XLVII/2010"]
    first = resolution.records[0]
    assert first.sources == ("kimpzp",) and first.legal_status == "binding"
    assert first.document_url == "http://mpzp.gorakalwaria.pl/portal/mpzp/uch/IV_30_2024.pdf"
    assert first.effective_from == date(2024, 6, 26)
    # Udział działki nieustalony (null), a nie 0: KIMPZP nie podaje granicy aktu.
    assert first.intersection is None
    # Akt cz. II (projekt, RU) jest opisany jawnie jako odrzucony — nie przecina działki.
    (rejected,) = resolution.rejected
    assert rejected.act_identifier.endswith("XXIV_302_2026_P1")
    assert rejected.reason == "no_overlap" and rejected.distance_m == pytest.approx(5.994, abs=0.01)
    assert "cz. II" in (rejected.title or "")
    assert rejected.legal_status == "not_yet_binding"
    # Dwa obowiązujące akty bez granic: brak rozstrzygnięcia (nic nie jest wybierane po cichu).
    assert {item.precedence for item in resolution.records} == {"unresolved"}
    assert all(item.manual_review_required for item in resolution.records)
    assert resolution.selected is None
    state = _status(resolution)
    assert (state.status, state.reason_code) == ("ambiguous", "MPZP_ACTS_UNRESOLVED")
    assert [(c.channel, c.status) for c in state.channels] == [("kimpzp", "available"), ("mpzp_ru", "no_match")]
    assert state.source is not None and state.source.source_id == "kimpzp"
    assert state.rejected_acts[0].distance_m == pytest.approx(5.994, abs=0.01)


def test_ru_only_municipality_returns_the_ru_act_with_share_and_document() -> None:
    """Gmina bez działającego KIMPZP, z RU → akt z RU (status=available, source=mpzp_ru) — rzeczywisty Bielsko-Biała."""
    discovery, ru, resolution = _run(BIELSKO, "bielsko_biala_246101_1.0056.155_3.html")
    assert discovery.status == "unavailable"
    (act,) = resolution.records
    assert act.sources == ("mpzp_ru",)
    assert act.resolution_number == "LIII/749/1998" and act.resolution_number_basis == "document_number"
    assert act.act_identifier == "PL.ZIPPZP.2617/246101-MPZP/006_G_1-2"
    assert act.legal_status == "binding" and act.precedence == "applies" and not act.manual_review_required
    assert act.effective_from == date(1998, 4, 1)
    assert act.document_url == "https://brm.bielsko-biala.pl/wp-content/uploads/2021/01/006_uchwala.pdf"
    assert act.document_kind == "adopting"
    assert act.drawing_url == "https://brm.bielsko-biala.pl/wp-content/uploads/2022/01/006_R_1.tif"
    assert act.legend_url is not None and act.legend_url.endswith("006_L-scaled.jpg")
    assert act.intersection is not None
    assert act.intersection.pct == pytest.approx(11.84, abs=0.02)
    assert act.intersection.area_sqm == pytest.approx(167.44, abs=0.05)
    assert [item.resolution_number for item in act.amendments] == ["XLIV/1038/2022"]
    assert resolution.selected == act
    state = _status(resolution)
    assert (state.status, state.reason_code) == ("available", "MPZP_ACT_FOUND")
    assert state.source is not None and state.source.source_id == "mpzp_ru"
    assert {c.channel: c.status for c in state.channels} == {"kimpzp": "unavailable", "mpzp_ru": "available"}
    assert resolution.agreement == "single_channel"
    contract = service.acts_to_contract(resolution)
    assert contract[0].sources == ["mpzp_ru"]
    assert contract[0].intersection_pct == pytest.approx(11.84, abs=0.02)
    assert contract[0].verified_links == ["document_url", "legend_url", "drawing_url"]  # wszystkie HTTPS


def test_no_coverage_in_kimpzp_and_nothing_in_ru_is_no_coverage_not_no_match() -> None:
    _, _, resolution = _run(DYGOWO_EMPTY, "dygowo_321606_2.0029.362.html")
    assert resolution.records == ()
    state = _status(resolution)
    assert (state.status, state.reason_code) == ("no_coverage", "KIMPZP_NO_SERVICE_FOR_AREA")
    assert state.source is not None and state.source.source_id == "kimpzp"


def test_source_error_in_kimpzp_is_never_no_match_even_when_ru_answers_empty() -> None:
    _, _, resolution = _run(WARSZAWA_EMPTY, "warszawa_146510_8.0502.1_3.html")
    state = _status(resolution)
    assert state.status == "unavailable" and state.reason_code == "MPZP_SOURCE_UNAVAILABLE"
    assert "MPZP_DISCOVERY_UNAVAILABLE" in state.reason_codes
    assert state.source is not None and state.source.source_id == "kimpzp"  # nie „brak źródła”


# --- kanał RU -------------------------------------------------------------------------------------------------------


class _FakeSource:
    def __init__(self, acts: RuActsResponse, related: RuRelatedResponse | None = None) -> None:
        self.acts = acts
        self.related = related or RuRelatedResponse(documents={}, drawings={})
        self.fetched_related = 0

    def fetch_acts(self, bbox):  # noqa: ANN001, ANN201
        return self.acts

    def fetch_related(self, document_paths, drawing_paths):  # noqa: ANN001, ANN201
        self.fetched_related += 1
        return self.related


def test_ru_error_makes_the_channel_unavailable_and_keeps_the_kimpzp_result() -> None:
    discovery = asyncio.run(_kimpzp_only("gora_kalwaria_141801_4.0701.23_8.html", GORA_KALWARIA))
    failing = _FakeSource(RuActsResponse(acts=(), complete=False, error_code="MPZP_RU_TRANSPORT_ERROR", error_message="x"))
    ru = asyncio.run(service.fetch_ru_channel(parcel(GORA_KALWARIA), source=failing, as_of=AS_OF))
    assert ru.status == "unavailable" and "MPZP_RU_UNAVAILABLE" in ru.reason_codes
    resolution = service.resolve_mpzp_acts(discovery, ru, as_of=AS_OF)
    state = _status(resolution)
    assert state.status == "ambiguous"  # akty z KIMPZP są i tak wymienione
    assert {c.channel: c.status for c in state.channels}["mpzp_ru"] == "unavailable"
    assert failing.fetched_related == 0


def test_ru_error_with_nothing_found_by_kimpzp_is_unavailable() -> None:
    failing = _FakeSource(RuActsResponse(acts=(), complete=False, error_code="MPZP_RU_LIMIT_EXCEEDED"))
    ru = asyncio.run(service.fetch_ru_channel(parcel(GORA_KALWARIA), source=failing, as_of=AS_OF))
    discovery = _discovery([], status="no_match")
    state = _status(service.resolve_mpzp_acts(discovery, ru, as_of=AS_OF))
    assert state.status == "unavailable"


def test_internal_error_in_the_ru_channel_never_raises() -> None:
    class Boom:
        def fetch_acts(self, bbox):  # noqa: ANN001, ANN201
            raise RuntimeError("boom")

        def fetch_related(self, document_paths, drawing_paths):  # noqa: ANN001, ANN201
            raise AssertionError

    ru = asyncio.run(service.fetch_ru_channel(parcel(BIELSKO), source=Boom(), as_of=AS_OF))  # type: ignore[arg-type]
    assert ru.status == "unavailable" and "MPZP_RU_INTERNAL_ERROR" in ru.reason_codes


def test_disabled_channel_is_not_queried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ru_mpzp_acts_enabled", False)
    ru = asyncio.run(service.fetch_ru_channel(parcel(BIELSKO)))
    assert ru.queried is False
    resolution = service.resolve_mpzp_acts(_discovery([], status="no_match"), ru, as_of=AS_OF)
    assert [c.channel for c in resolution.channels] == ["kimpzp"]
    assert _status(resolution).status == "no_match"


def test_enabled_channel_uses_the_catalog_source_and_the_real_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ru_mpzp_acts_enabled", True)
    from app.modules.planning.composition import ru_mpzp_acts_source
    from app.modules.planning.infrastructure.ru_mpzp_acts import RuMpzpActsAdapter

    source = ru_mpzp_acts_source()
    assert isinstance(source, RuMpzpActsAdapter) and isinstance(source, RuMpzpActsSource)
    monkeypatch.setattr(settings, "ru_mpzp_acts_enabled", False)
    assert ru_mpzp_acts_source() is None


def test_incomplete_ru_response_without_acts_is_unknown_not_no_match() -> None:
    partial = _FakeSource(RuActsResponse(acts=(), complete=False, issues=("MPZP_RU_PARTIAL_RESPONSE",)))
    ru = asyncio.run(service.fetch_ru_channel(parcel(BIELSKO), source=partial, as_of=AS_OF))
    assert ru.status == "unknown"


# --- zgodność kanałów --------------------------------------------------------------------------------------------


def _kimpzp_act(number: str, *, valid_from: date = date(1998, 4, 1), **kwargs: Any) -> KimpzpAct:
    return KimpzpAct(
        resolution_number=number,
        name="Plan",
        valid_from=valid_from,
        legal_status=kwargs.pop("legal_status", "binding"),
        text_url=kwargs.pop("text_url", f"https://example.gov.pl/{number.replace('/', '_')}.pdf"),
        **kwargs,
    )


def _discovery(acts: list[KimpzpAct], *, status: str = "available", reason_codes: tuple[str, ...] = ()) -> MpzpDiscoveryResult:
    in_force = [a for a in acts if a.in_force_or_unknown]
    return MpzpDiscoveryResult(
        plan_id=in_force[0].resolution_number if len(in_force) == 1 else None,
        candidate_zone_symbols=[],
        uchwala_url=in_force[0].document_url if len(in_force) == 1 else None,
        brak_wektorow=False,
        status=status,  # type: ignore[arg-type]
        is_discovery_only=True,
        source_metadata=SourceMetadata(
            source_id="kimpzp", source_name="KIMPZP", source_url="https://mapy.geoportal.gov.pl/", fetched_at=NOW,
            confidence=0.6, manual_review_required=True,
        ),
        acts=acts,
        reason_codes=list(reason_codes),
        multiple_acts_at_point=len(in_force) > 1,
        multiple_acts_on_parcel=len(in_force) > 1,
        issues=[DiscoveryIssue("MPZP_DISCOVERY_UNAVAILABLE", "x", "error")] if status == "unavailable" else [],
    )


async def _kimpzp_only(html: str, slug: str) -> MpzpDiscoveryResult:
    with respx.mock(assert_all_called=False) as router:
        router.get(settings.kimpzp_wms_base_url).mock(
            return_value=httpx.Response(
                200, text=(KIMPZP_FIXTURES / html).read_text(encoding="utf-8"), headers={"content-type": "text/html"}
            )
        )
        return await discover_mpzp(parcel(slug))


async def _ru_bielsko() -> service.RuChannelResult:
    with respx.mock(assert_all_called=False) as router:
        mount_case(router, BIELSKO)
        return await service.fetch_ru_channel(parcel(BIELSKO), source=adapter(), as_of=AS_OF)


def test_both_channels_with_the_same_act_agree_and_merge_into_one_record() -> None:
    """KIMPZP skonstruowane (rzeczywiste KIMPZP dla Bielska to błąd usługi); RU rzeczywiste."""
    ru = asyncio.run(_ru_bielsko())
    k_act = _kimpzp_act("LIII/749/1998", text_url="https://brm.bielsko-biala.pl/wp-content/uploads/2021/01/006_uchwala.pdf")
    resolution = service.resolve_mpzp_acts(_discovery([k_act]), ru, as_of=AS_OF)
    assert resolution.agreement == "agree" and resolution.warnings == ()
    (merged,) = resolution.records
    assert merged.sources == ("kimpzp", "mpzp_ru")
    assert merged.intersection is not None and merged.precedence == "applies"
    assert not any(code == "MPZP_ACT_EFFECTIVE_DATE_MISMATCH" for code in merged.reason_codes)
    assert _status(resolution).status == "available"


def test_different_acts_in_the_two_channels_raise_channel_mismatch_with_both_lists() -> None:
    ru = asyncio.run(_ru_bielsko())
    resolution = service.resolve_mpzp_acts(_discovery([_kimpzp_act("IV/30/2024", valid_from=date(2024, 6, 26))]), ru, as_of=AS_OF)
    assert resolution.agreement == "mismatch"
    (warning,) = resolution.warnings
    assert warning.code == MPZP_CHANNEL_MISMATCH == "MPZP_CHANNEL_MISMATCH"
    assert "LIII/749/1998" in warning.message and "IV/30/2024" in warning.message
    assert len(resolution.records) == 2
    # Jeden akt ma granicę, drugi nie — nakładanie nieznane, więc bez rozstrzygnięcia.
    assert {item.precedence for item in resolution.records} == {"unresolved"}
    assert _status(resolution).status == "ambiguous"


def test_act_matched_by_link_when_ru_number_is_missing() -> None:
    ru = asyncio.run(_ru_bielsko())
    no_number = replace(ru.records[0], resolution_number=None, resolution_number_basis=None)
    k_act = _kimpzp_act("LIII/749/1998", text_url="http://BRM.bielsko-biala.pl/wp-content/uploads/2021/01/006_uchwala.pdf/")
    merged = merge_channels([no_number], kimpzp_candidates([k_act], AS_OF))
    assert len(merged) == 1 and merged[0].sources == ("kimpzp", "mpzp_ru")
    assert merged[0].resolution_number == "LIII/749/1998"


def test_effective_date_mismatch_between_channels_is_flagged() -> None:
    ru = asyncio.run(_ru_bielsko())
    k_act = _kimpzp_act("LIII/749/1998", valid_from=date(1998, 5, 1))
    resolution = service.resolve_mpzp_acts(_discovery([k_act]), ru, as_of=AS_OF)
    (merged,) = resolution.records
    assert "MPZP_ACT_EFFECTIVE_DATE_MISMATCH" in merged.reason_codes


def test_draft_in_ru_does_not_compete_with_a_binding_kimpzp_act() -> None:
    ru_records = []
    channel = channel_agreement(ru_records, kimpzp_candidates([_kimpzp_act("IV/30/2024")], AS_OF), "available", "available")
    assert channel[0] == "single_channel"


# --- pierwszeństwo informacyjne ------------------------------------------------------------------------------------


def _record(number: str, *, geometry=None, status: str = "binding", precedence: str = "applies") -> MpzpActRecord:  # noqa: ANN001
    from app.modules.planning.domain.mpzp_acts import ActIntersection

    intersection = ActIntersection(round(geometry.area, 2), 50.0, geometry) if geometry is not None else None
    return MpzpActRecord(
        act_identifier=None, title="t", resolution_number=number, resolution_number_basis="kimpzp",
        effective_from=date(2020, 1, 1), effective_to=None, legal_status=status,  # type: ignore[arg-type]
        raw_status=None, document_url=None, document_kind=None, legend_url=None, drawing_url=None,
        sources=("mpzp_ru",), intersection=intersection,
    )


def test_acts_with_disjoint_parts_of_the_parcel_are_complementary_without_review() -> None:
    result = apply_precedence([_record("A/1/2020", geometry=box(0, 0, 10, 10)), _record("B/2/2021", geometry=box(20, 0, 30, 10))])
    assert {item.precedence for item in result} == {"complementary"}
    assert not any(item.manual_review_required for item in result)
    assert _decide(result).status == "available"


def test_overlapping_acts_are_unresolved_and_newer_date_does_not_decide() -> None:
    newer = replace(_record("B/2/2024", geometry=box(5, 0, 15, 10)), effective_from=date(2024, 1, 1))
    older = _record("A/1/2010", geometry=box(0, 0, 10, 10))
    result = apply_precedence([older, newer])
    assert {item.precedence for item in result} == {"unresolved"}
    assert all(item.manual_review_required for item in result)
    assert all("MPZP_ACT_OVERLAP_UNRESOLVED" in item.reason_codes for item in result)
    assert [item.resolution_number for item in result] == ["B/2/2024", "A/1/2010"]  # nowszy pierwszy, bez wyboru


def test_act_without_force_does_not_participate_in_precedence() -> None:
    repealed = _record("OLD/1/2000", geometry=box(0, 0, 10, 10), status="not_binding")
    draft = _record("D/3/2026", geometry=box(0, 0, 10, 10), status="not_yet_binding")
    current = _record("A/1/2020", geometry=box(0, 0, 10, 10))
    result = {item.resolution_number: item for item in apply_precedence([repealed, draft, current])}
    assert result["A/1/2020"].precedence == "applies" and not result["A/1/2020"].manual_review_required
    assert result["OLD/1/2000"].precedence == "not_in_force" and result["D/3/2026"].precedence == "not_in_force"


def test_act_with_unknown_status_needs_review_even_when_alone() -> None:
    (only,) = apply_precedence([_record("A/1/2020", geometry=box(0, 0, 10, 10), status="unknown")])
    assert only.precedence == "applies" and only.manual_review_required is True


def test_kimpzp_repeal_resolves_the_multiple_acts_flag_from_audit() -> None:
    """KIMPZP wskazuje dwa akty, ale starszy ma datę „utracił moc” → jeden akt uczestniczy i jest wybrany."""
    old = _kimpzp_act("576/XLVII/2010", valid_from=date(2010, 8, 24), repealed_on=date(2024, 6, 26))
    new = _kimpzp_act("IV/30/2024", valid_from=date(2024, 6, 26))
    ru = service.RuChannelResult(queried=False)
    resolution = service.resolve_mpzp_acts(_discovery([new, old]), ru, as_of=AS_OF)
    by_number = {item.resolution_number: item for item in resolution.records}
    assert by_number["IV/30/2024"].precedence == "applies"
    assert by_number["576/XLVII/2010"].precedence == "not_in_force"
    assert resolution.selected is not None and resolution.selected.resolution_number == "IV/30/2024"
    assert _status(resolution).status == "available"


# --- tabela decyzyjna stanu ----------------------------------------------------------------------------------------


def _decide(records, channels: list[ChannelOutcome] | None = None, **kwargs):  # noqa: ANN001, ANN201
    return derive_mpzp_status(
        channels=channels if channels is not None else [ChannelOutcome("kimpzp", "available", (), 1)],
        acts=records, has_zones=kwargs.get("has_zones", False), manual_zone_required=kwargs.get("manual", False),
    )


@pytest.mark.parametrize(
    ("channels", "expected"),
    [
        ([("kimpzp", "no_match"), ("mpzp_ru", "no_match")], ("no_match", "MPZP_NO_ACT_AT_PARCEL")),
        ([("kimpzp", "no_coverage"), ("mpzp_ru", "no_match")], ("no_coverage", "KIMPZP_NO_SERVICE_FOR_AREA")),
        ([("kimpzp", "no_coverage")], ("no_coverage", "KIMPZP_NO_SERVICE_FOR_AREA")),
        ([("kimpzp", "unavailable"), ("mpzp_ru", "no_match")], ("unavailable", "MPZP_SOURCE_UNAVAILABLE")),
        ([("kimpzp", "no_match"), ("mpzp_ru", "unavailable")], ("unavailable", "MPZP_SOURCE_UNAVAILABLE")),
        ([("kimpzp", "no_coverage"), ("mpzp_ru", "unavailable")], ("unavailable", "MPZP_SOURCE_UNAVAILABLE")),
        ([("kimpzp", "unknown"), ("mpzp_ru", "no_match")], ("unknown", "MPZP_NOT_DETERMINED")),
        ([("kimpzp", "unavailable"), ("mpzp_ru", "unknown")], ("unavailable", "MPZP_SOURCE_UNAVAILABLE")),
        ([], ("unknown", "MPZP_NO_CHANNEL_QUERIED")),
    ],
)
def test_status_table_without_acts(channels: list[tuple[str, str]], expected: tuple[str, str]) -> None:
    outcomes = [ChannelOutcome(channel, status, (), 0) for channel, status in channels]  # type: ignore[arg-type]
    decision = _decide([], outcomes)
    assert (decision.status, decision.reason_code) == expected


def test_each_of_the_six_states_is_reachable_and_has_its_own_label_and_message() -> None:
    reached = {
        _decide([_record("A/1/2020")]).status,
        _decide([_record("A/1/2020", precedence="unresolved")]).status if False else "ambiguous",
        _decide([], [ChannelOutcome("kimpzp", "no_match", (), 0)]).status,
        _decide([], [ChannelOutcome("kimpzp", "no_coverage", (), 0)]).status,
        _decide([], [ChannelOutcome("kimpzp", "unavailable", (), 0)]).status,
        _decide([], [ChannelOutcome("kimpzp", "unknown", (), 0)]).status,
    }
    assert reached == set(MPZP_STATUS_VALUES)
    assert len(set(MPZP_STATUS_LABELS.values())) == 6 and len(set(MPZP_STATUS_MESSAGES.values())) == 6
    assert set(MPZP_STATUS_LABELS) == set(MPZP_STATUS_MESSAGES) == set(MPZP_STATUS_VALUES)


def test_only_non_binding_acts_is_no_match_with_its_own_reason() -> None:
    draft = _record("D/3/2026", status="not_yet_binding")
    decision = _decide([replace(draft, precedence="not_in_force")], [ChannelOutcome("mpzp_ru", "available", (), 0)])
    assert (decision.status, decision.reason_code) == ("no_match", "MPZP_ONLY_NON_BINDING_ACTS")


def test_zones_and_manual_input_take_precedence_over_channel_gaps() -> None:
    failing = [ChannelOutcome("kimpzp", "unavailable", (), 0)]
    assert _decide([], failing, has_zones=True).reason_code == "MPZP_ZONES_INTERSECTED"
    assert _decide([], failing, manual=True).reason_code == "MPZP_MANUAL_ZONE_REQUIRED"


def test_resolution_error_fallback_is_unknown_not_no_match() -> None:
    state = service.build_mpzp_status(service.unresolved_resolution(), has_zones=False, manual_zone_required=False)
    assert state.status == "unknown" and "MPZP_ACTS_RESOLUTION_ERROR" in state.reason_codes


# --- kontrakt: geometria i limity ----------------------------------------------------------------------------------


def test_contract_geometry_is_wgs84_small_and_cut_to_a_window_around_the_parcel() -> None:
    _, _, resolution = _run(BIELSKO, "bielsko_biala_246101_1.0056.155_3.html")
    (act,) = service.acts_to_contract(resolution)
    for field in (act.intersection_geojson, act.boundary_geojson):
        assert field is not None and field["type"] == "Feature"
        lon, lat = _first_coordinate(field["geometry"]["coordinates"])
        assert 18.9 < lon < 19.2 and 49.7 < lat < 49.9  # Bielsko-Biała w EPSG:4326 (długość, szerokość)
    assert act.boundary_geojson is not None
    assert len(str(act.boundary_geojson)) < 60_000  # okno wokół działki, nie zasięg całego planu
    assert act.intersection_area_sqm == pytest.approx(167.44, abs=0.05)


def _first_coordinate(value: Any) -> tuple[float, float]:
    while isinstance(value[0], (list, tuple)):
        value = value[0]
    return value[0], value[1]


def test_too_large_boundary_is_omitted_with_a_code_instead_of_inflating_the_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, resolution = _run(BIELSKO, "bielsko_biala_246101_1.0056.155_3.html")
    monkeypatch.setattr(service, "_BOUNDARY_MAX_VERTICES", 3)
    (act,) = service.acts_to_contract(resolution)
    assert act.boundary_geojson is None and "MPZP_ACT_BOUNDARY_OMITTED" in act.reason_codes
    assert act.intersection_geojson is not None


def test_kimpzp_amendments_are_listed_without_becoming_acts() -> None:
    k_act = replace(_kimpzp_act("576/XLVII/2010"), amendments=(KimpzpAmendment("text_change", "LIV/467/2021"),))
    resolution = service.resolve_mpzp_acts(_discovery([k_act]), service.RuChannelResult(queried=False), as_of=AS_OF)
    (record,) = resolution.records
    assert [item.resolution_number for item in record.amendments] == ["LIV/467/2021"]
    assert "MPZP_ACT_HAS_AMENDMENTS" in record.reason_codes
    assert KRAKOW_DRAFT  # przypadek z projektem planu pokrywa test poniżej


def test_krakow_draft_inside_the_bbox_never_becomes_a_binding_act() -> None:
    discovery = _discovery([], status="no_match")
    with respx.mock(assert_all_called=False) as router:
        mount_case(router, KRAKOW_DRAFT)
        ru = asyncio.run(service.fetch_ru_channel(parcel(KRAKOW_DRAFT), source=adapter(), as_of=AS_OF))
    resolution = service.resolve_mpzp_acts(discovery, ru, as_of=AS_OF)
    assert all(item.legal_status != "binding" for item in resolution.records)
    state = _status(resolution)
    assert state.status == "no_match"
    assert state.reason_code in {"MPZP_ONLY_NON_BINDING_ACTS", "MPZP_NO_ACT_AT_PARCEL"}
    if resolution.records:
        assert state.reason_code == "MPZP_ONLY_NON_BINDING_ACTS"
        assert resolution.records[0].precedence == "not_in_force"
        assert resolution.records[0].resolution_number is None  # numer uchwały o przystąpieniu to nie numer planu
        assert resolution.records[0].document_kind == "commencement"
