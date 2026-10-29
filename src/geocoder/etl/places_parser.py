"""
Places Parser - Extracts barrios (neighbourhoods, suburbs, quarters) from OSM.

Parses nodes/ways/areas with place=neighbourhood|suburb|quarter|city_block.
"""
import osmium
from shapely.geometry import Point, Polygon
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from geocoder.config import SETTINGS
from geocoder.models import Barrio


@dataclass
class PlaceData:
    osm_id: int
    name: str
    name_es: Optional[str]
    place_type: str
    is_in_municipality: Optional[str]
    is_in_province: Optional[str]
    centroid: Point
    polygon: Optional[Polygon] = None


class PlacesHandler(osmium.SimpleHandler):
    """Extracts place=* tags for barrios and localities."""
    
    def __init__(self, bbox: Optional[Tuple] = None):
        super().__init__()
        self.bbox = bbox
        self.node_cache: Dict[int, Tuple[float, float]] = {}
        self.way_nodes: Dict[int, List[int]] = {}
        self.way_tags: Dict[int, Dict[str, str]] = {}
        self.places: List[PlaceData] = []
        self._node_count = 0
        self._way_count = 0
    
    def _in_bbox(self, lat: float, lon: float) -> bool:
        if not self.bbox:
            return True
        min_lon, min_lat, max_lon, max_lat = self.bbox
        return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat
    
    def node(self, n: osmium.osm.Node):
        self._node_count += 1
        if self._in_bbox(n.location.lat, n.location.lon):
            self.node_cache[n.id] = (n.location.lon, n.location.lat)
            
            tags = dict(n.tags)
            place = tags.get("place")
            if place in ("neighbourhood", "suburb", "quarter", "city_block"):
                name = tags.get("name", "")
                if name:
                    self.places.append(PlaceData(
                        osm_id=n.id,
                        name=name,
                        name_es=tags.get("name:es"),
                        place_type=place,
                        is_in_municipality=tags.get("is_in:municipality"),
                        is_in_province=tags.get("is_in:province"),
                        centroid=Point(n.location.lon, n.location.lat)
                    ))
    
    def way(self, w: osmium.osm.Way):
        self._way_count += 1
        tags = dict(w.tags)
        if tags.get("place") in ("neighbourhood", "suburb", "quarter", "city_block"):
            self.way_nodes[w.id] = [n.ref for n in w.nodes]
            self.way_tags[w.id] = tags
    
    def area(self, a: osmium.osm.Area):
        tags = dict(a.tags)
        place = tags.get("place")
        if place in ("neighbourhood", "suburb", "quarter", "city_block"):
            name = tags.get("name", "")
            if name:
                # Build centroid from area (approximate). osmium's
                # inner_rings() requires its parent outer ring.
                coords = []
                for outer in a.outer_rings():
                    for nr in outer:
                        coords.append((nr.lon, nr.lat))
                    for inner in a.inner_rings(outer):
                        for nr in inner:
                            coords.append((nr.lon, nr.lat))
                if coords:
                    centroid = Point(sum(c[0] for c in coords)/len(coords),
                                    sum(c[1] for c in coords)/len(coords))

                    # Try to build polygon from the first outer ring + its holes
                    polygon = None
                    try:
                        outers = list(a.outer_rings())
                        if outers:
                            outer = outers[0]
                            poly_coords = [(nr.lon, nr.lat) for nr in outer]
                            holes = [[(nr.lon, nr.lat) for nr in inner]
                                     for inner in a.inner_rings(outer)]
                            polygon = Polygon(poly_coords, holes)
                            if not polygon.is_valid:
                                polygon = polygon.buffer(0)
                    except Exception:
                        pass
                    
                    self.places.append(PlaceData(
                        osm_id=a.orig_id(),
                        name=name,
                        name_es=tags.get("name:es"),
                        place_type=place,
                        is_in_municipality=tags.get("is_in:municipality"),
                        is_in_province=tags.get("is_in:province"),
                        centroid=centroid,
                        polygon=polygon
                    ))
    
    def finalize(self) -> List[Barrio]:
        """Convert PlaceData to Barrio models with polygon geometries from ways."""
        print(f"Collected: {len(self.node_cache)} nodes, {len(self.way_nodes)} ways, {len(self.places)} places")
        
        # Build polygons from ways
        way_polygons: Dict[int, Polygon] = {}
        for way_id, node_ids in self.way_nodes.items():
            coords = []
            for nid in node_ids:
                if nid in self.node_cache:
                    coords.append(self.node_cache[nid])
            if len(coords) >= 3:
                try:
                    poly = Polygon(coords)
                    if poly.is_valid:
                        way_polygons[way_id] = poly
                except Exception:
                    pass
        
        # Upgrade places with polygon geometry if available
        barrios = []
        for p in self.places:
            polygon = p.polygon
            # Check if we have a way polygon for this OSM ID
            if not polygon and p.osm_id in way_polygons:
                polygon = way_polygons[p.osm_id]
            
            barrio = Barrio(
                name=p.name,
                name_es=p.name_es,
                municipio_id=None,  # Will be set during enrichment
                polygon=polygon,
                centroid=p.centroid,
                place_type=p.place_type,
                source_osm_id=str(p.osm_id)
            )
            barrios.append(barrio)
        
        print(f"Built {len(barrios)} barrios")
        return barrios


def parse_barrios(pbf_path: str, bbox: Optional[Tuple] = None) -> List[Barrio]:
    """
    Parse barrios from OSM PBF.
    
    Args:
        pbf_path: Path to .osm.pbf file.
        bbox: Optional bounding box filter.
        
    Returns:
        List of Barrio models.
    """
    handler = PlacesHandler(bbox=bbox)
    handler.apply_file(pbf_path)
    return handler.finalize()


if __name__ == "__main__":
    import sys
    pbf = sys.argv[1] if len(sys.argv) > 1 else str(SETTINGS.PBF_PATH)
    barrios = parse_barrios(pbf, bbox=SETTINGS.HABANA_BBOX)
    for b in barrios[:20]:
        print(f"  {b.name} ({b.place_type}) - centroid: {b.centroid.y:.5f}, {b.centroid.x:.5f} - poly: {b.polygon is not None}")