"""Reverse geocoding: a point -> a Cuban-style address.

The inverse of the address ladder in `resolver.py`. Cuban addresses are
relations between streets ("Calle 23 e/ L y M"), so describing a point means
naming the street it sits on and the two crossings that bracket it. Where the
crossing data is thin (as measured across the Havana periphery), this degrades to the
street, then to the nearest place, rather than inventing a corner.
"""
import math
from dataclasses import dataclass
from typing import Optional

from shapely.geometry import Point
from shapely.ops import nearest_points

from geocoder.config import SETTINGS

_M_PER_DEG = 111_000.0


@dataclass
class Described:
    texto: str
    method: str                      # describe_segment | describe_street | describe_place
    radius_m: float
    lat: float
    lon: float
    calle: Optional[str] = None
    entre: Optional[tuple] = None
    municipio: str = ""
    barrio: Optional[str] = None
    provincia: Optional[str] = None


def _dist_m(a: Point, b: Point) -> float:
    dx = (b.x - a.x) * _M_PER_DEG * math.cos(math.radians((a.y + b.y) / 2))
    dy = (b.y - a.y) * _M_PER_DEG
    return math.hypot(dx, dy)


def _deg_to_m(pt: Point) -> float:
    """Metres per degree of shapely arc length near this point."""
    return _M_PER_DEG * max(math.cos(math.radians(pt.y)), 0.1)


def _bbox_radius_m(geom) -> float:
    minx, miny, maxx, maxy = geom.bounds
    dx = (maxx - minx) * _M_PER_DEG * math.cos(math.radians((miny + maxy) / 2))
    dy = (maxy - miny) * _M_PER_DEG
    return math.hypot(dx, dy) / 2


def describe(store, lat: float, lon: float) -> Optional[Described]:
    """Best honest description of (lat, lon), or None if nothing is near."""
    pt = Point(lon, lat)
    hit = _nearest_street(store, pt)
    if hit is not None:
        street_norm, municipio, geom = hit
        crossings = _crossings_along(store, street_norm, geom)
        bounds = _bracket(geom, pt, crossings)
        if bounds is not None:
            (a_name, a_pos), (b_name, b_pos) = bounds
            span_m = abs(b_pos - a_pos) * _deg_to_m(pt)
            return Described(
                texto=f"{street_norm} e/ {a_name} y {b_name}",
                method="describe_segment",
                radius_m=max(span_m / 2, 20.0),
                lat=lat, lon=lon, calle=street_norm, entre=(a_name, b_name),
                municipio=municipio,
            )
        return Described(
            texto=f"cerca de {street_norm}",
            method="describe_street",
            radius_m=max(_bbox_radius_m(geom), 30.0),
            lat=lat, lon=lon, calle=street_norm, municipio=municipio,
        )

    near = store.nearest_lugares(lat, lon, 300, 1)
    if near:
        p = near[0]
        return Described(
            texto=f"cerca de {p['nombre']}",
            method="describe_place",
            radius_m=max(p["distance_meters"], 25.0),
            lat=lat, lon=lon,
            municipio=p.get("municipio") or "", barrio=p.get("barrio"),
            provincia=p.get("provincia"),
        )
    return None


def _nearest_street(store, pt: Point):
    """(street_norm, municipio, geom) of the nearest street within the cutoff."""
    tree, rows = store.street_tree()
    if not rows:
        return None
    idx = tree.nearest(pt)
    if idx is None:
        return None
    street_norm, municipio, geom = rows[int(idx)]
    p_on, _ = nearest_points(geom, pt)
    if _dist_m(p_on, pt) > SETTINGS.DESCRIBE_MAX_STREET_M:
        return None
    return street_norm, municipio, geom


def _crossings_along(store, street_norm, geom):
    """[(other_street_norm, arc_position)] where other streets cross this one."""
    tree, rows = store.street_tree()
    tol = SETTINGS.INTERSECTION_TOLERANCE
    buf = geom.buffer(tol)
    out = []
    for j in tree.query(buf, predicate="intersects"):
        other_norm, _, other_geom = rows[int(j)]
        if other_norm == street_norm:
            continue
        inter = buf.intersection(other_geom.buffer(tol))
        if inter.is_empty:
            continue
        c = inter.centroid
        if c.is_empty:
            continue
        out.append((other_norm, geom.project(c)))
    out.sort(key=lambda x: x[1])
    return out


def _bracket(geom, pt: Point, crossings):
    """The crossing before and the crossing after the point, or None.

    Bracketing, not nearest-two: a point drifting toward one end of a block must
    still be named by the crossings on either side of it.
    """
    if len(crossings) < 2:
        return None
    pos = geom.project(pt)
    before = [c for c in crossings if c[1] <= pos]
    after = [c for c in crossings if c[1] > pos]
    if not before or not after:
        return None
    return before[-1], after[0]
