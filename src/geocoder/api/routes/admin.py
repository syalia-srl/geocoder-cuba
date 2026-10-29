"""
Admin routes - Municipalities, barrios, stats.
"""
from typing import Literal

from fastapi import APIRouter, HTTPException, Depends, Query
from geocoder.store import GeoStore
from geocoder.api.schemas import MunicipioInfo, BarrioInfo
from geocoder.api.dependencies import get_store

router = APIRouter()


def _barrio_info(i: int, b: dict) -> BarrioInfo:
    return BarrioInfo(
        id=i,
        name=b["name"],
        name_es=b.get("name_es"),
        municipality=b.get("municipio") or "",
        centroid={"lat": b["centroid_lat"], "lon": b["centroid_lon"]},
        place_type=b.get("place_type") or "neighbourhood",
    )


@router.get(
    "/municipios",
    summary="List municipalities",
    description="Get all municipalities with centroids"
)
async def list_municipios(store: GeoStore = Depends(get_store)):
    """List all municipalities."""
    try:
        municipios = store.all_municipios()
        return [MunicipioInfo(
            id=i,
            name=m["name"],
            name_es=m.get("name_es"),
            province=m.get("province") or "",
            centroid={"lat": m["centroid_lat"], "lon": m["centroid_lon"]}
        ) for i, m in enumerate(municipios, 1)]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/municipios/{municipio_id}/barrios",
    summary="List barrios in municipality",
    description="Get all barrios/neighbourhoods for a municipality (resolved "
                "spatially: barrios whose centroid falls inside the municipio)."
)
async def list_barrios(
    municipio_id: int,
    store: GeoStore = Depends(get_store)
):
    """List barrios for a municipality by its /municipios id."""
    try:
        municipios = store.all_municipios()
        if municipio_id < 1 or municipio_id > len(municipios):
            raise HTTPException(status_code=404, detail="Municipio not found")
        name = municipios[municipio_id - 1]["name"]
        barrios = store.barrios_in_municipio(name)
        return [_barrio_info(i, {**b, "municipio": name}) for i, b in enumerate(barrios, 1)]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/barrios",
    summary="List barrios",
    description="List all barrios/neighbourhoods, optionally filtered by "
                "municipio name (?municipio=Plaza de la Revolución)."
)
async def all_barrios(
    municipio: str = Query(None, description="Filter by municipio name"),
    store: GeoStore = Depends(get_store)
):
    """List every barrio, or those inside a named municipio."""
    try:
        if municipio:
            name = store.resolve_municipio(municipio) or municipio
            barrios = [{**b, "municipio": name} for b in store.barrios_in_municipio(name)]
        else:
            barrios = store.all_barrios()
        return [
            _barrio_info(i, b)
            for i, b in enumerate(barrios, 1)
            if b.get("centroid_lat") is not None
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/admin/geometries",
    summary="Administrative shapes",
    description="Simplified GeoJSON of every provincia, municipio or barrio, for "
                "choropleths. Barrios without an OSM polygon come back as a Point "
                "at their centroid.",
)
async def admin_geometries(
    level: Literal["provincia", "municipio", "barrio"] = Query(..., description="Admin level"),
    store: GeoStore = Depends(get_store),
):
    return {"type": "FeatureCollection", "features": store.admin_geometries(level)}


@router.get(
    "/stats",
    summary="Database statistics",
    description="Get database statistics and counts"
)
async def stats(store: GeoStore = Depends(get_store)):
    """Get database statistics."""
    try:
        return store.stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
