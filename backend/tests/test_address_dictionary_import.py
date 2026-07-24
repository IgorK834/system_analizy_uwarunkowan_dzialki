"""Testy kontraktu i bezpiecznego parsera paczek słowników GUGiK."""

from __future__ import annotations

import io
import zipfile

import httpx
import pytest
import respx

from app.modules.location.infrastructure.dictionary_import import (
    AddressDictionaryClient,
    AddressIndexImporter,
    AddressIndexImportError,
    UpdateManifest,
    UpdatePackage,
    _release_label,
    _validate_package_url,
    _validate_scopes,
    iter_address_records_from_zip,
    parse_update_manifest,
)

_ENDPOINT = (
    "https://mapy.geoportal.gov.pl/wss/service/SLNOFF/guest/slowniki-offline"
)


def _zip_with_xml(xml: str, name: str = "adresy.xml") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, xml)
    return output.getvalue()


def test_parse_update_manifest_reads_full_and_incremental_packages() -> None:
    manifest = parse_update_manifest(
        b"""
        <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
          xmlns:srv="http://gugik.gov.pl/schemas/slowniki-offline-service/1.0">
          <soap:Body><srv:pobierzPelneResponse>
            <srv:updateList verId="v-20260723">
              <srv:update url="https://mapy.geoportal.gov.pl/sln/a.zip"
                incr="false" dt="2026-07-23T10:00:00+02:00" sln="adr"/>
              <srv:update url="https://mapy.geoportal.gov.pl/sln/b.zip"
                incr="true" dt="2026-07-23T11:00:00+02:00" sln="adr"/>
            </srv:updateList>
          </srv:pobierzPelneResponse></soap:Body>
        </soap:Envelope>
        """
    )

    assert manifest.version_id == "v-20260723"
    assert len(manifest.packages) == 2
    assert manifest.packages[0].incremental is False
    assert manifest.packages[1].incremental is True
    assert manifest.packages[0].dictionary_type == "adr"


def test_parse_update_manifest_rejects_fault_or_missing_list() -> None:
    with pytest.raises(AddressIndexImportError, match="SOAP Fault"):
        parse_update_manifest(
            b"<Envelope><Body><Fault><faultstring>boom</faultstring></Fault></Body></Envelope>"
        )
    with pytest.raises(AddressIndexImportError, match="updateList"):
        parse_update_manifest(b"<Envelope><Body/></Envelope>")


def test_parse_update_manifest_accepts_empty_incremental_update_list() -> None:
    manifest = parse_update_manifest(
        b'<Envelope><updateList verId="checkpoint-current"/></Envelope>'
    )
    assert manifest.version_id == "checkpoint-current"
    assert manifest.packages == ()


def test_zip_parser_keeps_current_address_and_marks_history_inactive() -> None:
    payload = _zip_with_xml(
        """
        <sln:lista-adresow xmlns:sln="http://gugik.gov.pl/schemas/slowniki-schema/1.0">
          <sln:adres>
            <pktPrgIIPPn>PL.PZGIK.200</pktPrgIIPPn>
            <pktPrgIIPId>address-1</pktPrgIIPId>
            <pktPrgIIPWersja>2026-07-23T10:00:00+02:00</pktPrgIIPWersja>
            <pktNumer>51</pktNumer><pktStatus>istniejacy</pktStatus>
            <pktKodPocztowy>80-299</pktKodPocztowy>
            <pktX>463000.0</pktX><pktY>728000.0</pktY>
            <ulNazwaGlowna>Odysei</ulNazwaGlowna><ulIdTeryt>12345</ulIdTeryt>
            <miejscNazwa>Gdańsk</miejscNazwa><miejscIdTeryt>0933934</miejscIdTeryt>
            <gmNazwa>Gdańsk</gmNazwa><gmIdTeryt>2261011</gmIdTeryt>
            <powNazwa>Gdańsk</powNazwa><wojNazwa>pomorskie</wojNazwa>
            <cyklZyciaOd>2024-01-01T00:00:00+01:00</cyklZyciaOd>
          </sln:adres>
          <sln:adres>
            <pktPrgIIPPn>PL.PZGIK.200</pktPrgIIPPn>
            <pktPrgIIPId>address-2</pktPrgIIPId>
            <pktNumer>10</pktNumer>
            <pktX>463100.0</pktX><pktY>728100.0</pktY>
            <miejscNazwa>Gdańsk</miejscNazwa>
            <cyklZyciaDo>2025-01-01T00:00:00+01:00</cyklZyciaDo>
          </sln:adres>
        </sln:lista-adresow>
        """
    )

    records = list(
        iter_address_records_from_zip(payload, max_uncompressed_bytes=1_000_000)
    )
    assert len(records) == 2
    assert records[0].active is True
    assert records[0].label == "Gdańsk, Odysei 51"
    assert "gdansk" in records[0].normalized_label
    assert records[0].source_object_id == "PL.PZGIK.200:address-1"
    assert records[1].active is False


def test_zip_parser_rejects_zip_slip_and_size_limit() -> None:
    with pytest.raises(AddressIndexImportError, match="niedozwoloną ścieżkę"):
        list(
            iter_address_records_from_zip(
                _zip_with_xml("<root/>", "../adresy.xml"),
                max_uncompressed_bytes=1_000,
            )
        )
    with pytest.raises(AddressIndexImportError, match="limit"):
        list(
            iter_address_records_from_zip(
                _zip_with_xml("<root>" + ("x" * 500) + "</root>"),
                max_uncompressed_bytes=100,
            )
        )


def test_package_url_is_fail_closed_to_official_host() -> None:
    _validate_package_url("https://mapy.geoportal.gov.pl/sln/data.zip")
    with pytest.raises(AddressIndexImportError):
        _validate_package_url("https://example.com/data.zip")
    with pytest.raises(AddressIndexImportError):
        _validate_package_url("file:///etc/passwd")
    with pytest.raises(AddressIndexImportError):
        _validate_package_url(
            "https://mapy.geoportal.gov.pl.evil.example/data.zip"
        )


def test_scope_and_release_label_are_deterministic() -> None:
    scopes = _validate_scopes(["14", "2261", "14"])
    assert scopes == ("14", "2261")
    with pytest.raises(ValueError):
        _validate_scopes(["PL"])

    first = parse_update_manifest(
        b'<Envelope><updateList verId="v1"><update url="https://mapy.geoportal.gov.pl/a.zip" incr="false" sln="adr"/></updateList></Envelope>'
    )
    label_a = _release_label(scopes, {"14": first, "2261": first})
    label_b = _release_label(tuple(reversed(scopes)), {"14": first, "2261": first})
    assert label_a == label_b
    assert label_a.startswith("sln-")


@respx.mock
def test_dictionary_client_fetches_full_and_incremental_manifests() -> None:
    responses = [
        httpx.Response(
            200,
            content=(
                b'<Envelope><updateList verId="full-v">'
                b'<update url="https://mapy.geoportal.gov.pl/full.zip" '
                b'incr="false" sln="adr"/></updateList></Envelope>'
            ),
        ),
        httpx.Response(
            200,
            content=b'<Envelope><updateList verId="increment-v"/></Envelope>',
        ),
    ]
    route = respx.post(_ENDPOINT).mock(side_effect=responses)
    client = AddressDictionaryClient(endpoint=_ENDPOINT)

    assert client.full_manifest("22").version_id == "full-v"
    assert client.incremental_manifest("full-v").version_id == "increment-v"
    requests = [call.request.content.decode("utf-8") for call in route.calls]
    assert "<ns:pobierzPelne>" in requests[0]
    assert "<ns:teryt>22</ns:teryt>" in requests[0]
    assert "<ns:pobierzPrzyrost>" in requests[1]
    assert "<ns:verId>full-v</ns:verId>" in requests[1]


@respx.mock
def test_dictionary_client_downloads_after_validated_redirect() -> None:
    start = "http://mapy.geoportal.gov.pl/sln/start.zip"
    final = "https://mapy.geoportal.gov.pl/sln/final.zip"
    respx.get(start).mock(
        return_value=httpx.Response(302, headers={"Location": final})
    )
    respx.get(final).mock(
        return_value=httpx.Response(
            200, content=b"zip-payload", headers={"Content-Length": "11"}
        )
    )

    assert AddressDictionaryClient(max_package_bytes=100).download(start) == b"zip-payload"


@respx.mock
def test_dictionary_client_enforces_declared_and_streamed_size_limits() -> None:
    declared = "https://mapy.geoportal.gov.pl/sln/declared.zip"
    streamed = "https://mapy.geoportal.gov.pl/sln/streamed.zip"
    respx.get(declared).mock(
        return_value=httpx.Response(
            200, content=b"x", headers={"Content-Length": "100"}
        )
    )
    respx.get(streamed).mock(return_value=httpx.Response(200, content=b"12345"))
    client = AddressDictionaryClient(max_package_bytes=4)

    with pytest.raises(AddressIndexImportError, match="limit"):
        client.download(declared)
    with pytest.raises(AddressIndexImportError, match="limit"):
        client.download(streamed)


@respx.mock
def test_dictionary_client_rejects_bad_http_and_redirect_loop() -> None:
    failed = "https://mapy.geoportal.gov.pl/sln/failed.zip"
    loop = "https://mapy.geoportal.gov.pl/sln/loop.zip"
    respx.get(failed).mock(return_value=httpx.Response(503))
    respx.get(loop).mock(
        return_value=httpx.Response(302, headers={"Location": loop})
    )
    client = AddressDictionaryClient()

    with pytest.raises(AddressIndexImportError, match="pobrać paczki"):
        client.download(failed)
    with pytest.raises(AddressIndexImportError, match="przekierowań"):
        client.download(loop)


def test_importer_manifest_modes_and_missing_checkpoint() -> None:
    manifest = UpdateManifest(
        version_id="v1",
        packages=(
            UpdatePackage(
                url="https://mapy.geoportal.gov.pl/a.zip",
                incremental=False,
                dictionary_type="adr",
                published_at=None,
            ),
        ),
    )

    class FakeClient:
        def full_manifest(self, scope: str) -> UpdateManifest:
            assert scope == "22"
            return manifest

        def incremental_manifest(self, version_id: str) -> UpdateManifest:
            assert version_id == "v0"
            return manifest

    importer = AddressIndexImporter(client=FakeClient())
    assert importer._manifests(("22",), "full", None)["22"] == manifest
    assert importer._manifests(("22",), "incremental", (1, {"22": "v0"}))[
        "22"
    ] == manifest
    with pytest.raises(AddressIndexImportError, match="checkpointu"):
        importer._manifests(("22",), "incremental", (1, {}))


def test_importer_sync_short_circuits_existing_release(monkeypatch) -> None:
    manifest = UpdateManifest(version_id="v1", packages=())
    importer = AddressIndexImporter(client=object())
    monkeypatch.setattr(importer, "_ensure_data_source", lambda entry: 7)
    monkeypatch.setattr(importer, "_active_state", lambda source_id: None)
    monkeypatch.setattr(
        importer,
        "_manifests",
        lambda scopes, mode, previous: {"22": manifest},
    )
    monkeypatch.setattr(importer, "_existing_release", lambda source_id, label: 42)

    result = importer.sync(["22"], mode="full")
    assert result["status"] == "unchanged"
    assert result["release_id"] == 42


def test_importer_rejects_increment_without_active_release(monkeypatch) -> None:
    importer = AddressIndexImporter(client=object())
    monkeypatch.setattr(importer, "_ensure_data_source", lambda entry: 7)
    monkeypatch.setattr(importer, "_active_state", lambda source_id: None)
    with pytest.raises(AddressIndexImportError, match="import pełny"):
        importer.sync(["22"], mode="incremental")
