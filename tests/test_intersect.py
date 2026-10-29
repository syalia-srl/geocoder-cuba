"""Unit tests for the pure intersection precompute (no DB)."""
from shapely.geometry import LineString
from geocoder.etl.intersect import compute_intersections


def test_two_streets_that_cross():
    # A vertical, B horizontal → cross at (0, 0)
    a = LineString([(0, -1), (0, 1)]).wkt
    b = LineString([(-1, 0), (1, 0)]).wkt
    merged = [("a", "M", a), ("b", "M", b)]
    recs = compute_intersections(merged, {"M": "La Habana"})
    assert len(recs) == 1
    r = recs[0]
    assert {r["a"], r["b"]} == {"a", "b"}
    assert r["muni"] == "M" and r["prov"] == "La Habana"
    assert abs(r["lat"]) < 1e-6 and abs(r["lon"]) < 1e-6


def test_parallel_streets_do_not_cross():
    a = LineString([(0, 0), (0, 1)]).wkt
    b = LineString([(1, 0), (1, 1)]).wkt
    assert compute_intersections([("a", "M", a), ("b", "M", b)], {"M": "x"}) == []


def test_same_street_name_is_not_a_corner():
    # two fragments of the same street that touch must not become an intersection
    a = LineString([(0, 0), (0, 1)]).wkt
    a2 = LineString([(0, 1), (1, 1)]).wkt
    assert compute_intersections([("23", "M", a), ("23", "M", a2)], {"M": "x"}) == []


def test_province_defaults_when_muni_unknown():
    a = LineString([(0, -1), (0, 1)]).wkt
    b = LineString([(-1, 0), (1, 0)]).wkt
    recs = compute_intersections([("a", "Z", a), ("b", "Z", b)], {})
    assert recs and recs[0]["prov"] == "La Habana"  # SETTINGS.TARGET_PROVINCE
