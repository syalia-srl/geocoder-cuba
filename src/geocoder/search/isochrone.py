"""How far can you drive in N minutes: multi-source Dijkstra with a time budget.

`reach` is pure graph-in / times-out, like tour.py, so it is tested on graphs
whose answer is known by inspection. Router.isochrone turns its times into
shapes, and the coverage analysis runs the same search with one origin per
centre.
"""
import heapq
import math

from geocoder.search.router import _edge_time_s


def reach(adj, sources, budget_s):
    """Every node reachable within `budget_s` seconds.

    `sources` is a list of (node, start_time_s, origin_id): an origin snapped
    partway along a street enters the graph with the time it takes to reach
    that street's end. Returns {node: (time_s, origin_id)} with the fastest
    origin per node. One-way streets are respected because `adj` is directed.
    """
    best = {}
    pq = []
    for node, t0, origin in sources:
        if t0 <= budget_s and t0 < best.get(node, (math.inf,))[0]:
            best[node] = (t0, origin)
            heapq.heappush(pq, (t0, node, origin))
    while pq:
        t, u, origin = heapq.heappop(pq)
        if t > best[u][0]:
            continue
        for e in adj.get(u, ()):
            nt = t + _edge_time_s(e)
            v = e["to"]
            if nt <= budget_s and nt < best.get(v, (math.inf,))[0]:
                best[v] = (nt, origin)
                heapq.heappush(pq, (nt, v, origin))
    return best


def point_times(reached, snaps):
    """Arrival (time_s, origin_id) at points that sit partway along a street.

    Each snap is (u, v, frac, edge_time_s, two_way) for a point `frac` of the
    way along the directed street u->v, or None for a point off the network. A
    car enters from u and drives frac of the street; on a two-way street it may
    also come from v and drive the rest back. None when neither end was reached.
    """
    out = []
    for snap in snaps:
        best = None
        if snap is not None:
            u, v, frac, te, two_way = snap
            if u in reached:
                best = (reached[u][0] + frac * te, reached[u][1])
            if two_way and v in reached:
                t = reached[v][0] + (1 - frac) * te
                if best is None or t < best[0]:
                    best = (t, reached[v][1])
        out.append(best)
    return out
