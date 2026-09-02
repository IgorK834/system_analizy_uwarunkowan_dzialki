"""Serwis ISOK — wykrywanie ryzyka powodziowego przez rzeczywiste przecięcie geometrii.

Ryzyko powodziowe jest twardym ograniczeniem inwestycyjnym, nie kosmetyczną
informacją poboczną — dlatego ten moduł traktujemy jako bezpieczeństwo-krytyczny,
w odróżnieniu np. od kiut.py.

W kiut.py fetch_kiut_networks celowo połyka timeout/błąd HTTP i zwraca [] —
to bezpieczne, bo brak danych o sieciach uzbrojenia to tylko utrata informacji
pomocniczej. Dla ryzyka powodziowego jest to NIEBEZPIECZNE: pusta lista
RiskFeature przy awarii usługi ISOK wygląda identycznie jak "sprawdzono, brak
zagrożenia", co jest fałszywym poczuciem bezpieczeństwa dla użytkownika
podejmującego decyzję inwestycyjną. Dlatego fetch_flood_risks musi odróżnić
"sprawdzono, brak stref w BBOX" (zwróć []) od "nie udało się sprawdzić"
(podnieś IsokServiceUnavailableError). Orchestrator kontekstu łapie ten wyjątek
i ustawia status sekcji na 'unavailable' zamiast HTTP 500.

Kontrakt usługi potwierdzono realnymi zapytaniami 2026-07-30 (fixtures
``tests/fixtures/source_contracts/isok_*``). Ustalenia, które kształtują ten
adapter:

1. **Endpoint.** Obowiązuje usługa INSPIRE PGW Wody Polskie
   ``.../INSPIRE_NZ_HY_MZPMRP_WFS``. Wcześniejszy adres ``wms.isok.gov.pl`` nie
   rozwiązuje się już w DNS, więc każde zapytanie kończyło się błędem połączenia.
2. **``srsName`` jest obowiązkowe i tylko w formie URN.** Domyślnym układem
   warstw jest EPSG:4258 (stopnie); bez wymuszenia EPSG:2180 geometria nigdy nie
   przecięłaby się z metryczną geometrią działki. Skrócona forma ``EPSG:2180``
   powoduje po stronie serwera HTTP 500, więc używamy ``urn:ogc:def:crs:EPSG::2180``.
3. **Klasa prawdopodobieństwa jest w ``nz-core:qualitativeLikelihood``** jako
   tekst "scenariusz Q 1% (raz na 100 lat)". Pola liczbowego
   ``probabilityOfOccurrence`` NIE używamy, bo w danych źródłowych jest
   niespójne (dla scenariusza 0,2% występują zarówno 0.002, jak i 0.02).
   Zapasem jest ``returnPeriod`` (10/100/500 lat), spójny z tekstem.

Zakres świadomie ograniczony do ``nz-core:HazardArea`` (obszary zagrożenia, MZP).
Warstwa ``nz-core:RiskZone`` opisuje ryzyko dla elementów narażonych (ok. 1,7 mln
cech) i odpowiada na inne pytanie niż "czy działka leży w obszarze zagrożenia",
a ``nz-core:ExposedElement`` to pojedyncze obiekty narażone. Mieszanie ich w
jednej sekcji zaciemniłoby znaczenie severity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Final

import httpx
from shapely.geometry.base import BaseGeometry

from app.core.settings import settings
from app.schemas.analyze import SourceMetadata
from app.services.gml import GmlFeature, GmlResponseError, parse_feature_collection
from app.services.kiut import bbox_from_geometry

logger = logging.getLogger(__name__)

# Realne odpowiedzi są duże: jedna cecha HazardArea to poligon o ok. 150 tys.
# wierzchołków, a odpowiedź dla małego BBOX-u działki osiągała 8-10 MB przy
# czasie odpowiedzi 6-9 s. Limit 10 s był więc na granicy timeoutu.
ISOK_TIMEOUT_S: Final[float] = 30.0

# Warstwa obszarów zagrożenia powodziowego (Mapy Zagrożenia Powodziowego).
ISOK_TYPE_NAME: Final[str] = "nz-core:HazardArea"

# Wyłącznie forma URN jest akceptowana przez serwer (skrót daje HTTP 500).
ISOK_SRS_NAME: Final[str] = "urn:ogc:def:crs:EPSG::2180"

# Domyślna, ostrożnościowa klasyfikacja severity gdy atrybut prawdopodobieństwa
# jest nierozpoznany albo brak go w danych źródłowych. Dla ryzyka bezpieczeństwa
# krytycznego lepiej ostrzec nadmiarowo niż zaniżyć ryzyko przy niepewnych danych.
# To założenie produktowe, nie twardy wymóg — zmiana decyzji jest jednolinijkowa.
UNKNOWN_PROBABILITY_SEVERITY: Final[str] = "medium"

# Nazwy atrybutów klasy prawdopodobieństwa, w kolejności zaufania. Pierwsza
# pozycja to realne pole INSPIRE usługi ISOK; pozostałe zachowano dla
# uproszczonych i starszych odpowiedzi WFS.
_KNOWN_PROBABILITY_ATTRIBUTES: Final[tuple[str, ...]] = (
    "qualitativelikelihood",
    "prawdopodobienstwo",
    "prawdopodobieństwo",
    "klasa_prawdopodobienstwa",
    "p",
    "probability",
)

# Sprawdzane w tej kolejności (od najbardziej specyficznych tokenów), żeby
# uniknąć fałszywych dopasowań substringów — np. token '1%' nie może dopasować
# się wewnątrz '10%', dlatego '10%' jest sprawdzane wcześniej niż '1%'.
_FLOOD_PROBABILITY_SEVERITY_RULES: Final[tuple[tuple[str, str], ...]] = (
    ("0,2%", "low"),
    ("0.2%", "low"),
    ("10%", "medium"),
    ("1%", "high"),
)

# Zapasowe odwzorowanie okresu powtarzalności na severity — spójne z regułami
# tekstowymi powyżej (10 lat = p10%, 100 lat = p1%, 500 lat = p0,2%).
_RETURN_PERIOD_SEVERITY: Final[dict[int, str]] = {10: "medium", 100: "high", 500: "low"}
_RETURN_PERIOD_ATTRIBUTE: Final[str] = "returnperiod"

# Próg poniżej którego przecięcie traktujemy jako czysto brzegowe (styk),
# nie powierzchniowe — działki dotykające granicy strefy mają matematycznie
# niezerowe, ale nieistotne pole przecięcia wynikające z precyzji Shapely.
_INTERSECTION_AREA_EPSILON_SQM: Final[float] = 1e-6


class IsokServiceUnavailableError(Exception):
    """
    Usługa ISOK nie odpowiedziała, zwróciła błąd HTTP, lub zwróciła odpowiedź
    niemożliwą do sparsowania.

    W odróżnieniu od KIUT, brak danych o ryzyku powodziowym NIE może być cicho
    zamieniony na pustą listę — patrz uzasadnienie w docstringu modułu.
    """


@dataclass(frozen=True)
class RiskFeature:
    """
    Pojedyncza strefa zagrożenia powodziowego przecinająca się z działką.

    Zawiera tylko strefy faktycznie stykające się lub nakładające z geometrią
    działki — strefy obecne w BBOX zapytania WFS, ale geometrycznie rozłączne
    z działką, nie są tu reprezentowane (patrz _build_risk_feature).
    """

    risk_type: str
    severity: str
    geometry: BaseGeometry
    intersection_area_sqm: float
    area_ratio: float
    probability_class: str | None
    source_metadata: SourceMetadata
    # Celowo lista, nie pojedynczy str — dla ryzyka powodziowego mogą wystąpić
    # jednocześnie np. boundary_touch i nierozpoznana klasa prawdopodobieństwa.
    warnings: list[str] = field(default_factory=list)


async def fetch_flood_risks(
    parcel_geometry: BaseGeometry,
    client: httpx.AsyncClient | None = None,
) -> list[RiskFeature]:
    """
    Wykrywa ryzyko powodziowe dla działki przez rzeczywiste przecięcie geometrii
    ze strefami zagrożenia powodziowego ISOK.

    Wejściem jest geometria działki w EPSG:2180 (nie BBOX — BBOX jest liczony
    wewnętrznie przez bbox_from_geometry). Funkcja wykrywa RZECZYWISTE
    przecięcie geometrii ze strefami zagrożenia, nie tylko obecność danych w
    BBOX zapytania WFS — strefa obecna w odpowiedzi usługi, ale geometrycznie
    rozłączna z działką, jest odrzucana.

    Pusta lista oznacza "sprawdzono, brak stref w sąsiedztwie działki". Błąd
    usługi, ``ows:ExceptionReport`` (usługi WFS zwracają go ze statusem HTTP
    200) lub nieparsowalna odpowiedź podnoszą IsokServiceUnavailableError —
    te przypadki NIGDY nie są mylone, w odróżnieniu od fetch_kiut_networks
    (patrz uzasadnienie w docstringu modułu), bo dla ryzyka powodziowego pusta
    lista przy awarii usługi byłaby fałszywym poczuciem bezpieczeństwa.

    WMS (settings.isok_wms_fallback_url) jest wyłącznie linkiem referencyjnym
    do ręcznej weryfikacji wizualnej w konfiguracji — nieużywanym jako aktywne
    źródło danych w tej funkcji. Analiza rastra WMS nie jest zaimplementowana.
    """
    minx, miny, maxx, maxy = bbox_from_geometry(parcel_geometry)
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": ISOK_TYPE_NAME,
        # Geometria musi wrócić w układzie metrycznym; bez tego byłaby w
        # stopniach (EPSG:4258 jest DefaultCRS warstwy) i nigdy nie przecięłaby
        # się z geometrią działki.
        "srsName": ISOK_SRS_NAME,
        # BBOX jest natomiast przyjmowany w kolejności (minE,minN,maxE,maxN),
        # odwrotnie do kolejności osi w zwracanej geometrii — patrz gml.py.
        "bbox": f"{minx},{miny},{maxx},{maxy},EPSG:2180",
    }

    if client is not None:
        response_text, source_url = await _fetch_isok_response_text(client, params)
    else:
        async with httpx.AsyncClient() as owned_client:
            response_text, source_url = await _fetch_isok_response_text(
                owned_client, params
            )

    fetched_at = datetime.now(timezone.utc)
    zone_features = _parse_zone_response(response_text)

    results: list[RiskFeature] = []
    for zone in zone_features:
        risk = _build_risk_feature(
            parcel_geometry, zone.geometry, zone.properties, source_url, fetched_at
        )
        if risk is not None:
            results.append(risk)
    return results


async def _fetch_isok_response_text(
    client: httpx.AsyncClient,
    params: dict[str, str],
) -> tuple[str, str]:
    try:
        response = await client.get(
            settings.isok_wfs_base_url,
            params=params,
            timeout=ISOK_TIMEOUT_S,
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise IsokServiceUnavailableError(
            "Usługa ISOK nie odpowiedziała w wymaganym czasie."
        ) from exc
    except httpx.HTTPError as exc:
        raise IsokServiceUnavailableError(f"Usługa ISOK zwróciła błąd: {exc}") from exc

    return response.text, str(response.url)


def _parse_zone_response(text: str) -> list[GmlFeature]:
    """Parsuje odpowiedź WFS na listę cech powierzchniowych w EPSG:2180.

    W odróżnieniu od kiut.py: błąd parsowania, nieoczekiwany układ współrzędnych
    oraz ``ows:ExceptionReport`` podnoszą wyjątek, nie zwracają [] — patrz
    critical_design_deviation w docstringu modułu.
    """
    try:
        return parse_feature_collection(text)
    except GmlResponseError as exc:
        raise IsokServiceUnavailableError(
            f"Nie udało się sparsować odpowiedzi ISOK: {exc}"
        ) from exc


def _severity_from_probability_text(value: str) -> str | None:
    lowered = value.lower().replace(" ", "")
    for token, severity in _FLOOD_PROBABILITY_SEVERITY_RULES:
        if token in lowered:
            return severity
    return None


def _severity_from_return_period(properties: dict) -> tuple[str | None, str | None]:
    """Zapasowa klasyfikacja po okresie powtarzalności (10/100/500 lat)."""
    for prop_key, value in properties.items():
        if prop_key.lower() != _RETURN_PERIOD_ATTRIBUTE:
            continue
        try:
            period = int(round(float(value)))
        except (TypeError, ValueError):
            continue
        severity = _RETURN_PERIOD_SEVERITY.get(period)
        if severity is not None:
            return f"raz na {period} lat", severity
    return None, None


def _classify_flood_probability(properties: dict) -> tuple[str | None, str, str | None]:
    """Zwraca (surowa_wartość_lub_None, severity, warning_lub_None)."""
    raw_value: str | None = None
    for key in _KNOWN_PROBABILITY_ATTRIBUTES:
        for prop_key, value in properties.items():
            if prop_key.lower() != key or not isinstance(value, str):
                continue
            if raw_value is None:
                raw_value = value
            severity = _severity_from_probability_text(value)
            if severity is not None:
                return value, severity, None

    period_label, period_severity = _severity_from_return_period(properties)
    if period_severity is not None:
        return raw_value or period_label, period_severity, None

    if raw_value is not None:
        return (
            raw_value,
            UNKNOWN_PROBABILITY_SEVERITY,
            f"Nierozpoznana klasa prawdopodobieństwa powodzi: {raw_value!r} — "
            f"przyjęto severity={UNKNOWN_PROBABILITY_SEVERITY} ostrożnościowo.",
        )
    return (
        None,
        UNKNOWN_PROBABILITY_SEVERITY,
        "Brak atrybutu klasy prawdopodobieństwa powodzi w danych źródłowych — "
        f"przyjęto severity={UNKNOWN_PROBABILITY_SEVERITY} ostrożnościowo.",
    )


def _build_risk_feature(
    parcel: BaseGeometry,
    zone: BaseGeometry,
    properties: dict,
    source_url: str,
    fetched_at: datetime,
) -> RiskFeature | None:
    """
    Zwraca None gdy strefa jest rozłączna z działką (rzeczywiste sprawdzenie
    geometryczne, nie tylko obecność w BBOX).

    Rozróżnia styk brzegowy (zerowe pole przecięcia) od rzeczywistego
    nakładania powierzchni — styk brzegowy dostaje severity='low' i warning
    'boundary_touch' NIEZALEŻNIE od klasy prawdopodobieństwa strefy, żeby nie
    sugerować fałszywie pełnego ryzyka tam, gdzie nie ma rzeczywistego
    nakładania powierzchni.
    """
    if not parcel.intersects(zone):
        return None

    intersection_geom = parcel.intersection(zone)
    intersection_area_sqm = intersection_geom.area
    parcel_area = parcel.area
    area_ratio = (intersection_area_sqm / parcel_area) if parcel_area > 0 else 0.0

    probability_class, probability_severity, probability_warning = (
        _classify_flood_probability(properties)
    )
    warnings: list[str] = []
    if probability_warning:
        warnings.append(probability_warning)

    if intersection_area_sqm < _INTERSECTION_AREA_EPSILON_SQM:
        # Styk brzegowy nadpisuje severity z klasyfikacji prawdopodobieństwa —
        # inaczej użytkownik zobaczyłby np. severity='high' dla działki, która
        # w rzeczywistości tylko dotyka granicy strefy zagrożenia.
        severity = "low"
        warnings.append(
            "boundary_touch: działka styka się z granicą strefy zagrożenia "
            "powodziowego, bez rzeczywistego nakładania powierzchni — nie "
            "traktuj jako pełnego ryzyka."
        )
    else:
        severity = probability_severity

    return RiskFeature(
        risk_type="flood",
        severity=severity,
        geometry=intersection_geom,
        intersection_area_sqm=intersection_area_sqm,
        area_ratio=area_ratio,
        probability_class=probability_class,
        source_metadata=SourceMetadata(
            source_name="ISOK",
            source_url=source_url,
            fetched_at=fetched_at,
            confidence=0.85 if probability_class else 0.4,
            manual_review_required=(probability_class is None),
        ),
        warnings=warnings,
    )
