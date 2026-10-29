"""Fastest-path routing over the in-memory street graph (A* by time)."""
import heapq
import math
from shapely import wkt as _wkt
from shapely.geometry import Point, LineString
from shapely.strtree import STRtree
from shapely.ops import linemerge, substring
from geocoder.config import SETTINGS
from geocoder.models import Route

_M_PER_DEG = 111_000.0


def _reverse(line):
    """A LineString with its coordinate order reversed."""
    return LineString(list(line.coords)[::-1])


def _as_line(geom, at):
    """Coerce a possibly-degenerate substring into a usable LineString.

    `substring(line, f, f)` returns a *Point* when the two fractions coincide,
    which happens whenever an endpoint projects exactly onto a node — and a
    LineString cannot be built from one coordinate. Emit a zero-length segment
    at `at` instead, matching what _trace already does for an empty path.
    """
    if geom.geom_type == "LineString" and len(geom.coords) > 1:
        return geom
    lat, lon = at
    return LineString([(lon, lat), (lon, lat)])


class _Overlay:
    """Read-only adjacency overlay: `extra` shadows `base` for the handful of
    nodes touched by endpoint splicing, so routing never copies the full 40k-list
    adjacency per query."""
    __slots__ = ("base", "extra")

    def __init__(self, base, extra):
        self.base = base
        self.extra = extra

    def get(self, k, default=()):
        v = self.extra.get(k)
        return v if v is not None else self.base.get(k, default)


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _edge_time_s(edge):
    """Travel time (s) for an edge: length / speed(highway)."""
    kmh = SETTINGS.SPEED_KMH.get(edge["highway"], SETTINGS.DEFAULT_SPEED_KMH)
    return edge["length_m"] / (kmh * 1000 / 3600)


def astar(nodes, adj, src, dst, heuristic):
    """A* over (nodes, adj) minimizing edge time. `heuristic(node_id)` must be
    admissible (≤ true remaining time). heuristic ≡ 0 is Dijkstra. Returns
    (path node-id list, total_time_s) or None if dst is unreachable."""
    if src == dst:
        return [src], 0.0
    g = {src: 0.0}
    came = {}
    pq = [(heuristic(src), 0.0, src)]
    while pq:
        _, gu, u = heapq.heappop(pq)
        if u == dst:
            path = [u]
            while u in came:
                u = came[u]
                path.append(u)
            return path[::-1], gu
        if gu > g.get(u, math.inf):
            continue
        for e in adj.get(u, ()):
            v = e["to"]
            ng = gu + _edge_time_s(e)
            if ng < g.get(v, math.inf):
                g[v] = ng
                came[v] = u
                heapq.heappush(pq, (ng + heuristic(v), ng, v))
    return None


class Router:
    def __init__(self, store):
        self.nodes, self.adj = store.routing_graph()
        self._vmax = SETTINGS.MAX_SPEED_KMH * 1000 / 3600
        self._build_edge_index()

    def _build_edge_index(self):
        """Parse every edge polyline once into an STRtree, so nearest_edge is a
        spatial lookup (O(log E)) instead of an O(E) WKT-parsing scan per query."""
        lines, meta = [], []
        for u, elist in self.adj.items():
            for e in elist:
                lines.append(_wkt.loads(e["polyline_wkt"]))
                meta.append((u, e["to"], e))
        self._edge_lines = lines
        self._edge_meta = meta
        self._edge_tree = STRtree(lines) if lines else None

    def nearest_node(self, lat, lon):
        """Nearest graph node id to (lat, lon), or None if farther than
        SNAP_MAX_M from every node."""
        best, bestd = None, math.inf
        for nid, (nlat, nlon) in self.nodes.items():
            d = haversine_m(lat, lon, nlat, nlon)
            if d < bestd:
                bestd, best = d, nid
        if best is None or bestd > SETTINGS.SNAP_MAX_M:
            return None
        return best

    def nearest_edge(self, lat, lon):
        """Nearest edge to (lat, lon), projected onto its polyline. Returns
        (u, v, edge, line, frac, proj_lat, proj_lon) or None if farther than
        SNAP_MAX_M. `frac` is the normalized position of the projection along
        `line` (the u→v polyline). Uses the prebuilt STRtree (O(log E))."""
        if self._edge_tree is None:
            return None
        pt = Point(lon, lat)
        idx = int(self._edge_tree.nearest(pt))
        line = self._edge_lines[idx]
        if line.distance(pt) * _M_PER_DEG > SETTINGS.SNAP_MAX_M:
            return None
        u, v, e = self._edge_meta[idx]
        frac = line.project(pt, normalized=True)
        proj = line.interpolate(frac, normalized=True)
        return (u, v, e, line, frac, proj.y, proj.x)

    def _heuristic_on(self, nodes, dst):
        dlat, dlon = nodes[dst]
        return lambda nid: haversine_m(
            nodes[nid][0], nodes[nid][1], dlat, dlon) / self._vmax

    def route(self, a_latlon, b_latlon):
        """Route between two (lat, lon) points. The endpoints snap onto the
        nearest edge (projected), spliced into the graph as virtual nodes so the
        route starts/ends at the true snap. Returns a Route; a non-ok `reason`
        means no usable route (empty_graph/off_network/disconnected)."""
        if not self.nodes:
            return Route(0, 0, "", a_latlon, b_latlon, reason="empty_graph")
        ha = self.nearest_edge(*a_latlon)
        hb = self.nearest_edge(*b_latlon)
        if ha is None or hb is None:
            return Route(0, 0, "", a_latlon, b_latlon, reason="off_network")

        # Splice virtual endpoint nodes into an overlay (only the handful of
        # touched adjacency lists are materialized; self.adj stays pristine).
        nodes = dict(self.nodes)
        nodes["__SRC__"] = (ha[5], ha[6])
        nodes["__DST__"] = (hb[5], hb[6])
        extra = {}
        pa = (ha[5], ha[6])
        pb = (hb[5], hb[6])
        self._splice(nodes, extra, "__SRC__", ha)
        self._splice(nodes, extra, "__DST__", hb)
        self._maybe_link_same_edge(extra, ha, hb, "__SRC__", "__DST__")
        adj = _Overlay(self.adj, extra)

        res = astar(nodes, adj, "__SRC__", "__DST__", self._heuristic_on(nodes, "__DST__"))
        if res is None:
            return Route(0, 0, "", pa, pb, reason="disconnected")
        path, duration_s = res
        distance_m, geom = self._trace(adj, nodes, path)
        return Route(distance_m, duration_s, geom, pa, pb, reason="ok")

    # Streets are drawn this wide (m) and merged, so a band is "the area along
    # the streets you can reach", not a hull that swallows sea and empty land.
    ISO_BUFFER_M = 80.0

    def isochrone(self, latlon, minutes):
        """Areas reachable by car from `latlon` within each of `minutes`.

        The origin snaps onto the nearest street, as in route(), and enters the
        graph partway along it. Each band is the union of the reachable street
        pieces (the last street of every branch cut where the time runs out),
        widened by ISO_BUFFER_M. Returns [{minutes, geometry, area_km2, edges}]
        in the order given, or None when the point is off the network.
        """
        import numpy as np
        import shapely
        from shapely.geometry import mapping
        from geocoder.search.isochrone import reach

        hit = self.nearest_edge(*latlon)
        if hit is None:
            return None
        u, v, e, line, frac, _, _ = hit
        te = _edge_time_s(e)
        back = self._two_way(u, v, e)
        sources = [(v, (1 - frac) * te, 0)]
        if back is not None:                 # a two-way street: we can also go back
            sources.append((u, frac * te, 0))
        budgets = [m * 60.0 for m in minutes]
        reached = reach(self.adj, sources, max(budgets))
        line_of = self._line_of_edge()

        r = self.ISO_BUFFER_M / _M_PER_DEG
        cos_lat = math.cos(math.radians(latlon[0]))

        def band(m, budget):
            step = budget / te if te else 1.0
            pieces = [substring(line, frac, min(1.0, frac + step), normalized=True)]
            if back is not None:
                pieces.append(substring(line, max(0.0, frac - step), frac, normalized=True))
            seen, n = set(), 0
            for node, (t, _) in reached.items():
                if t >= budget:
                    continue
                for edge in self.adj.get(node, ()):
                    left, et = budget - t, _edge_time_s(edge)
                    if et <= left:
                        # A two-way street is two directed edges over the same
                        # line; drawing it once gives the same shape in ~60% of
                        # the time (measured: 15 min from Vedado, 6.0 s -> 3.5 s).
                        key = (frozenset((node, edge["to"])), round(edge["length_m"], 1))
                        if key in seen:
                            continue
                        seen.add(key)
                        pieces.append(line_of[id(edge)])
                    else:
                        pieces.append(substring(line_of[id(edge)], 0.0, left / et, normalized=True))
                    n += 1
            lines = np.array([g for g in pieces if g.geom_type == "LineString"], dtype=object)
            area = shapely.union_all(shapely.buffer(lines, r, quad_segs=2)).simplify(r / 4)
            return {"minutes": m, "geometry": mapping(area), "edges": n,
                    "area_km2": round(area.area * (_M_PER_DEG / 1000) ** 2 * cos_lat, 2)}

        # Bands are independent and shapely releases the GIL while it buffers
        # and unions, so they build in parallel: wall time is the widest band's.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=len(budgets)) as pool:
            bands = list(pool.map(band, minutes, budgets))
        return bands

    def _two_way(self, u, v, e):
        """The v->u twin of edge u->v when the street runs both ways, else None."""
        return next((b for b in self.adj.get(v, ()) if b["to"] == u
                     and abs(b["length_m"] - e["length_m"]) < 1e-6), None)

    def coverage(self, centers, points, max_s):
        """Nearest centre by driving time for every point, up to `max_s`.

        One multi-source search from all centres (each snapped onto its street,
        like route()), then every point snaps onto its own street, vectorised so
        a whole spreadsheet costs one STRtree query. Returns
        {"centers": [{"ok"}], "center": [idx|None], "minutes": [float|None]},
        the lists aligned with `points`. None means off the network or beyond
        `max_s`.
        """
        import numpy as np
        import shapely
        from geocoder.search.isochrone import reach, point_times

        sources, ok = [], []
        center_hits = []
        for i, c in enumerate(centers):
            hit = self.nearest_edge(*c)
            ok.append({"ok": hit is not None})
            if hit is None:
                continue
            u, v, e, _, frac, _, _ = hit
            te = _edge_time_s(e)
            sources.append((v, (1 - frac) * te, i))
            if self._two_way(u, v, e) is not None:
                sources.append((u, frac * te, i))
            center_hits.append((i, u, v, frac, te))
        reached = reach(self.adj, sources, max_s)

        snaps = [None] * len(points)
        if points and self._edge_tree is not None:
            pts = shapely.points([(lon, lat) for lat, lon in points])
            idx = self._edge_tree.nearest(pts)
            lines = np.array(self._edge_lines, dtype=object)[idx]
            far = shapely.distance(lines, pts) * _M_PER_DEG > SETTINGS.SNAP_MAX_M
            fracs = shapely.line_locate_point(lines, pts, normalized=True)
            for k, (j, f) in enumerate(zip(idx, fracs)):
                if far[k]:
                    continue
                u, v, e = self._edge_meta[int(j)]
                snaps[k] = (u, v, float(f), _edge_time_s(e), self._two_way(u, v, e) is not None)
        times = point_times(reached, snaps)

        # A point on its centre's own street is reached along it directly,
        # which the node-to-node search above cannot see.
        for k, snap in enumerate(snaps):
            if snap is None:
                continue
            for i, u, v, fc, te in center_hits:
                if (snap[0], snap[1]) != (u, v):
                    continue
                d = snap[2] - fc
                if d >= 0 or snap[4]:
                    t = abs(d) * te
                    if times[k] is None or t < times[k][0]:
                        times[k] = (t, i)

        center, minutes = [], []
        for t in times:
            if t is None or t[0] > max_s:
                center.append(None)
                minutes.append(None)
            else:
                center.append(t[1])
                minutes.append(round(t[0] / 60, 1))
        return {"centers": ok, "center": center, "minutes": minutes}

    def _line_of_edge(self):
        """Edge dict (by identity) -> its parsed polyline, from the edge index."""
        if getattr(self, "_edge_line_by_id", None) is None:
            self._edge_line_by_id = {id(m[2]): ln for m, ln in zip(self._edge_meta, self._edge_lines)}
        return self._edge_line_by_id

    def _splice(self, nodes, extra, vid, hit):
        """Add a virtual node `vid` at the projection, joined to both endpoints
        of its host edge with the two partial polylines + partial lengths. Writes
        into the `extra` overlay (seeding anchor lists from the base adjacency)."""
        u, v, e, line, frac, plat, plon = hit
        nodes[vid] = (plat, plon)
        hw = e["highway"]
        total = e["length_m"]
        # Degenerate when the projection lands on an endpoint (frac 0 or 1):
        # substring returns a Point there, which is not a usable LineString.
        up = _as_line(substring(line, 0.0, frac, normalized=True), (plat, plon))
        vp = _as_line(substring(line, frac, 1.0, normalized=True), (plat, plon))
        len_u, len_v = total * frac, total * (1 - frac)
        extra.setdefault(vid, [])
        extra[vid].append({"to": u, "length_m": len_u, "highway": hw, "polyline_wkt": _reverse(up).wkt})
        extra[vid].append({"to": v, "length_m": len_v, "highway": hw, "polyline_wkt": vp.wkt})
        self._overlay_append(extra, u, {"to": vid, "length_m": len_u, "highway": hw, "polyline_wkt": up.wkt})
        self._overlay_append(extra, v, {"to": vid, "length_m": len_v, "highway": hw, "polyline_wkt": _reverse(vp).wkt})

    def _overlay_append(self, extra, node, edge):
        """Append `edge` to `node`'s adjacency in the overlay, seeding from the
        base adjacency the first time so the node's real edges aren't lost."""
        if node not in extra:
            extra[node] = list(self.adj.get(node, []))
        extra[node].append(edge)

    def _maybe_link_same_edge(self, extra, ha, hb, sid, did):
        """When both endpoints snapped to the same physical edge, connect their
        virtual nodes directly along it (else A* would detour to a shared node)."""
        ua, va, ea, la, fa = ha[0], ha[1], ha[2], ha[3], ha[4]
        ub, vb = hb[0], hb[1]
        if {ua, va} != {ub, vb}:
            return
        pb = Point(hb[6], hb[5])
        fb = la.project(pb, normalized=True)       # project b onto a's line frame
        seg = substring(la, min(fa, fb), max(fa, fb), normalized=True)
        length = ea["length_m"] * abs(fa - fb)
        hw = ea["highway"]
        extra[sid].append({"to": did, "length_m": length, "highway": hw, "polyline_wkt": seg.wkt})
        extra[did].append({"to": sid, "length_m": length, "highway": hw, "polyline_wkt": _reverse(seg).wkt})

    def _trace(self, adj, nodes, path):
        """Total length (m) + merged polyline WKT along a node-id path."""
        total, lines = 0.0, []
        for u, v in zip(path, path[1:]):
            edge = min((e for e in adj.get(u, ()) if e["to"] == v),
                       key=lambda e: e["length_m"])
            total += edge["length_m"]
            lines.append(_wkt.loads(edge["polyline_wkt"]))
        if not lines:
            lat, lon = nodes[path[0]]
            return 0.0, f"LINESTRING ({lon} {lat}, {lon} {lat})"
        merged = linemerge(lines) if len(lines) > 1 else lines[0]
        return total, merged.wkt
