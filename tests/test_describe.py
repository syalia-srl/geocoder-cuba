"""The describe rung: point -> Cuban-style address, degrading honestly."""
import tempfile

from geocoder.models import Lugar
from geocoder.store import GeoStore
from geocoder.search.describe import describe


def _grid():
    """L runs N-S at lon -82.385; 23 and 25 run E-W crossing it."""
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    s.put_calle_geom("l", None, "LINESTRING(-82.385 23.130, -82.385 23.150)")
    s.put_calle_geom("23", None, "LINESTRING(-82.395 23.138, -82.375 23.138)")
    s.put_calle_geom("25", None, "LINESTRING(-82.395 23.142, -82.375 23.142)")
    return s


def test_point_inside_a_block_is_described_as_a_segment():
    d = describe(_grid(), 23.140, -82.3851)   # on L, between 23 and 25
    assert d is not None
    assert d.method == "describe_segment"
    assert d.calle == "l"
    assert d.entre == ("23", "25")
    assert "e/" in d.texto
    assert 0 < d.radius_m < 400


def test_bracketing_picks_the_two_crossings_around_the_point():
    """A third cross-street outside the block must not be chosen just because
    the point drifts toward one end."""
    s = _grid()
    s.put_calle_geom("27", None, "LINESTRING(-82.395 23.146, -82.375 23.146)")
    d = describe(s, 23.1405, -82.3851)        # still between 23 and 25
    assert d.entre == ("23", "25")
    d2 = describe(s, 23.1440, -82.3851)       # now between 25 and 27
    assert d2.entre == ("25", "27")


def test_street_with_one_crossing_degrades_to_street():
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    s.put_calle_geom("l", None, "LINESTRING(-82.385 23.130, -82.385 23.150)")
    s.put_calle_geom("23", None, "LINESTRING(-82.395 23.138, -82.375 23.138)")
    d = describe(s, 23.145, -82.3851)
    assert d.method == "describe_street"
    assert d.calle == "l"
    assert d.entre is None
    assert "cerca de" in d.texto


def test_far_from_any_street_degrades_to_place():
    s = _grid()
    s.index_lugares([
        Lugar(nombre="Faro", norm_nombre="faro", tipo="poi",
              municipio="Plaza", municipio_norm="plaza", provincia="La Habana",
              lat=23.200, lon=-82.300),
    ])
    d = describe(s, 23.2001, -82.3001)
    assert d.method == "describe_place"
    assert "Faro" in d.texto
    assert d.calle is None


def test_nothing_near_returns_none():
    assert describe(_grid(), 20.0, -75.0) is None
