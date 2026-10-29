import tempfile
from geocoder.store import GeoStore
from geocoder.models import Lugar, Portal, Municipio

def _store():
    return GeoStore(tempfile.mktemp(suffix=".db"))

def test_index_and_search_lugares():
    s = _store()
    s.index_lugares([
        Lugar(nombre="Calle 23", norm_nombre="calle 23", tipo="calle",
              municipio="Plaza de la Revolución", municipio_norm="plaza de la revolucion",
              provincia="La Habana", lat=23.14, lon=-82.38, street_norm="23"),
        Lugar(nombre="Calle 12", norm_nombre="calle 12", tipo="calle",
              municipio="Playa", municipio_norm="playa", provincia="La Habana",
              lat=23.10, lon=-82.42, street_norm="12"),
    ])
    hits = s.search_lugares('"calle"* AND "23"', limit=5)
    assert any(h["nombre"] == "Calle 23" for h in hits)
    filtered = s.search_lugares('"calle"*', municipio_norm="playa", limit=5)
    assert all(h["municipio_norm"] == "playa" for h in filtered)
    s.close()

def test_find_portal_exact():
    s = _store()
    s.put_portal(Portal(street_norm="23", numero="415", lat=23.13, lon=-82.39,
                        municipio="Plaza de la Revolución", provincia="La Habana"))
    hit = s.find_portal("23", "415", "Plaza de la Revolución")
    assert hit and hit["lat"] == 23.13 and hit["numero"] == "415"
    assert s.find_portal("23", "999") is None
    # missing key on the municipio path must return None, not raise
    # (beaver dict.get raises KeyError on a miss)
    assert s.find_portal("23", "415", "Nonexistent Municipio") is None
    assert s.find_portal("nope", "1", "Plaza de la Revolución") is None
    assert s.get_calle_geom("nope", "Nonexistent") is None
    s.close()

def test_resolve_municipio_and_nearest():
    s = _store()
    s.put_municipio(Municipio(name="Plaza de la Revolución", province="La Habana",
                              centroid=None, polygon=None))
    s.index_lugares([Lugar(nombre="X", norm_nombre="x", tipo="poi", lat=23.14, lon=-82.38,
                           municipio="Plaza de la Revolución", municipio_norm="plaza de la revolucion")])
    assert s.resolve_municipio("plaza") == "Plaza de la Revolución"
    near = s.nearest_lugares(23.1401, -82.3801, radius_m=500, limit=5)
    assert near and near[0]["nombre"] == "X"
    s.close()

def test_barrio_storage_and_resolve():
    from shapely.geometry import Point, Polygon
    from geocoder.models import Barrio
    s = _store()
    s.put_barrio(Barrio(name="Vedado", municipio_id=1, place_type="neighbourhood",
                        polygon=Polygon([(-82.40, 23.13), (-82.37, 23.13),
                                         (-82.37, 23.15), (-82.40, 23.15)]),
                        centroid=Point(-82.385, 23.14)))
    got = s.resolve_barrio("vedado")
    assert got and got["name"] == "Vedado"
    assert abs(got["centroid_lat"] - 23.14) < 0.02
    assert got["bbox_minx"] is not None
    assert s.resolve_barrio("nowhere") is None
    s.close()


def test_intersections_roundtrip_canonical_key():
    s = _store()
    s.put_intersections([
        {"a": "23", "b": "l", "muni": "Plaza", "prov": "La Habana", "lat": 23.14, "lon": -82.38},
        {"a": "l", "b": "23", "muni": "Otro", "prov": "La Habana", "lat": 22.0, "lon": -80.0},
    ])
    # both orderings of the query hit the same canonical entry (both records)
    fwd = s.crossings("23", "l")
    rev = s.crossings("l", "23")
    assert len(fwd) == 2 and len(rev) == 2
    munis = {r["muni"] for r in fwd}
    assert munis == {"Plaza", "Otro"}
    assert s.crossings("23", "nope") == []
    s.close()


def test_street_alias_maps_article_dropped_names():
    """OSM writes the sign ('de las banderas'); people say 'las banderas'.
    The alias index recovers the full name from the article-dropped one."""
    import tempfile
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    s.put_calle_geom("de las banderas", None, "LINESTRING(-82.188 23.170, -82.188 23.180)")
    s.put_calle_geom("del puerto", None, "LINESTRING(-82.184 23.170, -82.184 23.180)")
    assert s.street_alias("las banderas") == "de las banderas"
    assert s.street_alias("puerto") == "del puerto"
    # articles strip progressively — speakers drop the whole leading run
    assert s.street_alias("banderas") == "de las banderas"
    assert s.street_alias("de las banderas") is None   # already the full name
    s.close()


def test_street_alias_skips_ambiguous_and_real_names():
    """An alias must never override a street the store holds verbatim, and must
    stay silent when the stripped form fits two different articled streets."""
    import tempfile
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    s.put_calle_geom("de acosta", None, "LINESTRING(-82.30 23.10, -82.30 23.11)")
    s.put_calle_geom("acosta", None, "LINESTRING(-82.40 23.10, -82.40 23.11)")
    s.put_calle_geom("de canada", None, "LINESTRING(-82.50 23.10, -82.50 23.11)")
    s.put_calle_geom("la canada", None, "LINESTRING(-82.60 23.10, -82.60 23.11)")
    assert s.street_alias("acosta") is None   # 'acosta' is itself a real street
    assert s.street_alias("canada") is None   # ambiguous: 'de canada' vs 'la canada'
    s.close()


def test_build_stamp_roundtrip():
    """Provenance lives inside the database, not in a sidecar file — a sidecar
    can be copied away from the data it describes."""
    import tempfile
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    assert s.build_stamp() is None
    s.stamp_build(commit="abc1234", version="0.4.0")
    st = s.build_stamp()
    assert st["commit"] == "abc1234"
    assert st["version"] == "0.4.0"
    assert st["built_at"].endswith("Z")
    s.close()


def test_street_tree_indexes_every_geometry():
    import tempfile
    from shapely.geometry import Point
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    s.put_calle_geom("l", None, "LINESTRING(-82.385 23.130, -82.385 23.150)")
    s.put_calle_geom("23", None, "LINESTRING(-82.395 23.138, -82.375 23.138)")
    tree, rows = s.street_tree()
    assert len(rows) == 2
    assert {r[0] for r in rows} == {"l", "23"}
    assert s.street_tree()[0] is tree          # cached, not rebuilt
    i = int(tree.nearest(Point(-82.385, 23.131)))
    assert rows[i][0] == "l"
    s.close()


def test_get_calle_geom_honours_a_named_municipio():
    """A named municipio is a filter, not a hint. It used to fall back to
    whichever municipio was indexed first, so a Havana query got a Pinar del Río
    geometry for any street name that repeats across Cuba."""
    s = _store()
    s.put_calle_geom("marti", "Minas de Matahambre", "LINESTRING(-83.95 22.58, -83.93 22.58)")
    assert s.get_calle_geom("marti", "Minas de Matahambre") is not None
    assert s.get_calle_geom("marti", "Habana Vieja") is None   # not: the Pinar fragment
    assert s.get_calle_geom("marti") is not None               # unscoped still widens
    s.close()


class _NoReads:
    """Stands in for a beaver dict once the store should be serving from memory."""
    def __getattr__(self, name):
        raise AssertionError(f"hit the database: {name}")
    def __getitem__(self, k):
        raise AssertionError(f"hit the database: [{k!r}]")
    def __contains__(self, k):
        raise AssertionError(f"hit the database: {k!r} in")


def test_portal_and_street_lookups_serve_from_memory_after_first_load():
    """Batch geocoding calls these per address. The unscoped portal lookup used
    to scan every portal key through the beaver bridge (~0.8 s per call on
    full Cuba) and each street fragment cost a SQLite round-trip."""
    s = _store()
    s.put_portal(Portal(street_norm="23", numero="415", lat=23.13, lon=-82.39,
                        municipio="Plaza de la Revolución", provincia="La Habana"))
    s.put_calle_geom("marti", "Guanabacoa", "LINESTRING(-82.30 23.12, -82.29 23.12)")
    s.put_calle_geom("marti", "Regla", "LINESTRING(-82.33 23.13, -82.32 23.13)")
    s.find_portal("23", "415")
    s.street_geoms("marti")                                   # first load
    real_p, real_g = s.portales, s.calles_geom
    s.portales, s.calles_geom = _NoReads(), _NoReads()
    assert s.find_portal("23", "415")["lat"] == 23.13
    assert s.find_portal("23", "415", "Plaza de la Revolución")["numero"] == "415"
    assert s.find_portal("23", "999") is None
    assert s.find_portal("23", "415", "Regla") is None
    assert {m for m, _ in s.street_geoms("marti")} == {"Guanabacoa", "Regla"}
    assert s.get_calle_geom("marti", "Regla").startswith("LINESTRING(-82.33")
    assert s.get_calle_geom("marti", "Playa") is None
    s.portales, s.calles_geom = real_p, real_g
    s.close()


def test_writes_after_a_read_are_visible():
    s = _store()
    s.put_portal(Portal(street_norm="23", numero="1", lat=1.0, lon=1.0,
                        municipio="Playa", provincia="La Habana"))
    s.put_calle_geom("l", "Playa", "LINESTRING(0 0, 1 1)")
    assert s.find_portal("23", "2") is None and s.street_geoms("k") == []
    s.put_portal(Portal(street_norm="23", numero="2", lat=2.0, lon=2.0,
                        municipio="Playa", provincia="La Habana"))
    s.put_calle_geom("k", "Playa", "LINESTRING(0 1, 1 0)")
    assert s.find_portal("23", "2")["lat"] == 2.0
    assert [m for m, _ in s.street_geoms("k")] == ["Playa"]
    s.close()


def test_fts_queries_survive_double_quotes_in_the_name():
    """Business listings quote names (`BAR-CAFETERIA "MAYA"`). A bare `"` inside
    an FTS5 string ends it early: the whole query 500'd with "unterminated string"."""
    from geocoder.utils.normalize import normalize_for_fts, normalize_for_fts_fuzzy
    s = _store()
    s.index_lugares([Lugar(nombre="Tienda La Concepción", norm_nombre="tienda la concepcion",
                           tipo="poi", municipio="Palma Soriano", municipio_norm="palma soriano",
                           provincia="Santiago de Cuba", lat=20.2, lon=-75.99)])
    q = 'Tienda "La Concepción"'
    assert s.search_lugares(normalize_for_fts(q), limit=5)[0]["nombre"] == "Tienda La Concepción"
    assert s.search_lugares(normalize_for_fts_fuzzy(q, prefix_len=3), limit=5)
    assert normalize_for_fts('" "') == ""
    s.close()


def test_strict_fts_query_skips_function_words_and_pins_single_letters():
    """`"la"*` or `"c"*` expands to a large share of the index and FTS5 ranks
    every match; on full Cuba that made name lookups ~70 ms and a lone "C" 2 s.
    Articles add no discrimination, so they drop unless they are all there is."""
    from geocoder.utils.normalize import normalize_for_fts
    assert normalize_for_fts("Bar de las Ruinas") == '"bar"* AND "ruinas"*'
    assert normalize_for_fts("C") == '"c"'
    assert normalize_for_fts("Calle 23") == '"calle"* AND "23"'
    assert normalize_for_fts("La") == '"la"*'          # nothing else to search on


def test_admin_geometries_per_level():
    """Lote's "Por zona" view colours each area by how many rows fell in it; the
    rows already carry provincia/municipio/barrio, so the store only serves the
    shapes. Barrios mostly have no polygon in OSM: those come back as a point at
    their centroid, tagged with the municipio their centroid falls in."""
    from shapely.geometry import Point, Polygon
    from geocoder.models import Barrio
    s = _store()
    plaza = Polygon([(-82.42, 23.10), (-82.36, 23.10), (-82.36, 23.16), (-82.42, 23.16)])
    s.put_municipio(Municipio(name="Plaza de la Revolución", province="La Habana",
                              polygon=plaza, centroid=plaza.centroid))
    s.put_barrio(Barrio(name="Vedado", polygon=Polygon([(-82.40, 23.13), (-82.37, 23.13),
                                                        (-82.37, 23.15), (-82.40, 23.15)]),
                        centroid=Point(-82.385, 23.14)))
    s.put_barrio(Barrio(name="Carmelo", centroid=Point(-82.41, 23.12)))
    s.put_barrio(Barrio(name="Lejos", centroid=Point(-75.0, 20.0)))   # outside every municipio

    munis = s.admin_geometries("municipio")
    assert [f["properties"]["name"] for f in munis] == ["Plaza de la Revolución"]
    assert munis[0]["geometry"]["type"] == "Polygon"
    assert munis[0]["properties"]["provincia"] == "La Habana"

    barrios = {f["properties"]["name"]: f for f in s.admin_geometries("barrio")}
    assert barrios["Vedado"]["geometry"]["type"] == "Polygon"
    assert barrios["Carmelo"]["geometry"] == {"type": "Point", "coordinates": [-82.41, 23.12]}
    assert barrios["Carmelo"]["properties"]["municipio"] == "Plaza de la Revolución"
    assert barrios["Lejos"]["properties"]["municipio"] is None
    assert s.admin_geometries("barrio") is s.admin_geometries("barrio")   # cached
    s.close()
