"""Multi-point tour — the optimal order to visit several stops."""
from fastapi import APIRouter, HTTPException, Depends
from shapely import wkt as _wkt
from shapely.geometry import mapping

from geocoder.search.pipeline import SearchPipeline
from geocoder.api.dependencies import get_search_pipeline
from geocoder.api.schemas import TourRequest, TourResponse

router = APIRouter()


# Deliberately sync (`def`, not `async def`): building the cost matrix is N^2
# CPU-bound A* runs. As a coroutine it would freeze the event loop and take
# every other request with it — the trap already hit by /route and /reverse.
@router.post("/tour", response_model=TourResponse,
             summary="Optimal order to visit several stops")
def tour(body: TourRequest,
         pipeline: SearchPipeline = Depends(get_search_pipeline)):
    pts = [(p.lat, p.lon) for p in body.points]
    try:
        t = pipeline.tour(pts, body.return_to_start)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if t.reason == "too_many":
        raise HTTPException(status_code=422, detail="a tour takes at most 12 points")
    if t.reason == "too_few":
        raise HTTPException(status_code=422, detail="a tour needs at least 2 points")
    geometry = None
    if t.reason == "ok" and t.polyline_wkt:
        geometry = mapping(_wkt.loads(t.polyline_wkt))
    return {
        "order": t.order,
        "stops": [{"lat": la, "lon": lo, "nombre": body.points[i].nombre}
                  for i, (la, lo) in zip(t.order, t.stops)] if t.reason == "ok" else [],
        "total_s": round(t.total_s, 1),
        "distance_m": round(t.total_m, 1),
        "duration_min": round(t.total_s / 60, 1),
        "geometry": geometry,
        "reason": t.reason,
    }
