"""Cluster endpoint — DBSCAN over a batch of points, in metres."""
from typing import List

from fastapi import APIRouter
from pydantic import BaseModel, Field, confloat, conint

from geocoder.search.cluster import dbscan, summarize

router = APIRouter()


class LatLon(BaseModel):
    lat: float
    lon: float


class ClusterRequest(BaseModel):
    points: List[LatLon] = Field(..., min_length=1, max_length=50000)
    radius_m: confloat(gt=0, le=20000) = 500
    min_points: conint(ge=2, le=1000) = 5


@router.post("/cluster", summary="Density clusters (DBSCAN) of a batch of points")
def cluster(body: ClusterRequest):
    """A cluster label per point (0 = biggest, -1 = noise) and, per cluster, its
    size, centroid, hull and hull area. Sync: CPU-bound, runs in the threadpool."""
    pts = [(p.lat, p.lon) for p in body.points]
    labels = dbscan(pts, body.radius_m, body.min_points)
    return {"labels": labels, "clusters": summarize(pts, labels, body.radius_m)}
