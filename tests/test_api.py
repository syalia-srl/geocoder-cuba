import tempfile
import pytest
from fastapi.testclient import TestClient
from pathlib import Path
from geocoder.etl.build import build_database
from geocoder.store import GeoStore
from geocoder.search.pipeline import SearchPipeline

FIX = Path(__file__).parent / "fixtures" / "habana_sample.osm"

@pytest.fixture
def client():
    db = tempfile.mktemp(suffix=".db")
    build_database(str(FIX), bbox=None, force_rebuild=True, db_path=db)
    from geocoder.api import main as appmod
    from geocoder.api.dependencies import get_store, get_search_pipeline
    store = GeoStore(db)
    pipe = SearchPipeline(store)
    appmod.app.dependency_overrides[get_store] = lambda: store
    appmod.app.dependency_overrides[get_search_pipeline] = lambda: pipe
    appmod.app.state._test = True
    with TestClient(appmod.app) as c:
        yield c
    appmod.app.dependency_overrides.clear()

def test_resolve_street(client):
    r = client.get("/api/v1/resolve?q=Calle 23")
    assert r.status_code == 200
    data = r.json()
    assert data["results"], "must resolve the street"
    assert data["results"][0]["coordinates"]["lat"]


def test_reverse(client):
    # the fixture has entities at ~23.14,-82.38, so a 1km reverse query must hit
    r = client.get("/api/v1/reverse?lat=23.14&lon=-82.38&radius=1000")
    assert r.status_code == 200
    data = r.json()
    assert len(data["results"]) > 0, "reverse must find the fixture entities nearby"
    first = data["results"][0]
    assert "distance_meters" in first and first["distance_meters"] <= 1000
    assert "coordinates" in first and "lat" in first["coordinates"]
    # results must be sorted nearest-first
    dists = [x["distance_meters"] for x in data["results"]]
    assert dists == sorted(dists)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "healthy"


def test_health_v1_path(client):
    # health is also mounted under /api/v1 (README/docs use this path)
    r = client.get("/api/v1/health")
    assert r.status_code == 200 and r.json()["status"] == "healthy"


def test_resolve_returns_ranked_list(client):
    # POI/name queries return up to `limit` ranked candidates
    data = client.get("/api/v1/resolve?q=Hotel&limit=5").json()
    assert isinstance(data["results"], list) and len(data["results"]) >= 1
    assert any(x["nombre"] == "Hotel Nacional" for x in data["results"])


def test_municipios(client):
    r = client.get("/api/v1/municipios")
    assert r.status_code == 200
    data = r.json()
    # the fixture defines the Plaza de la Revolución municipio
    assert any(m["name"] == "Plaza de la Revolución" for m in data)
    assert all("centroid" in m for m in data)
    assert all(m["id"] >= 1 for m in data)  # guard the id=0 regression


def test_resolve_endpoint(client):
    r = client.get("/api/v1/resolve?q=Hotel Nacional")
    assert r.status_code == 200
    results = r.json()["results"]
    assert results, "must resolve"
    res = results[0]
    assert res["nombre"] == "Hotel Nacional"
    assert res["precision"] == "poi"
    assert res["radius_m"] > 0
    assert "resolved" in res and res["resolved"]["kind"] == "place"


def test_resolve_geojson(client):
    r = client.get("/api/v1/resolve?q=Hotel Nacional&format=geojson")
    assert r.status_code == 200
    fc = r.json()
    assert fc["type"] == "FeatureCollection"
    feat = fc["features"][0]
    assert feat["geometry"]["type"] == "Point"
    lon, lat = feat["geometry"]["coordinates"]
    assert -85 < lon < -74 and 19 < lat < 24  # inside Cuba, [lon, lat] order
    assert feat["properties"]["precision"] == "poi" and "radius_m" in feat["properties"]


def test_resolve_geojson_unresolved(client):
    r = client.get("/api/v1/resolve?q=zzzzz nowhere qqqq&format=geojson")
    assert r.status_code == 200
    fc = r.json()
    assert fc["type"] == "FeatureCollection" and fc["features"] == []


def test_resolve_batch(client):
    r = client.post("/api/v1/resolve/batch",
                    json={"queries": ["Hotel Nacional", "zzzzz nowhere qqqq"]})
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 2
    assert [x["query"] for x in data["results"]] == ["Hotel Nacional", "zzzzz nowhere qqqq"]
    assert data["results"][0]["results"][0]["nombre"] == "Hotel Nacional"
    assert data["results"][1]["results"] == []  # unresolved → empty list, order preserved


def test_barrios_in_municipio(client):
    # find Plaza de la Revolución's id, then its barrios (Vedado is inside it)
    munis = client.get("/api/v1/municipios").json()
    plaza = next(m for m in munis if m["name"] == "Plaza de la Revolución")
    r = client.get(f"/api/v1/municipios/{plaza['id']}/barrios")
    assert r.status_code == 200
    barrios = r.json()
    assert any(b["name"] == "Vedado" for b in barrios)
    assert all(b["municipality"] == "Plaza de la Revolución" for b in barrios)


def test_barrios_bad_municipio_id(client):
    r = client.get("/api/v1/municipios/99999/barrios")
    assert r.status_code == 404


def test_list_all_barrios(client):
    r = client.get("/api/v1/barrios")
    assert r.status_code == 200
    barrios = r.json()
    assert any(b["name"] == "Vedado" for b in barrios)
    assert all("centroid" in b and b["id"] >= 1 for b in barrios)


@pytest.fixture
def geom_client():
    from shapely.geometry import Polygon, Point
    from geocoder.models import Municipio, Lugar
    from geocoder.api import main as appmod
    from geocoder.api.dependencies import get_search_pipeline
    db = tempfile.mktemp(suffix=".db")
    s = GeoStore(db)
    s.put_municipio(Municipio(
        name="Plaza de la Revolución", province="La Habana",
        polygon=Polygon([(-82.42, 23.10), (-82.36, 23.10), (-82.36, 23.16), (-82.42, 23.16)]),
        centroid=Point(-82.39, 23.13)))
    s.index_lugares([
        Lugar(nombre="Hotel Nacional", norm_nombre="hotel nacional", tipo="poi",
              municipio="Plaza de la Revolución", municipio_norm="plaza de la revolucion",
              provincia="La Habana", lat=23.143, lon=-82.381),
    ])
    pipe = SearchPipeline(s)
    appmod.app.dependency_overrides[get_search_pipeline] = lambda: pipe
    appmod.app.state._test = True
    with TestClient(appmod.app) as c:
        yield c
    appmod.app.dependency_overrides.clear()


def test_resolve_area_has_geojson_geometry(geom_client):
    r = geom_client.get("/api/v1/resolve?q=Plaza de la Revolución")
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["precision"] == "municipio"
    g = res["geometry"]
    assert g is not None
    assert g["type"] in ("Polygon", "MultiPolygon")
    assert isinstance(g["coordinates"], list) and g["coordinates"]


def test_resolve_poi_has_null_geometry(geom_client):
    r = geom_client.get("/api/v1/resolve?q=Hotel Nacional")
    res = r.json()["results"][0]
    assert res["precision"] == "poi"
    assert res["geometry"] is None


@pytest.fixture
def region_client():
    from geocoder.api import main as appmod
    from geocoder.api.dependencies import get_search_pipeline
    db = tempfile.mktemp(suffix=".db")
    s = GeoStore(db)
    s.put_calle_geom("23", None, "LINESTRING(-82.395 23.138, -82.375 23.138)")
    s.put_calle_geom("linea", None, "LINESTRING(-82.395 23.150, -82.375 23.150)")
    s.put_calle_geom("paseo", None, "LINESTRING(-82.390 23.130, -82.390 23.156)")
    s.put_calle_geom("g", None, "LINESTRING(-82.380 23.130, -82.380 23.156)")
    pipe = SearchPipeline(s)
    appmod.app.dependency_overrides[get_search_pipeline] = lambda: pipe
    appmod.app.state._test = True
    with TestClient(appmod.app) as c:
        yield c
    appmod.app.dependency_overrides.clear()


def test_region_endpoint(region_client):
    r = region_client.post("/api/v1/region",
                           json={"streets": ["23", "Línea", "Paseo", "G"]})
    assert r.status_code == 200
    d = r.json()
    assert d["method"] == "region_hull"
    assert len(d["vertices"]) == 4
    assert d["n_crossings"] == 4
    assert 120.0 < d["area_ha"] < 150.0
    assert d["centroid"]["lat"] and d["centroid"]["lon"]
    assert d["polygon_wkt"].startswith("POLYGON")


def test_region_too_few_streets(region_client):
    r = region_client.post("/api/v1/region", json={"streets": ["23", "Paseo"]})
    assert r.status_code == 422


def test_region_no_enclosure(region_client):
    # two parallel streets resolve but never cross → no region
    r = region_client.post("/api/v1/region",
                           json={"streets": ["23", "Línea", "NoSuchA", "NoSuchB"]})
    assert r.status_code == 422
    assert "region" in r.json()["detail"].lower()


@pytest.fixture
def route_client():
    """Client backed by the routing_grid fixture (has a connected graph)."""
    db = tempfile.mktemp(suffix=".db")
    FIXG = Path(__file__).parent / "fixtures" / "routing_grid.osm"
    build_database(str(FIXG), bbox=None, force_rebuild=True, db_path=db)
    from geocoder.api import main as appmod
    from geocoder.api.dependencies import get_store, get_search_pipeline
    store = GeoStore(db)
    pipe = SearchPipeline(store)
    appmod.app.dependency_overrides[get_store] = lambda: store
    appmod.app.dependency_overrides[get_search_pipeline] = lambda: pipe
    with TestClient(appmod.app) as c:
        yield c
    appmod.app.dependency_overrides.clear()


def test_route_endpoint_returns_geometry(route_client):
    # 'B y Calle 1' (node 2) → 'B y Calle 5' (node 8): both are graph nodes, so
    # nearest-node snapping reaches them and the route runs down column B.
    r = route_client.post("/api/v1/route",
                          json={"from": "B y Calle 1", "to": "B y Calle 5"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["distance_m"] > 0
    assert body["duration_min"] > 0
    assert body["geometry"]["type"] == "LineString"
    assert len(body["geometry"]["coordinates"]) >= 2


def test_reverse_carries_an_address_and_keeps_results_shape(client):
    r = client.get("/api/v1/reverse", params={"lat": 23.14, "lon": -82.38,
                                              "radius": 1000, "limit": 5})
    assert r.status_code == 200
    body = r.json()
    # additive promise: the existing shape is untouched
    assert set(body["coordinates"]) == {"lat", "lon"}
    assert isinstance(body["results"], list)
    # the new field is present (may be null when nothing is near)
    assert "address" in body
    if body["address"] is not None:
        a = body["address"]
        assert a["method"].startswith("describe_")
        assert isinstance(a["texto"], str) and a["texto"]
        assert a["radius_m"] > 0


def test_tour_orders_points_and_returns_geometry(client):
    body = {"points": [{"lat": 23.14, "lon": -82.385},
                       {"lat": 23.138, "lon": -82.380},
                       {"lat": 23.142, "lon": -82.390}],
            "return_to_start": False}
    r = client.post("/api/v1/tour", json=body)
    assert r.status_code == 200
    d = r.json()
    assert d["reason"] in {"ok", "unreachable", "empty_graph"}
    if d["reason"] == "ok":
        assert d["order"][0] == 0
        assert sorted(d["order"]) == [0, 1, 2]
        assert d["duration_min"] >= 0


def test_tour_rejects_more_than_twelve_points(client):
    body = {"points": [{"lat": 23.14 + i * 0.001, "lon": -82.385} for i in range(13)],
            "return_to_start": False}
    r = client.post("/api/v1/tour", json=body)
    assert r.status_code == 422
    assert "12" in str(r.json())


def test_resolve_batch_isolates_a_failing_query(client, monkeypatch):
    """One bad row used to 500 the whole batch, losing the other 99 answers."""
    from geocoder.api import main as appmod
    from geocoder.api.dependencies import get_search_pipeline
    pipe = appmod.app.dependency_overrides[get_search_pipeline]()
    real = pipe.resolve

    def resolve(q, **kw):
        if q == "boom":
            raise ValueError("unterminated string")
        return real(q, **kw)
    monkeypatch.setattr(pipe, "resolve", resolve)
    r = client.post("/api/v1/resolve/batch", json={"queries": ["Hotel Nacional", "boom"]})
    assert r.status_code == 200
    ok, bad = r.json()["results"]
    assert ok["results"][0]["nombre"] == "Hotel Nacional" and ok.get("error") is None
    assert bad["results"] == [] and bad["error"] == "unterminated string"


def test_admin_geometries_endpoint(client):
    r = client.get("/api/v1/admin/geometries", params={"level": "municipio"})
    assert r.status_code == 200
    fc = r.json()
    assert fc["type"] == "FeatureCollection"
    plaza = next(f for f in fc["features"] if f["properties"]["name"] == "Plaza de la Revolución")
    assert plaza["geometry"]["type"] in ("Polygon", "MultiPolygon")
    vedado = next(f for f in client.get("/api/v1/admin/geometries?level=barrio").json()["features"]
                  if f["properties"]["name"] == "Vedado")
    assert vedado["properties"]["municipio"] == "Plaza de la Revolución"
    assert client.get("/api/v1/admin/geometries?level=calle").status_code == 422


def test_isochrone_endpoint(route_client):
    r = route_client.post("/api/v1/isochrone", json={"lat": 23.115, "lon": -82.40, "minutes": [3, 1]})
    assert r.status_code == 200, r.text
    fc = r.json()
    assert fc["type"] == "FeatureCollection"
    assert [f["properties"]["minutes"] for f in fc["features"]] == [3, 1]   # widest first draws under
    assert all(f["geometry"]["type"] in ("Polygon", "MultiPolygon") for f in fc["features"])
    assert fc["features"][0]["properties"]["area_km2"] > fc["features"][1]["properties"]["area_km2"]


def test_isochrone_endpoint_rejects_bad_input(route_client):
    off = route_client.post("/api/v1/isochrone", json={"lat": 20.0, "lon": -75.0, "minutes": [5]})
    assert off.status_code == 422 and "off_network" in off.text
    for minutes in ([], [5, 10, 15, 20], [0], [31]):
        bad = route_client.post("/api/v1/isochrone", json={"lat": 23.115, "lon": -82.40, "minutes": minutes})
        assert bad.status_code == 422, minutes


def test_coverage_endpoint(route_client):
    body = {"centers": [{"lat": 23.12, "lon": -82.395}, {"lat": 23.10, "lon": -82.385}],
            "points": [{"lat": 23.119, "lon": -82.396}, {"lat": 23.101, "lon": -82.384},
                       {"lat": 20.0, "lon": -75.0}],
            "max_minutes": 30}
    r = route_client.post("/api/v1/coverage", json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["center"] == [0, 1, None]
    assert d["minutes"][2] is None and all(0 <= m < 2 for m in d["minutes"][:2])
    assert [c["ok"] for c in d["centers"]] == [True, True]


def test_coverage_endpoint_limits(route_client):
    pt = {"lat": 23.12, "lon": -82.395}
    too_many = route_client.post("/api/v1/coverage", json={"centers": [pt] * 9, "points": [pt]})
    assert too_many.status_code == 422
    none = route_client.post("/api/v1/coverage", json={"centers": [], "points": [pt]})
    assert none.status_code == 422
    slow = route_client.post("/api/v1/coverage", json={"centers": [pt], "points": [pt], "max_minutes": 121})
    assert slow.status_code == 422


def test_cluster_endpoint(client):
    m = 1 / 111_000
    pts = [{"lat": 23.10 + i * 20 * m, "lon": -82.40} for i in range(5)] + [{"lat": 22.0, "lon": -80.0}]
    r = client.post("/api/v1/cluster", json={"points": pts, "radius_m": 50, "min_points": 3})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["labels"] == [0, 0, 0, 0, 0, -1]
    assert [c["n"] for c in d["clusters"]] == [5]
    assert d["clusters"][0]["hull"]["type"] == "Polygon"
    bad = client.post("/api/v1/cluster", json={"points": pts, "radius_m": 0, "min_points": 3})
    assert bad.status_code == 422
