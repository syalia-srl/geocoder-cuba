from geocoder.models import Region


def _region():
    return Region(
        streets=["23", "Línea", "Paseo", "G"],
        unresolved=[],
        vertices=[[23.138, -82.390], [23.138, -82.380],
                  [23.150, -82.380], [23.150, -82.390]],
        polygon_wkt="POLYGON((-82.390 23.138, -82.380 23.138, "
                    "-82.380 23.150, -82.390 23.150, -82.390 23.138))",
        centroid_lat=23.144, centroid_lon=-82.385,
        area_m2=1360000.0, area_ha=136.0,
        bbox=(23.138, -82.390, 23.150, -82.380),
        municipio="Plaza de la Revolución", provincia="La Habana",
        n_crossings=4,
    )


def test_region_defaults():
    r = _region()
    assert r.method == "region_hull"
    assert r.degenerate is False and r.ambiguous is False


def test_region_to_dict_shape():
    d = _region().to_dict()
    assert d["streets"] == ["23", "Línea", "Paseo", "G"]
    assert d["unresolved"] == []
    assert d["vertices"][0] == [23.138, -82.39]
    assert d["centroid"] == {"lat": 23.144, "lon": -82.385}
    assert d["bbox"] == {"min_lat": 23.138, "min_lon": -82.39,
                         "max_lat": 23.15, "max_lon": -82.38}
    assert d["area_ha"] == 136.0
    assert d["method"] == "region_hull" and d["n_crossings"] == 4
    assert d["degenerate"] is False and d["ambiguous"] is False
    assert d["municipio"] == "Plaza de la Revolución"
    assert d["provincia"] == "La Habana"
    assert "polygon_wkt" in d
