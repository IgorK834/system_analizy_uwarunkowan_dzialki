"""Wstępne rozpoznanie MPZP przez wielopunktowe WMS GetFeatureInfo.

``discover_mpzp`` działa best-effort i nie podnosi błędów zapytań na poziomie
całej funkcji: awaria jednego lub wszystkich punktów próbki trafia do wyniku
jako status ``unavailable`` i ostrzeżenia. Centroid nigdy nie jest jedynym
planowanym punktem — zawsze uwzględniamy także ``representative_point``, a dla
dużych i wieloczęściowych działek dalsze próbki.

Odpowiedź KIMPZP (``plany_granice``) parsuje adapter modułu planowania
(``app.modules.planning.infrastructure.kimpzp_feature_info``, AU-004): wynik to
lista aktów obecnych w punkcie (numer uchwały, daty, linki tekstu/legendy/BIP,
zmiany) i rozłączny status źródła ``available|no_match|no_coverage|unavailable|unknown``.
Wynik pozostaje discovery, nie finalnym przecięciem geometrii wektorowej MPZP.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Final, Literal

import httpx
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from app.core.settings import settings
from app.modules.planning.composition import kimpzp_feature_info_parser
from app.modules.planning.domain.kimpzp_discovery import (
    KIMPZP_NO_SERVICE_FOR_AREA,
    KIMPZP_PARTIAL_SERVICE_ERROR,
    MPZP_MULTIPLE_ACTS_AT_POINT,
    KimpzpAct,
    KimpzpDiscoverySummary,
    KimpzpPointResult,
    KimpzpSourceStatus,
    summarize_points,
)
from app.schemas.analyze import (
    MpzpDiscoveryAct,
    MpzpDiscoveryAmendment,
    MpzpDiscoverySection,
    SourceMetadata,
)

logger = logging.getLogger(__name__)

KIMPZP_TIMEOUT_S: Final[float] = 10.0

# Dla dużej działki pojedyncze punkty są mniej reprezentatywne, dlatego próg
# uruchamia dodatkowe próbki ćwiartek. To heurystyka produktowa, nie prawna.
_LARGE_PARCEL_AREA_THRESHOLD_SQM: Final[float] = 2000.0

MPZP_MULTIPLE_ACTS_ON_PARCEL: Final[str] = "MPZP_MULTIPLE_ACTS_ON_PARCEL"
MPZP_DISCOVERY_UNAVAILABLE: Final[str] = "MPZP_DISCOVERY_UNAVAILABLE"
MPZP_DISCOVERY_UNKNOWN: Final[str] = "MPZP_DISCOVERY_UNKNOWN"


@dataclass(frozen=True)
class DiscoveryIssue:
    """Ostrzeżenie discovery z jawnym kodem kontraktu ``WarningMessage``."""

    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"


@dataclass(frozen=True)
class MpzpDiscoveryResult:
    """Wstępne rozpoznanie MPZP na próbce punktów działki.

    ``is_discovery_only`` jest zawsze True, analogicznie do
    ``is_technical_approximation`` w ``TechnicalSetbackResult``. Dalsze warstwy
    nie mogą prezentować wyniku jako finalnego przecięcia geometrii wektorowej.

    ``acts`` to wszystkie akty wskazane w punktach próbki, posortowane malejąco
    wg „obowiązuje od”. ``plan_id`` i ``uchwala_url`` są wypełnione wyłącznie,
    gdy wskazany jest dokładnie jeden obowiązujący akt — przy kilku aktach
    system nie wybiera po cichu (flaga ``MPZP_MULTIPLE_ACTS_AT_POINT``,
    rozstrzygnięcie w AU-101).
    """

    plan_id: str | None
    candidate_zone_symbols: list[str]
    uchwala_url: str | None
    brak_wektorow: bool
    status: KimpzpSourceStatus
    is_discovery_only: bool
    source_metadata: SourceMetadata
    warnings: list[str] = field(default_factory=list)
    acts: list[KimpzpAct] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    issues: list[DiscoveryIssue] = field(default_factory=list)
    multiple_acts_at_point: bool = False
    multiple_acts_on_parcel: bool = False
    sampled_points: int = 0
    failed_points: int = 0


async def discover_mpzp(parcel_geometry: BaseGeometry) -> MpzpDiscoveryResult:
    """Rozpoznaje wstępnie MPZP dla geometrii działki w EPSG:2180.

    Wynik jest discovery na próbce punktów, a NIE finalnym przecięciem danych
    wektorowych. Centroid nigdy nie jest jedynym planowanym punktem: zawsze
    sprawdzamy też ``representative_point`` gwarantowany wewnątrz poligonu,
    a duże i wieloczęściowe działki dostają dodatkowe próbki.

    Funkcja nie podnosi błędów zapytań na poziomie całości. Awaria pojedynczego
    lub wszystkich punktów jest tolerowana i opisana w ``warnings``/``issues``.
    """
    sample_points = _build_sample_points(parcel_geometry)
    fetched_at = datetime.now(timezone.utc)

    async with httpx.AsyncClient(timeout=KIMPZP_TIMEOUT_S) as client:
        point_results = await asyncio.gather(
            *(_query_point_safe(client, x, y) for x, y in sample_points)
        )

    summary, warnings = _aggregate_point_results(point_results)
    single = summary.single_act
    status = summary.status
    # Kontrakt JSON części usług: brak obiektów z ``vector_available=false`` oznacza
    # gminę bez wektora stref — to tryb ręcznego symbolu, a nie „brak planu”.
    brak_wektorow = summary.saw_vector_unavailable and status != "available"
    issues = _discovery_issues(summary)

    if status == "no_match" and not brak_wektorow:
        warnings.append(
            "Nie znaleziono miejscowego planu zagospodarowania przestrzennego "
            "dla żadnego z próbkowanych punktów działki."
        )
    if brak_wektorow:
        warnings.append(
            "Gmina nie udostępnia wektorowych danych MPZP przez KIMPZP dla "
            "próbkowanych punktów — wymagana ręczna weryfikacja treści planu."
        )
    warnings.append(
        "To jest wstępne rozpoznanie (discovery) na próbce punktów, nie finalne "
        "przecięcie geometrii wektorowej działki ze strefami MPZP."
    )

    return MpzpDiscoveryResult(
        plan_id=single.resolution_number if single else None,
        candidate_zone_symbols=list(summary.zone_symbols),
        uchwala_url=single.document_url if single else None,
        brak_wektorow=brak_wektorow,
        status=status,
        is_discovery_only=True,
        source_metadata=SourceMetadata(
            source_id="kimpzp",
            source_name="KIMPZP",
            source_url=settings.kimpzp_wms_base_url,
            fetched_at=fetched_at,
            confidence=0.6 if status == "available" else 0.3,
            manual_review_required=True,
        ),
        warnings=warnings,
        acts=list(summary.acts),
        reason_codes=list(summary.reason_codes),
        issues=issues,
        multiple_acts_at_point=summary.multiple_acts_at_point,
        multiple_acts_on_parcel=summary.multiple_acts_on_parcel,
        sampled_points=summary.queried_points,
        failed_points=summary.failed_points,
    )


def _build_sample_points(parcel: BaseGeometry) -> list[tuple[float, float]]:
    """Buduje wielopunktową próbkę do zapytań GetFeatureInfo.

    Centroid oraz ``representative_point`` są dodawane niezależnie, a następnie
    deduplikowane, gdy geometrycznie wypadają w tym samym miejscu. MultiPolygon
    dodaje punkt każdej części, a działka od 2000 m² także próbki ćwiartek BBOX.
    """
    points: list[tuple[float, float]] = []
    centroid = parcel.centroid
    points.append((round(centroid.x, 3), round(centroid.y, 3)))

    representative_point = parcel.representative_point()
    points.append(
        (round(representative_point.x, 3), round(representative_point.y, 3))
    )

    if parcel.geom_type == "MultiPolygon":
        for part in parcel.geoms:
            part_point = part.representative_point()
            points.append((round(part_point.x, 3), round(part_point.y, 3)))

    if parcel.area >= _LARGE_PARCEL_AREA_THRESHOLD_SQM:
        points.extend(_grid_sample_points(parcel))

    return list(dict.fromkeys(points))


def _grid_sample_points(parcel: BaseGeometry) -> list[tuple[float, float]]:
    minx, miny, maxx, maxy = parcel.bounds
    midx, midy = (minx + maxx) / 2, (miny + maxy) / 2
    quadrants = [
        box(minx, miny, midx, midy),
        box(midx, miny, maxx, midy),
        box(minx, midy, midx, maxy),
        box(midx, midy, maxx, maxy),
    ]

    points: list[tuple[float, float]] = []
    for quadrant in quadrants:
        clipped = parcel.intersection(quadrant)
        if not clipped.is_empty and clipped.area > 0:
            representative_point = clipped.representative_point()
            points.append(
                (
                    round(representative_point.x, 3),
                    round(representative_point.y, 3),
                )
            )
    return points


def _build_get_feature_info_params(x: float, y: float) -> dict[str, str]:
    """Buduje GetFeatureInfo dla centralnego piksela BBOX 1x1 m wokół punktu.

    WMS 1.1.1 zachowuje kolejność x/y dla EPSG:2180. Obraz 2x2 i środkowy
    piksel pozwalają odpytać dokładnie próbkę bez ryzyka odwrócenia osi przez
    reguły WMS 1.3.0.
    """
    half = 0.5
    return {
        "service": "WMS",
        "version": "1.1.1",
        "request": "GetFeatureInfo",
        "layers": "plany_granice",
        "query_layers": "plany_granice",
        "srs": "EPSG:2180",
        "bbox": f"{x - half},{y - half},{x + half},{y + half}",
        "width": "2",
        "height": "2",
        "x": "1",
        "y": "1",
        "info_format": "text/html",
        "feature_count": "5",
    }


async def _query_point_safe(
    client: httpx.AsyncClient, x: float, y: float
) -> KimpzpPointResult | Exception:
    """Odpytuje jeden punkt, zwracając błąd sieci/HTTP jako wartość do agregacji."""
    try:
        params = _build_get_feature_info_params(x, y)
        response = await client.get(settings.kimpzp_wms_base_url, params=params)
        response.raise_for_status()
        return _parse_get_feature_info_response(response.text)
    except httpx.HTTPError as exc:
        return exc


def _parse_get_feature_info_response(text: str) -> KimpzpPointResult:
    """Odpowiedź punktu (HTML KIMPZP albo JSON) przez adapter za portem planowania."""
    return kimpzp_feature_info_parser()(text)


def _parse_html_get_feature_info_response(text: str) -> KimpzpPointResult:
    """Normalizuje sklejone odpowiedzi HTML usług gminnych zbiorczej warstwy KIMPZP.

    Bloki planu („Obowiązujące MPZP”) dają akty; tabele zmian („Zmiany
    tekstowe”, „Zmiany”) — wyłącznie ``amendments`` aktu, nigdy akt.
    """
    return kimpzp_feature_info_parser()(text)


def _aggregate_point_results(
    point_results: list[KimpzpPointResult | Exception],
) -> tuple[KimpzpDiscoverySummary, list[str]]:
    """Agreguje wiele punktów bez uprzywilejowania wyniku centroidu."""
    warnings: list[str] = []
    results: list[KimpzpPointResult | None] = []
    for result in point_results:
        if isinstance(result, Exception):
            warnings.append(
                "Zapytanie GetFeatureInfo do KIMPZP nie powiodło się dla jednego "
                f"z punktów próbki: {result}"
            )
            results.append(None)
            continue
        results.append(result)
    return summarize_points(results), warnings


def _act_label(act: KimpzpAct) -> str:
    number = act.resolution_number or "bez numeru"
    if act.valid_from:
        return f"{number} (obowiązuje od {act.valid_from.isoformat()})"
    return number


def _discovery_issues(summary: KimpzpDiscoverySummary) -> list[DiscoveryIssue]:
    """Kodowane ostrzeżenia statusu źródła i wielu aktów."""
    issues: list[DiscoveryIssue] = []
    if summary.status == "no_coverage":
        issues.append(
            DiscoveryIssue(
                code=KIMPZP_NO_SERVICE_FOR_AREA,
                message=(
                    "KIMPZP nie ma usługi gminnej dla obszaru działki („brak serwisu "
                    "dla wskazanego obszaru”). To brak danych w KIMPZP, a nie "
                    "potwierdzenie braku planu — sprawdź MPZP w gminie."
                ),
            )
        )
    elif summary.status == "unavailable":
        issues.append(
            DiscoveryIssue(
                code=MPZP_DISCOVERY_UNAVAILABLE,
                message=(
                    "Usługa MPZP gminy w KIMPZP zwróciła błąd albo nie odpowiedziała dla "
                    "żadnego punktu działki. Nie ustalono, czy obowiązuje plan — błąd "
                    "źródła nie oznacza braku ograniczeń."
                ),
                severity="error",
            )
        )
    elif summary.status == "unknown":
        issues.append(
            DiscoveryIssue(
                code=MPZP_DISCOVERY_UNKNOWN,
                message=(
                    "Odpowiedź KIMPZP ma nierozpoznany format — nie ustalono, czy "
                    "obowiązuje plan. Wymagana ręczna weryfikacja."
                ),
            )
        )
    if KIMPZP_PARTIAL_SERVICE_ERROR in summary.reason_codes:
        issues.append(
            DiscoveryIssue(
                code=KIMPZP_PARTIAL_SERVICE_ERROR,
                message=(
                    "Część punktów lub usług gminnych KIMPZP zwróciła błąd — lista "
                    "aktów może być niepełna."
                ),
            )
        )
    in_force = [act for act in summary.acts if act.in_force_or_unknown]
    if summary.multiple_acts_at_point:
        issues.append(
            DiscoveryIssue(
                code=MPZP_MULTIPLE_ACTS_AT_POINT,
                message=(
                    f"KIMPZP wskazuje w punkcie działki {len(in_force)} akty MPZP: "
                    + "; ".join(_act_label(act) for act in in_force)
                    + ". System nie wybiera aktu automatycznie — ustal w uchwałach, "
                    "który akt rozstrzyga o przeznaczeniu."
                ),
            )
        )
    elif summary.multiple_acts_on_parcel:
        issues.append(
            DiscoveryIssue(
                code=MPZP_MULTIPLE_ACTS_ON_PARCEL,
                message=(
                    "Punkty próbki wskazują na różne plany miejscowe ("
                    + "; ".join(_act_label(act) for act in in_force)
                    + ") — działka może przecinać więcej niż jeden plan MPZP."
                ),
            )
        )
    return issues


def discovery_section(result: MpzpDiscoveryResult | None) -> MpzpDiscoverySection:
    """Sekcja ``mpzp_discovery`` odpowiedzi API; ``None`` = discovery się nie wykonało."""
    if result is None:
        return MpzpDiscoverySection(status="unknown", reason_codes=["MPZP_DISCOVERY_ERROR"])
    return MpzpDiscoverySection(
        status=result.status,
        reason_codes=list(result.reason_codes),
        acts=[_act_model(act) for act in result.acts],
        selected_act=result.plan_id,
        multiple_acts_at_point=result.multiple_acts_at_point,
        multiple_acts_on_parcel=result.multiple_acts_on_parcel,
        candidate_zone_symbols=list(result.candidate_zone_symbols),
        sampled_points=result.sampled_points,
        failed_points=result.failed_points,
        is_discovery_only=result.is_discovery_only,
        source=result.source_metadata,
    )


def _act_model(act: KimpzpAct) -> MpzpDiscoveryAct:
    return MpzpDiscoveryAct(
        resolution_number=act.resolution_number,
        resolution_date=act.resolution_date,
        name=act.name,
        valid_from=act.valid_from,
        repealed_on=act.repealed_on,
        legal_status=act.legal_status,
        text_url=act.text_url,
        legend_url=act.legend_url,
        drawing_url=act.drawing_url,
        bip_url=act.bip_url,
        www_url=act.www_url,
        journal=act.journal,
        informatization=act.informatization,
        zone_symbols=list(act.zone_symbols),
        amendments=[
            MpzpDiscoveryAmendment(
                kind=amendment.kind,
                resolution_number=amendment.resolution_number,
                name=amendment.name,
                adopted_on=amendment.adopted_on,
                valid_from=amendment.valid_from,
                document_url=amendment.document_url,
                bip_url=amendment.bip_url,
                raw_text=amendment.raw_text,
            )
            for amendment in act.amendments
        ],
        source_format=act.source_format,
    )
