"""Region route — the convex polygon enclosed by a set of streets."""
from fastapi import APIRouter, Depends, HTTPException
from geocoder.api.schemas import RegionRequest, RegionResponse
from geocoder.search.pipeline import SearchPipeline
from geocoder.api.dependencies import get_search_pipeline

router = APIRouter()


@router.post(
    "/region",
    response_model=RegionResponse,
    summary="Resolve a region polygon",
    description="Given ≥3 street names, return the convex polygon their pairwise "
                "crossings enclose (vertices, centroid, area, bbox).",
)
async def region(
    body: RegionRequest,
    pipeline: SearchPipeline = Depends(get_search_pipeline),
):
    try:
        reg = pipeline.resolve_region(body.streets, body.municipio)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if reg is None:
        raise HTTPException(status_code=422, detail="the streets do not enclose a region")
    return reg.to_dict()
