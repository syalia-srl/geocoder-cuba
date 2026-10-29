from pathlib import Path
from typer.testing import CliRunner
from geocoder.cli.main import cli
from geocoder.etl.build import build_database

runner = CliRunner()
FIXG = Path(__file__).parent / "fixtures" / "routing_grid.osm"


def test_cli_route_between_two_corners(tmp_path, monkeypatch):
    db = tmp_path / "grid.db"
    build_database(str(FIXG), bbox=None, force_rebuild=True, db_path=db)
    monkeypatch.setenv("GEOCODER_DB_PATH", str(db))
    # 'B y Calle 1' (node 2) and 'B y Calle 5' (node 8) are graph nodes that
    # survive contraction, so nearest-node snapping (VS1) reaches them; the route
    # runs down column B. (Corner esquinas need VS4 nearest-edge snapping.)
    res = runner.invoke(cli, ["route", "B y Calle 1", "B y Calle 5"])
    assert res.exit_code == 0, res.output
    assert "km" in res.output.lower()
    assert "min" in res.output.lower()
