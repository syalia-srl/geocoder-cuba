"""
FastAPI Application - Main entry point with lifespan and DI.
"""
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from geocoder import __version__
from geocoder.config import SETTINGS
from geocoder.api.dependencies import init_dependencies, close_dependencies
from geocoder.api.routes import geocode, reverse, admin, health, region, route, tour, isochrone, cluster

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - initialize resources on startup."""
    if not getattr(app.state, "_test", False):
        if not SETTINGS.DB_PATH.exists():
            raise RuntimeError(f"Database not found: {SETTINGS.DB_PATH}. Run 'geocoder build' first.")
        init_dependencies(str(SETTINGS.DB_PATH))

    yield

    # Cleanup
    close_dependencies()


app = FastAPI(
    title="Cuba Geocoder API",
    description="Offline geocoding service for Cuba based on OpenStreetMap",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(geocode.router, prefix="/api/v1", tags=["geocode"])
app.include_router(reverse.router, prefix="/api/v1", tags=["reverse"])
app.include_router(admin.router, prefix="/api/v1", tags=["admin"])
app.include_router(region.router, prefix="/api/v1", tags=["region"])
app.include_router(route.router, prefix="/api/v1", tags=["route"])
app.include_router(tour.router, prefix="/api/v1", tags=["tour"])
app.include_router(isochrone.router, prefix="/api/v1", tags=["isochrone"])
app.include_router(cluster.router, prefix="/api/v1", tags=["cluster"])
# health is exposed at both /health (probes) and /api/v1/health (docs use this)
app.include_router(health.router, tags=["health"])
app.include_router(health.router, prefix="/api/v1", tags=["health"])


@app.get("/api", tags=["meta"])
async def api_info():
    """Service metadata (the web UI is served at `/`)."""
    return {
        "service": "Cuba Geocoder",
        "version": __version__,
        "docs": "/docs",
        "health": "/api/v1/health",
    }


# Web UI (MapLibre map + resolver console) served at the root. Mounted last so the
# API routes above take precedence; `html=True` serves index.html at `/` and the
# static assets under /static.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
# Offline basemap tiles (PMTiles) — mounted before the "/" catch-all so it wins.
# Starlette's StaticFiles serves HTTP Range (206), which pmtiles.js needs.
_TILES_DIR = SETTINGS.DATA_DIR / "tiles"
_TILES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/tiles", StaticFiles(directory=str(_TILES_DIR)), name="tiles")
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui")
