import json
from typer.testing import CliRunner
from geocoder.cli.main import cli
from geocoder.models import Region

runner = CliRunner()


def _fake_region():
    return Region(
        streets=["23", "Línea", "Paseo", "G"], unresolved=[],
        vertices=[[23.138, -82.39], [23.138, -82.38],
                  [23.15, -82.38], [23.15, -82.39]],
        polygon_wkt="POLYGON((-82.39 23.138, -82.38 23.138, "
                    "-82.38 23.15, -82.39 23.15, -82.39 23.138))",
        centroid_lat=23.144, centroid_lon=-82.385,
        area_m2=1360000.0, area_ha=136.0,
        bbox=(23.138, -82.39, 23.15, -82.38),
        municipio="Plaza de la Revolución", provincia="La Habana", n_crossings=4,
    )


def _patch(monkeypatch):
    # stub the store and pipeline the CLI constructs
    monkeypatch.setattr("geocoder.store.GeoStore", lambda *a, **k: object())

    class _Pipe:
        def __init__(self, store):
            pass

        def resolve_region(self, streets, municipio=None):
            return _fake_region()

    monkeypatch.setattr("geocoder.search.pipeline.SearchPipeline", _Pipe)


def test_cli_region_text(monkeypatch):
    _patch(monkeypatch)
    res = runner.invoke(cli, ["region", "23", "Línea", "Paseo", "G"])
    assert res.exit_code == 0
    assert "136" in res.stdout          # area in ha
    assert "Plaza de la Revolución" in res.stdout


def test_cli_region_json(monkeypatch):
    _patch(monkeypatch)
    res = runner.invoke(cli, ["region", "23", "Línea", "Paseo", "G", "--json"])
    assert res.exit_code == 0
    d = json.loads(res.stdout)
    assert d["method"] == "region_hull" and len(d["vertices"]) == 4


def test_cli_region_too_few(monkeypatch):
    _patch(monkeypatch)
    res = runner.invoke(cli, ["region", "23", "Paseo"])
    assert res.exit_code == 1
    assert "3 streets" in res.stdout
