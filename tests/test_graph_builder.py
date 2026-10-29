from geocoder.etl.graph_builder import build_graph


def test_contracts_degree2_chain_into_one_edge():
    # A straight street of 3 nodes 1-2-3; node 2 is a pass-through (degree 2)
    # and must be contracted away, leaving a single 1<->3 edge whose polyline
    # still passes through node 2.
    node_cache = {1: (-82.40, 23.10), 2: (-82.39, 23.10), 3: (-82.38, 23.10)}
    way_nodes = {10: [1, 2, 3]}
    way_tags = {10: {"highway": "residential", "name": "Calle 1"}}

    nodes, edges = build_graph(way_nodes, way_tags, node_cache)

    assert set(nodes) == {"1", "3"}          # node 2 contracted away
    assert len(edges["1"]) == 1
    e = edges["1"][0]
    assert e["to"] == "3"
    assert e["highway"] == "residential"
    assert 1900 < e["length_m"] < 2100        # ~2 km across two 0.01° hops
    assert "-82.39 23.1" in e["polyline_wkt"]  # intermediate node kept in geometry


def test_intersection_node_is_significant():
    # Two crossing streets share node 2 → node 2 is degree 4 → kept as a node.
    node_cache = {1: (-82.40, 23.10), 2: (-82.39, 23.10), 3: (-82.38, 23.10),
                  4: (-82.39, 23.11), 5: (-82.39, 23.09)}
    way_nodes = {10: [1, 2, 3], 20: [4, 2, 5]}
    way_tags = {10: {"highway": "residential", "name": "Calle 1"},
                20: {"highway": "residential", "name": "A"}}

    nodes, edges = build_graph(way_nodes, way_tags, node_cache)

    assert "2" in nodes                       # intersection survives contraction
    assert len(edges["2"]) == 4               # four stubs meet at the crossing


def test_non_drivable_way_is_skipped():
    node_cache = {1: (-82.40, 23.10), 2: (-82.39, 23.10)}
    way_nodes = {10: [1, 2]}
    way_tags = {10: {"highway": "footway", "name": "Sendero"}}
    nodes, edges = build_graph(way_nodes, way_tags, node_cache)
    assert nodes == {} and edges == {}
