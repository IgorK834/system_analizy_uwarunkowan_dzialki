"""Serwis GDOŚ — formy ochrony przyrody przez przecięcie geometrii.

Formy ochrony przyrody mogą stanowić twarde ograniczenie inwestycyjne. Awaria
usługi nie może więc wyglądać tak samo jak poprawnie sprawdzony brak kolizji.
Pusta lista oznacza wyłącznie "sprawdzono, brak przecięcia", natomiast timeout,
błąd HTTP lub nieparsowalna odpowiedź podnoszą GdosServiceUnavailableError.
Orchestrator kontekstu zamienia ten wyjątek na niedostępność sekcji i
ostrzeżenie, zachowując częściowe wyniki pozostałych usług.

Kontrakt usługi potwierdzono realnymi zapytaniami 2026-07-30
(GetCapabilities + DescribeFeatureType + GetFeature, fixtures
``tests/fixtures/source_contracts/gdos_*``). Trzy ustalenia zmieniają kształt
tego adaptera w stosunku do naiwnej implementacji:

1. **``typeNames`` jest obowiązkowe.** Bez niego usługa zwraca
   ``ows:ExceptionReport`` ze statusem HTTP **200**, co bez jawnego rozpoznania
   wyglądałoby jak poprawna odpowiedź bez cech, czyli jak brak form ochrony
   przyrody na działce.
2. **Jedna warstwa na zapytanie.** Podanie kilku warstw naraz kończy się
   błędem ``Join filter inconsistent with regard to feature types``, dlatego
   każda warstwa jest odpytywana osobnym żądaniem, równolegle.
3. **Rodzaj ochrony wynika z warstwy, nie z atrybutów cechy.** Schemat warstw
   nie zawiera żadnego pola typu ``forma_ochrony`` — są wyłącznie ``gid``,
   ``nazwa``, ``kodinspire`` (czasem ``kod``) i geometria. Klasyfikacja po
   atrybutach dałaby więc dla realnych danych zawsze ``unknown``.

Od Task 23.6 adapter obsługuje również pomniki punktowe oraz warstwy Ramsar,
korytarzy ekologicznych i elektrowni wiatrowych. Trzy ostatnie są oznaczane
``informational_only``: aplikacja podaje wyłącznie relację geometryczną i nie
wyznacza z niej strefy prawnej. Warstwy proponowanych i konsultowanych zmian
Natura 2000 oraz Mezoregiony pozostają poza zakresem.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, replace, field
from datetime import datetime, timezone
from typing import Final

import httpx
from shapely.geometry.base import BaseGeometry

from app.core.settings import settings
from app.modules.analysis.domain.environment import EnvironmentalProximity
from app.schemas.analyze import SourceMetadata
from app.services.dataset_dates import aggregate_dataset_dates, response_dataset_date
from app.services.gml import GmlFeature, GmlResponseError, parse_feature_collection
from app.services.kiut import bbox_from_geometry

logger = logging.getLogger(__name__)

# Odpowiedź dla jednej warstwy bywa duża — pojedyncza cecha "Kampinoski Park
# Narodowy" to ok. 0,9 MB GML-a (229 poligonów). Limit 10 s okazał się w realnych
# pomiarach zbyt ciasny, gdy działka leży w dużym obszarze chronionym.
GDOS_TIMEOUT_S: Final[float] = 20.0
GDOS_MAX_RESPONSE_BYTES: Final[int] = 16 * 1024 * 1024
GDOS_MAX_ATTEMPTS: Final[int] = 2
GDOS_DEFAULT_SEARCH_RADIUS_M: Final[float] = 1_000.0
GDOS_MONUMENT_SEARCH_RADIUS_M: Final[float] = 200.0
_GDOS_ALLOWED_HOSTS: Final[frozenset[str]] = frozenset({"sdi.gdos.gov.pl"})

# Warstwy WFS odpytywane dla działki: typeName -> znormalizowany rodzaj ochrony.
# Kolejność jest kolejnością malejącej istotności ograniczenia i jednocześnie
# kolejnością prezentacji wyników.
GDOS_PROTECTION_LAYERS: Final[dict[str, str]] = {
    "GDOS:ParkiNarodowe": "park_narodowy",
    "GDOS:Rezerwaty": "rezerwat_przyrody",
    "GDOS:ObszarySpecjalnejOchrony": "natura2000",
    "GDOS:SpecjalneObszaryOchrony": "natura2000",
    "GDOS:ParkiKrajobrazowe": "park_krajobrazowy",
    "GDOS:ObszaryChronionegoKrajobrazu": "obszar_chronionego_krajobrazu",
    "GDOS:UzytkiEkologiczne": "uzytek_ekologiczny",
    "GDOS:ZespolyPrzyrodniczoKrajobrazowe": "zespol_przyrodniczo_krajobrazowy",
    "GDOS:StanowiskaDokumentacyjne": "stanowisko_dokumentacyjne",
    "GDOS:PomnikiPrzyrodyPowierzchniowe": "pomnik_przyrody",
    "GDOS:PomnikiPrzyrodyPunktowe": "pomnik_przyrody",
    "GDOS:ramsar": "obszar_ramsar",
    "GDOS:korytarzeEkologiczne": "korytarz_ekologiczny",
    "GDOS:ElektrownieWiatrowe": "elektrownia_wiatrowa",
}

_GDOS_LAYER_LABELS: Final[dict[str, str]] = {
    "GDOS:ParkiNarodowe": "Parki narodowe",
    "GDOS:Rezerwaty": "Rezerwaty przyrody",
    "GDOS:ObszarySpecjalnejOchrony": "Natura 2000 — obszary specjalnej ochrony ptaków",
    "GDOS:SpecjalneObszaryOchrony": "Natura 2000 — specjalne obszary ochrony siedlisk",
    "GDOS:ParkiKrajobrazowe": "Parki krajobrazowe",
    "GDOS:ObszaryChronionegoKrajobrazu": "Obszary chronionego krajobrazu",
    "GDOS:UzytkiEkologiczne": "Użytki ekologiczne",
    "GDOS:ZespolyPrzyrodniczoKrajobrazowe": "Zespoły przyrodniczo-krajobrazowe",
    "GDOS:StanowiskaDokumentacyjne": "Stanowiska dokumentacyjne",
    "GDOS:PomnikiPrzyrodyPowierzchniowe": "Pomniki przyrody (powierzchniowe)",
    "GDOS:PomnikiPrzyrodyPunktowe": "Pomniki przyrody (punktowe)",
    "GDOS:ramsar": "Obszary Ramsar",
    "GDOS:korytarzeEkologiczne": "Korytarze ekologiczne",
    "GDOS:ElektrownieWiatrowe": "Elektrownie wiatrowe",
}

_GDOS_INFORMATIONAL_LAYERS: Final[frozenset[str]] = frozenset(
    {"GDOS:ramsar", "GDOS:korytarzeEkologiczne", "GDOS:ElektrownieWiatrowe"}
)

# Odpowiedź identyfikuje warstwę lokalną nazwą elementu cechy (bez prefiksu
# przestrzeni nazw), dlatego klasyfikacja korzysta z klucza bez ``GDOS:``.
_LAYER_LOCAL_NAME_TO_TYPE: Final[dict[str, str]] = {
    type_name.split(":")[-1]: protection_type for type_name, protection_type in GDOS_PROTECTION_LAYERS.items()
}

_INTERSECTION_AREA_EPSILON_SQM: Final[float] = 1e-6
_FULL_COVERAGE_RATIO_THRESHOLD: Final[float] = 0.999
_RATIO_HIGH_THRESHOLD: Final[float] = 0.5
_RATIO_MEDIUM_THRESHOLD: Final[float] = 0.1

# Rezerwaty i parki narodowe mają praktycznie całkowity zakaz zabudowy nawet
# przy małym przecięciu. To założenie produktowe można skorygować w jednej stałej.
_ALWAYS_HIGH_PROTECTION_TYPES: Final[frozenset[str]] = frozenset({"rezerwat_przyrody", "park_narodowy"})

# Atrybutowa klasyfikacja rodzaju ochrony jest ścieżką zapasową, używaną tylko
# gdy odpowiedź nie pozwala ustalić warstwy (np. GeoJSON bez nazwy typu cechy).
# W realnym WFS GDOŚ takich atrybutów nie ma — patrz docstring modułu.
_KNOWN_PROTECTION_TYPE_ATTRIBUTES: Final[tuple[str, ...]] = (
    "forma_ochrony",
    "typ_ochrony",
    "kategoria",
    "typ",
    "rodzaj",
)
_KNOWN_NAME_ATTRIBUTES: Final[tuple[str, ...]] = (
    "nazwa",
    "nazwa_przedsiewziecia",
    "name",
    "nazwa_obszaru",
)

# Wartości atrybutów bywają pełnymi opisami, dlatego dopasowanie działa przez
# podłańcuch na tekście znormalizowanym do małych liter i pojedynczych spacji.
_PROTECTION_TYPE_KEYWORDS: Final[dict[str, str]] = {
    "natura 2000": "natura2000",
    "natura2000": "natura2000",
    "rezerwat": "rezerwat_przyrody",
    "park narodowy": "park_narodowy",
    "park krajobrazowy": "park_krajobrazowy",
    "obszar chronionego krajobrazu": "obszar_chronionego_krajobrazu",
    "pomnik przyrody": "pomnik_przyrody",
    "uzytek ekologiczny": "uzytek_ekologiczny",
    "użytek ekologiczny": "uzytek_ekologiczny",
}


GDOS_SOURCE_NAME: Final[str] = "GDOŚ"
GDOS_SOURCE_ID: Final[str] = "gdos"
REASON_TIMEOUT: Final[str] = "SERVICE_TIMEOUT"
REASON_HTTP_ERROR: Final[str] = "SERVICE_HTTP_ERROR"
REASON_INVALID_RESPONSE: Final[str] = "INVALID_RESPONSE"


class GdosServiceUnavailableError(Exception):
    """Usługa GDOŚ jest niedostępna albo zwróciła nieparsowalną odpowiedź.

    Analogicznie do IsokServiceUnavailableError brak danych o formach ochrony
    przyrody nie może być cicho zamieniony na pustą listę. Wyjątek niesie kod
    przyczyny i provenance nieudanej próby (BK-303).
    """

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = REASON_INVALID_RESPONSE,
        source_metadata: SourceMetadata | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.source_metadata = source_metadata


@dataclass(frozen=True)
class NatureProtectionFeature:
    """Forma ochrony przyrody przecinająca się z geometrią działki."""

    protection_type: str
    name: str | None
    geometry: BaseGeometry
    intersection_area_sqm: float
    area_ratio: float
    severity: str
    source_metadata: SourceMetadata
    warnings: list[str] = field(default_factory=list)
    # Unikalny identyfikator obiektu ``gml:id`` formy ochrony (BK-303).
    feature_id: str | None = None
    # Styk granicy bez wspólnej powierzchni (pole przecięcia < epsilon).
    touches_boundary: bool = False
    distance_m: float = 0.0
    proximity_status: str = "intersection"
    search_radius_m: float = GDOS_DEFAULT_SEARCH_RADIUS_M
    layer_name: str | None = None
    informational_only: bool = False


@dataclass(frozen=True)
class NatureProtectionSection:
    """Wynik sprawdzenia wszystkich warstw GDOŚ z provenance całej sekcji."""

    features: list[NatureProtectionFeature]
    source_metadata: SourceMetadata
    proximities: list[EnvironmentalProximity] = field(default_factory=list)


async def fetch_nature_protection_areas(
    parcel_geometry: BaseGeometry,
    client: httpx.AsyncClient | None = None,
) -> list[NatureProtectionFeature]:
    """Wykrywa rzeczywiste przecięcia działki z formami ochrony przyrody.

    Geometria wejściowa musi być w EPSG:2180, aby pola przecięć i udział
    powierzchni miały znaczenie metryczne. Pusta lista oznacza "sprawdzono,
    brak przecięcia", a nie brak sekcji.

    Każda warstwa z ``GDOS_PROTECTION_LAYERS`` jest odpytywana osobnym,
    równoległym żądaniem (usługa nie przyjmuje wielu warstw w jednym zapytaniu).
    Awaria dowolnej warstwy podnosi GdosServiceUnavailableError i NIE jest
    zamieniana na częściowy wynik: gdyby jedna kategoria ochrony nie została
    sprawdzona, wynik "brak kolizji" byłby nieprawdziwy.
    """
    return (await fetch_nature_protection_section(parcel_geometry, client)).features


async def fetch_nature_protection_section(
    parcel_geometry: BaseGeometry,
    client: httpx.AsyncClient | None = None,
) -> NatureProtectionSection:
    """Jak ``fetch_nature_protection_areas``, z provenance sekcji (BK-303).

    Cechy o tym samym ``gml:id`` są liczone raz. Nakładające się formy ochrony
    pozostają osobnymi obiektami — ich udziały nie są sumowane.
    """
    if client is not None:
        return await _fetch_all_layers(parcel_geometry, client)

    async with httpx.AsyncClient() as owned_client:
        return await _fetch_all_layers(parcel_geometry, owned_client)


async def _fetch_all_layers(
    parcel_geometry: BaseGeometry,
    client: httpx.AsyncClient,
) -> NatureProtectionSection:
    fetched_at = datetime.now(timezone.utc)
    requests = [
        (
            type_name,
            _search_radius(type_name),
            _expanded_bbox(parcel_geometry, _search_radius(type_name)),
        )
        for type_name in GDOS_PROTECTION_LAYERS
    ]
    per_layer = await asyncio.gather(*(_fetch_layer(client, type_name, bbox) for type_name, _radius, bbox in requests))

    results: list[NatureProtectionFeature] = []
    proximities: list[EnvironmentalProximity] = []
    seen_ids: set[tuple[str, str]] = set()
    layer_digests: list[str] = []
    for (type_name, radius, _bbox), (features, source_url, digest, dates) in zip(requests, per_layer):
        layer_digests.append(f"{type_name}:{digest}")
        default_type = GDOS_PROTECTION_LAYERS[type_name]
        candidates: list[NatureProtectionFeature] = []
        for gml_feature in features:
            if gml_feature.feature_id is not None:
                identity = (type_name, gml_feature.feature_id)
                if identity in seen_ids:
                    continue
                seen_ids.add(identity)
            feature = _build_nature_protection_feature(
                parcel_geometry,
                gml_feature.geometry,
                gml_feature.properties,
                source_url,
                fetched_at,
                protection_type=_resolve_protection_type(gml_feature, default_type),
                feature_id=gml_feature.feature_id,
                include_near=True,
                search_radius_m=radius,
                layer_name=type_name,
                informational_only=type_name in _GDOS_INFORMATIONAL_LAYERS,
            )
            if feature is not None:
                feature = replace(feature, source_metadata=feature.source_metadata.model_copy(update=dates))
                candidates.append(feature)
        intersecting = [item for item in candidates if item.proximity_status == "intersection"]
        nearest = min(candidates, key=lambda item: item.distance_m) if candidates else None
        results.extend(intersecting or ([nearest] if nearest is not None else []))
        proximities.append(
            EnvironmentalProximity(
                category=default_type,
                label=_GDOS_LAYER_LABELS[type_name],
                layer_name=type_name,
                status=(nearest.proximity_status if nearest is not None else "none_within_radius"),  # type: ignore[arg-type]
                search_radius_m=radius,
                distance_m=nearest.distance_m if nearest is not None else None,
                feature_id=nearest.feature_id if nearest is not None else None,
                name=nearest.name if nearest is not None else None,
                informational_only=type_name in _GDOS_INFORMATIONAL_LAYERS,
            )
        )
    # Skrót sekcji: SHA-256 z uporządkowanej listy skrótów odpowiedzi warstw.
    section_digest = hashlib.sha256("\n".join(layer_digests).encode("utf-8")).hexdigest()
    return NatureProtectionSection(
        features=results,
        source_metadata=_section_source(
            str(httpx.URL(settings.gdos_wfs_base_url, params={"search": "parcel-buffer"})),
            fetched_at,
            section_digest,
        ).model_copy(update=aggregate_dataset_dates(item[3] for item in per_layer)),
        proximities=proximities,
    )


def _search_radius(type_name: str) -> float:
    return (
        GDOS_MONUMENT_SEARCH_RADIUS_M
        if type_name in {"GDOS:PomnikiPrzyrodyPowierzchniowe", "GDOS:PomnikiPrzyrodyPunktowe"}
        else GDOS_DEFAULT_SEARCH_RADIUS_M
    )


def _expanded_bbox(parcel_geometry: BaseGeometry, radius_m: float) -> str:
    minx, miny, maxx, maxy = bbox_from_geometry(parcel_geometry)
    return f"{minx - radius_m},{miny - radius_m},{maxx + radius_m},{maxy + radius_m},EPSG:2180"


def _section_source(
    source_url: str,
    fetched_at: datetime,
    artifact_sha256: str | None,
    *,
    failed: bool = False,
    response_status: int | None = 200,
) -> SourceMetadata:
    """Provenance sprawdzenia wszystkich warstw GDOŚ — także bez przecięć."""
    return SourceMetadata(
        source_id=GDOS_SOURCE_ID,
        source_name=GDOS_SOURCE_NAME,
        source_version=f"WFS 2.0.0; {len(GDOS_PROTECTION_LAYERS)} warstw GDOŚ",
        source_url=source_url,
        fetched_at=fetched_at,
        response_status=response_status,
        artifact_sha256=artifact_sha256,
        confidence=0.0 if failed else 0.85,
        manual_review_required=failed,
    )


def _resolve_protection_type(gml_feature: GmlFeature, default_type: str) -> str | None:
    """Ustala rodzaj ochrony na podstawie warstwy, z której pochodzi cecha.

    Zwrócenie ``None`` oznacza "nie wiadomo z warstwy" i uruchamia zapasową
    klasyfikację po atrybutach w ``_build_nature_protection_feature``.
    """
    if not gml_feature.layer:
        return None
    return _LAYER_LOCAL_NAME_TO_TYPE.get(gml_feature.layer, default_type)


async def _fetch_layer(
    client: httpx.AsyncClient,
    type_name: str,
    bbox: str,
) -> tuple[list[GmlFeature], str, str, dict]:
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": type_name,
        "bbox": bbox,
    }
    response_text, source_url, dates = await _fetch_gdos_response_text(client, params)
    digest = hashlib.sha256(response_text.encode("utf-8")).hexdigest()
    failure = _section_source(source_url, datetime.now(timezone.utc), digest, failed=True)
    return _parse_layer_response(response_text, type_name, failure), source_url, digest, dates


async def _fetch_gdos_response_text(
    client: httpx.AsyncClient,
    params: dict[str, str],
) -> tuple[str, str, dict]:
    request_url = str(httpx.URL(settings.gdos_wfs_base_url, params=params))
    attempted_at = datetime.now(timezone.utc)
    parsed = httpx.URL(settings.gdos_wfs_base_url)
    if parsed.scheme != "https" or parsed.host not in _GDOS_ALLOWED_HOSTS:
        raise GdosServiceUnavailableError(
            "Adres usługi GDOŚ nie należy do allowlisty HTTPS.",
            reason_code=REASON_INVALID_RESPONSE,
            source_metadata=_section_source(request_url, attempted_at, None, failed=True, response_status=None),
        )

    last_error: httpx.HTTPError | None = None
    for attempt in range(GDOS_MAX_ATTEMPTS):
        try:
            response = await client.get(
                settings.gdos_wfs_base_url,
                params=params,
                timeout=GDOS_TIMEOUT_S,
            )
            response.raise_for_status()
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > GDOS_MAX_RESPONSE_BYTES:
                raise GdosServiceUnavailableError(
                    f"Odpowiedź GDOŚ przekracza limit {GDOS_MAX_RESPONSE_BYTES} bajtów.",
                    reason_code=REASON_INVALID_RESPONSE,
                    source_metadata=_section_source(
                        str(response.url), attempted_at, None, failed=True, response_status=response.status_code
                    ),
                )
            if len(response.content) > GDOS_MAX_RESPONSE_BYTES:
                raise GdosServiceUnavailableError(
                    f"Odpowiedź GDOŚ przekracza limit {GDOS_MAX_RESPONSE_BYTES} bajtów.",
                    reason_code=REASON_INVALID_RESPONSE,
                    source_metadata=_section_source(
                        str(response.url), attempted_at, None, failed=True, response_status=response.status_code
                    ),
                )
            return response.text, str(response.url), response_dataset_date(response)
        except GdosServiceUnavailableError:
            raise
        except httpx.TimeoutException as exc:
            last_error = exc
            if attempt + 1 < GDOS_MAX_ATTEMPTS:
                await asyncio.sleep(0.2 * (attempt + 1))
                continue
            raise GdosServiceUnavailableError(
                "Usługa GDOŚ nie odpowiedziała w wymaganym czasie.",
                reason_code=REASON_TIMEOUT,
                source_metadata=_section_source(request_url, attempted_at, None, failed=True, response_status=None),
            ) from exc
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code in {429, 502, 503, 504} and attempt + 1 < GDOS_MAX_ATTEMPTS:
                await asyncio.sleep(0.2 * (attempt + 1))
                continue
            raise GdosServiceUnavailableError(
                f"Usługa GDOŚ zwróciła błąd: {exc}",
                reason_code=REASON_HTTP_ERROR,
                source_metadata=_section_source(
                    request_url, attempted_at, None, failed=True, response_status=exc.response.status_code
                ),
            ) from exc
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt + 1 < GDOS_MAX_ATTEMPTS:
                await asyncio.sleep(0.2 * (attempt + 1))
                continue
            raise GdosServiceUnavailableError(
                f"Usługa GDOŚ zwróciła błąd: {exc}",
                reason_code=REASON_HTTP_ERROR,
                source_metadata=_section_source(request_url, attempted_at, None, failed=True, response_status=None),
            ) from exc
    assert last_error is not None  # pragma: no cover
    raise last_error


def _parse_layer_response(
    text: str,
    type_name: str = "",
    failure_source: SourceMetadata | None = None,
) -> list[GmlFeature]:
    """Parsuje odpowiedź jednej warstwy WFS.

    Pusta lista jest zarezerwowana dla potwierdzonego braku cech w BBOX.
    Uszkodzona odpowiedź oraz ``ows:ExceptionReport`` (który usługa zwraca ze
    statusem HTTP 200) muszą pozostać odróżnialne dla orchestratora, dlatego
    podnoszą GdosServiceUnavailableError.
    """
    if not text.strip():
        raise GdosServiceUnavailableError(
            f"Pusta odpowiedź GDOŚ{f' (warstwa {type_name})' if type_name else ''} nie potwierdza braku obiektów.",
            reason_code=REASON_INVALID_RESPONSE,
            source_metadata=failure_source,
        )
    try:
        return parse_feature_collection(text)
    except GmlResponseError as exc:
        layer_info = f" (warstwa {type_name})" if type_name else ""
        raise GdosServiceUnavailableError(
            f"Nie udało się sparsować odpowiedzi GDOŚ{layer_info}: {exc}",
            reason_code=REASON_INVALID_RESPONSE,
            source_metadata=failure_source,
        ) from exc


def _normalize_protection_type_value(raw_value: str) -> str:
    lowered = " ".join(raw_value.lower().split())
    for keyword, protection_type in _PROTECTION_TYPE_KEYWORDS.items():
        if keyword in lowered:
            return protection_type
    return "unknown"


def _classify_protection_type(properties: dict) -> tuple[str, str | None]:
    for key in _KNOWN_PROTECTION_TYPE_ATTRIBUTES:
        for prop_key, value in properties.items():
            if prop_key.lower() == key and isinstance(value, str):
                normalized = _normalize_protection_type_value(value)
                if normalized != "unknown":
                    return normalized, None
    return (
        "unknown",
        "Nie rozpoznano typu formy ochrony przyrody na podstawie dostępnych atrybutów — oznaczono jako unknown.",
    )


def _extract_name(properties: dict) -> str | None:
    for key in _KNOWN_NAME_ATTRIBUTES:
        for prop_key, value in properties.items():
            if prop_key.lower() == key and isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _ratio_based_severity(area_ratio: float) -> str:
    if area_ratio >= _RATIO_HIGH_THRESHOLD:
        return "high"
    if area_ratio >= _RATIO_MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _classify_severity(protection_type: str, area_ratio: float) -> tuple[str, str | None]:
    """Zwraca severity oraz opcjonalne ostrzeżenie o sposobie oszacowania.

    Pełne pokrycie zawsze daje ``high``. Rezerwaty przyrody i parki narodowe
    dają ``high`` nawet przy małym udziale z powodu praktycznie całkowitego
    zakazu zabudowy. Pozostałe i nieznane typy skalują wynik przez area_ratio.
    """
    if area_ratio >= _FULL_COVERAGE_RATIO_THRESHOLD:
        return "high", None
    if protection_type in _ALWAYS_HIGH_PROTECTION_TYPES:
        return "high", None

    severity = _ratio_based_severity(area_ratio)
    if protection_type == "unknown":
        return severity, (
            "Nieznany typ formy ochrony przyrody — severity oszacowano "
            "konserwatywnie na podstawie powierzchni przecięcia (area_ratio)."
        )
    return severity, None


def _build_nature_protection_feature(
    parcel: BaseGeometry,
    zone: BaseGeometry,
    properties: dict,
    source_url: str,
    fetched_at: datetime,
    protection_type: str | None = None,
    feature_id: str | None = None,
    *,
    include_near: bool = False,
    search_radius_m: float = GDOS_DEFAULT_SEARCH_RADIUS_M,
    layer_name: str | None = None,
    informational_only: bool = False,
) -> NatureProtectionFeature | None:
    """Buduje wynik dla jednej formy ochrony albo ``None`` przy braku przecięcia.

    ``protection_type`` przekazany jawnie pochodzi z odpytanej warstwy WFS i ma
    pierwszeństwo, bo realne cechy GDOŚ nie mają atrybutu rodzaju ochrony. Gdy
    jest ``None``, uruchamiana jest zapasowa klasyfikacja po atrybutach.
    """
    distance_m = float(parcel.distance(zone))
    intersects = parcel.intersects(zone)
    if not intersects and (not include_near or distance_m > search_radius_m):
        return None

    intersection_geom = parcel.intersection(zone)
    intersection_area_sqm = intersection_geom.area
    parcel_area = parcel.area
    area_ratio = (intersection_area_sqm / parcel_area) if parcel_area > 0 else 0.0

    warnings: list[str] = []
    if protection_type is None:
        protection_type, type_warning = _classify_protection_type(properties)
        if type_warning:
            warnings.append(type_warning)
    name = _extract_name(properties)

    # Dla punktu leżącego wewnątrz działki pole przecięcia wynosi 0,
    # ale nie jest to styk granicy. Dla geometrii powierzchniowych zachowujemy
    # dotychczasową ostrożną regułę pola epsilon.
    point_like = zone.geom_type in {"Point", "MultiPoint"}
    touches_boundary = intersects and (
        parcel.boundary.intersects(zone) if point_like else intersection_area_sqm < _INTERSECTION_AREA_EPSILON_SQM
    )
    if touches_boundary:
        # Styk granicą nie jest powierzchniowym ograniczeniem, więc nadpisuje
        # nawet stałą regułę high dla rezerwatów i parków narodowych.
        severity = "low"
        warnings.append(
            "boundary_touch: działka styka się z granicą formy ochrony przyrody, "
            "bez rzeczywistego nakładania powierzchni — nie traktuj jako pełnego "
            "ryzyka."
        )
    elif intersects:
        severity, severity_warning = _classify_severity(protection_type, area_ratio)
        if severity_warning:
            warnings.append(severity_warning)
    else:
        severity = "low"

    if informational_only:
        warnings.append(
            "informational_only: podano wyłącznie relację geometryczną; wynik nie wyznacza strefy "
            "wyłączenia ani samodzielnego ograniczenia prawnego."
        )

    display_geometry = intersection_geom if intersects else zone.intersection(parcel.buffer(search_radius_m))

    return NatureProtectionFeature(
        protection_type=protection_type,
        name=name,
        geometry=display_geometry,
        intersection_area_sqm=intersection_area_sqm,
        area_ratio=area_ratio,
        severity=severity,
        feature_id=feature_id,
        touches_boundary=touches_boundary,
        distance_m=0.0 if intersects else distance_m,
        proximity_status="intersection" if intersects else "near",
        search_radius_m=search_radius_m,
        layer_name=layer_name,
        informational_only=informational_only,
        source_metadata=SourceMetadata(
            source_id=GDOS_SOURCE_ID,
            source_name=GDOS_SOURCE_NAME,
            source_url=source_url,
            fetched_at=fetched_at,
            confidence=0.85 if protection_type != "unknown" else 0.4,
            manual_review_required=(protection_type == "unknown"),
        ),
        warnings=warnings,
    )
