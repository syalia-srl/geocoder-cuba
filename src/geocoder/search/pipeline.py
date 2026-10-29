"""Search pipeline — a thin wrapper over the resolver, the single engine.

There is one resolution path: `Resolver.resolve_many`. Addresses, intersections
and areas yield one result; POIs yield up to `limit` ranked candidates. Callers
that want the single best take `[0]`.
"""
import threading
from typing import List
from geocoder.store import GeoStore
from geocoder.models import GeoResult, Tour
from geocoder.config import SETTINGS
from geocoder.search.resolver import Resolver


class SearchPipeline:
    """Complete geocoding pipeline over a single resolver engine."""

    def __init__(self, store: GeoStore):
        self.store = store
        self._router = None
        self._router_lock = threading.Lock()

    @property
    def router_ready(self) -> bool:
        """True once the routing graph is loaded and its edge index built."""
        return self._router is not None

    def router(self):
        """Cached routing engine (loads the graph + builds the edge index once).

        Locked because it is warmed on a background thread while requests may
        already be arriving — without it a route request landing mid-warm would
        build a second graph.
        """
        if self._router is None:
            with self._router_lock:
                if self._router is None:          # re-check: another thread may have won
                    from geocoder.search.router import Router
                    self._router = Router(self.store)
        return self._router

    def warm_router_async(self) -> threading.Thread:
        """Build the routing engine on a daemon thread and return it.

        On full-Cuba data this is ~70 s (352k nodes + an STRtree edge index).
        Doing it inline at startup keeps /health silent for ~90 s, which reads
        as a broken deployment; doing it on the first /route request would block
        the event loop for just as long. Background is the only option that
        costs nobody the wait.
        """
        t = threading.Thread(target=self.router, name="warm-router", daemon=True)
        t.start()
        return t

    def describe(self, lat: float, lon: float):
        """Reverse-geocode a point to an address description (or None)."""
        return Resolver(self.store).describe(lat, lon)

    def resolve_region(self, streets, municipio=None):
        """Resolve the convex polygon enclosed by ≥3 streets (or None)."""
        return Resolver(self.store).resolve_region(streets, municipio)

    # Cap: at 12 points the matrix is 132 warm A* runs (~3 s) and Held-Karp is
    # 2^12*12^2 (instant). Past that the DP explodes — see the spec.
    TOUR_MAX_POINTS = 12

    def tour(self, points, return_to_start: bool):
        """Optimal visiting order over `points` (a list of (lat, lon)).

        Builds an N*N time matrix with pairwise A*, solves it exactly, then
        re-traces the real geometry in the chosen order so the drawn line
        follows streets rather than the matrix's straight edges.
        """
        from geocoder.search.tour import solve_tour
        n = len(points)
        if n < 2:
            return Tour([], [], 0.0, 0.0, "", reason="too_few")
        if n > self.TOUR_MAX_POINTS:
            return Tour([], [], 0.0, 0.0, "", reason="too_many")

        r = self.router()
        if not r.nodes:
            return Tour([], [], 0.0, 0.0, "", reason="empty_graph")

        INF = float("inf")
        legs = {}
        matrix = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                rt = r.route(points[i], points[j])
                if rt.reason != "ok":
                    matrix[i][j] = INF
                else:
                    matrix[i][j] = rt.duration_s
                    legs[(i, j)] = rt

        sol = solve_tour(matrix, return_to_start)
        if not sol.feasible:
            return Tour([], [], 0.0, 0.0, "", reason="unreachable")

        seq = list(sol.order)
        if return_to_start:
            seq.append(0)
        total_s = total_m = 0.0
        coords = []
        for a, b in zip(seq, seq[1:]):
            rt = legs.get((a, b))
            if rt is None:
                return Tour([], [], 0.0, 0.0, "", reason="unreachable")
            total_s += rt.duration_s
            total_m += rt.distance_m
            coords.append(rt.polyline_wkt)

        return Tour(
            order=sol.order,
            stops=[points[i] for i in sol.order],
            total_s=total_s, total_m=total_m,
            polyline_wkt=_merge_lines(coords),
            reason="ok",
        )

    def route(self, from_query, to_query):
        """Resolve two address queries and route between them. Returns
        (Route|None, from_dict|None, to_dict|None) where each dict is
        {query, nombre, lat, lon} (None if that endpoint did not resolve)."""
        def _resolve(qy):
            rs = Resolver(self.store).resolve_many(qy, 1)
            if not rs:
                return None
            r = rs[0]
            return {"query": qy, "nombre": r.nombre, "lat": r.lat, "lon": r.lon}

        a, b = _resolve(from_query), _resolve(to_query)
        if a is None or b is None:
            return None, a, b
        rt = self.router().route((a["lat"], a["lon"]), (b["lat"], b["lon"]))
        return rt, a, b

    def resolve(self, query: str, limit: int = SETTINGS.FINAL_LIMIT) -> List[GeoResult]:
        """Resolve a query to up to `limit` ranked GeoResults (possibly empty)."""
        if not query or not query.strip():
            return []
        results = Resolver(self.store).resolve_many(query, limit)
        return [
            GeoResult(
                nombre=r.nombre, tipo=r.tipo, lat=r.lat, lon=r.lon,
                municipio=r.municipio, provincia=r.provincia, barrio=r.barrio,
                score=r.score, match_type=r.method, geometry_wkt=r.geometry_wkt,
                precision=r.method, radius_m=r.radius_m, resolved=r.resolved,
            )
            for r in results
        ]


def resolve(store: GeoStore, query: str, limit: int = SETTINGS.FINAL_LIMIT) -> List[GeoResult]:
    """Convenience function for a one-off resolution."""
    return SearchPipeline(store).resolve(query, limit)


def _merge_lines(wkts):
    """Concatenate leg polylines into one MULTILINESTRING for drawing.

    Kept as separate members rather than a single LINESTRING: the legs are
    already contiguous, and a MULTILINESTRING survives a leg the tracer left
    empty without silently joining two distant points with a straight line.
    """
    from shapely import wkt as _w
    from shapely.geometry import MultiLineString
    parts = []
    for w in wkts:
        if not w:
            continue
        g = _w.loads(w)
        if g.is_empty:
            continue
        parts.extend(g.geoms if g.geom_type == "MultiLineString" else [g])
    return MultiLineString(parts).wkt if parts else ""
