"""
Admin Boundary Parser - Extracts municipalities and provinces from OSM relations.

Parses relations with boundary=administrative and admin_level=6 (province) or 8 (municipality).
Builds valid Shapely polygons from relation members (outer/inner ways).
"""
import osmium
from shapely.geometry import LineString, Polygon
from shapely.ops import linemerge, polygonize
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from geocoder.config import SETTINGS
from geocoder.models import Municipio, Provincia


def _polys_from_lines(lines: List[LineString]) -> List[Polygon]:
    """Assemble boundary member-way segments into polygons.

    OSM admin boundaries are made of many member ways that must be stitched
    end-to-end into closed rings. `linemerge` joins segments sharing endpoints;
    `polygonize` then builds polygons from the closed rings.
    """
    if not lines:
        return []
    polys = list(polygonize(linemerge(lines)))
    if polys:
        return polys
    # fallback: any member way that is already a closed ring
    out = []
    for g in lines:
        try:
            if g.is_ring:
                out.append(Polygon(g.coords))
        except Exception:
            pass
    return out


@dataclass
class RelationData:
    """Collected data for an administrative relation."""
    osm_id: int
    name: str
    name_es: Optional[str]
    admin_level: str
    is_in_province: str
    outer_way_ids: List[int] = field(default_factory=list)
    inner_way_ids: List[int] = field(default_factory=list)
    admin_centre_node_id: Optional[int] = None


class AdminBoundaryHandler(osmium.SimpleHandler):
    """
    Osmium handler for administrative boundaries.
    
    Two-pass approach:
    1. Collect all nodes, ways, and relations
    2. Build polygons from relation member ways
    """
    
    def __init__(self, bbox: Optional[Tuple] = None):
        super().__init__()
        self.bbox = bbox
        self.node_cache: Dict[int, Tuple[float, float]] = {}
        self.way_nodes: Dict[int, List[int]] = {}
        self.way_tags: Dict[int, Dict[str, str]] = {}
        self.relations: Dict[int, RelationData] = {}
    
    def _in_bbox(self, lat: float, lon: float) -> bool:
        if not self.bbox:
            return True
        min_lon, min_lat, max_lon, max_lat = self.bbox
        return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat
    
    def node(self, n: osmium.osm.Node):
        if self._in_bbox(n.location.lat, n.location.lon):
            self.node_cache[n.id] = (n.location.lon, n.location.lat)
    
    def way(self, w: osmium.osm.Way):
        if len(w.nodes) < 2:
            return
        # Store every way's node refs: coastal/river admin boundaries reuse
        # tagged ways (natural=coastline, waterway=*) as member ways, so we
        # can't filter by tag or those boundary segments go missing and the
        # ring never closes. Resolved against relation membership later.
        self.way_nodes[w.id] = [n.ref for n in w.nodes]
    
    def relation(self, r: osmium.osm.Relation):
        tags = dict(r.tags)
        levels = (SETTINGS.ADMIN_LEVEL_PROVINCE, SETTINGS.ADMIN_LEVEL_MUNICIPIO)
        if tags.get("boundary") == "administrative" and tags.get("admin_level") in levels:
            name = tags.get("name", "")
            name_es = tags.get("name:es")
            admin_level = tags.get("admin_level")
            is_in_province = tags.get("is_in:province", "")
            
            rel_data = RelationData(
                osm_id=r.id,
                name=name,
                name_es=name_es,
                admin_level=admin_level,
                is_in_province=is_in_province
            )
            
            for member in r.members:
                if member.type == 'w' and member.role in ('outer', 'inner'):
                    if member.role == 'outer':
                        rel_data.outer_way_ids.append(member.ref)
                    else:
                        rel_data.inner_way_ids.append(member.ref)
                elif member.type == 'n' and member.role == 'admin_centre':
                    rel_data.admin_centre_node_id = member.ref
            
            if rel_data.outer_way_ids:
                self.relations[r.id] = rel_data
    
    def build_polygons(self) -> Tuple[List[Municipio], List[Provincia]]:
        """
        Build Shapely polygons from collected relation data.
        
        Returns:
            Tuple of (municipios, provincias) lists.
        """
        # Build LineString geometries for all ways
        way_geoms: Dict[int, LineString] = {}
        for way_id, node_ids in self.way_nodes.items():
            coords = []
            for nid in node_ids:
                if nid in self.node_cache:
                    coords.append(self.node_cache[nid])
            if len(coords) >= 2:
                try:
                    way_geoms[way_id] = LineString(coords)
                except Exception:
                    pass
        
        municipios = []
        provincias = []
        
        for rel_id, rel in self.relations.items():
            # Member ways are boundary SEGMENTS, not individually-closed rings.
            # Stitch them end-to-end with linemerge, then build rings with
            # polygonize — this is what assembles multi-way admin boundaries
            # (e.g. Havana municipios have dozens of member ways).
            outer_lines = [way_geoms[w] for w in rel.outer_way_ids if w in way_geoms]
            inner_lines = [way_geoms[w] for w in rel.inner_way_ids if w in way_geoms]
            if not outer_lines:
                continue

            try:
                outer_polys = _polys_from_lines(outer_lines)
                if not outer_polys:
                    continue
                poly = max(outer_polys, key=lambda p: p.area)

                # Subtract holes (inner rings)
                for hole in _polys_from_lines(inner_lines):
                    poly = poly.difference(hole)

                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.geom_type == "MultiPolygon":
                    poly = max(poly.geoms, key=lambda p: p.area)
                if poly.is_empty or poly.area < 1e-10:
                    continue
                
                # Calculate centroid
                centroid = poly.centroid
                bbox = poly.bounds
                
                if rel.admin_level == SETTINGS.ADMIN_LEVEL_MUNICIPIO:
                    muni = Municipio(
                        name=rel.name,
                        name_es=rel.name_es,
                        province=rel.is_in_province or SETTINGS.TARGET_PROVINCE,
                        polygon=poly,
                        centroid=centroid,
                        admin_level=rel.admin_level,
                        source_osm_id=str(rel.osm_id)
                    )
                    # Set bbox fields
                    muni.bbox_minx, muni.bbox_miny, muni.bbox_maxx, muni.bbox_maxy = bbox
                    municipios.append(muni)
                elif rel.admin_level == SETTINGS.ADMIN_LEVEL_PROVINCE:
                    prov = Provincia(
                        name=rel.name,
                        polygon=poly,
                        centroid=centroid,
                        admin_level=rel.admin_level,
                        source_osm_id=str(rel.osm_id)
                    )
                    provincias.append(prov)
                    
            except Exception as e:
                print(f"Warning: Failed to build polygon for {rel.name} (id={rel_id}): {e}")
        
        print(f"Built {len(municipios)} municipios, {len(provincias)} provincias")
        return municipios, provincias


def parse_admin_boundaries(pbf_path: str, bbox: Optional[Tuple] = None) -> Tuple[List[Municipio], List[Provincia]]:
    """
    Parse administrative boundaries from OSM PBF.
    
    Args:
        pbf_path: Path to .osm.pbf file.
        bbox: Optional bounding box filter.
        
    Returns:
        Tuple of (municipios_list, provincias_list).
    """
    handler = AdminBoundaryHandler(bbox=bbox)
    handler.apply_file(pbf_path)
    print(f"Collected: {len(handler.node_cache)} nodes, {len(handler.way_nodes)} ways, {len(handler.relations)} relations")
    return handler.build_polygons()


if __name__ == "__main__":
    import sys
    pbf = sys.argv[1] if len(sys.argv) > 1 else str(SETTINGS.PBF_PATH)
    municipios, provincias = parse_admin_boundaries(pbf, bbox=SETTINGS.HABANA_BBOX)
    for m in municipios:
        print(f"  {m.name} ({m.province}) - centroid: {m.centroid_lat:.5f}, {m.centroid_lon:.5f} - area: {m.polygon.area:.6f}")