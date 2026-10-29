import pytest
from geocoder.search.isochrone import reach

# residential = 30 km/h, so 500 m takes exactly 60 s
_E = {"length_m": 500.0, "highway": "residential", "polyline_wkt": "LINESTRING (0 0, 1 1)"}


def _chain(*names, both=True):
    """a -> b -> c ... with 60 s edges, and the reverse edges when `both`."""
    adj = {n: [] for n in names}
    for a, b in zip(names, names[1:]):
        adj[a].append({**_E, "to": b})
        if both:
            adj[b].append({**_E, "to": a})
    return adj


def test_reach_stops_at_the_budget():
    got = reach(_chain("a", "b", "c", "d"), [("a", 0.0, 0)], budget_s=150)
    assert {n: t for n, (t, _) in got.items()} == pytest.approx({"a": 0.0, "b": 60.0, "c": 120.0})


def test_reach_takes_the_nearest_origin():
    got = reach(_chain("a", "b", "c", "d", "e"), [("a", 0.0, 0), ("e", 0.0, 1)], budget_s=600)
    assert got["b"] == (pytest.approx(60.0), 0)
    assert got["d"] == (pytest.approx(60.0), 1)
    assert got["c"][0] == pytest.approx(120.0)         # a tie: either origin is right


def test_reach_respects_one_way_streets():
    got = reach(_chain("a", "b", both=False), [("b", 0.0, 0)], budget_s=600)
    assert "a" not in got


def test_reach_honours_a_start_offset():
    """An origin snapped mid-street starts partway into its first edge."""
    got = reach(_chain("a", "b", "c"), [("a", 100.0, 0)], budget_s=150)
    assert set(got) == {"a"}
    assert reach(_chain("a", "b"), [("a", 200.0, 0)], budget_s=150) == {}


def _grid_router(tmp_path):
    from pathlib import Path
    from geocoder.etl.build import build_database
    from geocoder.store import GeoStore
    from geocoder.search.router import Router
    fix = Path(__file__).parent / "fixtures" / "routing_grid.osm"
    db = tmp_path / "grid.db"
    build_database(str(fix), bbox=None, force_rebuild=True, db_path=db)
    return Router(GeoStore(db))


def test_isochrone_bands_nest_and_contain_the_origin(tmp_path):
    from shapely.geometry import Point, shape
    r = _grid_router(tmp_path)
    origin = (23.115, -82.40)
    bands = r.isochrone(origin, [1, 3])
    assert [b["minutes"] for b in bands] == [1, 3]
    small, big = (shape(b["geometry"]) for b in bands)
    assert small.is_valid and big.is_valid
    assert small.area < big.area
    assert big.buffer(1e-6).contains(small)
    assert small.contains(Point(origin[1], origin[0]))
    assert 0 < bands[0]["area_km2"] < bands[1]["area_km2"]


def test_isochrone_off_network_is_none(tmp_path):
    r = _grid_router(tmp_path)
    assert r.isochrone((20.0, -75.0), [5]) is None


def test_point_times_enter_the_street_from_either_end():
    from geocoder.search.isochrone import point_times
    reached = {"a": (0.0, 0), "b": (60.0, 1)}
    # a point 25% along a->b (a 60 s street): from a it is 15 s away; back from b
    # it would be 45 s more, but only if the street runs both ways
    two_way = ("a", "b", 0.25, 60.0, True)
    one_way_from_b = ("b", "c", 0.5, 60.0, False)
    t, origin = point_times(reached, [two_way])[0]
    assert (t, origin) == (pytest.approx(15.0), 0)
    assert point_times({"b": (60.0, 1)}, [two_way])[0] == (pytest.approx(105.0), 1)
    assert point_times(reached, [one_way_from_b])[0] == (pytest.approx(90.0), 1)
    assert point_times({"c": (0.0, 0)}, [one_way_from_b]) == [None]   # c cannot drive back
    assert point_times(reached, [None]) == [None]                        # off the network


def test_coverage_assigns_each_point_to_its_nearest_centre(tmp_path):
    r = _grid_router(tmp_path)
    # centres near the NW and SE corners of the 2.2 km grid; points next to each
    got = r.coverage([(23.12, -82.395), (23.10, -82.385)],
                     [(23.119, -82.396), (23.101, -82.384), (20.0, -75.0)], max_s=3600)
    assert [c["ok"] for c in got["centers"]] == [True, True]
    assert got["center"][:2] == [0, 1]
    assert all(m is not None and m < 2 for m in got["minutes"][:2])
    assert got["center"][2] is None and got["minutes"][2] is None      # off the network


def test_coverage_leaves_points_beyond_the_budget_unassigned(tmp_path):
    r = _grid_router(tmp_path)
    got = r.coverage([(23.12, -82.395)], [(23.10, -82.38)], max_s=30)
    assert got["center"] == [None] and got["minutes"] == [None]
