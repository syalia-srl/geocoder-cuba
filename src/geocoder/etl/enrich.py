"""
Entity Enrichment - Assigns municipio and barrio via Point-in-Polygon.

Uses STRtree (Shapely) for efficient spatial indexing.
"""
from typing import List, Dict, Optional
from shapely.geometry import Point
from shapely.strtree import STRtree
from geocoder.models import Municipio, Barrio
from geocoder.utils.normalize import normalize_text


def build_municipio_tree(municipios: List[Municipio]) -> Dict:
    """
    Build STRtree index for municipalities.

    Returns dict with tree and lookup structures.
    """
    # Filter valid polygons
    valid_munis = [m for m in municipios if m.polygon and m.polygon.is_valid and m.polygon.area > 1e-10]

    centroid_munis = [m for m in municipios if m.centroid]
    if not valid_munis:
        return {"tree": None, "geoms": [], "munis": [], "ctree": _centroid_tree(centroid_munis)}

    geometries = [m.polygon for m in valid_munis]
    tree = STRtree(geometries)

    return {
        "tree": tree,
        "geoms": geometries,
        "munis": valid_munis,
        "ctree": _centroid_tree(centroid_munis),
    }


def _centroid_tree(items: List) -> Dict:
    """STRtree over the centroids of `items` (for O(log n) nearest lookup).
    Returns {"tree", "items"} where a returned index maps back into `items`."""
    pts = [it.centroid for it in items]
    return {"tree": STRtree(pts) if pts else None, "items": items}


def _nearest_within(ctree: Dict, pt: Point, max_deg: float):
    """Nearest item whose centroid is within `max_deg` of `pt`, or None.
    O(log n) via STRtree.query_nearest — replaces a linear scan over all items."""
    tree = ctree["tree"]
    if tree is None:
        return None
    idx = tree.query_nearest(pt, max_distance=max_deg, all_matches=False)
    if len(idx) == 0:
        return None
    return ctree["items"][int(idx[0])]


def build_barrio_tree(barrios: List[Barrio]) -> Dict:
    """Build STRtree index for barrios.

    Many OSM barrios are `place=*` nodes with only a centroid (no polygon); they
    can't be point-in-polygon tested, so we also keep every barrio with a
    centroid for a nearest-centroid fallback (see find_containing_barrio).
    """
    poly_barrios = [b for b in barrios if b.polygon and b.polygon.is_valid and b.polygon.area > 1e-10]
    centroid_barrios = [b for b in barrios if b.centroid]

    tree = STRtree([b.polygon for b in poly_barrios]) if poly_barrios else None
    return {
        "tree": tree,
        "geoms": [b.polygon for b in poly_barrios],
        "barrios": poly_barrios,
        "fallback": centroid_barrios,
        "ctree": _centroid_tree(centroid_barrios),
    }


def find_containing_municipio(
    lat: float, 
    lon: float, 
    muni_index: Dict,
    fallback_munis: List[Municipio]
) -> Optional[str]:
    """
    Find municipality containing a point.
    
    Uses STRtree for fast spatial query, falls back to nearest centroid.
    """
    pt = Point(lon, lat)
    
    # Try STRtree. NB: STRtree.query applies the predicate as
    # `pt.<predicate>(tree_geom)`, so "within" (pt within polygon) is the
    # correct point-in-polygon test — "contains" would be pt.contains(polygon).
    if muni_index["tree"]:
        candidates = muni_index["tree"].query(pt, predicate="within")
        for idx in candidates:
            return muni_index["munis"][idx].name

    # Fallback: nearest centroid (uncapped). STRtree.query_nearest → O(log n)
    # instead of a linear scan over every municipio for each entity.
    ctree = muni_index.get("ctree")
    if ctree is not None:
        m = _nearest_within(ctree, pt, None)
        if m is not None:
            return m.name

    # Legacy list fallback (used only by callers that pass fallback_munis and
    # a muni_index without a centroid tree, e.g. the module self-test).
    if fallback_munis:
        best, best_dist = None, float("inf")
        for m in fallback_munis:
            if m.centroid:
                d = pt.distance(m.centroid)
                if d < best_dist:
                    best_dist, best = d, m
        if best:
            return best.name

    return None


# Max distance (degrees, ~2.2 km) for the nearest-barrio-centroid fallback.
# Barrios are neighborhood-scale; beyond this a "nearest" barrio is meaningless.
_BARRIO_FALLBACK_MAX_DEG = 0.02


def find_containing_barrio(
    lat: float,
    lon: float,
    barrio_index: Dict,
    municipio_name: str = None
) -> Optional[str]:
    """
    Find the barrio for a point.

    First tries point-in-polygon over barrios that have polygons; if none
    contains the point (e.g. the area's barrios are OSM nodes with no polygon),
    falls back to the nearest barrio centroid within a small distance cap.
    """
    pt = Point(lon, lat)

    # 1. Point-in-polygon. "within" = pt within polygon (correct PIP direction).
    if barrio_index["tree"]:
        for idx in barrio_index["tree"].query(pt, predicate="within"):
            return barrio_index["barrios"][idx].name

    # 2. Nearest-centroid fallback (covers node-only barrios), distance-capped.
    #    STRtree.query_nearest → O(log n); the old linear scan over every barrio
    #    made full-Cuba enrichment O(entities × barrios).
    ctree = barrio_index.get("ctree")
    if ctree is not None:
        b = _nearest_within(ctree, pt, _BARRIO_FALLBACK_MAX_DEG)
        if b is not None:
            return b.name
    return None


def enrich_entities(
    streets: List,
    portals: List,
    pois: List,
    municipios: List[Municipio],
    barrios: List[Barrio]
) -> None:
    """
    Enrich all entities with municipio and barrio via Point-in-Polygon.
    
    Modifies objects in-place.
    
    Args:
        streets: List of street entities
        portals: List of portal entities
        pois: List of POI entities
        municipios: List of Municipio models with polygons
        barrios: List of Barrio models with polygons
    """
    # Build spatial indexes
    muni_index = build_municipio_tree(municipios)
    barrio_index = build_barrio_tree(barrios)

    # Build fallback list for municipalities without valid polygons
    fallback_munis = [m for m in municipios if m.centroid]
    # name → province, so we don't rescan every municipio per entity (O(N×M))
    prov_by_name = {m.name: (m.province or "") for m in municipios}

    all_entities = []
    for e in streets:
        all_entities.append(('calle', e))
    for e in portals:
        all_entities.append(('portal', e))
    for e in pois:
        all_entities.append(('poi', e))
    
    print(f"Enriching {len(all_entities)} entities...")
    
    enriched_count = 0
    for etype, entity in all_entities:
        lat, lon = entity.lat, entity.lon
        
        # Find municipality
        municipio = find_containing_municipio(lat, lon, muni_index, fallback_munis)
        if municipio:
            entity.municipio = municipio
            entity.municipio_norm = normalize_text(municipio)
            entity.provincia = prov_by_name.get(municipio, "")
            enriched_count += 1
        
        # Find barrio
        barrio = find_containing_barrio(lat, lon, barrio_index, municipio)
        if barrio:
            entity.barrio = barrio
    
    print(f"  Enriched {enriched_count} entities with municipio/barrio")


def enrich_with_fallback(
    entities: List,
    municipios: List[Municipio]
) -> None:
    """
    Simple fallback enrichment using nearest centroid.
    
    Used when spatial indexes are not available.
    """
    for entity in entities:
        if entity.municipio:
            continue  # Already enriched
        
        lat, lon = entity.lat, entity.lon
        pt = Point(lon, lat)
        
        best_muni = None
        best_dist = float('inf')
        
        for m in municipios:
            if m.centroid:
                d = pt.distance(m.centroid)
                if d < best_dist:
                    best_dist = d
                    best_muni = m
        
        if best_muni:
            entity.municipio = best_muni.name
            entity.provincia = best_muni.province
            # Try barrio via nearest centroid (approximate)
            # Would need barrio centroids


if __name__ == "__main__":
    # Test with mock data
    from shapely.geometry import Polygon, Point
    from geocoder.models import Municipio, Barrio
    
    m = Municipio(
        name="Test",
        province="Test",
        polygon=Polygon([(0,0), (1,0), (1,1), (0,1)]),
        centroid=Point(0.5, 0.5)
    )
    
    idx = build_municipio_tree([m])
    print(find_containing_municipio(0.5, 0.5, idx, [m]))