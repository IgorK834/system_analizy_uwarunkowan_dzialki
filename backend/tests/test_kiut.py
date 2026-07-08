import httpx
import pytest
import respx

from app.core.settings import settings
from app.services.geometry import parse_parcel_geometry
from app.services.kiut import (
    NetworkFeature,
    _classify_network_type,
    _normalize_network_type_value,
    _parse_kiut_response,
    bbox_from_geometry,
    fetch_kiut_networks,
)

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


def test_parse_empty_string_returns_empty_list() -> None:
    result = _parse_kiut_response("", "https://example/kiut", None)

    assert result == []


def test_parse_malformed_gml_returns_empty_list_not_raises() -> None:
    result = _parse_kiut_response("<not><valid", "https://example/kiut", None)

    assert result == []


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


# --- fetch_kiut_networks (respx) ---------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_kiut_networks_success_via_respx() -> None:
    respx.get(settings.kiut_wfs_base_url).mock(
        return_value=httpx.Response(200, text=MOCK_GML_MULTIPLE_TYPES)
    )

    result = await fetch_kiut_networks((500000.0, 200000.0, 500100.0, 200100.0))

    assert len(result) == 3


@pytest.mark.asyncio
@respx.mock
async def test_fetch_kiut_networks_timeout_returns_empty_list_not_raises() -> None:
    respx.get(settings.kiut_wfs_base_url).mock(
        side_effect=httpx.TimeoutException("timeout")
    )

    result = await fetch_kiut_networks((0.0, 0.0, 1.0, 1.0))

    assert result == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_kiut_networks_http_500_returns_empty_list_not_raises() -> None:
    respx.get(settings.kiut_wfs_base_url).mock(return_value=httpx.Response(500))

    result = await fetch_kiut_networks((0.0, 0.0, 1.0, 1.0))

    assert result == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_kiut_networks_sends_bbox_with_epsg2180() -> None:
    route = respx.get(settings.kiut_wfs_base_url).mock(
        return_value=httpx.Response(200, text=MOCK_GML_MULTIPLE_TYPES)
    )

    await fetch_kiut_networks((500000.0, 200000.0, 500100.0, 200100.0))

    assert "EPSG:2180" in route.calls.last.request.url.params["bbox"]
