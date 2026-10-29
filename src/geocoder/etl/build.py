"""
ETL Build Orchestrator - Coordinates the full pipeline.

Pipeline:
1. Admin boundaries (municipios, provincias)
2. Barrios (neighbourhoods, suburbs)
3. Entities (streets, portals, POIs)
4. Point-in-polygon enrichment (municipio, barrio)
5. Bulk insert into GeoStore (beaver-backed)
"""
import time
from typing import Optional, Tuple, Dict, Any
from pathlib import Path
from geocoder.config import SETTINGS
from geocoder.etl.admin_parser import parse_admin_boundaries
from geocoder.etl.places_parser import parse_barrios
from geocoder.etl.entity_parser import parse_entities, EntityParserState
from geocoder.etl.enrich import enrich_entities
from geocoder.store import GeoStore
from geocoder.models import Lugar


def build_database(
    pbf_path: str,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    force_rebuild: bool = False,
    full_cuba: bool = False,
    db_path=None
) -> Dict[str, Any]:
    """
    Execute complete ETL pipeline.

    Args:
        pbf_path: Path to .osm / .osm.pbf file
        bbox: Optional bounding box filter (min_lon, min_lat, max_lon, max_lat)
        force_rebuild: Delete the DB file and rebuild from scratch
        full_cuba: Process entire Cuba (ignore bbox)
        db_path: Optional path to the beaver DB (defaults to SETTINGS.DB_PATH)

    Returns:
        Statistics dict with counts
    """
    if full_cuba:
        bbox = None

    resolved_db = Path(db_path) if db_path else Path(SETTINGS.DB_PATH)

    print("=" * 60)
    print("Cuba Geocoder - ETL Pipeline")
    print("=" * 60)
    print(f"PBF: {pbf_path}")
    print(f"BBox: {bbox or 'Full Cuba'}")
    print(f"Force rebuild: {force_rebuild}")
    print(f"DB: {resolved_db}")
    print()

    # force_rebuild: wipe the beaver DB files before opening
    if force_rebuild:
        for suffix in ("", "-wal", "-shm"):
            p = resolved_db.with_name(resolved_db.name + suffix)
            p.unlink(missing_ok=True)
        print("Removed existing DB files.")

    store = GeoStore(resolved_db)

    total_start = time.time()

    # ===== Step 1: Admin Boundaries =====
    # Parse admin boundaries UNCLIPPED (bbox=None): a municipio/province boundary
    # relation's member ways run outside the entity bbox, so clipping node caching
    # leaves their polygons unassembled (only peripheral ones survive). We cache
    # all nodes here so polygons are complete, then rely on point-in-polygon
    # enrichment to assign the correct municipio to the (bbox-filtered) entities.
    print("\n[1/5] Parsing administrative boundaries (unclipped)...")
    t0 = time.time()
    municipios, provincias = parse_admin_boundaries(pbf_path, bbox=None)
    t1 = time.time()
    print(f"  Found {len(municipios)} municipios, {len(provincias)} provincias ({t1-t0:.1f}s)")

    # Assign each municipio its province by spatial containment. OSM rarely tags
    # `is_in:province` on Cuban municipio relations, so the parser default
    # (TARGET_PROVINCE) mislabels every municipio outside Havana. The province
    # polygons (admin_level 4) are parsed here too — PIP the municipio centroid.
    _assign_provinces(municipios, provincias)

    for m in municipios:
        store.put_municipio(m)

    for p in provincias:
        store.put_provincia(p)

    # ===== Step 2: Barrios =====
    print("\n[2/5] Parsing barrios/neighbourhoods...")
    t0 = time.time()
    barrios = parse_barrios(pbf_path, bbox=bbox)
    t1 = time.time()
    print(f"  Found {len(barrios)} barrios ({t1-t0:.1f}s)")

    for b in barrios:
        store.put_barrio(b)

    # ===== Step 3: Entities (streets, portals, POIs) =====
    print("\n[3/5] Parsing streets, portals, and POIs...")
    t0 = time.time()
    state: EntityParserState = parse_entities(pbf_path, bbox=bbox)
    t1 = time.time()
    print(f"  Streets: {len(state.streets)}, Portals: {len(state.portals)}, POIs: {len(state.pois)} ({t1-t0:.1f}s)")

    # ===== Step 4: Enrichment (Point-in-Polygon) =====
    print("\n[4/5] Enriching with municipio/barrio (Point-in-Polygon)...")
    t0 = time.time()
    enrich_entities(
        streets=state.streets,
        portals=state.portals,
        pois=state.pois,
        municipios=municipios,
        barrios=barrios
    )
    t1 = time.time()
    print(f"  Enrichment completed ({t1-t0:.1f}s)")

    # ===== Step 5: Insert into GeoStore =====
    print("\n[5/5] Inserting into GeoStore...")
    t0 = time.time()

    all_lugares = []
    for s in state.streets:
        all_lugares.append(_lugar_from_street(s))
    for p in state.portals:
        all_lugares.append(_lugar_from_portal(p))
    for poi in state.pois:
        all_lugares.append(_lugar_from_poi(poi))

    print(f"  Indexing {len(all_lugares)} lugares...")
    store.index_lugares(all_lugares)

    print(f"  Inserting {len(state.portals)} portals...")
    store.put_portals(state.portals)

    # Merge all way-fragments of the same street (per municipio) into one
    # geometry — a street is many OSM ways, and keeping only one ~20 m fragment
    # breaks intersection / entre-segment resolution.
    print("  Merging + inserting street geometries...")
    from collections import defaultdict
    from shapely import wkt as _wkt
    from shapely.ops import linemerge
    _groups: Dict = defaultdict(list)
    for s in state.streets:
        if s.geom_wkt:
            try:
                _groups[(s.street_norm, s.municipio)].append(_wkt.loads(s.geom_wkt))
            except Exception:
                pass
    _merged = []
    for (sn, mun), geoms in _groups.items():
        g = linemerge(geoms) if len(geoms) > 1 else geoms[0]
        _merged.append((sn, mun, g.wkt))
    print(f"  {len(state.streets)} street ways → {len(_merged)} merged streets")
    store.put_calle_geoms(_merged)

    # Precompute every street crossing once, so the resolver reads a table
    # instead of intersecting geometries per query.
    from geocoder.etl.intersect import compute_intersections
    muni_prov = {m.name: (m.province or SETTINGS.TARGET_PROVINCE) for m in municipios}
    inter = compute_intersections(_merged, muni_prov)
    print(f"  {len(inter)} street crossings precomputed")
    store.put_intersections(inter)

    # Routing graph — built from the raw way topology the entity parser already
    # cached (node refs + coords), contracting degree-2 chains into edges.
    from geocoder.etl.graph_builder import build_graph
    print("  Building routing graph...")
    gnodes, gedges = build_graph(state.way_nodes, state.way_tags, state.node_cache)
    gn, ge = store.put_graph(gnodes, gedges)
    print(f"  Routing graph: {gn} nodes, {ge} adjacency lists")

    # Count by tipo
    por_tipo: Dict[str, int] = {}
    for lugar in all_lugares:
        por_tipo[lugar.tipo] = por_tipo.get(lugar.tipo, 0) + 1

    store.meta["counts"] = {"lugares": len(all_lugares), "por_tipo": por_tipo}

    t1 = time.time()
    print(f"  GeoStore insert completed ({t1-t0:.1f}s)")

    total_time = time.time() - total_start

    stats = store.stats()
    # Ensure municipios count surfaces at the top level
    stats["municipios"] = len(municipios)

    print(f"\n{'='*60}")
    print(f"BUILD COMPLETED in {total_time:.1f}s")
    print(f"{'='*60}")
    for k, v in stats.items():
        if isinstance(v, dict):
            print(f"  {k}:")
            for kk, vv in v.items():
                print(f"    {kk}: {vv}")
        else:
            print(f"  {k}: {v}")

    # Stamp which code produced this data. The ETL and the query path share
    # normalize_street_name, so a change there invalidates the keys written
    # here; the packager refuses to bake a store whose stamp does not match.
    stamp = store.stamp_build(commit=_git_commit(), version=_pkg_version())
    print(f"  build stamp: {stamp['commit']} (v{stamp['version']}) {stamp['built_at']}")

    store.close()
    return stats


def _git_commit() -> str:
    """Short SHA of the checkout that built this, or 'unknown' outside one."""
    import subprocess
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
            cwd=Path(__file__).parent,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _pkg_version() -> str:
    from importlib.metadata import version, PackageNotFoundError
    try:
        return version("geocoder-cuba")
    except PackageNotFoundError:
        return "unknown"


def _assign_provinces(municipios, provincias) -> None:
    """Set each municipio's province to the admin_level-4 province polygon that
    contains its centroid (falls back to the parser default when no polygon
    contains it). Corrects the Havana-only default for the whole country."""
    from shapely.strtree import STRtree
    named = [(p.name, p.polygon) for p in provincias
             if p.polygon and p.polygon.is_valid and p.polygon.area > 1e-10]
    if not named:
        return
    tree = STRtree([poly for _, poly in named])
    for m in municipios:
        if not m.centroid:
            continue
        hit = tree.query(m.centroid, predicate="within")
        if len(hit):
            m.province = named[int(hit[0])][0]


# Helper functions for model conversion

def _lugar_from_street(s) -> Lugar:
    return Lugar(
        nombre=s.nombre,
        norm_nombre=s.norm_nombre,
        tipo="calle",
        municipio=s.municipio,
        municipio_norm=s.municipio_norm if hasattr(s, "municipio_norm") else None,
        provincia=s.provincia,
        barrio=s.barrio,
        lat=s.lat,
        lon=s.lon,
        geom_wkt=s.geom_wkt,
        street_norm=s.street_norm,
        portal_number=None,
        tags_json=s.tags_json if hasattr(s, "tags_json") else None
    )


def _lugar_from_portal(p) -> Lugar:
    return Lugar(
        nombre=f"{p.street_norm} {p.numero}",
        norm_nombre=f"{p.street_norm} {p.numero}",
        tipo="portal",
        municipio=p.municipio,
        municipio_norm=p.municipio_norm,
        provincia=p.provincia,
        barrio=p.barrio,
        lat=p.lat,
        lon=p.lon,
        geom_wkt=None,
        street_norm=p.street_norm,
        portal_number=p.numero,
        tags_json=p.tags_json if hasattr(p, "tags_json") else None
    )


def _lugar_from_poi(poi) -> Lugar:
    return Lugar(
        nombre=poi.nombre,
        norm_nombre=poi.norm_nombre,
        tipo="poi",
        municipio=poi.municipio,
        municipio_norm=poi.municipio_norm if hasattr(poi, "municipio_norm") else None,
        provincia=poi.provincia,
        barrio=poi.barrio,
        lat=poi.lat,
        lon=poi.lon,
        geom_wkt=poi.geom_wkt if hasattr(poi, "geom_wkt") else None,
        street_norm=None,
        portal_number=None,
        tags_json=poi.tags_json if hasattr(poi, "tags_json") else None
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        pbf = sys.argv[1]
    else:
        pbf = str(SETTINGS.PBF_PATH)

    build_database(pbf, bbox=SETTINGS.HABANA_BBOX)
