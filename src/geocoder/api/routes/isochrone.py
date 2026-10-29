"""Isochrone endpoint — the area reachable by car within N minutes of a point."""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, conint

from geocoder.api.dependencies import get_search_pipeline
from geocoder.search.pipeline import SearchPipeline

router = APIRouter()


class IsochroneRequest(BaseModel):
    lat: float
    lon: float
    # Capped: a band's cost grows with the area it covers, and past ~30 min by
    # car it spans a province and stops being a useful picture.
    minutes: List[conint(ge=1, le=30)] = Field(..., min_length=1, max_length=3,
                                               examples=[[5, 10, 15]])


@router.post("/isochrone", summary="Area reachable by car within N minutes")
# Sync on purpose, like /route: CPU-bound search over the street graph, and it
# may wait on the background graph warm; the threadpool keeps that off the loop.
def isochrone(body: IsochroneRequest,
              pipeline: SearchPipeline = Depends(get_search_pipeline)):
    minutes = sorted(set(body.minutes), reverse=True)   # widest first, so it draws under
    try:
        bands = pipeline.router().isochrone((body.lat, body.lon), minutes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if bands is None:
        raise HTTPException(status_code=422, detail="off_network: the point is not near any street")
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": b["geometry"],
         "properties": {"minutes": b["minutes"], "area_km2": b["area_km2"], "edges": b["edges"]}}
        for b in bands]}


class LatLon(BaseModel):
    lat: float
    lon: float


class CoverageRequest(BaseModel):
    # Eight centres: past that the dots cannot carry one distinguishable colour
    # each on the map (the categorical palette has eight validated slots).
    centers: List[LatLon] = Field(..., min_length=1, max_length=8)
    points: List[LatLon] = Field(..., min_length=1, max_length=20000)
    max_minutes: conint(ge=1, le=120) = 60


@router.post("/coverage", summary="Nearest centre by driving time for many points")
def coverage(body: CoverageRequest,
             pipeline: SearchPipeline = Depends(get_search_pipeline)):
    """For each point: the index of the centre it reaches fastest by car and
    the minutes it takes, or null when off the network or beyond max_minutes."""
    try:
        return pipeline.router().coverage(
            [(c.lat, c.lon) for c in body.centers],
            [(p.lat, p.lon) for p in body.points], body.max_minutes * 60.0)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
