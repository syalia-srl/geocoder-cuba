"""
Health check endpoints.
"""
from fastapi import APIRouter, Depends
from geocoder.store import GeoStore
from geocoder.api.dependencies import get_store

router = APIRouter()


@router.get("/health", summary="Health check")
async def health(store: GeoStore = Depends(get_store)):
    """Health check - verifies database connectivity."""
    try:
        store.ping()
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


@router.get("/ready", summary="Readiness check")
async def ready():
    """Readiness check for Kubernetes."""
    return {"status": "ready"}
