"""BK-107: metadane CSW RU, powiązanie po identyfikatorze i oficjalne URL-e."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.modules.imports.domain.pog import PogObjectId, PogValidationError
from app.modules.imports.infrastructure.ogc_client import OgcClient, OgcClientConfig
from app.modules.imports.infrastructure.pog.csw_metadata import (
    attach_metadata,
    csw_record_url,
    fetch_act_metadata,
    parse_iso_records,
    ru_object_gml_url,
)
from app.modules.imports.infrastructure.pog.reader import parse_ru_app_feature_collection

FIXTURES = Path(__file__).parent / "fixtures" / "ru"
CSW_URL = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/csw"
WFS_URL = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-pog/wfs"
POG_ID = "9223a9d0-e8b7-453b-b7bc-000e5e4d4d87"
MPZP_ID = "0c573ae9-a731-4866-9171-ca9efd080f8e"


def _csw_payload() -> bytes:
    return (FIXTURES / "csw_getrecords_iso_226401.xml").read_bytes()


def _sopot_act():
    return parse_ru_app_feature_collection(
        (FIXTURES / "wfs_pog_getfeature_act.xml").read_bytes()
    ).acts[0]


def _client(handler) -> OgcClient:
    return OgcClient(
        source_id="pog_app",
        config=OgcClientConfig(
            allowed_hosts=frozenset({"rejestr-urbanistyczny.gov.pl"}), retries=0, backoff_seconds=0
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=lambda _host: ("151.101.1.1",),
        sleep=lambda _seconds: None,
    )


def test_iso_records_are_parsed_with_dates_identifiers_and_hashes() -> None:
    records = {r.record_id: r for r in parse_iso_records(_csw_payload(), csw_url=CSW_URL)}
    assert set(records) == {POG_ID, MPZP_ID}
    pog = records[POG_ID]
    assert pog.resource_identifier and pog.resource_identifier.endswith("/PL.ZIPPZP.10011/226401-POG/")
    assert pog.title == "Zbiór danych przestrzennych dla planu ogólnego gminy, Sopot (226401)"
    assert (str(pog.publication_date), str(pog.creation_date)) == ("2026-08-12", "2024-05-31")
    assert str(pog.date_stamp) == "2026-08-12"
    assert "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-pog/wfs" in pog.references
    assert pog.record_sha256 != records[MPZP_ID].record_sha256
    assert pog.response_sha256 == records[MPZP_ID].response_sha256
    assert pog.metadata_url == csw_record_url(CSW_URL, POG_ID)
    query = parse_qs(urlsplit(pog.metadata_url).query)
    assert query["request"] == ["GetRecordById"] and query["id"] == [POG_ID]


def test_metadata_is_linked_by_identifier_not_by_title() -> None:
    records = parse_iso_records(_csw_payload(), csw_url=CSW_URL)
    (linked,), warnings = attach_metadata((_sopot_act(),), records)
    assert [record.record_id for record in linked.metadata] == [POG_ID]
    assert warnings == ()

    # Rekord o identycznym tytule, ale innym identyfikatorze zasobu nie jest wiązany.
    decoy = tuple(
        replace(record, resource_identifier=record.resource_identifier.replace("10011", "99999"))
        for record in records
        if record.record_id == POG_ID
    )
    (unlinked,), warnings = attach_metadata((_sopot_act(),), decoy)
    assert unlinked.metadata == ()
    assert warnings == ("csw_metadata_unresolved:PL.ZIPPZP.10011/226401-POG/1POG",)


def test_act_without_idiip_has_no_catalog_identifier() -> None:
    act = replace(_sopot_act(), object_id=None)
    (linked,), warnings = attach_metadata((act,), parse_iso_records(_csw_payload()))
    assert linked.metadata == () and warnings


@pytest.mark.parametrize(
    "payload",
    [
        b"<not-xml",
        b'<gmd:MD_Metadata xmlns:gmd="http://www.isotc211.org/2005/gmd"/>',
    ],
)
def test_invalid_csw_payloads_are_rejected(payload: bytes) -> None:
    with pytest.raises(PogValidationError):
        parse_iso_records(payload)


def test_gml_url_is_exact_version_filter_on_https_service() -> None:
    url = ru_object_gml_url(
        WFS_URL,
        "StrefaPlanistyczna",
        PogObjectId("PL.ZIPPZP.10011/226401-POG", "1POG-100SU", "20260819T010000"),
    )
    assert url is not None and url.startswith(WFS_URL + "?")
    query = parse_qs(urlsplit(url).query)
    assert query["typeNames"] == ["app-pog:StrefaPlanistyczna"]
    fes = query["FILTER"][0]
    assert "<fes:Literal>PL.ZIPPZP.10011/226401-POG</fes:Literal>" in fes
    assert "<fes:Literal>1POG-100SU</fes:Literal>" in fes
    assert "<fes:Literal>20260819T010000</fes:Literal>" in fes

    unversioned = ru_object_gml_url(WFS_URL, "DokumentFormalny", PogObjectId("A/B", "x<&>\"y"))
    assert unversioned is not None
    fes = parse_qs(urlsplit(unversioned).query)["FILTER"][0]
    assert "wersjaId" not in fes and "x&lt;&amp;&gt;&quot;y" in fes


@pytest.mark.parametrize(
    ("service", "object_id"),
    [
        ("http://rejestr-urbanistyczny.gov.pl/wfs", PogObjectId("A/B", "1")),
        (None, PogObjectId("A/B", "1")),
        (WFS_URL, None),
    ],
)
def test_gml_url_requires_https_service_and_identifier(service, object_id) -> None:
    assert ru_object_gml_url(service, "AktPlanowaniaPrzestrzennego", object_id) is None


def test_fetch_uses_shared_ogc_client_and_parses_each_record() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_csw_payload(), headers={"content-type": "application/xml"})

    result = fetch_act_metadata(_client(handler), CSW_URL, "226401")
    assert result.error is None and result.complete is True
    assert {record.record_id for record in result.records} == {POG_ID, MPZP_ID}
    assert result.source is not None and result.source.operation == "CSW:GetRecords"
    assert requests[0].url.params["constraint"] == "dc:title like '%(226401)%'"


def test_fetch_outage_returns_explicit_error_with_provenance() -> None:
    result = fetch_act_metadata(
        _client(lambda _request: httpx.Response(503, text="down")), CSW_URL, "226401"
    )
    assert result.records == () and result.complete is False
    assert result.error and result.error.startswith("csw_unavailable:")
    assert result.source is not None and result.source.error_code


def test_fetch_invalid_records_and_teryt_are_reported() -> None:
    broken = (
        b'<csw:GetRecordsResponse xmlns:csw="http://www.opengis.net/cat/csw/2.0.2">'
        b'<csw:SearchResults nextRecord="0">'
        b'<gmd:MD_Metadata xmlns:gmd="http://www.isotc211.org/2005/gmd"/>'
        b"</csw:SearchResults></csw:GetRecordsResponse>"
    )
    result = fetch_act_metadata(
        _client(lambda _r: httpx.Response(200, content=broken, headers={"content-type": "application/xml"})),
        CSW_URL,
        "226401",
    )
    assert result.error and result.error.startswith("csw_invalid:")
    assert fetch_act_metadata(_client(lambda _r: httpx.Response(500)), CSW_URL, "22'6").error == (
        "csw_invalid_teryt"
    )
