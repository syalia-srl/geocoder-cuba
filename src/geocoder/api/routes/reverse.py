"""
Reverse geocoding - Coordinates to address.
"""
from fastapi import APIRouter, Query, HTTPException, Depends
from geocoder.store import GeoStore
from geocoder.search.pipeline import SearchPipeline
from geocoder.api.dependencies import get_store, get_search_pipeline
from geocoder.api.schemas import ReverseResponse

router = APIRouter()


@router.get(
    "/reverse",
    response_model=ReverseResponse,
    summary="Reverse geocoding",
    description="Find nearest addresses/places for coordinates"
)
# Deliberately sync (`def`, not `async def`): describe() is CPU-bound shapely
# work, and on the first call it may block on the background street-index warm
# (~81 s on full Cuba). FastAPI runs a sync endpoint in a threadpool, so that
# cost falls on this request alone — as `async def` it would freeze the event
# loop and take every other request with it.
def reverse(
    lat: float = Query(..., ge=-90, le=90, description="Latitude"),
    lon: float = Query(..., ge=-180, le=180, description="Longitude"),
    radius: int = Query(100, ge=10, le=5000, description="Search radius in meters"),
    limit: int = Query(5, ge=1, le=20),
    store: GeoStore = Depends(get_store),
    pipeline: SearchPipeline = Depends(get_search_pipeline),
):
    """Reverse geocoding - coordinates to an address plus the nearest places."""
    try:
        results = store.nearest_lugares(lat, lon, radius, limit)
        d = pipeline.describe(lat, lon)
        return {
            "coordinates": {"lat": lat, "lon": lon},
            "address": None if d is None else {
                "texto": d.texto, "method": d.method, "radius_m": d.radius_m,
                "calle": d.calle,
                "entre": list(d.entre) if d.entre else None,
                "municipio": d.municipio, "barrio": d.barrio,
                "provincia": d.provincia,
            },
            "results": [
                {
                    "nombre": r["nombre"],
                    "tipo": r["tipo"],
                    "coordinates": {"lat": r["lat"], "lon": r["lon"]},
                    "municipio": r["municipio"],
                    "provincia": r["provincia"],
                    "barrio": r.get("barrio"),
                    "distance_meters": r["distance_meters"],
                }
                for r in results
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
