import io
import json
import socket
import zipfile

import httpx
import pytest
import respx

from app.services.pog_fetch import (
    PogVectorSecurityError,
    _extract_vector_entries_from_zip,
    fetch_pog_vector_data,
)

PUBLIC_URL = "https://bip.example.test/pog.gml"
ZIP_URL = "https://bip.example.test/pog.zip"
HTML_URL = "https://bip.example.test/pog"

GML = b"""<?xml version="1.0" encoding="UTF-8"?>
<gml:FeatureCollection xmlns:gml="http://www.opengis.net/gml/3.2"
 xmlns:app="urn:example:app">
  <gml:featureMember>
    <app:AktPlanowaniaPrzestrzennego>
      <app:numerUchwaly>X/20/2026</app:numerUchwaly>
      <app:data>2026-02-10</app:data>
    </app:AktPlanowaniaPrzestrzennego>
  </gml:featureMember>
  <gml:featureMember>
    <app:StrefaPlanistyczna>
      <app:zone_type>SJ</app:zone_type>
      <app:geometria><gml:Polygon srsName="EPSG:2180">
        <gml:exterior><gml:LinearRing><gml:posList>
          500000 200000 500100 200000 500100 200100 500000 200100 500000 200000
        </gml:posList></gml:LinearRing></gml:exterior>
      </gml:Polygon></app:geometria>
    </app:StrefaPlanistyczna>
  </gml:featureMember>
  <gml:featureMember>
    <app:ObszarUzupelnieniaZabudowy>
      <app:geometria><gml:Polygon srsName="EPSG:2180">
        <gml:exterior><gml:LinearRing><gml:posList>
          500000 200000 500050 200000 500050 200100 500000 200100 500000 200000
        </gml:posList></gml:LinearRing></gml:exterior>
      </gml:Polygon></app:geometria>
    </app:ObszarUzupelnieniaZabudowy>
  </gml:featureMember>
  <gml:featureMember>
    <app:ObszarZabudowySrodmiejskiej>
      <app:geometria><gml:Polygon srsName="EPSG:2180">
        <gml:exterior><gml:LinearRing><gml:posList>
          500000 200000 500020 200000 500020 200020 500000 200020 500000 200000
        </gml:posList></gml:LinearRing></gml:exterior>
      </gml:Polygon></app:geometria>
    </app:ObszarZabudowySrodmiejskiej>
  </gml:featureMember>
</gml:FeatureCollection>
"""


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))
        ],
    )


def make_zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.asyncio
@respx.mock
async def test_gml_app_layers_are_parsed_in_epsg_2180() -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/gml+xml"},
            content=GML,
        )
    )

    result = await fetch_pog_vector_data([PUBLIC_URL])

    assert result.status == "available"
    assert result.wms_fallback_required is False
    assert len(result.planning_zones) == 1
    assert len(result.ouz_areas) == 1
    assert len(result.downtown_areas) == 1
    assert result.planning_zones[0].geometry.area == pytest.approx(10_000.0)
    assert result.planning_zones[0].source_crs == "EPSG:2180"
    assert result.app_metadata["numerUchwaly"] == "X/20/2026"
    assert result.source_metadata.manual_review_required is True


@pytest.mark.asyncio
@respx.mock
async def test_zip_with_gml_is_parsed_only_in_memory() -> None:
    respx.get(ZIP_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/zip"},
            content=make_zip([("app/pog.gml", GML)]),
        )
    )

    result = await fetch_pog_vector_data([ZIP_URL])

    assert len(result.planning_zones) == 1
    assert len(result.ouz_areas) == 1


def test_zip_slip_path_is_rejected() -> None:
    malicious = make_zip([("../../evil.gml", GML)])

    with pytest.raises(PogVectorSecurityError):
        _extract_vector_entries_from_zip(malicious)


@pytest.mark.asyncio
@respx.mock
async def test_zip_slip_returns_controlled_fallback_from_public_function() -> None:
    respx.get(ZIP_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/zip"},
            content=make_zip([("../evil.gml", GML)]),
        )
    )

    result = await fetch_pog_vector_data([ZIP_URL])

    assert result.status == "wms_fallback_required"
    assert result.wms_fallback_required is True
    assert any(
        warning.code == "POG_VECTOR_SECURITY_BLOCK" for warning in result.warnings
    )


@pytest.mark.asyncio
@respx.mock
async def test_geojson_wgs84_is_transformed_to_epsg_2180() -> None:
    geojson_url = "https://bip.example.test/pog.geojson"
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"layer_type": "planning_zone", "zone_type": "SJ"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[19.0, 52.0], [19.001, 52.0], [19.001, 52.001], [19.0, 52.0]]
                    ],
                },
            }
        ],
    }
    respx.get(geojson_url).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/geo+json"},
            content=json.dumps(geojson).encode(),
        )
    )

    result = await fetch_pog_vector_data([geojson_url])

    geometry = result.planning_zones[0].geometry
    assert 300_000 < geometry.centroid.x < 800_000
    assert 100_000 < geometry.centroid.y < 900_000
    assert result.planning_zones[0].source_crs == "EPSG:4326"
    assert any(warning.code == "POG_GEOJSON_CRS_ASSUMED" for warning in result.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_html_link_to_gml_is_followed() -> None:
    respx.get(HTML_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b'<html><a href="/pog.gml">APP GML</a></html>',
        )
    )
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/xml"},
            content=GML,
        )
    )

    result = await fetch_pog_vector_data([HTML_URL])

    assert len(result.planning_zones) == 1
    assert result.source_metadata.source_url == PUBLIC_URL


@pytest.mark.asyncio
@respx.mock
async def test_corrupted_gml_returns_manual_wms_fallback() -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/xml"},
            content=b"<broken",
        )
    )

    result = await fetch_pog_vector_data([PUBLIC_URL])

    assert result.status == "wms_fallback_required"
    assert result.source_metadata.manual_review_required is True
    assert any(warning.code == "POG_VECTOR_UNAVAILABLE" for warning in result.warnings)


@pytest.mark.asyncio
async def test_no_links_returns_wms_fallback_without_exception() -> None:
    result = await fetch_pog_vector_data([])

    assert result.status == "wms_fallback_required"
    assert result.planning_zones == []
    assert result.ouz_areas == []
    assert result.source_metadata.source_url is None
