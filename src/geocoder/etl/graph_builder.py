"""Build a routing graph from OSM highway ways.

OSM ways ARE the topology: a way is an ordered list of node ids, and nodes
shared between ways are the intersections. Node identity = OSM node id, so
connectivity (including across municipio boundaries) is automatic. Degree-2
pass-through nodes are contracted into single edges that keep the full polyline
(for drawing) plus total length + highway type (for cost).
"""
import math
from collections import defaultdict
from shapely.geometry import LineString
from geocoder.config import SETTINGS


def _haversine_m(lon1, lat1, lon2, lat2):
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def build_graph(way_nodes, way_tags, node_cache):
    """Return (nodes {id→(lat,lon)}, edges {id→[edge dict]}) — see put_graph."""
    # 1. Drivable segments as consecutive in-cache node pairs; count node degree.
    #    A segment is [highway, a, b]. Ways carry (lon, lat) in node_cache.
    segments = []
    degree = defaultdict(int)
    seg_by_node = defaultdict(list)  # node id → indices into `segments`
    for wid, refs in way_nodes.items():
        hw = way_tags.get(wid, {}).get("highway")
        if hw not in SETTINGS.DRIVABLE_HIGHWAYS:
            continue
        prev = None
        for r in refs:
            if r not in node_cache:      # node outside bbox ⇒ break the chain
                prev = None
                continue
            if prev is not None:
                idx = len(segments)
                segments.append([hw, prev, r])
                degree[prev] += 1
                degree[r] += 1
                seg_by_node[prev].append(idx)
                seg_by_node[r].append(idx)
            prev = r

    if not segments:
        return {}, {}

    # 2. Significant nodes: degree != 2 (intersections + endpoints). Degree-2
    #    nodes are pass-throughs to contract. A degree-2 node whose two segments
    #    differ in highway type is ALSO significant (so speed stays per-type).
    def significant(nid):
        if degree[nid] != 2:
            return True
        hws = {segments[i][0] for i in seg_by_node[nid]}
        return len(hws) > 1

    sig = {nid for nid in degree if significant(nid)}

    # 3. Walk each stub out of every significant node, contracting degree-2
    #    chains, until the next significant node. Emit one edge per stub.
    nodes = {str(nid): (node_cache[nid][1], node_cache[nid][0]) for nid in sig}
    edges = defaultdict(list)
    seen_stub = set()  # (segment idx, entry node) already walked, so we don't dup

    def other(seg_idx, nid):
        _hw, a, b = segments[seg_idx]
        return b if a == nid else a

    for start in sig:
        for seg_idx in seg_by_node[start]:
            if (seg_idx, start) in seen_stub:
                continue
            first = other(seg_idx, start)
            coords = [node_cache[start], node_cache[first]]
            length = _haversine_m(*node_cache[start], *node_cache[first])
            hw = segments[seg_idx][0]
            prev_seg, cur = seg_idx, first
            # follow the chain through pass-throughs to the next significant node
            while cur not in sig:
                nxts = [i for i in seg_by_node[cur] if i != prev_seg]
                if not nxts:
                    break
                nseg = nxts[0]
                nxt = other(nseg, cur)
                coords.append(node_cache[nxt])
                length += _haversine_m(*node_cache[cur], *node_cache[nxt])
                prev_seg, cur = nseg, nxt
            end = cur
            # discover each chain once (from either end), but emit it in BOTH
            # adjacency directions — the graph is undirected.
            seen_stub.add((seg_idx, start))
            seen_stub.add((prev_seg, end))
            length = round(length, 1)
            edges[str(start)].append({
                "to": str(end), "length_m": length,
                "highway": hw, "polyline_wkt": LineString(coords).wkt,
            })
            edges[str(end)].append({
                "to": str(start), "length_m": length,
                "highway": hw, "polyline_wkt": LineString(coords[::-1]).wkt,
            })

    return nodes, dict(edges)
