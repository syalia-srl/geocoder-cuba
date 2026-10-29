"""Route endpoint — fastest path A→B over the street graph."""
from fastapi import APIRouter, Depends, HTTPException
from shapely import wkt as _wkt
from shapely.geometry import mapping
from geocoder.api.schemas import RouteRequest, RouteResponse
from geocoder.search.pipeline import SearchPipeline
from geocoder.api.dependencies import get_search_pipeline

router = APIRouter()


@router.post("/route", response_model=RouteResponse,
             summary="Fastest route between two addresses")
# Deliberately sync (`def`, not `async def`): pipeline.route() is CPU-bound A*
# over the street graph, and during the first ~70 s it may also block on the
# background routing-graph warm. FastAPI runs a sync endpoint in a threadpool, so
# that wait costs this request only — as `async def` it would stall the whole
# event loop and take every other request down with it.
def route(body: RouteRequest,
          pipeline: SearchPipeline = Depends(get_search_pipeline)):
    try:
        rt, a, b = pipeline.route(body.from_, body.to)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if a is None:
        raise HTTPException(status_code=422, detail=f"could not resolve origin: {body.from_}")
    if b is None:
        raise HTTPException(status_code=422, detail=f"could not resolve destination: {body.to}")
    if rt is None or rt.reason != "ok":
        raise HTTPException(status_code=422, detail=f"no route: {rt.reason if rt else 'unknown'}")
    geometry = mapping(_wkt.loads(rt.polyline_wkt)) if rt.polyline_wkt else None
    return {
        "distance_m": round(rt.distance_m, 1),
        "duration_s": round(rt.duration_s, 1),
        "duration_min": round(rt.duration_s / 60, 1),
        "from": a, "to": b, "geometry": geometry,
    }
