"""Integration tests for the search pipeline wired onto GeoStore."""
import tempfile
from geocoder.store import GeoStore
from geocoder.models import Lugar, Portal
from geocoder.search.pipeline import SearchPipeline


def _pipe():
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    s.index_lugares([
        Lugar(nombre="Calle 23", norm_nombre="calle 23", tipo="calle",
              municipio="Plaza de la Revolución", municipio_norm="plaza de la revolucion",
              provincia="La Habana", lat=23.14, lon=-82.38, street_norm="23"),
        Lugar(nombre="Hotel Nacional", norm_nombre="hotel nacional", tipo="poi",
              municipio="Plaza de la Revolución", municipio_norm="plaza de la revolucion",
              provincia="La Habana", lat=23.14, lon=-82.38),
    ])
    s.put_portal(Portal(street_norm="23", numero="415", lat=23.13, lon=-82.39,
                        municipio="Plaza de la Revolución", provincia="La Habana"))
    return SearchPipeline(s)


def test_poi_search():
    r = _pipe().resolve("Hotel Nacional")
    assert r and r[0].nombre == "Hotel Nacional" and r[0].tipo == "poi"


def test_exact_portal():
    r = _pipe().resolve("Calle 23 415")
    assert r and r[0].match_type == "exact_portal"
    assert abs(r[0].lat - 23.13) < 1e-9


def test_pipeline_resolve_sets_precision():
    p = _pipe()  # store with Hotel Nacional (poi) + portal
    res = p.resolve("Hotel Nacional")
    assert res and res[0].precision == "poi"
    assert res[0].radius_m is not None and res[0].radius_m > 0
    assert res[0].resolved and res[0].resolved["kind"] == "place"


def test_pipeline_area_carries_geometry():
    import tempfile
    from shapely.geometry import Polygon, Point
    from geocoder.store import GeoStore
    from geocoder.models import Municipio
    from geocoder.search.pipeline import SearchPipeline
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    s.put_municipio(Municipio(
        name="Plaza de la Revolución", province="La Habana",
        polygon=Polygon([(-82.42, 23.10), (-82.36, 23.10), (-82.36, 23.16), (-82.42, 23.16)]),
        centroid=Point(-82.39, 23.13)))
    res = SearchPipeline(s).resolve("Plaza de la Revolución")
    assert res and res[0].match_type == "municipio"
    assert res[0].geometry_wkt is not None and res[0].geometry_wkt.startswith("POLYGON")


def test_pipeline_resolve_region():
    import tempfile
    from geocoder.store import GeoStore
    from geocoder.search.pipeline import SearchPipeline
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    s.put_calle_geom("23", None, "LINESTRING(-82.395 23.138, -82.375 23.138)")
    s.put_calle_geom("linea", None, "LINESTRING(-82.395 23.150, -82.375 23.150)")
    s.put_calle_geom("paseo", None, "LINESTRING(-82.390 23.130, -82.390 23.156)")
    s.put_calle_geom("g", None, "LINESTRING(-82.380 23.130, -82.380 23.156)")
    reg = SearchPipeline(s).resolve_region(["23", "Línea", "Paseo", "G"])
    assert reg is not None and reg.method == "region_hull"
    assert len(reg.vertices) == 4
    assert SearchPipeline(s).resolve_region(["23"]) is None
