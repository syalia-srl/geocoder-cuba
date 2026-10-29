"""Unit tests for point-in-polygon enrichment helpers."""
from shapely.geometry import Point, Polygon
from geocoder.models import Barrio, Municipio
from geocoder.etl.enrich import (
    build_barrio_tree, find_containing_barrio,
    build_municipio_tree, find_containing_municipio,
)


def test_municipio_point_in_polygon():
    # a unit square around (0.5, 0.5); a point inside must resolve to it
    m = Municipio(name="Plaza", province="La Habana",
                  polygon=Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                  centroid=Point(0.5, 0.5))
    idx = build_municipio_tree([m])
    assert find_containing_municipio(0.5, 0.5, idx, [m]) == "Plaza"  # lat,lon inside


def test_barrio_polygon_wins():
    poly = Barrio(name="Vedado", polygon=Polygon([(-1, -1), (1, -1), (1, 1), (-1, 1)]),
                  centroid=Point(0, 0))
    idx = build_barrio_tree([poly])
    assert find_containing_barrio(0.0, 0.0, idx) == "Vedado"


def test_barrio_node_fallback_within_cap():
    # node-only barrio (no polygon) — must be found via nearest-centroid fallback
    node = Barrio(name="Cayo Hueso", polygon=None, centroid=Point(-82.38, 23.14))
    idx = build_barrio_tree([node])
    assert idx["tree"] is None  # no polygons at all
    # a point ~0.005 deg away (~0.5 km) is within the 0.02 cap
    assert find_containing_barrio(23.142, -82.383, idx) == "Cayo Hueso"


def test_barrio_node_fallback_beyond_cap_is_none():
    node = Barrio(name="Cayo Hueso", polygon=None, centroid=Point(-82.38, 23.14))
    idx = build_barrio_tree([node])
    # ~0.1 deg away (~11 km) is well beyond the cap -> no barrio
    assert find_containing_barrio(23.24, -82.28, idx) is None
