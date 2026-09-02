from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.logging import configure_logging
from app.schemas.analyze import ErrorResponse
from app.core.settings import settings
from app.modules.location.api.router import router as address_search_router
from app.modules.documents.api.router import router as documents_router
from app.routers.analyze import router as analyze_router
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
    expose_headers=["Age", "ETag", "X-Tile-Cache"],
)

@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request: Request, exc: RequestValidationError):
    # Endpointy /api/v1/* zwracają błędy walidacji przez wspólny kontrakt
    # ErrorResponse (bez stack trace). Starsze endpointy zachowują dotychczasowy
    # format odpowiedzi FastAPI, aby nie zmieniać istniejącego kontraktu.
    if request.url.path.startswith("/api/v1/"):
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error="VALIDATION_ERROR",
                detail="Nieprawidłowe parametry zapytania.",
                section="search",
            ).model_dump(),
        )
    return await request_validation_exception_handler(request, exc)


app.include_router(analyze_router)
app.include_router(health_router)
app.include_router(geocode_router)
app.include_router(map_tiles_router)
app.include_router(raster_admin_router)
app.include_router(report_router)
app.include_router(address_search_router)
app.include_router(documents_router)
