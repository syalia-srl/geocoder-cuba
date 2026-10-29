import tempfile
from shapely.geometry import Point, Polygon
from geocoder.store import GeoStore
from geocoder.models import Lugar, Portal, Municipio, Barrio, Provincia
from geocoder.search.resolver import Resolver

def _store():
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    s.put_municipio(Municipio(name="Plaza de la Revolución", province="La Habana",
        polygon=Polygon([(-82.42,23.10),(-82.36,23.10),(-82.36,23.16),(-82.42,23.16)]),
        centroid=Point(-82.39,23.13)))
    s.put_barrio(Barrio(name="Vedado", municipio_id=1, place_type="neighbourhood",
        polygon=Polygon([(-82.40,23.13),(-82.37,23.13),(-82.37,23.15),(-82.40,23.15)]),
        centroid=Point(-82.385,23.14)))
    s.index_lugares([
        Lugar(nombre="Hotel Nacional", norm_nombre="hotel nacional", tipo="poi",
              municipio="Plaza de la Revolución", municipio_norm="plaza de la revolucion",
              provincia="La Habana", barrio="Vedado", lat=23.143, lon=-82.381),
    ])
    s.put_portal(Portal(street_norm="23", numero="415", lat=23.138, lon=-82.386,
                       municipio="Plaza de la Revolución", provincia="La Habana"))
    return s

def test_poi():
    r = Resolver(_store()).resolve("Hotel Nacional")
    assert r.method == "poi" and r.nombre == "Hotel Nacional"
    assert 0 < r.radius_m < 100

def test_municipio_area():
    r = Resolver(_store()).resolve("Plaza de la Revolución")
    assert r.method == "municipio"
    assert r.radius_m > 500  # big polygon → big uncertainty

def test_barrio_area():
    r = Resolver(_store()).resolve("Vedado")
    assert r.method == "barrio"

def test_exact_portal():
    r = Resolver(_store()).resolve("Calle 23 #415")
    assert r.method == "exact_portal"
    assert abs(r.lat - 23.138) < 1e-6 and r.radius_m <= 20


def test_poi_substring_of_municipio_stays_poi():
    # a POI whose name substring-matches a municipio must NOT resolve to the
    # municipio (guard must require exact name round-trip or kind=area)
    s = _store()
    s.index_lugares([Lugar(nombre="Plaza Vieja", norm_nombre="plaza vieja", tipo="poi",
                           municipio="Plaza de la Revolución", municipio_norm="plaza de la revolucion",
                           provincia="La Habana", lat=23.136, lon=-82.352)])
    r = Resolver(s).resolve("Plaza Vieja")
    assert r.method == "poi" and r.nombre == "Plaza Vieja"
    # exact municipio name still resolves as area
    assert Resolver(s).resolve("Plaza de la Revolución").method == "municipio"
    s.close()


def test_area_result_carries_geometry():
    r = Resolver(_store()).resolve("Plaza de la Revolución")
    assert r.method == "municipio"
    assert r.geometry_wkt is not None and r.geometry_wkt.startswith("POLYGON")


def test_poi_result_has_no_geometry():
    r = Resolver(_store()).resolve("Hotel Nacional")
    assert r.method == "poi"
    assert r.geometry_wkt is None


def _store_streets():
    import tempfile
    from geocoder.store import GeoStore
    from geocoder.models import Lugar
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    # L runs N-S at lon -82.385; 23 and 25 run E-W crossing it
    s.put_calle_geom("l", None, "LINESTRING(-82.385 23.130, -82.385 23.150)")
    s.put_calle_geom("23", None, "LINESTRING(-82.395 23.138, -82.375 23.138)")
    s.put_calle_geom("25", None, "LINESTRING(-82.395 23.142, -82.375 23.142)")
    s.index_lugares([
        Lugar(nombre="L", norm_nombre="l", tipo="calle", street_norm="l",
              lat=23.140, lon=-82.385, geom_wkt="LINESTRING(-82.385 23.130, -82.385 23.150)"),
    ])
    return s

def test_entre_midpoint():
    r = Resolver(_store_streets()).resolve("Calle L e/ 23 y 25")
    assert r.method == "segment_midpoint"
    assert abs(r.lat - 23.140) < 0.001 and abs(r.lon + 82.385) < 0.001
    assert r.radius_m > 0

def test_esquina_point():
    r = Resolver(_store_streets()).resolve("L y 23")
    assert r.method == "esquina"
    assert abs(r.lat - 23.138) < 0.001 and r.radius_m <= 20

def test_street_only():
    r = Resolver(_store_streets()).resolve("Calle L")
    assert r.method == "street"


def test_esquina_from_precomputed_table():
    """esquina resolves from the precomputed table even with no street geometry
    (proves the table-first path); the geometry cases above prove the fallback."""
    import tempfile
    from geocoder.store import GeoStore
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    s.put_intersections([
        {"a": "l", "b": "23", "muni": "Plaza", "prov": "La Habana", "lat": 23.5, "lon": -82.5},
    ])
    r = Resolver(s).resolve("L y 23")
    assert r.method == "esquina"
    assert abs(r.lat - 23.5) < 1e-6 and abs(r.lon + 82.5) < 1e-6
    assert r.municipio == "Plaza"
    s.close()


# ---- geometry-robustness regression tests (Task 4) ----

def _store_multiline():
    """Store where the main street is stored as a MultiLineString (two disjoint segments)."""
    import tempfile
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    # Main street L stored as MultiLineString (two disconnected segments — real OSM situation)
    s.put_calle_geom("l", None, "MULTILINESTRING((-82.385 23.130, -82.385 23.138), (-82.385 23.142, -82.385 23.150))")
    # Cross-streets 23 and 25 are normal LineStrings
    s.put_calle_geom("23", None, "LINESTRING(-82.395 23.138, -82.375 23.138)")
    s.put_calle_geom("25", None, "LINESTRING(-82.395 23.142, -82.375 23.142)")
    return s


def _store_non_intersecting():
    """Store where the cross-street does not actually intersect the main street."""
    import tempfile
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    # L runs N-S at lon -82.385
    s.put_calle_geom("l", None, "LINESTRING(-82.385 23.130, -82.385 23.150)")
    # 23 and 25 run parallel far away — they never cross L
    s.put_calle_geom("23", None, "LINESTRING(-82.300 23.138, -82.280 23.138)")
    s.put_calle_geom("25", None, "LINESTRING(-82.300 23.142, -82.280 23.142)")
    return s


def test_entre_multilinestring_does_not_crash():
    """entre rung must not crash when the main street is a MultiLineString."""
    r = Resolver(_store_multiline()).resolve("Calle L e/ 23 y 25")
    # Must not raise; result is either a coarser method or None, never an exception.
    assert r is None or isinstance(r.method, str)


def test_entre_non_intersecting_does_not_crash():
    """entre rung must not crash when cross-streets don't intersect the main street."""
    r = Resolver(_store_non_intersecting()).resolve("Calle L e/ 23 y 25")
    # Falls through to street or None — must not raise AttributeError.
    assert r is None or isinstance(r.method, str)


def test_esquina_non_intersecting_does_not_crash():
    """esquina rung must not crash when the two streets don't intersect."""
    r = Resolver(_store_non_intersecting()).resolve("L y 23")
    # Falls through — must not raise.
    assert r is None or isinstance(r.method, str)


# ---- region_hull rung ----

def _store_region():
    """Four orthogonal streets enclosing a rectangle in Vedado.
    23 and Línea run E-W (parallel); Paseo and G run N-S (parallel)."""
    import tempfile
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    s.put_calle_geom("23", None, "LINESTRING(-82.395 23.138, -82.375 23.138)")
    s.put_calle_geom("linea", None, "LINESTRING(-82.395 23.150, -82.375 23.150)")
    s.put_calle_geom("paseo", None, "LINESTRING(-82.390 23.130, -82.390 23.156)")
    s.put_calle_geom("g", None, "LINESTRING(-82.380 23.130, -82.380 23.156)")
    return s


def test_region_rectangle():
    reg = Resolver(_store_region()).resolve_region(["23", "Línea", "Paseo", "G"])
    assert reg is not None
    assert reg.method == "region_hull"
    assert reg.n_crossings == 4
    assert len(reg.vertices) == 4          # a rectangle, hull closes to 4 corners
    assert reg.unresolved == []
    # centroid is the rectangle center
    assert abs(reg.centroid_lat - 23.144) < 1e-4
    assert abs(reg.centroid_lon + 82.385) < 1e-4
    # ~1.02 km × ~1.33 km ≈ 136 ha
    assert 120.0 < reg.area_ha < 150.0
    assert 1_200_000 < reg.area_m2 < 1_500_000
    assert reg.provincia == "La Habana"
    assert reg.degenerate is False and reg.ambiguous is False
    assert reg.polygon_wkt.startswith("POLYGON")


def test_region_requires_three_streets():
    assert Resolver(_store_region()).resolve_region(["23", "Paseo"]) is None


def test_region_parallel_streets_no_region():
    # only parallel streets → no crossings → no polygon
    assert Resolver(_store_region()).resolve_region(["23", "Línea"]) is None
    # 3 parallels: 23, Línea, plus a third E-W street
    s = _store_region()
    s.put_calle_geom("25", None, "LINESTRING(-82.395 23.144, -82.375 23.144)")
    assert Resolver(s).resolve_region(["23", "Línea", "25"]) is None


def test_region_reports_unresolved_street():
    reg = Resolver(_store_region()).resolve_region(
        ["23", "Línea", "Paseo", "G", "NoSuchStreetXYZ"])
    assert reg is not None
    assert reg.unresolved == ["NoSuchStreetXYZ"]
    assert len(reg.vertices) == 4


# ---- street-name canonicalization (OSM names carry articles queries omit) ----

def _store_article_street():
    """The street OSM calls 'de las banderas' — a query says 'ave las banderas'.
    7 and 9 run E-W across it."""
    import tempfile
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    s.put_calle_geom("de las banderas", None, "LINESTRING(-82.188 23.170, -82.188 23.180)")
    s.put_calle_geom("7", None, "LINESTRING(-82.195 23.172, -82.180 23.172)")
    s.put_calle_geom("9", None, "LINESTRING(-82.195 23.176, -82.180 23.176)")
    return s


def test_entre_resolves_when_query_drops_the_article():
    """'ave las banderas' must find OSM's 'de las banderas'. Without this the
    whole address ladder misses and the query falls through to a spurious POI."""
    r = Resolver(_store_article_street()).resolve("ave las banderas e/ 7 y 9")
    assert r is not None
    assert r.method == "segment_midpoint"
    assert abs(r.lat - 23.174) < 0.001 and abs(r.lon + 82.188) < 0.001


def test_esquina_resolves_when_query_drops_the_article():
    r = Resolver(_store_article_street()).resolve("las banderas y 7")
    assert r is not None
    assert r.method == "esquina"
    assert abs(r.lat - 23.172) < 0.001 and abs(r.lon + 82.188) < 0.001


def test_street_fallback_resolves_when_query_drops_the_article():
    r = Resolver(_store_article_street()).resolve("Avenida las banderas")
    assert r is not None and r.method == "street"


def test_region_resolves_streets_that_drop_the_article():
    s = _store_article_street()
    s.put_calle_geom("11", None, "LINESTRING(-82.195 23.178, -82.180 23.178)")
    s.put_calle_geom("del puerto", None, "LINESTRING(-82.184 23.170, -82.184 23.180)")
    reg = Resolver(s).resolve_region(["las banderas", "puerto", "7", "9"])
    assert reg is not None
    assert reg.unresolved == []          # both articled streets must resolve
    assert reg.n_crossings == 4


def test_exact_street_name_is_never_fuzzy_replaced():
    """Canonicalization must only fire on a miss — a street the store knows
    verbatim keeps its own geometry even when a similar name exists."""
    s = _store_article_street()
    s.put_calle_geom("banderas", None, "LINESTRING(-82.300 23.100, -82.300 23.110)")
    r = Resolver(s).resolve("calle banderas")
    assert r is not None and r.method == "street"
    assert abs(r.lon + 82.300) < 0.001   # its own geometry, not 'de las banderas'


# ---- a failed address must not be retried as a place-name search ----

def _store_address_miss():
    """A municipio + barrio + a POI whose name fuzzy-matches almost anything.
    No street geometry at all, so every address rung must fail."""
    import tempfile
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    s.put_municipio(Municipio(name="Habana del Este", province="La Habana",
        polygon=Polygon([(-82.30,23.14),(-82.10,23.14),(-82.10,23.20),(-82.30,23.20)]),
        centroid=Point(-82.20,23.17)))
    s.put_barrio(Barrio(name="Alamar", municipio_id=1, place_type="neighbourhood",
        polygon=Polygon([(-82.28,23.15),(-82.24,23.15),(-82.24,23.18),(-82.28,23.18)]),
        centroid=Point(-82.26,23.165)))
    s.index_lugares([
        Lugar(nombre="5ta E y 168 Zona 7", norm_nombre="5ta e y 168 zona 7", tipo="portal",
              municipio="Habana del Este", municipio_norm="habana del este",
              provincia="La Habana", lat=23.1636, lon=-82.2713),
    ])
    return s


def test_failed_address_falls_to_the_municipio_not_a_poi():
    """The attractor bug: an address whose streets are absent used to come back
    as an unrelated POI with a 25 m radius. It must fall to an honest coarse
    point instead."""
    r = Resolver(_store_address_miss()).resolve(
        "ave las banderas e/ 7 y 9, Habana del Este")
    assert r is not None
    assert r.method == "municipio"
    assert r.tipo != "portal"
    assert r.radius_m > 500              # honest about being coarse
    assert abs(r.lat - 23.1636) > 1e-3   # NOT the POI attractor


def test_failed_address_prefers_the_barrio_when_given_one():
    r = Resolver(_store_address_miss()).resolve("ave las banderas e/ 7 y 9, Alamar")
    assert r is not None and r.method == "barrio"
    assert r.radius_m > 100


def test_failed_address_with_no_context_returns_nothing():
    r = Resolver(_store_address_miss()).resolve("ave las banderas e/ 7 y 9")
    assert r is None


def test_a_real_place_query_still_reaches_the_poi_rung():
    """Suppression is scoped to parsed addresses — place queries are untouched."""
    r = Resolver(_store()).resolve("Hotel Nacional")
    assert r.method == "poi" and r.nombre == "Hotel Nacional"


# --- named-area scoping: a scope the query states must never be silently dropped ---

def _store_two_provinces():
    """The same POI name and the same street pair in two provinces, so every
    query below is ambiguous until the named area disambiguates it."""
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    s.put_provincia(Provincia(name="La Habana",
        polygon=Polygon([(-82.55,22.90),(-82.20,22.90),(-82.20,23.20),(-82.55,23.20)]),
        centroid=Point(-82.37,23.05)))
    s.put_provincia(Provincia(name="Pinar del Río",
        polygon=Polygon([(-84.10,22.40),(-83.60,22.40),(-83.60,22.80),(-84.10,22.80)]),
        centroid=Point(-83.85,22.60)))
    s.put_municipio(Municipio(name="Habana Vieja", province="La Habana",
        polygon=Polygon([(-82.36,23.12),(-82.33,23.12),(-82.33,23.15),(-82.36,23.15)]),
        centroid=Point(-82.345,23.135)))
    # the province's capital municipio shares its name — a query naming the
    # province lands on the municipio, which holds none of the POIs below
    s.put_municipio(Municipio(name="Pinar del Río", province="Pinar del Río",
        polygon=Polygon([(-83.75,22.38),(-83.65,22.38),(-83.65,22.45),(-83.75,22.45)]),
        centroid=Point(-83.70,22.415)))
    s.put_municipio(Municipio(name="Minas de Matahambre", province="Pinar del Río",
        polygon=Polygon([(-83.98,22.55),(-83.90,22.55),(-83.90,22.62),(-83.98,22.62)]),
        centroid=Point(-83.94,22.585)))
    s.index_lugares([
        Lugar(nombre="El Capitolio", norm_nombre="el capitolio", tipo="poi",
              municipio="Habana Vieja", municipio_norm="habana vieja",
              provincia="La Habana", lat=23.1355, lon=-82.3597),
        Lugar(nombre="El Capitolio", norm_nombre="el capitolio", tipo="poi",
              municipio="Minas de Matahambre", municipio_norm="minas de matahambre",
              provincia="Pinar del Río", lat=22.5880, lon=-83.9411),
    ])
    # "heredia" crosses "lacret" ONLY in Pinar del Río — Habana Vieja has no such corner
    s.put_calle_geom("heredia", "Minas de Matahambre", "LINESTRING(-83.95 22.58, -83.93 22.58)")
    s.put_calle_geom("lacret", "Minas de Matahambre", "LINESTRING(-83.94 22.57, -83.94 22.59)")
    return s


def test_named_province_scopes_an_ambiguous_place():
    """'El Capitolio, La Habana' must land in La Habana. The province is stated
    in the query; dropping it answers 200 km away with full confidence."""
    r = Resolver(_store_two_provinces()).resolve("El Capitolio, La Habana")
    assert r is not None
    assert r.provincia == "La Habana"
    assert abs(r.lat - 23.1355) < 1e-3 and abs(r.lon + 82.3597) < 1e-3


def test_named_province_scopes_the_other_way_too():
    """Same query shape, other province — guards against a hardcoded Havana win."""
    r = Resolver(_store_two_provinces()).resolve("El Capitolio, Pinar del Río")
    assert r is not None
    assert r.provincia == "Pinar del Río"
    assert abs(r.lat - 22.5880) < 1e-3


def test_named_municipio_without_the_corner_does_not_answer_elsewhere():
    """A crossing that exists only in another province must not satisfy a query
    that named Habana Vieja. Degrade to the named area, never cross the country."""
    r = Resolver(_store_two_provinces()).resolve(
        "Calle Heredia e/ Lacret y Hartmann, Habana Vieja")
    assert r is not None
    assert r.method == "municipio"        # honest coarse point, not a false corner
    assert r.radius_m > 500
    assert abs(r.lat - 22.58) > 1e-2      # NOT the Pinar del Río corner


def test_province_named_via_its_capital_municipio_widens_to_the_province():
    """'Pinar del Río' resolves to the municipio, which holds no Capitolio. The
    municipio is a preference, so the search widens to its province rather than
    returning nothing."""
    r = Resolver(_store_two_provinces()).resolve("El Capitolio, Pinar del Río")
    assert r is not None, "widening to the province should have found it"
    assert r.provincia == "Pinar del Río"
    assert abs(r.lat - 22.5880) < 1e-3
