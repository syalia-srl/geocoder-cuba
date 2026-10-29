"""DBSCAN over geocoded points, in metres, with numpy + shapely only.

The air-gapped bundle ships no scikit-learn, and it does not need one: the
neighbour search is one vectorised STRtree query and the cluster merge is a
union-find over core-core pairs.
"""
import math

import numpy as np
import shapely
from shapely.strtree import STRtree

_M_PER_DEG_LAT = 110_574.0
_M_PER_DEG_LON_EQ = 111_320.0


def dbscan(latlons, radius_m, min_points):
    """Cluster label per point: 0, 1, ... numbered by cluster size (biggest
    first), -1 for noise.

    Classic DBSCAN: a point with at least `min_points` points (itself included)
    within `radius_m` is core; cores within `radius_m` of each other share a
    cluster; a non-core point within `radius_m` of a core joins that core's
    cluster but extends it no further. Distances are metres on a local
    equirectangular projection, accurate to well under 1% across Cuba.
    """
    n = len(latlons)
    if n == 0:
        return []
    lat = np.array([p[0] for p in latlons], dtype=float)
    lon = np.array([p[1] for p in latlons], dtype=float)
    kx = _M_PER_DEG_LON_EQ * math.cos(math.radians(float(lat.mean())))
    pts = shapely.points(lon * kx, lat * _M_PER_DEG_LAT)
    left, right = STRtree(pts).query(pts, predicate="dwithin", distance=radius_m)
    core = np.bincount(left, minlength=n) >= min_points

    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    both = core[left] & core[right]
    for a, b in zip(left[both].tolist(), right[both].tolist()):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    root = [-1] * n
    for i in np.flatnonzero(core).tolist():
        root[i] = find(i)
    # border points: the first core neighbour's cluster
    attach = ~core[left] & core[right]
    for a, b in zip(left[attach].tolist(), right[attach].tolist()):
        if root[a] == -1:
            root[a] = root[b]

    sizes = {}
    for r in root:
        if r != -1:
            sizes[r] = sizes.get(r, 0) + 1
    order = sorted(sizes, key=lambda r: (-sizes[r], r))
    rank = {r: k for k, r in enumerate(order)}
    return [rank[r] if r != -1 else -1 for r in root]


def summarize(latlons, labels, radius_m):
    """Per cluster: id, n, centroid, area_km2 (convex hull, metres) and a hull
    to draw (the convex hull widened by radius_m / 2, so a two-point cluster is
    still a visible shape). Ordered by id."""
    from shapely.geometry import MultiPoint, mapping

    groups = {}
    for (lat, lon), c in zip(latlons, labels):
        if c != -1:
            groups.setdefault(c, []).append((lon, lat))
    out = []
    for c in sorted(groups):
        g = groups[c]
        mlat = sum(p[1] for p in g) / len(g)
        kx = _M_PER_DEG_LON_EQ * math.cos(math.radians(mlat))
        metres = MultiPoint([(x * kx, y * _M_PER_DEG_LAT) for x, y in g]).convex_hull
        pad = radius_m / 2 / _M_PER_DEG_LAT
        out.append({
            "id": c, "n": len(g),
            "centroid": {"lat": mlat, "lon": sum(p[0] for p in g) / len(g)},
            "area_km2": round(metres.area / 1e6, 3),
            "hull": mapping(MultiPoint(g).convex_hull.buffer(pad, quad_segs=4).simplify(pad / 4)),
        })
    return out
