"""ETL integration tests using a hand-written .osm fixture."""
import tempfile
from pathlib import Path
from geocoder.etl.build import build_database
from geocoder.store import GeoStore

FIX = Path(__file__).parent / "fixtures" / "habana_sample.osm"


def test_build_from_fixture_populates_store():
    db = tempfile.mktemp(suffix=".db")
    stats = build_database(str(FIX), bbox=None, force_rebuild=True, db_path=db)
    assert stats["municipios"] >= 1
    s = GeoStore(db)
    hits = s.search_lugares('"calle"* AND "23"', limit=5)
    assert any(h["nombre"] == "Calle 23" for h in hits)
    assert any(h["tipo"] == "poi" for h in s.search_lugares('"hotel"*', limit=5))
    assert s.find_portal("23", "415") is not None
    # barrio enrichment: Hotel Nacional sits inside the Vedado barrio polygon
    hotel = [h for h in s.search_lugares('"hotel"*', limit=5) if h["nombre"] == "Hotel Nacional"]
    assert hotel and hotel[0]["barrio"] == "Vedado", "barrio point-in-polygon enrichment must populate barrio"
    # alt_name: "Calle 23" also carries alt_name="La Rampa" → the colloquial name
    # must resolve to the same street geometry.
    assert s.get_calle_geom("la rampa") is not None, "alt_name street must be indexed"
    assert s.get_calle_geom("la rampa") == s.get_calle_geom("23"), "alt_name shares the geometry"
    assert any(h["nombre"] == "La Rampa" for h in s.search_lugares('"rampa"*', limit=5))
    # parenthetical alias: "Avenida de Italia (Galiano)" → searchable as "galiano"
    # and as the base "avenida de italia" (normalized "de italia").
    assert s.get_calle_geom("galiano") is not None, "parenthetical alias must be indexed"
    assert s.get_calle_geom("de italia") == s.get_calle_geom("galiano"), "base + alias share geometry"
    s.close()


def test_street_name_variants_keeps_numbered_base():
    """A numbered street with a colloquial parenthetical ('Calle 23 (La Rampa)')
    must still feed the numbered street '23' — else La Rampa becomes a
    disconnected object and 23 never reaches its crossing with O near the Malecón.
    But a *numeric parenthetical alias* ('Neptuno (Avenida 22)') must NOT pollute
    the numbered grid street '22'."""
    from geocoder.etl.entity_parser import _street_name_variants
    from geocoder.utils.normalize import normalize_street_name

    def norms(nm):
        return {normalize_street_name(v) for v in _street_name_variants({"name": nm})}

    assert {"23", "la rampa"} <= norms("Calle 23 (La Rampa)")
    assert "22" not in norms("Neptuno (Avenida 22)")
    assert "neptuno" in norms("Neptuno (Avenida 22)")


def test_build_populates_routing_graph(tmp_path):
    FIXG = Path(__file__).parent / "fixtures" / "routing_grid.osm"
    db = tmp_path / "grid.db"
    build_database(str(FIXG), bbox=None, force_rebuild=True, db_path=db)
    s = GeoStore(db)
    gnodes, gadj = s.load_graph()
    # In a pure grid the 4 corners are degree-2 (one E-W + one N-S residential
    # way) and contract away, absorbed into edges that pass through them. What
    # survives: the 4 edge-midpoints (degree 3) + the centre (degree 4) = 5.
    assert len(gnodes) == 5
    assert all(gadj.get(k) for k in gnodes)
    # The whole graph is one connected component (BFS from any node reaches all).
    start = next(iter(gnodes))
    seen, stack = {start}, [start]
    while stack:
        u = stack.pop()
        for e in gadj.get(u, []):
            if e["to"] not in seen:
                seen.add(e["to"])
                stack.append(e["to"])
    assert seen == set(gnodes)
    s.close()
