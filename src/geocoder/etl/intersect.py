"""Precompute all street crossings once, from merged street geometries.

Runs in the ETL (after streets are merged per name/municipio) instead of at
query time. The runtime resolver reads the resulting table and only falls back
to geometry for pairs that aren't precomputed.
"""
from shapely import wkt as _wkt
from shapely.strtree import STRtree
from geocoder.config import SETTINGS


def compute_intersections(merged, muni_prov):
    """Every crossing between two differently-named streets.

    Args:
        merged: iterable of (street_norm, municipio, wkt) — one merged geometry
            per (street name, municipio), as produced by the ETL street merge.
        muni_prov: {municipio_name: provincia} for stamping the province.

    Returns:
        list of canonical crossing records:
        {"a", "b", "muni", "prov", "lat", "lon"}.
    """
    geoms, meta = [], []
    tol = SETTINGS.INTERSECTION_TOLERANCE
    for sn, mun, w in merged:
        try:
            g = _wkt.loads(w)
        except Exception:
            continue
        if g.is_empty:
            continue
        geoms.append(g.buffer(tol))
        meta.append((sn, mun))
    if not geoms:
        return []
    tree = STRtree(geoms)
    out = []
    for i, gi in enumerate(geoms):
        sn_i, mun_i = meta[i]
        for j in tree.query(gi, predicate="intersects"):
            j = int(j)
            if j <= i:            # unordered pairs, once
                continue
            sn_j, mun_j = meta[j]
            if sn_i == sn_j:      # same street name → not a corner
                continue
            inter = gi.intersection(geoms[j])
            if inter.is_empty:
                continue
            c = inter.centroid
            if c.is_empty:
                continue
            out.append({
                "a": sn_i, "b": sn_j, "muni": mun_i,
                "prov": muni_prov.get(mun_i, SETTINGS.TARGET_PROVINCE),
                "lat": c.y, "lon": c.x,
            })
    return out
