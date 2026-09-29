from datetime import date

import httpx
import pytest
import respx

from app.core.data_sources import DataSourceCatalog, parse_catalog
from app.services.geometry import parse_parcel_geometry
from app.services.kiut import (
    REASON_EXCEPTION_REPORT,
    REASON_HTTP_ERROR,
    REASON_INCOMPLETE_RESPONSE,
    REASON_MALFORMED_RESPONSE,
    REASON_SERVICE_TIMEOUT,
    REASON_TRANSPORT_ERROR,
    REASON_VECTOR_SOURCE_NOT_CONFIRMED,
    KiutNetworkSection,
    KiutServiceUnavailableError,
    KiutSourceNotRunnableError,
    NetworkFeature,
    _classify_network_type,
    _MalformedKiutResponse,
    _normalize_network_type_value,
    _parse_kiut_response,
    bbox_from_geometry,
    fetch_kiut_network_section,
)

KIUT_TEST_WFS_URL = "https://kiut.example.test/wfs"
BBOX = (500000.0, 200000.0, 500100.0, 200100.0)

SQUARE_WKT = (
    "POLYGON((500000 200000, 500100 200000, 500100 200100, "
    "500000 200100, 500000 200000))"
)

MOCK_GML_MULTIPLE_TYPES = """<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:kiut="http://kiut.example/">
  <wfs:member>
    <kiut:SiecUzbrojenia gml:id="f1">
      <kiut:rodzaj>wodociągowa</kiut:rodzaj>
      <kiut:geometria>
        <gml:LineString srsName="EPSG:2180">
          <gml:posList>500010 200010 500090 200010</gml:posList>
        </gml:LineString>
      </kiut:geometria>
    </kiut:SiecUzbrojenia>
  </wfs:member>
  <wfs:member>
    <kiut:SiecUzbrojenia gml:id="f2">
      <kiut:rodzaj>gazowa</kiut:rodzaj>
      <kiut:geometria>
        <gml:LineString srsName="EPSG:2180">
          <gml:posList>500020 200020 500080 200020</gml:posList>
        </gml:LineString>
      </kiut:geometria>
    </kiut:SiecUzbrojenia>
  </wfs:member>
  <wfs:member>
    <kiut:SiecUzbrojenia gml:id="f3">
      <kiut:rodzaj>XYZ_NIEZNANY</kiut:rodzaj>
      <kiut:geometria>
        <gml:LineString srsName="EPSG:2180">
          <gml:posList>500030 200030 500070 200030</gml:posList>
        </gml:LineString>
      </kiut:geometria>
    </kiut:SiecUzbrojenia>
  </wfs:member>
</wfs:FeatureCollection>"""

MOCK_GEOJSON_MULTIPLE_TYPES = """{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {"typ": "elektroenergetyczna"},
      "geometry": {"type": "LineString", "coordinates": [[500010, 200010], [500090, 200010]]}
    },
    {
      "type": "Feature",
      "properties": {},
      "geometry": {"type": "LineString", "coordinates": [[500020, 200020], [500080, 200020]]}
    }
  ]
}"""

EMPTY_GML = '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"></wfs:FeatureCollection>'
EMPTY_GEOJSON = '{"type": "FeatureCollection", "features": []}'


# --- Parsowanie GML ---------------------------------------------------------


def test_parse_gml_returns_list_of_network_features() -> None:
    result = _parse_kiut_response(MOCK_GML_MULTIPLE_TYPES, "https://example/kiut", None)

    assert len(result) == 3
    assert all(isinstance(f, NetworkFeature) for f in result)


def test_parse_gml_classifies_water_network() -> None:
    result = _parse_kiut_response(MOCK_GML_MULTIPLE_TYPES, "https://example/kiut", None)

    assert any(f.network_type == "water" for f in result)


def test_parse_gml_classifies_gas_network() -> None:
    result = _parse_kiut_response(MOCK_GML_MULTIPLE_TYPES, "https://example/kiut", None)

    assert any(f.network_type == "gas" for f in result)


def test_parse_gml_unknown_type_has_warning() -> None:
    result = _parse_kiut_response(MOCK_GML_MULTIPLE_TYPES, "https://example/kiut", None)

    unknown_feat = next(f for f in result if f.network_type == "unknown")
    assert unknown_feat.warning is not None


def test_parse_gml_unknown_type_sets_manual_review_required() -> None:
    result = _parse_kiut_response(MOCK_GML_MULTIPLE_TYPES, "https://example/kiut", None)

    unknown_feat = next(f for f in result if f.network_type == "unknown")
    assert unknown_feat.source_metadata.manual_review_required is True


def test_parse_gml_feature_geometry_is_linestring() -> None:
    result = _parse_kiut_response(MOCK_GML_MULTIPLE_TYPES, "https://example/kiut", None)

    assert result[0].geometry.geom_type in ("LineString", "MultiLineString")


# --- Parsowanie GeoJSON ------------------------------------------------------


def test_parse_geojson_returns_list_of_network_features() -> None:
    result = _parse_kiut_response(
        MOCK_GEOJSON_MULTIPLE_TYPES, "https://example/kiut", None
    )

    assert len(result) == 2


def test_parse_geojson_classifies_power_network() -> None:
    result = _parse_kiut_response(
        MOCK_GEOJSON_MULTIPLE_TYPES, "https://example/kiut", None
    )

    assert any(f.network_type == "power" for f in result)


def test_parse_geojson_missing_attribute_is_unknown() -> None:
    result = _parse_kiut_response(
        MOCK_GEOJSON_MULTIPLE_TYPES, "https://example/kiut", None
    )

    assert any(f.network_type == "unknown" for f in result)


# --- Przypadki puste i błędne ------------------------------------------------


def test_parse_empty_gml_returns_empty_list() -> None:
    result = _parse_kiut_response(EMPTY_GML, "https://example/kiut", None)

    assert result == []


def test_parse_empty_geojson_returns_empty_list() -> None:
    result = _parse_kiut_response(EMPTY_GEOJSON, "https://example/kiut", None)

    assert result == []


def test_parse_empty_string_raises_malformed() -> None:
    with pytest.raises(_MalformedKiutResponse):
        _parse_kiut_response("", "https://example/kiut", None)


def test_parse_malformed_gml_raises_malformed() -> None:
    with pytest.raises(_MalformedKiutResponse):
        _parse_kiut_response("<not><valid", "https://example/kiut", None)


def test_parse_exception_report_raises_with_dedicated_reason() -> None:
    report = '<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1"/>'

    with pytest.raises(_MalformedKiutResponse) as exc_info:
        _parse_kiut_response(report, "https://example/kiut", None)

    assert exc_info.value.reason_code == REASON_EXCEPTION_REPORT


def test_parse_partial_gml_response_raises_incomplete() -> None:
    partial = MOCK_GML_MULTIPLE_TYPES.replace(
        "<wfs:FeatureCollection ", '<wfs:FeatureCollection numberMatched="10" ', 1
    )

    with pytest.raises(_MalformedKiutResponse) as exc_info:
        _parse_kiut_response(partial, "https://example/kiut", None)

    assert exc_info.value.reason_code == REASON_INCOMPLETE_RESPONSE


# --- bbox_from_geometry -------------------------------------------------------


def test_bbox_from_geometry_returns_bounds_tuple() -> None:
    geometry = parse_parcel_geometry(SQUARE_WKT)

    assert bbox_from_geometry(geometry) == (500000.0, 200000.0, 500100.0, 200100.0)


# --- Normalizacja / klasyfikacja typów sieci ---------------------------------


def test_normalize_network_type_value_water_variants() -> None:
    assert _normalize_network_type_value("sieć wodociągowa rozdzielcza") == "water"


def test_normalize_network_type_value_sewage_variants() -> None:
    assert _normalize_network_type_value("kanalizacja sanitarna") == "sewage"


def test_normalize_network_type_value_unrecognized_returns_unknown() -> None:
    assert _normalize_network_type_value("coś zupełnie innego") == "unknown"


def test_classify_network_type_tries_multiple_attribute_names() -> None:
    # Weryfikuje, że różne nazwy atrybutów (rodzaj, typ, kind) są rozpoznawane —
    # różne powiaty mogą nazywać ten sam atrybut inaczej.
    assert _classify_network_type({"kind": "gazowa"})[0] == "gas"


# --- fetch_kiut_network_section (respx) ---------------------------------------


def _kiut_catalog(*, production: bool = True) -> DataSourceCatalog:
    """Katalog z potwierdzonym (lub nie) kontraktem WFS ``networks``."""
    entry = {
        "source_id": "kiut_gesut",
        "name": "KIUT/GESUT (kontrakt testowy)",
        "owner": "Przykładowy właściciel",
        "status": "production" if production else "contract_required",
        "production_ready": production,
        "contract_confirmed": production,
        "access_type": "wfs",
        "capabilities_url": f"{KIUT_TEST_WFS_URL}?service=WFS&request=GetCapabilities",
        "file_url": None,
        "type_names": ["kiut:SiecUzbrojenia"],
        "layers": None,
        "protocol_version": "2.0.0",
        "source_crs": "EPSG:2180",
        "target_crs": "EPSG:2180",
        "teryt_scope": ["*"],
        "license": "Licencja testowa.",
        "attribution": "Źródło: test.",
        "expected_update_interval": "not_published",
        "sla": "not_published",
        "last_manual_verification": date(2026, 9, 28) if production else None,
        "resources": [
            {
                "role": "networks",
                "access_type": "wfs",
                "url": KIUT_TEST_WFS_URL,
                "type_names": ["kiut:SiecUzbrojenia"],
                "protocol_version": "2.0.0",
                "source_crs": "EPSG:2180",
            }
        ],
    }
    return parse_catalog({"schema_version": "1.0", "sources": [entry]})


@pytest.mark.asyncio
@respx.mock
async def test_fetch_kiut_network_section_success_via_respx() -> None:
    respx.get(KIUT_TEST_WFS_URL).mock(
        return_value=httpx.Response(200, text=MOCK_GML_MULTIPLE_TYPES)
    )

    result = await fetch_kiut_network_section(BBOX, catalog=_kiut_catalog())

    assert isinstance(result, KiutNetworkSection)
    assert len(result.features) == 3
    assert result.relation == "features_found"
    assert result.status == "available"
    assert result.source_metadata.response_status == 200
    assert result.source_metadata.manual_review_required is True  # typ "unknown"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_kiut_network_section_empty_collection_is_checked_no_match() -> None:
    respx.get(KIUT_TEST_WFS_URL).mock(
        return_value=httpx.Response(200, text=EMPTY_GEOJSON)
    )

    result = await fetch_kiut_network_section(BBOX, catalog=_kiut_catalog())

    assert result.features == []
    assert result.relation == "no_match"
    assert result.source_metadata.source_url is not None
    assert result.source_metadata.confidence > 0


@pytest.mark.asyncio
@respx.mock
async def test_fetch_kiut_network_section_timeout_raises_unavailable() -> None:
    respx.get(KIUT_TEST_WFS_URL).mock(side_effect=httpx.TimeoutException("timeout"))

    with pytest.raises(KiutServiceUnavailableError) as exc_info:
        await fetch_kiut_network_section(BBOX, catalog=_kiut_catalog())

    assert exc_info.value.reason_code == REASON_SERVICE_TIMEOUT
    assert exc_info.value.source_metadata.response_status is None
    assert exc_info.value.source_metadata.confidence == 0.0


@pytest.mark.asyncio
@respx.mock
async def test_fetch_kiut_network_section_transport_error_raises_unavailable() -> None:
    respx.get(KIUT_TEST_WFS_URL).mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(KiutServiceUnavailableError) as exc_info:
        await fetch_kiut_network_section(BBOX, catalog=_kiut_catalog())

    assert exc_info.value.reason_code == REASON_TRANSPORT_ERROR


@pytest.mark.asyncio
@respx.mock
async def test_fetch_kiut_network_section_http_500_raises_unavailable() -> None:
    respx.get(KIUT_TEST_WFS_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(KiutServiceUnavailableError) as exc_info:
        await fetch_kiut_network_section(BBOX, catalog=_kiut_catalog())

    assert exc_info.value.reason_code == REASON_HTTP_ERROR
    assert exc_info.value.source_metadata.response_status == 500


@pytest.mark.asyncio
@respx.mock
async def test_fetch_kiut_network_section_malformed_body_raises_unavailable() -> None:
    respx.get(KIUT_TEST_WFS_URL).mock(
        return_value=httpx.Response(200, text="<not><valid")
    )

    with pytest.raises(KiutServiceUnavailableError) as exc_info:
        await fetch_kiut_network_section(BBOX, catalog=_kiut_catalog())

    assert exc_info.value.reason_code == REASON_MALFORMED_RESPONSE


@pytest.mark.asyncio
@respx.mock
async def test_fetch_kiut_network_section_sends_bbox_with_epsg2180() -> None:
    route = respx.get(KIUT_TEST_WFS_URL).mock(
        return_value=httpx.Response(200, text=MOCK_GML_MULTIPLE_TYPES)
    )

    await fetch_kiut_network_section(BBOX, catalog=_kiut_catalog())

    params = route.calls.last.request.url.params
    assert "EPSG:2180" in params["bbox"]
    assert params["typeNames"] == "kiut:SiecUzbrojenia"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_kiut_network_section_unconfirmed_source_sends_no_request() -> None:
    route = respx.get(KIUT_TEST_WFS_URL).mock(
        return_value=httpx.Response(200, text=MOCK_GML_MULTIPLE_TYPES)
    )

    with pytest.raises(KiutSourceNotRunnableError) as exc_info:
        await fetch_kiut_network_section(BBOX, catalog=_kiut_catalog(production=False))

    assert exc_info.value.reason_code == REASON_VECTOR_SOURCE_NOT_CONFIRMED
    assert route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_fetch_kiut_network_section_default_catalog_blocks_before_request() -> None:
    # Realny katalog: kiut_gesut ma status contract_required, więc guard musi
    # zablokować adapter zanim powstanie jakiekolwiek żądanie.
    route = respx.route().mock(return_value=httpx.Response(200, text=EMPTY_GEOJSON))

    with pytest.raises(KiutSourceNotRunnableError):
        await fetch_kiut_network_section(BBOX)

    assert route.call_count == 0
