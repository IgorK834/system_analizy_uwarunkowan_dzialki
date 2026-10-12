"""Adapter kanału ``mpzp_ru`` na ``OgcClient`` i logika aktów na zamrożonych odpowiedziach RU (AU-101).

Odpowiedzi usługi (``fixtures/source_contracts/ru_mpzp_acts``) są rzeczywiste; geometrie działek to rzeczywiste
wielokąty ULDK. Elementy zbudowane ręcznie (styk granic, szum geometrii, odwrócone osie) są oznaczone w nazwie testu
albo w opisie jako pochodne.
"""

from __future__ import annotations

import re
from datetime import date

import httpx
import pytest
import respx
from shapely import Polygon, box
from shapely.affinity import translate

from app.modules.planning.domain.mpzp_acts import (
    MIN_INTERSECTION_AREA_SQM,
    RuActRecord,
    intersect_act_with_parcel,
    latest_versions,
    number_from_identifier,
    ru_legal_status,
)
from app.modules.planning.infrastructure.ru_mpzp_acts import (
    RU_CONTRACT_ERROR,
    RU_GEOMETRY_AXIS_SUSPECT,
    RU_LIMIT_EXCEEDED,
    RU_SERVICE_EXCEPTION,
    RU_TRANSPORT_ERROR,
    gml_id_for,
    parse_act,
    parse_document,
    parse_drawing,
    reference_path,
    without_version,
)
from tests.ru_mpzp_fixtures import (
    BIELSKO,
    DYGOWO_EMPTY,
    FIXTURE_DIR,
    GORA_KALWARIA,
    KRAKOW_DRAFT,
    WARSZAWA_EMPTY,
    WFS_URL,
    adapter,
    manifest,
    mount_case,
    parcel,
    raw,
    verify_manifest_hashes,
)

AS_OF = date(2026, 10, 10)


def _bbox(slug: str) -> tuple[float, float, float, float]:
    return tuple(parcel(slug).bounds)  # type: ignore[return-value]


def _fetch(slug: str):
    with respx.mock(assert_all_called=False) as router:
        mount_case(router, slug)
        response = adapter().fetch_acts(_bbox(slug))
        return response, [call.request for call in router.calls]


# --- integralność zamrożonych odpowiedzi -------------------------------------------------------------------------


def test_frozen_ru_responses_match_the_manifest_hashes() -> None:
    assert verify_manifest_hashes() == []
    assert set(manifest()["cases"]) == {GORA_KALWARIA, BIELSKO, KRAKOW_DRAFT, WARSZAWA_EMPTY, DYGOWO_EMPTY}


# --- zapytanie: kolejność osi północ,wschód -------------------------------------------------------------------


def test_request_uses_northing_easting_bbox_for_urn_epsg_2180() -> None:
    response, requests = _fetch(GORA_KALWARIA)
    params = requests[0].url.params
    min_x, min_y, max_x, max_y = _bbox(GORA_KALWARIA)
    assert params["service"] == "WFS" and params["version"] == "2.0.0"
    assert params["typeNames"] == "app-mpzp:AktPlanowaniaPrzestrzennego"
    assert params["srsName"] == "urn:ogc:def:crs:EPSG::2180"
    # Współrzędne: północ (y) przed wschodem (x) — zweryfikowane na żywej usłudze 2026-10-05 i 2026-10-10.
    assert params["bbox"] == f"{min_y:.3f},{min_x:.3f},{max_y:.3f},{max_x:.3f},urn:ogc:def:crs:EPSG::2180"
    assert response.error_code is None


def test_request_url_in_manifest_matches_the_adapter_request() -> None:
    _, requests = _fetch(BIELSKO)
    recorded = manifest()["files"][f"{BIELSKO}__acts.xml"]["request_url"]
    mine = requests[0].url
    for key in ("typeNames", "bbox", "srsName", "count", "startIndex", "outputFormat", "service", "version"):
        assert httpx.URL(recorded).params[key] == mine.params[key], key


# --- rzeczywiste akty --------------------------------------------------------------------------------------------


def test_gora_kalwaria_returns_only_the_elaboration_act_with_swapped_axes() -> None:
    response, _ = _fetch(GORA_KALWARIA)
    assert response.error_code is None and response.complete is True
    (act,) = response.acts
    assert act.identifier == "PL.ZIPPZP.6728/141801-MPZP/XXIV_302_2026_P1"
    assert act.status_code == "elaboration" and act.status_title == "w opracowaniu"
    assert act.effective_from == date(2026, 9, 22)
    assert "cz. II" in (act.title or "")
    # posList zaczyna się od „460386.0789 652106.8515” (północ, wschód); w modelu x = wschód, y = północ.
    min_x, min_y, max_x, max_y = act.geometry.bounds  # type: ignore[union-attr]
    assert (round(min_x, 4), round(min_y, 4)) == (652106.8515, 460323.1955)
    assert (round(max_x, 3), round(max_y, 4)) == (652195.756, 460401.8456)
    assert act.document_refs == (
        ("dokumentPrzystepujacy", "PL.ZIPPZP.6728/141801-MPZP/XXIV_302_2026_DOC1_przystapienie"),
    )


def test_gora_kalwaria_act_touches_only_the_bbox_and_is_rejected_with_a_distance() -> None:
    """Rozstrzygnięcie z audytu: akt cz. II zwrócony dla bbox 23/8 NIE przecina działki (leży ok. 6 m dalej)."""
    response, _ = _fetch(GORA_KALWARIA)
    (act,) = response.acts
    parcel_geometry = parcel(GORA_KALWARIA)
    assert act.geometry.bounds[1] < parcel_geometry.bounds[3]  # type: ignore[union-attr]  # bbox się pokrywają…
    outcome = intersect_act_with_parcel(parcel_geometry, act.geometry)
    assert outcome.accepted is None  # …ale powierzchnie nie
    assert outcome.rejection == "no_overlap"
    assert outcome.distance_m == pytest.approx(5.994, abs=0.01)
    assert outcome.area_sqm == 0.0
    assert parcel_geometry.area == pytest.approx(1199.0, abs=0.5)


def test_bielsko_act_intersects_the_parcel_in_epsg_2180() -> None:
    response, _ = _fetch(BIELSKO)
    (act,) = response.acts
    assert act.identifier == "PL.ZIPPZP.2617/246101-MPZP/006_G_1-2"
    assert act.status_code == "legalForce" and act.effective_from == date(1998, 4, 1)
    outcome = intersect_act_with_parcel(parcel(BIELSKO), act.geometry)
    assert outcome.accepted is not None and outcome.rejection is None
    assert outcome.accepted.area_sqm == pytest.approx(167.44, abs=0.05)
    assert outcome.accepted.pct == pytest.approx(11.84, abs=0.02)
    assert 0 < outcome.accepted.pct < 100


def test_krakow_draft_returned_for_the_bbox_envelope_is_not_mistaken_for_swapped_axes() -> None:
    """Regresja z rzeczywistej odpowiedzi: RU filtruje po obwiedni, więc wielokąt aktu może nie dotykać ``bbox``.

    Pierwsza wersja kontroli osi wymagała przecięcia wielokąta z ``bbox`` i uznawała ten rzeczywisty projekt planu
    (Wzgórze Wawelskie, 540/15) za dane z zamienionymi osiami. Kontrola porównuje teraz obwiednie.
    """
    response, _ = _fetch(KRAKOW_DRAFT)
    (act,) = response.acts
    assert act.status_code == "elaboration" and act.geometry is not None and act.geometry_issue is None
    assert RU_GEOMETRY_AXIS_SUSPECT not in response.issues
    assert ru_legal_status(act, AS_OF)[0] == "not_yet_binding"
    parcel_geometry = parcel(KRAKOW_DRAFT)
    min_x, min_y, max_x, max_y = act.geometry.bounds
    p_min_x, p_min_y, p_max_x, p_max_y = parcel_geometry.bounds
    assert min_x <= p_max_x and max_x >= p_min_x and min_y <= p_max_y and max_y >= p_min_y  # obwiednie nachodzą
    outcome = intersect_act_with_parcel(parcel_geometry, act.geometry)
    assert outcome.accepted is None and outcome.rejection in {"no_overlap", "boundary_touch_only"}


@pytest.mark.parametrize("slug", [WARSZAWA_EMPTY, DYGOWO_EMPTY])
def test_empty_ru_response_is_a_clean_empty_result_not_an_error(slug: str) -> None:
    response, _ = _fetch(slug)
    assert response.acts == () and response.complete is True
    assert response.error_code is None and response.issues == ()


def test_related_documents_and_drawings_for_bielsko() -> None:
    with respx.mock(assert_all_called=False) as router:
        mount_case(router, BIELSKO)
        client_adapter = adapter()
        response = client_adapter.fetch_acts(_bbox(BIELSKO))
        (act,) = response.acts
        related = client_adapter.fetch_related(
            [path for _, path in act.document_refs], list(act.drawing_refs)
        )
    uchwala = related.documents["PL.ZIPPZP.2617/246101-MPZP/006_U_1"]
    assert uchwala.number == "LIII/749/1998" and uchwala.number_basis == "document_number"
    assert uchwala.effective_from == date(1998, 4, 1)
    assert uchwala.links == ("https://brm.bielsko-biala.pl/wp-content/uploads/2021/01/006_uchwala.pdf",)
    assert ("uchwala", "PL.ZIPPZP.2617/246101-MPZP/006_G_1-2") in uchwala.relations
    change = related.documents["PL.ZIPPZP.2617/246101-MPZP/243_U"]
    assert change.number == "XLIV/1038/2022"
    assert ("zmienia", "PL.ZIPPZP.2617/246101-MPZP/006_G_1-2") in change.relations  # wersja w href odcięta
    assert len(related.drawings) == 2
    drawing = related.drawings["PL.ZIPPZP.2617/246101-MPZP/006_R_1-2/20260917T000000"]
    assert drawing.link.endswith(".tif") and drawing.scale_denominator == 1000
    assert related.unresolved_documents == () and related.unresolved_drawings == ()
    assert related.error_codes == ()


def test_commencement_document_number_is_not_the_plan_number() -> None:
    with respx.mock(assert_all_called=False) as router:
        mount_case(router, GORA_KALWARIA)
        client_adapter = adapter()
        (act,) = client_adapter.fetch_acts(_bbox(GORA_KALWARIA)).acts
        related = client_adapter.fetch_related([path for _, path in act.document_refs], [])
    (document,) = related.documents.values()
    assert document.number == "XXIV/302/2026" and document.number_basis == "document_title"
    assert "przystąpienia" in (document.title or "")
    assert ("przystapienie", act.identifier) in document.relations


def test_missing_related_object_is_reported_as_unresolved() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(WFS_URL).mock(
            return_value=httpx.Response(
                200,
                content=raw(f"{GORA_KALWARIA}__documents.xml"),
                headers={"content-type": "application/gml+xml"},
            )
        )
        related = adapter().fetch_related(
            ["PL.ZIPPZP.6728/141801-MPZP/XXIV_302_2026_DOC1_przystapienie", "PL.X/1/NIE_MA"], []
        )
    assert related.unresolved_documents == ("PL.X/1/NIE_MA",)


# --- błędy źródła nigdy nie są „brakiem aktów” -------------------------------------------------------------------


def test_real_ows_exception_report_with_http_400_is_a_source_error() -> None:
    body = raw("ows_exception_unknown_type.xml")
    with respx.mock(assert_all_called=False) as router:
        router.get(WFS_URL).mock(
            return_value=httpx.Response(400, content=body, headers={"content-type": "application/xml"})
        )
        response = adapter().fetch_acts(_bbox(GORA_KALWARIA))
    assert response.error_code == RU_TRANSPORT_ERROR
    assert response.acts == () and response.complete is False


def test_exception_report_returned_with_http_200_is_a_service_exception() -> None:
    body = raw("ows_exception_unknown_type.xml")
    with respx.mock(assert_all_called=False) as router:
        router.get(WFS_URL).mock(
            return_value=httpx.Response(200, content=body, headers={"content-type": "application/xml"})
        )
        response = adapter().fetch_acts(_bbox(GORA_KALWARIA))
    assert response.error_code == RU_SERVICE_EXCEPTION
    assert response.acts == ()


@pytest.mark.parametrize(
    ("factory", "code"),
    [
        (lambda: httpx.Response(503, content=b"unavailable"), RU_TRANSPORT_ERROR),
        (lambda: httpx.ConnectTimeout("timeout"), RU_TRANSPORT_ERROR),
        (lambda: httpx.RemoteProtocolError("Server disconnected"), RU_TRANSPORT_ERROR),
        (lambda: httpx.Response(200, content=b"<html>not xml?</html>", headers={"content-type": "text/html"}), RU_CONTRACT_ERROR),
    ],
)
def test_transport_and_contract_errors_are_reported_not_swallowed(factory, code: str) -> None:  # noqa: ANN001
    with respx.mock(assert_all_called=False) as router:
        value = factory()
        if isinstance(value, Exception):
            router.get(WFS_URL).mock(side_effect=value)
        else:
            router.get(WFS_URL).mock(return_value=value)
        response = adapter().fetch_acts(_bbox(GORA_KALWARIA))
    assert response.error_code == code
    assert response.acts == () and response.complete is False


def test_response_size_limit_is_reported_as_limit_exceeded() -> None:
    with respx.mock(assert_all_called=False) as router:
        mount_case(router, BIELSKO)
        response = adapter(max_response_bytes=500).fetch_acts(_bbox(BIELSKO))
    assert response.error_code == RU_LIMIT_EXCEEDED


def test_transient_connection_reset_is_retried_like_the_audit_observation() -> None:
    """RU potrafi zerwać połączenie (obserwacja 2026-10-10, Kraków 49/2); ponowienie kończy się odpowiedzią."""
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("Remote end closed connection without response")
        return httpx.Response(200, content=raw(f"{WARSZAWA_EMPTY}__acts.xml"), headers={"content-type": "application/gml+xml"})

    with respx.mock(assert_all_called=False) as router:
        router.get(WFS_URL).mock(side_effect=flaky)
        response = adapter(retries=2).fetch_acts(_bbox(WARSZAWA_EMPTY))
    assert calls["n"] == 2 and response.error_code is None and response.acts == ()


def test_second_page_is_requested_until_number_matched_is_reached() -> None:
    """Paginacja RU po 10 obiektów (pochodna: dwie strony zbudowane z rzeczywistego aktu Bielska)."""
    feature = re.search(rb"<wfs:member>.*?</wfs:member>", raw(f"{BIELSKO}__acts.xml"), re.S).group(0)  # type: ignore[union-attr]
    head = raw(f"{BIELSKO}__acts.xml").split(b"<wfs:member>", 1)[0]
    first = re.sub(rb'numberMatched="\d+" numberReturned="\d+"', b'numberMatched="2" numberReturned="1"', head)
    second = first
    page_one = first + feature + b"</wfs:FeatureCollection>"
    other = feature.replace(b"006_G_1-2", b"007_G_1-1")
    page_two = second + other + b"</wfs:FeatureCollection>"
    pages = iter([page_one, page_two])
    seen: list[str] = []

    def paged(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["startIndex"])
        return httpx.Response(200, content=next(pages), headers={"content-type": "application/gml+xml"})

    with respx.mock(assert_all_called=False) as router:
        router.get(WFS_URL).mock(side_effect=paged)
        response = adapter().fetch_acts(_bbox(BIELSKO))
    assert seen == ["0", "1"]
    assert [act.identifier.rsplit("/", 1)[-1] for act in response.acts] == ["006_G_1-2", "007_G_1-1"]
    assert response.complete is True


# --- osie: wykrycie zmiany kolejności po stronie usługi ------------------------------------------------------------


def _swap_pos_lists(xml: bytes) -> bytes:
    def swap(match: re.Match[bytes]) -> bytes:
        numbers = match.group(1).split()
        pairs = [(numbers[i], numbers[i + 1]) for i in range(0, len(numbers), 2)]
        return b"<gml:posList>" + b" ".join(b + b" " + a for a, b in pairs) + b"</gml:posList>"

    return re.sub(rb"<gml:posList>([^<]+)</gml:posList>", swap, xml)


def test_axis_order_change_on_the_service_side_is_detected_not_read_as_no_overlap() -> None:
    """Pochodna rzeczywistej odpowiedzi z zamienionymi osiami: akt nie może zostać po cichu uznany za „poza działką”."""
    swapped = _swap_pos_lists(raw(f"{BIELSKO}__acts.xml"))
    assert swapped != raw(f"{BIELSKO}__acts.xml")
    with respx.mock(assert_all_called=False) as router:
        router.get(WFS_URL).mock(
            return_value=httpx.Response(200, content=swapped, headers={"content-type": "application/gml+xml"})
        )
        response = adapter().fetch_acts(_bbox(BIELSKO))
    (act,) = response.acts
    assert act.geometry is None and act.geometry_issue == "axis_suspect"
    assert RU_GEOMETRY_AXIS_SUSPECT in response.issues
    assert intersect_act_with_parcel(parcel(BIELSKO), act.geometry).rejection == "geometry_unusable"


# --- parsery: wejścia nietypowe --------------------------------------------------------------------------------


def _members(name: str) -> list[bytes]:
    """Elementy obiektów tak, jak serializuje je ``OgcClient`` (z deklaracjami przestrzeni nazw)."""
    from xml.etree import ElementTree

    root = ElementTree.fromstring(raw(name))
    return [ElementTree.tostring(next(iter(member)), encoding="utf-8") for member in root]


def test_parsers_ignore_other_feature_types_and_missing_identifiers() -> None:
    (document,) = _members(f"{GORA_KALWARIA}__documents.xml")
    (act,) = _members(f"{GORA_KALWARIA}__acts.xml")
    assert parse_act(document) is None
    assert parse_drawing(document) is None
    assert parse_document(act) is None
    assert parse_act(act) is not None and parse_document(document) is not None
    without_identifier = re.sub(rb"<(\w+):idIIP>.*?</\1:idIIP>", b"", act, flags=re.S)
    assert parse_act(without_identifier) is None


def test_reference_helpers() -> None:
    href = "https://www.gov.pl/zagospodarowanieprzestrzenne/app/DokumentFormalny/PL.ZIPPZP.2617/246101-MPZP/006_U_1"
    assert reference_path(href, "DokumentFormalny") == "PL.ZIPPZP.2617/246101-MPZP/006_U_1"
    assert reference_path(href, "AktPlanowaniaPrzestrzennego") is None
    assert reference_path(None, "DokumentFormalny") is None
    assert without_version("A/B/C/20260917T000000") == "A/B/C"
    assert without_version("A/B/C") == "A/B/C"
    assert gml_id_for("PL.ZIPPZP.2617/246101-MPZP/006_U_1") == "PL.ZIPPZP.2617_246101-MPZP_006_U_1"


# --- przecięcie: przypadki graniczne (konstrukcje pochodne) ------------------------------------------------------


PARCEL = Polygon([(1000, 2000), (1040, 2000), (1040, 2030), (1000, 2030)])  # 1200 m²


def test_act_covering_the_whole_parcel_has_pct_100_and_exact_area() -> None:
    outcome = intersect_act_with_parcel(PARCEL, box(900, 1900, 1200, 2200))
    assert outcome.accepted is not None
    assert outcome.accepted.pct == 100.0 and outcome.accepted.area_sqm == 1200.0


def test_act_sharing_only_an_edge_is_boundary_touch_only() -> None:
    outcome = intersect_act_with_parcel(PARCEL, box(1040, 1990, 1100, 2040))
    assert outcome.accepted is None and outcome.rejection == "boundary_touch_only"
    assert outcome.distance_m == 0.0


def test_sliver_below_threshold_is_rejected_with_its_area() -> None:
    sliver = box(1039.9, 1990, 1100, 2040)  # 0,1 m × 30 m = 3 m² — powyżej progu
    tiny = box(1039.99, 2000, 1100, 2020)  # 0,01 m × 20 m = 0,2 m² — poniżej progu
    assert intersect_act_with_parcel(PARCEL, sliver).accepted is not None
    outcome = intersect_act_with_parcel(PARCEL, tiny)
    assert outcome.accepted is None and outcome.rejection == "below_min_area"
    assert 0 < outcome.area_sqm < MIN_INTERSECTION_AREA_SQM


def test_act_far_from_the_parcel_has_a_distance() -> None:
    outcome = intersect_act_with_parcel(PARCEL, box(1100, 2000, 1200, 2100))
    assert outcome.rejection == "no_overlap" and outcome.distance_m == pytest.approx(60.0)


def test_invalid_bow_tie_geometry_is_repaired_and_flagged() -> None:
    bow_tie = Polygon([(990, 1990), (1050, 2040), (1050, 1990), (990, 2040)])
    assert not bow_tie.is_valid
    outcome = intersect_act_with_parcel(PARCEL, bow_tie)
    assert outcome.geometry_repaired is True
    assert outcome.accepted is not None and outcome.accepted.geometry_repaired is True


@pytest.mark.parametrize("geometry", [None, Polygon()])
def test_missing_or_empty_geometry_is_unusable_not_zero_overlap(geometry) -> None:  # noqa: ANN001
    outcome = intersect_act_with_parcel(PARCEL, geometry)
    assert outcome.rejection == "geometry_unusable" and outcome.accepted is None


def test_translated_act_far_from_bbox_is_not_confused_with_overlap() -> None:
    moved = translate(box(1000, 2000, 1040, 2030), xoff=500_000)
    assert intersect_act_with_parcel(PARCEL, moved).rejection == "no_overlap"


# --- numer uchwały -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("identifier", "number"),
    [
        ("PL.ZIPPZP.6728/141801-MPZP/IV_30_2024_P1", "IV/30/2024"),
        ("PL.ZIPPZP.6728/141801-MPZP/XXIV_302_2026_P1", "XXIV/302/2026"),
        ("PL.ZIPPZP.2617/246101-MPZP/006_G_1-2", None),
        ("PL.ZIPPZP.4873/126101-MPZP/480_P1", None),
        ("PL.ZIPPZP.1/1/12_345_2020", "12/345/2020"),
    ],
)
def test_number_from_identifier_is_conservative(identifier: str, number: str | None) -> None:
    assert number_from_identifier(identifier) == number


# --- wersje ---------------------------------------------------------------------------------------------------------


def test_latest_version_of_an_act_wins_and_older_is_reported() -> None:
    (act,) = _fetch(BIELSKO)[0].acts
    from dataclasses import replace
    from datetime import datetime

    older = replace(act, gml_id=act.gml_id + "_old", version_id="20200101T000000", version_started=datetime(2020, 1, 1))
    kept, dropped = latest_versions([older, act])
    assert kept == [act] and dropped == [older]
    assert latest_versions([act]) == ([act], [])


def test_unknown_ru_status_code_is_not_binding() -> None:
    (act,) = _fetch(BIELSKO)[0].acts
    from dataclasses import replace

    assert ru_legal_status(act, AS_OF)[0] == "binding"
    assert ru_legal_status(replace(act, status_code="legalForce", effective_to=date(2020, 1, 1)), AS_OF)[0] == "not_binding"
    assert ru_legal_status(replace(act, effective_from=date(2030, 1, 1)), AS_OF)[0] == "not_yet_binding"
    assert ru_legal_status(replace(act, status_code="obsolete"), AS_OF)[0] == "not_binding"
    assert ru_legal_status(replace(act, status_code="adoption"), AS_OF)[0] == "not_yet_binding"
    assert ru_legal_status(replace(act, status_code="whatever"), AS_OF) == ("unknown", ["MPZP_ACT_STATUS_UNKNOWN"])
    assert ru_legal_status(replace(act, status_code=None), AS_OF)[0] == "unknown"


def test_frozen_fixture_directory_has_only_expected_files() -> None:
    names = {path.name for path in FIXTURE_DIR.iterdir()}
    assert "manifest.json" in names
    assert {name for name in names if name != "manifest.json"} == set(manifest()["files"])
    _ = RuActRecord  # typ domenowy dostarczany adapterem
