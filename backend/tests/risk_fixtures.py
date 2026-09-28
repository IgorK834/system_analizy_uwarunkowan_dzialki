"""Odpowiedzi WFS ISOK/GDOŚ do testów BK-303 (bez internetu).

Struktura odpowiada zamrożonym kontraktom z ``fixtures/source_contracts``:
ISOK — ``nz-core:HazardArea`` z ``qualitativeLikelihood`` i ``returnPeriod``;
GDOŚ — cechy warstw ``GDOS:*`` z ``gml:id`` i ``nazwa``. Współrzędne w
``srsName="EPSG:2180"`` są w kolejności (easting, northing).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import httpx
import respx

from app.core.settings import settings

EMPTY_COLLECTION = (
    '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
    'numberMatched="0" numberReturned="0"></wfs:FeatureCollection>'
)


@dataclass(frozen=True)
class Zone:
    feature_id: str
    bounds: tuple[float, float, float, float]
    likelihood: str | None = None
    return_period: str | None = None
    name: str | None = None


def _pos_list(bounds: tuple[float, float, float, float]) -> str:
    minx, miny, maxx, maxy = bounds
    return f"{minx} {miny} {maxx} {miny} {maxx} {maxy} {minx} {maxy} {minx} {miny}"


def isok_collection(*zones: Zone) -> str:
    members = []
    for zone in zones:
        attributes = ""
        if zone.likelihood is not None:
            attributes += f"<nz-core:qualitativeLikelihood>{zone.likelihood}</nz-core:qualitativeLikelihood>"
        if zone.return_period is not None:
            attributes += f"<nz-core:returnPeriod>{zone.return_period}</nz-core:returnPeriod>"
        members.append(
            f'<wfs:member><nz-core:HazardArea gml:id="{zone.feature_id}">{attributes}'
            '<nz-core:geometry><gml:Polygon srsName="EPSG:2180"><gml:exterior><gml:LinearRing>'
            f"<gml:posList>{_pos_list(zone.bounds)}</gml:posList>"
            "</gml:LinearRing></gml:exterior></gml:Polygon></nz-core:geometry>"
            "</nz-core:HazardArea></wfs:member>"
        )
    return (
        '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
        'xmlns:gml="http://www.opengis.net/gml/3.2" '
        'xmlns:nz-core="http://inspire.ec.europa.eu/schemas/nz-core/4.0">'
        + "".join(members)
        + "</wfs:FeatureCollection>"
    )


def gdos_collection(layer: str, *zones: Zone) -> str:
    members = []
    for zone in zones:
        name = f"<GDOS:nazwa>{zone.name}</GDOS:nazwa>" if zone.name else ""
        members.append(
            f'<wfs:member><GDOS:{layer} gml:id="{zone.feature_id}"><GDOS:gid>1</GDOS:gid>{name}'
            '<GDOS:geom><gml:MultiSurface srsName="EPSG:2180"><gml:surfaceMember><gml:Polygon>'
            f"<gml:exterior><gml:LinearRing><gml:posList>{_pos_list(zone.bounds)}</gml:posList>"
            "</gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface>"
            f"</GDOS:geom></GDOS:{layer}></wfs:member>"
        )
    return (
        '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
        'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:GDOS="https://sdi.gdos.gov.pl">'
        + "".join(members)
        + "</wfs:FeatureCollection>"
    )


def mock_isok(router: respx.Router, response: str | Exception) -> respx.Route:
    route = router.get(settings.isok_wfs_base_url)
    if isinstance(response, Exception):
        return route.mock(side_effect=response)
    return route.mock(return_value=httpx.Response(200, text=response))


def mock_gdos(
    router: respx.Router,
    layers: Mapping[str, str] | Exception,
) -> respx.Route:
    """Każda warstwa ``GDOS:*`` dostaje własną odpowiedź (domyślnie pustą)."""
    route = router.get(settings.gdos_wfs_base_url)
    if isinstance(layers, Exception):
        return route.mock(side_effect=layers)

    def respond(request: httpx.Request) -> httpx.Response:
        type_name = request.url.params["typeNames"]
        return httpx.Response(200, text=layers.get(type_name, EMPTY_COLLECTION))

    return route.mock(side_effect=respond)
