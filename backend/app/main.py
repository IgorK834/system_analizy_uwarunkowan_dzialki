from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.logging import configure_logging
from app.core.request_id import RequestIdMiddleware
from app.core.settings import settings
from app.modules.location.api.router import router as address_search_router
from app.modules.documents.api.router import router as documents_router
from app.routers.analyze import router as analyze_router
from app.routers.error_handlers import EXPOSED_RESPONSE_HEADERS, register_error_handlers
from app.routers.geocode import router as geocode_router
from app.routers.health import router as health_router
from app.routers.map_tiles import router as map_tiles_router
from app.routers.raster_admin import router as raster_admin_router
from app.routers.report import router as report_router
from app.services.wms_tiles import wms_tile_registry

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await wms_tile_registry.aclose()


app = FastAPI(
    title=settings.app_title,
    version=settings.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "Age",
        "ETag",
        "X-Tile-Cache",
        "X-Pog-Release",
        "X-Pog-Edition",
        "X-Pog-Tile-Schema",
        "X-Pog-Tile-Features",
        # BK-505: front pokazuje hash paczki podawany poza archiwum.
        "X-Audit-Package-SHA256",
        "X-Audit-Exporter-Version",
        # AU-003: front odczytuje identyfikator zgłoszenia i czas oczekiwania po 429.
        *EXPOSED_RESPONSE_HEADERS,
    ],
)
# Dodany po CORS, więc najbardziej zewnętrzny z middleware aplikacji: ``X-Request-ID`` trafia też do
# odpowiedzi na preflight i do odpowiedzi błędów. Handler ``Exception`` (poza middleware) odczytuje
# identyfikator ze ``scope["state"]``.
app.add_middleware(RequestIdMiddleware)

register_error_handlers(app)


app.include_router(analyze_router)
app.include_router(health_router)
app.include_router(geocode_router)
app.include_router(map_tiles_router)
app.include_router(raster_admin_router)
app.include_router(report_router)
app.include_router(address_search_router)
app.include_router(documents_router)
