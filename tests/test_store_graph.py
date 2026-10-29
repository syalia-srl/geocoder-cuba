from geocoder.store import GeoStore


def test_put_and_load_graph_roundtrips(tmp_path):
    db = tmp_path / "g.db"
    store = GeoStore(db)
    nodes = {"1": (23.10, -82.40), "2": (23.11, -82.39)}
    edges = {
        "1": [{"to": "2", "length_m": 1200.0, "highway": "residential",
               "polyline_wkt": "LINESTRING (-82.40 23.10, -82.39 23.11)"}],
        "2": [{"to": "1", "length_m": 1200.0, "highway": "residential",
               "polyline_wkt": "LINESTRING (-82.39 23.11, -82.40 23.10)"}],
    }
    n, e = store.put_graph(nodes, edges)
    assert (n, e) == (2, 2)
    store.close()

    store2 = GeoStore(db)
    gnodes, gadj = store2.load_graph()
    assert gnodes["1"] == (23.10, -82.40)
    assert gadj["2"][0]["to"] == "1"
    assert gadj["2"][0]["length_m"] == 1200.0
    store2.close()
