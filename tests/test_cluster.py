from geocoder.search.cluster import dbscan

M = 1 / 111_000   # ~1 m in degrees of latitude


def _blob(lat, lon, n, step_m=20):
    """n points in a row, step_m apart (a tight group)."""
    return [(lat + i * step_m * M, lon) for i in range(n)]


def test_two_groups_and_a_stray_point():
    pts = _blob(23.10, -82.40, 6) + _blob(23.20, -82.30, 6) + [(22.0, -80.0)]
    labels = dbscan(pts, radius_m=50, min_points=3)
    assert len(set(labels[:6])) == 1 and len(set(labels[6:12])) == 1
    assert labels[0] != labels[6] and -1 not in labels[:12]
    assert labels[12] == -1                       # the stray one is noise


def test_min_points_turns_small_groups_into_noise():
    pts = _blob(23.10, -82.40, 3)
    assert dbscan(pts, radius_m=50, min_points=4) == [-1, -1, -1]
    assert len(set(dbscan(pts, radius_m=50, min_points=3))) == 1


def test_chains_link_through_core_points_but_border_points_do_not_extend():
    # a row 40 m apart: with radius 50 every inner point has 3 neighbours (itself
    # included) and is core, so the whole chain is one cluster
    assert len(set(dbscan(_blob(23.10, -82.40, 10, step_m=40), radius_m=50, min_points=3))) == 1
    # an end point 45 m past a core is a border point: it joins, but nothing
    # reachable only through it does
    core = _blob(23.10, -82.40, 4, step_m=10)                  # 0..30 m, 4 neighbours each
    border = [(23.10 + 75 * M, -82.40)]                         # 45 m from the last core; 3 neighbours
    beyond = [(23.10 + 115 * M, -82.40)]                        # 40 m from the border only
    labels = dbscan(core + border + beyond, radius_m=50, min_points=4)
    assert labels[4] == labels[0] and labels[5] == -1


def test_distances_are_metres_not_degrees():
    # 30 m east-west at 23°N is ~0.000293° of longitude, more than 30 m of
    # latitude (0.00027°): a degree-based radius would split this pair
    lon_step = 30 / (111_320 * 0.9205)
    pts = [(23.0, -82.0), (23.0, -82.0 + lon_step), (23.0, -82.0 + 2 * lon_step)]
    assert len(set(dbscan(pts, radius_m=31, min_points=3))) == 1


def test_labels_are_numbered_by_size():
    pts = _blob(23.10, -82.40, 4) + _blob(23.20, -82.30, 7)
    labels = dbscan(pts, radius_m=50, min_points=3)
    assert labels[4] == 0 and labels[0] == 1      # the biggest cluster is 0


def test_empty():
    assert dbscan([], radius_m=50, min_points=3) == []


def test_summarize_gives_size_centroid_and_a_hull_around_each_cluster():
    from shapely.geometry import Point, shape
    from geocoder.search.cluster import summarize
    square = [(23.10, -82.40), (23.10 + 100 * M, -82.40),
              (23.10, -82.40 + 100 * M / 0.92), (23.10 + 100 * M, -82.40 + 100 * M / 0.92)]
    pts = square + [(22.0, -80.0)]
    labels = dbscan(pts, radius_m=150, min_points=3)
    [c] = summarize(pts, labels, radius_m=150)
    assert c["id"] == 0 and c["n"] == 4
    assert abs(c["centroid"]["lat"] - (23.10 + 50 * M)) < 1e-5
    assert 0.008 < c["area_km2"] < 0.012                       # ~100 m x 100 m
    hull = shape(c["hull"])
    assert all(hull.contains(Point(lon, lat)) for lat, lon in square)
