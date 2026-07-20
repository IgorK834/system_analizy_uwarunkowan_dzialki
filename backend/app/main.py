from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.logging import configure_logging
from app.core.settings import settings
from app.routers.analyze import router as analyze_router
from app.routers.geocode import router as geocode_router
from app.routers.health import router as health_router
from app.routers.map_tiles import router as map_tiles_router
from app.services.wms_tiles import wms_tile_proxy

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await wms_tile_proxy.aclose()


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

app.include_router(analyze_router)
app.include_router(health_router)
app.include_router(geocode_router)
app.include_router(map_tiles_router)
