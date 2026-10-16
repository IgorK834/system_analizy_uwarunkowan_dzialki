"""Publiczny, wersjonowany kontrakt kafelków mapowych.

Rastrowe kafle PNG (``/tiles/{source}/…png``) są wyłącznie podglądem WMS.
Wektorowe kafle POG (``/pog/states/{state_id}/…mvt``, BK-401, ADR-026) pochodzą z
krajowego stanu rejestru wersji aktów w PostGIS i mają te same atrybuty co analiza.
Szczegóły obiektu dla inspektora (BK-404) i gotowe agregaty stref aktu/gminy
(BK-405) są przypięte do tego samego ``state_id``.
"""

import asyncio
from datetime import date, datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.rate_limit import rate_limit
from app.core.pog_presentation import load_pog_presentation
from app.db.session import get_db
from app.modules.planning.application.pog_state_queries import (
    PogAreaSummaryNotFoundError,
    PogFeatureAmbiguousError,
    PogFeatureNotFoundError,
    PogStateQueryService,
)
from app.modules.planning.application.pog_tiles import (
    PogStateNotFoundError,
    PogTileService,
    PogTileTooLargeError,
)
from app.modules.planning.composition import (
    build_pog_state_query_service,
    build_pog_tile_service,
)
from app.modules.planning.domain.pog_area_summary import (
    InvalidPogSummaryQueryError,
    PogAreaSummaryQuery,
    PogAreaSummaryView,
)
from app.modules.planning.domain.pog_inspector import (
    InvalidPogFeatureIdError,
    PogFeatureDetails,
    payload_etag,
)
from app.modules.planning.domain.pog_tiles import (
    DEFAULT_POG_TILE_EDITION,
    LAYER_MIN_ZOOM,
    MVT_BUFFER,
    MVT_EXTENT,
    POG_TILE_EDITIONS,
    POG_TILE_LAYERS,
    POG_TILE_MEDIA_TYPE,
    POG_TILE_SCHEMA_VERSION,
    TEXT_ATTRIBUTES_MIN_ZOOM,
    InvalidPogTileRequestError,
    PogCoverageArea,
    PogStateInfo,
    PogTileRequest,
)
from app.core.settings import settings
from app.schemas.analyze import UtilitiesPreviewResult
from app.shared.provenance import is_verified_https_url
from app.services.kiut_coverage import check_kiut_coverage_wgs84
from app.services.wms_tiles import (
    InvalidTileCoordinatesError,
    WmsTileProxy,
    WmsTilePreviewRegistry,
    WmsTileUnavailableError,
    wms_tile_registry,
)

router = APIRouter(prefix="/api/v1/map", tags=["map-tiles"])
PreviewSourceKey = Literal["mpzp", "pog", "kiut"]

_coverage_limit = rate_limit(settings.rate_limit_coverage_per_minute)
# Kafle (WMS PNG i MVT) mapa pobiera seriami — osobny, wyższy próg (AU-006); metadane
# i odczyty wydań POG mają próg ogólny. Wszystkie liczone per klient wspólnym kluczem.
_tiles_limit = rate_limit(settings.rate_limit_tiles_per_minute)
_pog_tiles_limit = rate_limit(settings.rate_limit_tiles_per_minute)
_map_data_limit = rate_limit(settings.rate_limit_data_per_minute)


class PreviewSourceResponse(BaseModel):
    source_key: PreviewSourceKey
    label: str
    attribution: str
    min_zoom: int
    max_zoom: int
    tile_size: int
    tile_url_template: str
    legal_note: str
    info_url: str
    catalog_status: str


def get_wms_tile_registry() -> WmsTilePreviewRegistry:
    return wms_tile_registry


async def _load_tile_until_disconnect(
    request: Request,
    proxy: WmsTileProxy,
    z: int,
    x: int,
    y: int,
):
    """Anuluje kosztowny MISS, gdy MapLibre porzuci kafel podczas zoomowania."""

    tile_task = asyncio.create_task(proxy.get_tile(z, x, y))

    async def wait_for_disconnect() -> None:
        while not await request.is_disconnected():
            await asyncio.sleep(0.05)

    disconnect_task = asyncio.create_task(wait_for_disconnect())
    done, _ = await asyncio.wait(
        {tile_task, disconnect_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if tile_task in done:
        disconnect_task.cancel()
        await asyncio.gather(disconnect_task, return_exceptions=True)
        return await tile_task

    tile_task.cancel()
    await asyncio.gather(tile_task, return_exceptions=True)
    return None


@router.get(
    "/preview-sources",
    response_model=list[PreviewSourceResponse],
    dependencies=[Depends(_map_data_limit)],
)
async def get_preview_sources(
    registry: Annotated[WmsTilePreviewRegistry, Depends(get_wms_tile_registry)],
) -> list[PreviewSourceResponse]:
    """Zwraca bezpieczne metadane allowlisty podglądowych źródeł WMS."""

    return [
        PreviewSourceResponse(
            source_key=cast(PreviewSourceKey, source.source_key),
            label=source.label,
            attribution=source.attribution,
            min_zoom=source.min_zoom,
            max_zoom=source.max_zoom,
            tile_size=source.tile_size,
            tile_url_template=(
                f"/api/v1/map/tiles/{source.source_key}/{{z}}/{{x}}/{{y}}.png"
            ),
            legal_note=source.legal_note,
            info_url=source.info_url,
            catalog_status=source.catalog_status,
        )
        for source in registry.sources
    ]


@router.get(
    "/coverage/kiut",
    response_model=UtilitiesPreviewResult,
    dependencies=[Depends(_coverage_limit)],
    description=(
        "Sprawdza przez publiczną warstwę WMS gesut, czy powiat publikuje "
        "podgląd uzbrojenia. Wynik nie jest analizą obecności ani odległości sieci."
    ),
)
async def get_kiut_coverage(
    lon: Annotated[float, Query(ge=-180.0, le=180.0)],
    lat: Annotated[float, Query(ge=-90.0, le=90.0)],
) -> UtilitiesPreviewResult:
    return await check_kiut_coverage_wgs84(lon, lat)


@router.get(
    "/tiles/{source}/{z}/{x}/{y}.png",
    dependencies=[Depends(_tiles_limit)],
    responses={
        429: {"description": "Przekroczono limit zapytań o kafle (Retry-After)."},
        200: {"content": {"image/png": {}}},
        304: {"description": "Kafelek nie zmienił się od podanego ETag."},
        422: {"description": "Nieprawidłowe współrzędne kafelka."},
        503: {"description": "WMS i cache kafelków są niedostępne."},
    },
    description="Zwraca cache'owany kafelek z wybranej oficjalnej usługi WMS.",
)
async def get_wms_tile(
    source: PreviewSourceKey,
    z: int,
    x: int,
    y: int,
    request: Request,
    registry: Annotated[WmsTilePreviewRegistry, Depends(get_wms_tile_registry)],
) -> Response:
    proxy = registry.get(source)
    try:
        result = await _load_tile_until_disconnect(request, proxy, z, x, y)
    except InvalidTileCoordinatesError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except WmsTileUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Nakładka {proxy.source.label} jest chwilowo niedostępna.",
        ) from exc

    if result is None:
        # Nginx używa 499 dla przerwanego połączenia klienta. Odpowiedź zwykle
        # nie dotrze już do przeglądarki, ale kończy handler bez pracy upstream.
        return Response(status_code=499)

    headers = proxy.response_headers(result)
    if request.headers.get("if-none-match") == result.etag:
        return Response(status_code=304, headers=headers)
    return Response(content=result.content, media_type="image/png", headers=headers)


# --- Wektorowe kafle POG z krajowego stanu rejestru (BK-401, ADR-026) ----------

POG_TILE_URL_TEMPLATE = "/api/v1/map/pog/states/{state_id}/{{z}}/{{x}}/{{y}}.mvt"
_POG_EDITION_QUERY = {"binding": "binding", "project": "project"}


class PogCoverageAreaResponse(BaseModel):
    """Zasięg danych aktu w stanie krajowym — podstawa stanu pokrycia mapy (BK-406)."""

    act_id: str
    teryt: str | None
    legal_status: str
    bounds: list[float] | None = Field(
        default=None, description="[min_lon, min_lat, max_lon, max_lat] w EPSG:4326."
    )
    has_boundary: bool
    is_complete: bool | None = Field(
        default=None,
        description="Kompletność agregatu stref aktu (BK-405); null — nie policzono.",
    )
    incomplete_reasons: list[str] = Field(default_factory=list)


class PogTileEditionSource(BaseModel):
    """Szablon URL kafli jednej edycji (źródła wektorowe obowiązujących i projektów są rozdzielone)."""

    edition: str
    tile_url_template: str


class PogTileStateResponse(BaseModel):
    """Metadane stanu krajowego POG z URL-ami kafli przypiętymi do ``state_id``.

    Klient pobiera je raz na sesję mapy i nie podmienia źródła, gdy w tle zostanie zatwierdzony nowszy stan —
    URL zawsze odtwarza ten sam zbiór wersji aktów, także po późniejszych importach innych gmin.
    """

    state_id: int
    source_id: str
    kind: str
    created_at: datetime | None
    is_current: bool
    act_count: int
    tile_url_template: str = Field(
        description="Względny szablon URL kafli MVT przypiętych do stanu (wszystkie edycje)."
    )
    editions_sources: list[PogTileEditionSource] = Field(
        description="Osobne szablony URL dla obowiązujących (binding) i projektów (project)."
    )
    tile_schema: str
    tile_format: str = POG_TILE_MEDIA_TYPE
    layers: list[str]
    layer_min_zoom: dict[str, int] = Field(
        description="Najmniejszy zoom, od którego warstwa jest w kaflach (jedno źródło prawdy dla klienta)."
    )
    text_attributes_min_zoom: int
    editions: list[str]
    default_edition: str
    min_zoom: int
    max_zoom: int
    extent: int = MVT_EXTENT
    buffer: int = MVT_BUFFER
    bounds: list[float] | None = Field(
        default=None, description="Zasięg stanu [min_lon, min_lat, max_lon, max_lat] w EPSG:4326."
    )
    acts_by_legal_status: dict[str, int]
    coverage_areas: list[PogCoverageAreaResponse] = Field(default_factory=list)
    map_ready: bool = Field(
        description="false — część aktywnych wersji nie ma jeszcze wierszy mapy (np. po migracji); mapa jest niepełna."
    )
    versions_without_map_rows: int = 0
    style_version: str
    style_sha256: str
    attribution: str
    legal_note: str


def get_pog_tile_service(db: Annotated[Session, Depends(get_db)]) -> PogTileService:
    return build_pog_tile_service(db)


def _coverage_response(area: PogCoverageArea) -> PogCoverageAreaResponse:
    return PogCoverageAreaResponse(
        act_id=area.act_id,
        teryt=area.teryt,
        legal_status=area.legal_status,
        bounds=list(area.bounds) if area.bounds else None,
        has_boundary=area.has_boundary,
        is_complete=area.is_complete,
        incomplete_reasons=list(area.incomplete_reasons),
    )


def _state_response(info: PogStateInfo, service: PogTileService) -> PogTileStateResponse:
    presentation = load_pog_presentation()
    created_at = info.created_at if isinstance(info.created_at, datetime) else None
    template = POG_TILE_URL_TEMPLATE.format(state_id=info.state_id)
    return PogTileStateResponse(
        state_id=info.state_id,
        source_id=info.source_id,
        kind=info.kind,
        created_at=created_at,
        is_current=info.is_current,
        act_count=info.act_count,
        tile_url_template=template,
        editions_sources=[
            PogTileEditionSource(edition=edition, tile_url_template=f"{template}?edition={edition}")
            for edition in _POG_EDITION_QUERY
        ],
        tile_schema=POG_TILE_SCHEMA_VERSION,
        layers=list(POG_TILE_LAYERS),
        layer_min_zoom=dict(LAYER_MIN_ZOOM),
        text_attributes_min_zoom=TEXT_ATTRIBUTES_MIN_ZOOM,
        editions=list(POG_TILE_EDITIONS),
        default_edition=DEFAULT_POG_TILE_EDITION,
        min_zoom=service.limits.min_zoom,
        max_zoom=service.limits.max_zoom,
        bounds=list(info.bounds) if info.bounds else None,
        acts_by_legal_status=dict(info.acts_by_legal_status),
        coverage_areas=[_coverage_response(area) for area in info.coverage_areas],
        map_ready=info.versions_without_map_rows == 0,
        versions_without_map_rows=info.versions_without_map_rows,
        style_version=presentation.style_version,
        style_sha256=presentation.sha256,
        attribution="Rejestr Urbanistyczny (APP POG) — lokalny stan danych",
        legal_note=(
            "Mapa prezentuje zapisany stan danych POG zaimportowanych z Rejestru Urbanistycznego. Projekt aktu nie "
            "jest wiążący, a brak obiektu na mapie nie potwierdza braku planu (gmina może nie być zaimportowana)."
        ),
    )


@router.get(
    "/pog/state",
    dependencies=[Depends(_map_data_limit)],
    response_model=PogTileStateResponse,
    responses={404: {"description": "Brak lokalnych danych POG (rejestr stanu krajowego jest pusty)."}},
    description="Metadane bieżącego stanu krajowego POG i URL-e kafli MVT przypięte do jego numeru.",
)
def get_current_pog_state(
    service: Annotated[PogTileService, Depends(get_pog_tile_service)],
) -> PogTileStateResponse:
    try:
        return _state_response(service.current_state(), service)
    except PogStateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/pog/states/{state_id}",
    dependencies=[Depends(_map_data_limit)],
    response_model=PogTileStateResponse,
    responses={404: {"description": "Stan krajowy POG nie istnieje."}},
    description="Metadane konkretnego (także historycznego) stanu krajowego POG.",
)
def get_pog_state(
    state_id: int,
    service: Annotated[PogTileService, Depends(get_pog_tile_service)],
) -> PogTileStateResponse:
    try:
        return _state_response(service.state(state_id), service)
    except PogStateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    candidates = {item.strip().removeprefix("W/") for item in header.split(",")}
    return "*" in candidates or etag in candidates


# --- Inspektor obiektu (BK-404) i agregaty stref (BK-405) ---------------------


class PogProfileResponse(BaseModel):
    code: str
    label: str | None
    dictionary_source: str


class PogFeatureActResponse(BaseModel):
    act_id: str
    act_version: str | None
    name: str | None
    teryt: str | None
    legal_status: str
    legal_status_code: str | None = Field(
        description="Urzędowy kod statusu (URI INSPIRE/RU) — dowód dla legal_status."
    )
    resolution_number: str | None
    resolution_date: date | None
    legal_valid_from: date | None
    legal_valid_to: date | None
    effective_date: date | None = Field(default=None, description="Data wejścia w życie uchwały (dokument formalny).")
    version_started_on: date | None = Field(default=None, description="Początek wersji aktu w rejestrze (APP).")
    official_journal: str | None = Field(default=None, description="Dziennik urzędowy uchwały (etykieta słownika RU).")
    document_link: str | None = Field(default=None, description="Odnośnik do tekstu uchwały (dokument formalny).")
    ru_act_url: str | None = Field(
        default=None, description="Karta aktu w Rejestrze Urbanistycznym (gdy znany identyfikator wersji w katalogu RU)."
    )
    publication_id: str | None
    manual_review_required: bool
    act_version_id: int | None = None
    data_release_id: int | None = None
    release_label: str | None = None
    artifact_sha256: str | None = None
    content_hash: str | None = None


class PogFeatureStateResponse(BaseModel):
    state_id: int
    kind: str
    created_at: datetime | None
    is_current: bool


class PogFeatureDetailsResponse(BaseModel):
    """Szczegóły obiektu POG, których kafel MVT nie mieści (BK-404).

    Parametr bez wartości ma ``null`` — nigdy 0. Obiekty OUZ/OZS/OSDIS nie mają
    parametrów ani kodu strefy.
    """

    model_config = ConfigDict(populate_by_name=True)

    schema_: str = Field(alias="schema")
    tile_schema: str
    state: PogFeatureStateResponse
    feature_pk: int
    feature_id: str
    feature_version: str | None
    layer: str
    feature_type: str
    symbol: str | None
    designation: str | None
    label: str | None
    zone_code: str | None
    source_zone_type: str | None
    parameters: dict[str, float | None]
    parameters_informational: bool
    primary_profiles: list[PogProfileResponse]
    additional_profiles: list[PogProfileResponse]
    act: PogFeatureActResponse
    source_reference: str | None


class PogAreaSummaryZoneResponse(BaseModel):
    zone_code: str
    area_sqm: float
    area_sqkm: float
    share_pct: float | None = Field(
        description="100·pole/pole granicy; null, gdy brak mianownika (nie 100%)."
    )
    zone_count: int


class PogAreaSummaryResponse(BaseModel):
    """Struktura powierzchniowa stref aktu albo gminy w stanie krajowym (BK-405)."""

    model_config = ConfigDict(populate_by_name=True)

    schema_: str = Field(alias="schema")
    state_id: int
    computed_in_state_id: int | None
    state_is_current: bool
    release_id: int
    release_label: str
    artifact_sha256: str | None
    scope: Literal["act", "municipality"]
    act_id: str | None
    act_version: str | None
    teryt: str | None
    edition: str | None
    legal_status: str | None
    act_ids: list[str]
    act_count: int
    denominator_area_sqm: float | None
    denominator_area_sqkm: float | None
    denominator_source: str | None
    zones_area_sqm: float
    zones_area_sqkm: float
    missing_area_sqm: float | None
    missing_area_sqkm: float | None
    overlap_area_sqm: float
    outside_area_sqm: float
    deduplicated_area_sqm: float | None
    share_sum_pct: float | None
    share_tolerance_pct: float
    area_tolerance_sqm: float
    zone_count: int
    is_complete: bool
    incomplete_reasons: list[str]
    zones: list[PogAreaSummaryZoneResponse]
    method_version: str
    computed_at: datetime


def get_pog_state_query_service(
    db: Annotated[Session, Depends(get_db)],
) -> PogStateQueryService:
    return build_pog_state_query_service(db)


RU_ACT_URL_TEMPLATE = "https://rejestr-urbanistyczny.gov.pl/published/details/pog/{ru_id}"


def _details_response(details: PogFeatureDetails) -> PogFeatureDetailsResponse:
    state = details.state
    act = details.act
    return PogFeatureDetailsResponse(
        schema_=details.schema,
        tile_schema=details.tile_schema,
        state=PogFeatureStateResponse(
            state_id=state.state_id,
            kind=state.kind,
            created_at=state.created_at if isinstance(state.created_at, datetime) else None,
            is_current=state.is_current,
        ),
        feature_pk=details.feature_pk,
        feature_id=details.feature_id,
        feature_version=details.feature_version,
        layer=details.layer,
        feature_type=details.feature_type,
        symbol=details.symbol,
        designation=details.designation,
        label=details.label,
        zone_code=details.zone_code,
        source_zone_type=details.source_zone_type,
        parameters=dict(details.parameters),
        parameters_informational=details.parameters_informational,
        primary_profiles=[
            PogProfileResponse.model_validate(item) for item in details.primary_profiles
        ],
        additional_profiles=[
            PogProfileResponse.model_validate(item) for item in details.additional_profiles
        ],
        act=PogFeatureActResponse(
            act_id=act.act_id,
            act_version=act.act_version,
            name=act.name,
            teryt=act.teryt,
            legal_status=act.legal_status,
            legal_status_code=act.legal_status_code,
            resolution_number=act.resolution_number,
            resolution_date=act.resolution_date,
            legal_valid_from=act.legal_valid_from,
            legal_valid_to=act.legal_valid_to,
            effective_date=act.effective_date,
            version_started_on=act.version_started_on,
            official_journal=act.official_journal,
            document_link=act.document_link if is_verified_https_url(act.document_link) else None,
            ru_act_url=(
                RU_ACT_URL_TEMPLATE.format(ru_id=act.ru_published_act_id)
                if act.ru_published_act_id is not None
                else None
            ),
            publication_id=act.publication_id,
            manual_review_required=act.manual_review_required,
            act_version_id=act.act_version_id,
            data_release_id=act.data_release_id,
            release_label=act.release_label,
            artifact_sha256=act.artifact_sha256,
            content_hash=act.content_hash,
        ),
        source_reference=details.source_reference,
    )


def _sqkm(value: float | None) -> float | None:
    return value / 1_000_000.0 if value is not None else None


def _summary_response(summary: PogAreaSummaryView) -> PogAreaSummaryResponse:
    return PogAreaSummaryResponse(
        schema_=summary.schema,
        state_id=summary.state_id,
        computed_in_state_id=summary.computed_in_state_id,
        state_is_current=summary.state_is_current,
        release_id=summary.release_id,
        release_label=summary.release_label,
        artifact_sha256=summary.artifact_sha256,
        scope=cast(Literal["act", "municipality"], summary.scope),
        act_id=summary.act_id,
        act_version=summary.act_version,
        teryt=summary.teryt,
        edition=summary.edition,
        legal_status=summary.legal_status,
        act_ids=list(summary.act_ids),
        act_count=summary.act_count,
        denominator_area_sqm=summary.denominator_area_sqm,
        denominator_area_sqkm=_sqkm(summary.denominator_area_sqm),
        denominator_source=summary.denominator_source,
        zones_area_sqm=summary.zones_area_sqm,
        zones_area_sqkm=summary.zones_area_sqm / 1_000_000.0,
        missing_area_sqm=summary.missing_area_sqm,
        missing_area_sqkm=_sqkm(summary.missing_area_sqm),
        overlap_area_sqm=summary.overlap_area_sqm,
        outside_area_sqm=summary.outside_area_sqm,
        deduplicated_area_sqm=summary.deduplicated_area_sqm,
        share_sum_pct=summary.share_sum_pct,
        share_tolerance_pct=summary.share_tolerance_pct,
        area_tolerance_sqm=summary.area_tolerance_sqm,
        zone_count=summary.zone_count,
        is_complete=summary.is_complete,
        incomplete_reasons=list(summary.incomplete_reasons),
        zones=[
            PogAreaSummaryZoneResponse.model_validate(zone, from_attributes=True)
            for zone in summary.zones
        ],
        method_version=summary.method_version,
        computed_at=cast(datetime, summary.computed_at),
    )


def _cached_json(request: Request, kind: str, model: BaseModel) -> Response:
    """JSON z ETag i ``Cache-Control``; stan krajowy jest niezmienny → 304 bez treści."""
    payload = model.model_dump(mode="json", by_alias=True)
    etag = payload_etag(kind, payload)
    headers = {
        "ETag": etag,
        "Cache-Control": f"public, max-age={settings.pog_tile_browser_ttl_seconds}",
    }
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(
        content=model.model_dump_json(by_alias=True),
        media_type="application/json",
        headers=headers,
    )


@router.get(
    "/pog/states/{state_id}/features/{feature_id:path}",
    dependencies=[Depends(_map_data_limit)],
    response_model=PogFeatureDetailsResponse,
    responses={
        304: {"description": "Szczegóły nie zmieniły się od podanego ETag."},
        404: {"description": "Stan albo obiekt nie istnieje w tym stanie."},
        409: {"description": "Identyfikator wskazuje więcej niż jeden obiekt stanu."},
        422: {"description": "Niepoprawny identyfikator obiektu."},
    },
    description=(
        "Szczegóły obiektu POG (strefa, OUZ, OZS, OSDIS) z przypiętego stanu krajowego, "
        "których kafel MVT nie mieści: pełna etykieta, nazwy profili, dane aktu (uchwała, daty, dziennik "
        "urzędowy, link do aktu w RU) i urzędowy kod statusu. Identyfikator to atrybut feature_id z kafla."
    ),
)
def get_pog_feature_details(
    state_id: int,
    feature_id: str,
    request: Request,
    service: Annotated[PogStateQueryService, Depends(get_pog_state_query_service)],
) -> Response:
    try:
        details = service.feature_details(state_id, feature_id)
    except InvalidPogFeatureIdError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (PogStateNotFoundError, PogFeatureNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PogFeatureAmbiguousError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _cached_json(request, "pog-feature", _details_response(details))


@router.get(
    "/pog/states/{state_id}/summary",
    dependencies=[Depends(_map_data_limit)],
    response_model=PogAreaSummaryResponse,
    responses={
        304: {"description": "Agregat nie zmienił się od podanego ETag."},
        404: {"description": "Brak stanu albo agregatu dla zakresu."},
        422: {"description": "Podaj dokładnie jeden zakres: act_id albo teryt."},
    },
    description=(
        "Struktura powierzchniowa stref aktu (act_id) albo gminy (teryt + edition) "
        "policzona przy imporcie w EPSG:2180. Odczyt nie wykonuje obliczeń "
        "przestrzennych; is_complete=false oznacza dane niepełne, a brak granicy "
        "aktu daje share_pct=null (nie 100%)."
    ),
)
def get_pog_area_summary(
    state_id: int,
    request: Request,
    service: Annotated[PogStateQueryService, Depends(get_pog_state_query_service)],
    act_id: Annotated[str | None, Query(max_length=200)] = None,
    teryt: Annotated[str | None, Query(max_length=7)] = None,
    edition: Annotated[str | None, Query(max_length=20)] = None,
) -> Response:
    query = PogAreaSummaryQuery(state_id, act_id=act_id, teryt=teryt, edition=edition)
    try:
        summary = service.area_summary(query)
    except InvalidPogSummaryQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (PogStateNotFoundError, PogAreaSummaryNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _cached_json(request, "pog-summary", _summary_response(summary))


@router.get(
    "/pog/states/{state_id}/{z}/{x}/{y}.mvt",
    dependencies=[Depends(_pog_tiles_limit)],
    responses={
        429: {"description": "Przekroczono limit zapytań o kafle (Retry-After)."},
        200: {
            "content": {POG_TILE_MEDIA_TYPE: {}},
            "description": "Kafel MVT; pusty kafel to poprawny, pusty protobuf.",
        },
        304: {"description": "Kafel nie zmienił się od podanego ETag."},
        404: {"description": "Stan krajowy nie istnieje."},
        413: {"description": "Kafel przekracza limit liczby obiektów albo bajtów."},
        422: {"description": "Nieprawidłowe z/x/y albo edycja."},
    },
    description=(
        "Wektorowy kafel POG (warstwy zones, ouz, downtown, "
        "social_infrastructure_standard, act_boundary) z przypiętego stanu krajowego. "
        "Przy małym zoomie dostępne są tylko granice aktów (kolor statusu); strefy od z11, OUZ/OZS/OSDIS od z12."
    ),
)
def get_pog_tile(
    state_id: int,
    z: int,
    x: int,
    y: int,
    request: Request,
    service: Annotated[PogTileService, Depends(get_pog_tile_service)],
    edition: Annotated[str, Query(max_length=20)] = DEFAULT_POG_TILE_EDITION,
) -> Response:
    tile_request = PogTileRequest(state_id=state_id, z=z, x=x, y=y, edition=edition)
    try:
        tile = service.get_tile(tile_request)
    except InvalidPogTileRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PogStateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PogTileTooLargeError as exc:
        raise HTTPException(
            status_code=413,
            detail=str(exc),
            headers={"X-Pog-Tile-Limit": exc.limit},
        ) from exc

    headers = {
        "ETag": tile.etag,
        "Cache-Control": f"public, max-age={settings.pog_tile_browser_ttl_seconds}",
        "X-Tile-Cache": tile.cache_status,
        "X-Pog-State": str(tile.state_id),
        "X-Pog-Edition": tile.edition,
        "X-Pog-Tile-Schema": POG_TILE_SCHEMA_VERSION,
        "X-Pog-Tile-Features": str(tile.feature_count),
    }
    if _etag_matches(request.headers.get("if-none-match"), tile.etag):
        return Response(status_code=304, headers=headers)
    return Response(content=tile.content, media_type=POG_TILE_MEDIA_TYPE, headers=headers)
