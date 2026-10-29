import pytest
from geocoder.search.router import haversine_m, astar, _edge_time_s


def _grid():
    """3x3 grid graph, ids '1'..'9' row-major, residential edges between
    orthogonal neighbours. Returns (nodes, adj) in load_graph() shape."""
    coords = {}
    for r in range(3):
        for c in range(3):
            coords[str(r * 3 + c + 1)] = (23.12 - 0.01 * r, -82.40 + 0.01 * c)
    adj = {k: [] for k in coords}

    def link(a, b):
        la, lo = coords[a]
        lb, ob = coords[b]
        d = haversine_m(la, lo, lb, ob)
        adj[a].append({"to": b, "length_m": d, "highway": "residential",
                       "polyline_wkt": f"LINESTRING ({lo} {la}, {ob} {lb})"})
        adj[b].append({"to": a, "length_m": d, "highway": "residential",
                       "polyline_wkt": f"LINESTRING ({ob} {lb}, {lo} {la})"})

    for r in range(3):
        for c in range(3):
            n = str(r * 3 + c + 1)
            if c < 2:
                link(n, str(r * 3 + c + 2))
            if r < 2:
                link(n, str((r + 1) * 3 + c + 1))
    return coords, adj


def _time_heuristic(nodes, dst):
    dlat, dlon = nodes[dst]
    vmax = 90 * 1000 / 3600
    return lambda nid: haversine_m(nodes[nid][0], nodes[nid][1], dlat, dlon) / vmax


def test_astar_finds_path_corner_to_corner():
    nodes, adj = _grid()
    path, t = astar(nodes, adj, "1", "9", _time_heuristic(nodes, "9"))
    assert path[0] == "1" and path[-1] == "9"
    assert t > 0


def test_astar_equals_dijkstra_oracle():
    # A* with an admissible heuristic must return the same optimal cost as
    # Dijkstra (heuristic ≡ 0) on the same pair.
    nodes, adj = _grid()
    _, t_astar = astar(nodes, adj, "1", "9", _time_heuristic(nodes, "9"))
    _, t_dijkstra = astar(nodes, adj, "1", "9", lambda nid: 0.0)
    assert t_astar == pytest.approx(t_dijkstra, rel=1e-9)


def test_astar_same_node_is_zero():
    nodes, adj = _grid()
    path, t = astar(nodes, adj, "5", "5", lambda nid: 0.0)
    assert path == ["5"] and t == 0.0


def test_astar_disconnected_returns_none():
    nodes, adj = _grid()
    nodes["99"] = (23.20, -82.10)
    adj["99"] = []
    assert astar(nodes, adj, "1", "99", lambda nid: 0.0) is None


def test_edge_time_uses_speed_table():
    # 3600 m at residential 30 km/h ⇒ 432 s; primary 50 km/h ⇒ 72 s for 1 km.
    assert _edge_time_s({"length_m": 3600.0, "highway": "residential"}) == pytest.approx(432.0)
    assert _edge_time_s({"length_m": 1000.0, "highway": "primary"}) == pytest.approx(72.0)


def _grid_db(tmp_path):
    from geocoder.store import GeoStore
    from geocoder.etl.build import build_database
    from pathlib import Path
    FIXG = Path(__file__).parent / "fixtures" / "routing_grid.osm"
    db = tmp_path / "grid.db"
    build_database(str(FIXG), bbox=None, force_rebuild=True, db_path=db)
    return GeoStore(db)


def test_nearest_edge_projects_between_nodes(tmp_path):
    from geocoder.search.router import Router
    r = Router(_grid_db(tmp_path))
    # A point near the middle of the A/Calle-1..Calle-3 edge (between node 2 and
    # node 4, through contracted corner 1) projects onto that edge: 0 < frac < 1.
    hit = r.nearest_edge(23.115, -82.40)
    assert hit is not None
    frac = hit[4]
    assert 0.0 < frac < 1.0


def test_route_snaps_to_edge_near_contracted_corner(tmp_path):
    from geocoder.search.router import Router
    r = Router(_grid_db(tmp_path))
    # Corners (nodes 1 and 9) are degree-2 and contract away; VS1 nearest-node
    # snapping (300 m cap) missed them. Nearest-edge snapping projects onto the
    # edge that passes through the corner, so the route resolves.
    rt = r.route((23.12, -82.40), (23.10, -82.38))
    assert rt.reason == "ok", rt.reason
    assert rt.distance_m > 0
    assert rt.polyline_wkt.startswith("LINESTRING")


def test_store_routing_graph_is_cached(tmp_path):
    s = _grid_db(tmp_path)
    assert s.routing_graph() is s.routing_graph()


def test_pipeline_caches_router(tmp_path):
    from geocoder.search.pipeline import SearchPipeline
    pipe = SearchPipeline(_grid_db(tmp_path))
    assert pipe.router() is pipe.router()


def test_route_from_a_point_exactly_on_a_node():
    """A point projecting exactly onto an edge endpoint used to crash.

    `substring(line, 0.0, 0.0)` returns a Point, not a LineString, and building
    a LineString from its single coordinate raises in GEOS. Reachable from any
    routing endpoint by passing a coordinate that lands on a graph node.
    """
    import tempfile
    from geocoder.store import GeoStore
    from geocoder.search.router import Router

    a, b = (23.130, -82.400), (23.130, -82.380)
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    line = f"LINESTRING({a[1]} {a[0]}, {b[1]} {b[0]})"
    rev = f"LINESTRING({b[1]} {b[0]}, {a[1]} {a[0]})"
    s.put_graph(
        {"1": a, "2": b},
        {"1": [{"to": "2", "length_m": 2040.0, "highway": "residential", "polyline_wkt": line}],
         "2": [{"to": "1", "length_m": 2040.0, "highway": "residential", "polyline_wkt": rev}]},
    )
    rt = Router(s).route(a, b)          # both endpoints sit exactly on nodes
    assert rt.reason == "ok"
    assert rt.distance_m > 0
    s.close()
