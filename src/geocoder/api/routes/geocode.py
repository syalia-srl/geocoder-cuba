"""
Resolve routes — the single geocoding endpoint. Always returns a list of
results (one for addresses/intersections/areas; up to `limit` for POIs).
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import JSONResponse
from shapely import wkt as _wkt
from shapely.geometry import mapping as _mapping
from geocoder.api.schemas import ResolveResponse, BatchResolveRequest, BatchResolveResponse
from geocoder.search.pipeline import SearchPipeline
from geocoder.api.dependencies import get_search_pipeline

router = APIRouter()


def _area_geometry(wkt_str):
    """WKT → lightly simplified GeoJSON geometry (or None). Never raises."""
    if not wkt_str:
        return None
    try:
        geom = _wkt.loads(wkt_str)
        geom = geom.simplify(0.0005, preserve_topology=True)
        if geom.is_empty:
            return None
        return _mapping(geom)
    except Exception:
        return None


def _resolve_list(pipeline: SearchPipeline, q: str, limit: int) -> list[dict]:
    """Run the resolver → list of ResolveResult dicts (possibly empty)."""
    out = []
    for r in pipeline.resolve(q, limit=limit):
        out.append({
            "nombre": r.nombre, "tipo": r.tipo,
            "coordinates": {"lat": r.lat, "lon": r.lon},
            "municipio": r.municipio, "provincia": r.provincia, "barrio": r.barrio,
            "precision": r.precision, "radius_m": r.radius_m, "resolved": r.resolved,
            "geometry": _area_geometry(r.geometry_wkt),
        })
    return out


def _as_geojson(q: str, results: list[dict]) -> dict:
    """Wrap results as a GeoJSON FeatureCollection."""
    feats = []
    for res in results:
        c = res["coordinates"]
        props = {k: v for k, v in res.items() if k != "coordinates"}
        props["query"] = q
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]},
            "properties": props,
        })
    return {"type": "FeatureCollection", "features": feats}


@router.get(
    "/resolve",
    response_model=ResolveResponse,
    summary="Resolve a location",
    description="Resolve a Cuban address / place / area. Always returns a list of "
                "results (one for an address/intersection/area, up to `limit` for a "
                "POI/name). Pass `format=geojson` for a GeoJSON FeatureCollection.",
)
async def resolve(
    q: str = Query(..., min_length=1, max_length=200, description="Address / place / area"),
    limit: int = Query(5, ge=1, le=50, description="Max results (POI/name queries)"),
    format: str = Query("json", pattern="^(json|geojson)$", description="Response format"),
    pipeline: SearchPipeline = Depends(get_search_pipeline),
):
    """Unified resolver: `Calle L e/ 23 y 25, Vedado`, `Hotel Nacional`, `Vedado`, …"""
    try:
        results = _resolve_list(pipeline, q, limit)
        if format == "geojson":
            return JSONResponse(_as_geojson(q, results))
        return ResolveResponse(query=q, results=results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/resolve/batch",
    response_model=BatchResolveResponse,
    summary="Resolve many locations",
    description="Resolve up to 100 queries in one call; one entry per query, in "
                "input order (each carries its own `results` list).",
)
async def resolve_batch(
    body: BatchResolveRequest,
    limit: int = Query(1, ge=1, le=50, description="Max results per query"),
    pipeline: SearchPipeline = Depends(get_search_pipeline),
):
    """Batch resolver — one ResolveResponse per input query, in order. A query
    that raises gets an empty result plus `error`; the rest of the batch stands."""
    results = []
    for qi in body.queries:
        try:
            results.append(ResolveResponse(query=qi, results=_resolve_list(pipeline, qi, limit)))
        except Exception as e:
            results.append(ResolveResponse(query=qi, error=str(e)))
    return BatchResolveResponse(count=len(results), results=results)
