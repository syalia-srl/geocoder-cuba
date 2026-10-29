"""Held-Karp tour solver — pure matrix in, optimal order out."""
import math
import tempfile

from geocoder.store import GeoStore
from geocoder.search.pipeline import SearchPipeline
from geocoder.search.tour import solve_tour


def test_single_point_is_a_trivial_tour():
    r = solve_tour([[0.0]], return_to_start=False)
    assert r.order == [0] and r.total_cost == 0.0 and r.feasible


def test_open_path_picks_the_cheaper_order():
    # 0->1 = 1, 1->2 = 1  (total 2)   vs   0->2 = 9, 2->1 = 9
    m = [
        [0.0, 1.0, 9.0],
        [1.0, 0.0, 1.0],
        [9.0, 1.0, 0.0],
    ]
    r = solve_tour(m, return_to_start=False)
    assert r.order == [0, 1, 2]
    assert r.total_cost == 2.0


def test_closed_tour_includes_the_return_leg():
    m = [
        [0.0, 1.0, 9.0],
        [1.0, 0.0, 1.0],
        [9.0, 1.0, 0.0],
    ]
    r = solve_tour(m, return_to_start=True)
    # 0->1->2->0 = 1+1+9 = 11 ; 0->2->1->0 = 9+1+1 = 11 — both are optimal
    assert r.total_cost == 11.0
    assert r.order[0] == 0 and sorted(r.order) == [0, 1, 2]


def test_asymmetric_matrix_beats_the_naive_order():
    """One-way streets make i->j != j->i; the solver must not assume symmetry."""
    m = [
        [0.0, 1.0, 1.0],
        [50.0, 0.0, 1.0],   # leaving 1 is expensive except to 2
        [1.0, 50.0, 0.0],   # leaving 2 is cheap only back to 0
    ]
    r = solve_tour(m, return_to_start=True)
    assert r.order == [0, 1, 2]        # 0->1->2->0 = 1+1+1 = 3
    assert r.total_cost == 3.0


def test_unreachable_stop_is_reported_infeasible():
    inf = float("inf")
    m = [
        [0.0, 1.0, inf],
        [1.0, 0.0, inf],
        [inf, inf, 0.0],   # nothing can reach 2
    ]
    r = solve_tour(m, return_to_start=False)
    assert r.feasible is False


def test_larger_instance_is_optimal_and_visits_everything():
    """A ring where consecutive hops cost 1 and every other pair costs 10."""
    n = 8
    m = [[10.0] * n for _ in range(n)]
    for i in range(n):
        m[i][i] = 0.0
        m[i][(i + 1) % n] = 1.0
    r = solve_tour(m, return_to_start=True)
    assert sorted(r.order) == list(range(n))
    assert r.order[0] == 0
    assert r.total_cost == float(n)      # all-consecutive ring = n hops of 1
    assert math.isfinite(r.total_cost)

# The router reads graph_nodes/graph_edges, NOT calles_geom — a store with only
# street geometry yields an empty graph and every tour returns "empty_graph".
# Build a real square graph so the ordering assertions actually exercise A*.
#
#   SW(0) ---- 2040 m ---- SE(1)
#    |                      |
#  1110 m                 1110 m
#    |                      |
#   NW(3) ---- 2040 m ---- NE(2)
SW, SE, NE, NW = (23.130, -82.400), (23.130, -82.380), (23.140, -82.380), (23.140, -82.400)


def _square_store():
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    nodes = {"1": SW, "2": SE, "3": NE, "4": NW}
    ring = [("1", "2", 2040.0), ("2", "3", 1110.0), ("3", "4", 2040.0), ("4", "1", 1110.0)]
    edges = {k: [] for k in nodes}

    def line(a, b):
        (la1, lo1), (la2, lo2) = nodes[a], nodes[b]
        return f"LINESTRING({lo1} {la1}, {lo2} {la2})"

    for a, b, m in ring:                       # bidirectional, same highway type
        edges[a].append({"to": b, "length_m": m, "highway": "residential",
                         "polyline_wkt": line(a, b)})
        edges[b].append({"to": a, "length_m": m, "highway": "residential",
                         "polyline_wkt": line(b, a)})
    s.put_graph(nodes, edges)
    return s


def test_tour_rejects_too_many_points():
    p = SearchPipeline(_square_store())
    pts = [(23.13 + i * 0.001, -82.40) for i in range(13)]
    t = p.tour(pts, return_to_start=False)
    assert t.reason == "too_many"


def test_tour_rejects_too_few_points():
    p = SearchPipeline(_square_store())
    t = p.tour([SW], return_to_start=False)
    assert t.reason == "too_few"


def test_tour_picks_the_cheaper_visiting_order():
    """From SW, visiting SE and NW: going NW first is shorter.

    SW->SE->NW = 2040 + (SE->NE->NW = 1110+2040) = 5190
    SW->NW->SE = 1110 + (NW->NE->SE = 2040+1110) = 4260   <- optimal
    All edges share a highway type, so time is proportional to distance.
    """
    p = SearchPipeline(_square_store())
    t = p.tour([SW, SE, NW], return_to_start=False)
    assert t.reason == "ok", t.reason
    assert t.order == [0, 2, 1]
    assert t.stops[0] == SW
    assert t.total_m > 0 and t.total_s > 0


def test_closed_tour_is_longer_than_the_open_one():
    p = SearchPipeline(_square_store())
    open_t = p.tour([SW, SE, NW], return_to_start=False)
    closed = p.tour([SW, SE, NW], return_to_start=True)
    assert open_t.reason == "ok" and closed.reason == "ok"
    assert closed.total_m > open_t.total_m      # the return leg is real
