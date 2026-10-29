"""Unit tests for the Gazetteer (place index) built from a small store."""
import tempfile

from shapely.geometry import Polygon, Point

from geocoder.store import GeoStore
from geocoder.models import Municipio, Barrio, Provincia
from geocoder.search.gazetteer import Gazetteer


def _store():
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    # province La Habana (big square), municipio Plaza inside it, barrio Vedado inside Plaza
    s.put_provincia(Provincia(
        name="La Habana",
        polygon=Polygon([(-82.6, 22.9), (-82.0, 22.9), (-82.0, 23.3), (-82.6, 23.3)]),
        centroid=Point(-82.3, 23.1)))
    s.put_municipio(Municipio(
        name="Plaza de la Revolución", province="La Habana",
        polygon=Polygon([(-82.42, 23.10), (-82.36, 23.10), (-82.36, 23.16), (-82.42, 23.16)]),
        centroid=Point(-82.39, 23.13)))
    s.put_barrio(Barrio(
        name="Vedado", place_type="neighbourhood",
        polygon=Polygon([(-82.40, 23.12), (-82.37, 23.12), (-82.37, 23.15), (-82.40, 23.15)]),
        centroid=Point(-82.385, 23.135)))
    return s


def test_lookup_levels():
    gz = Gazetteer.from_store(_store())
    assert gz.lookup("vedado")[0]["level"] == "barrio"
    assert gz.lookup("plaza de la revolucion")[0]["level"] == "municipio"
    assert gz.lookup("la habana")[0]["level"] == "provincia"
    assert gz.lookup("nowhere") == []


def test_barrio_hierarchy():
    gz = Gazetteer.from_store(_store())
    v = gz.lookup("vedado")[0]
    assert v["parent_muni"] == "Plaza de la Revolución"
    assert v["parent_prov"] == "La Habana"
    assert v["radius_m"] > 0


def test_match_longest_phrase():
    gz = Gazetteer.from_store(_store())
    hit = gz.match_longest("calle 23 plaza de la revolucion")
    assert hit is not None
    recs, span = hit
    assert span == "plaza de la revolucion" and recs[0]["level"] == "municipio"


def test_fuzzy():
    gz = Gazetteer.from_store(_store())
    recs = gz.fuzzy("vedadoo")
    assert recs and recs[0]["canonical"] == "Vedado"


def test_curated_aliases():
    gz = Gazetteer.from_store(_store())
    # 'habana' → La Habana province (curated alias over the automatic index)
    recs = gz.lookup("habana")
    assert recs and recs[0]["canonical"] == "La Habana" and recs[0]["level"] == "provincia"


def test_records_carry_polygon_wkt():
    gz = Gazetteer.from_store(_store())
    muni = gz.lookup("plaza de la revolucion")[0]
    prov = gz.lookup("la habana")[0]
    barrio = gz.lookup("vedado")[0]
    assert muni["polygon_wkt"].startswith("POLYGON")
    assert prov["polygon_wkt"].startswith("POLYGON")
    assert barrio["polygon_wkt"].startswith("POLYGON")
