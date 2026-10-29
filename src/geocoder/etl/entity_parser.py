"""
Entity Parser - Extracts streets, portals, and POIs from OSM.

Single pass with node cache:
- Streets: ways with highway=* + name=*
- Portals: nodes/ways with addr:housenumber + addr:street
- POIs: any element with name + relevant tags (amenity, shop, tourism, etc.)
"""
import re
import osmium
from shapely.geometry import Point, LineString, Polygon
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from geocoder.config import SETTINGS
from geocoder.models import Lugar, Portal
from geocoder.utils.normalize import normalize_street_name, normalize_text


# Highway types that represent streets/roads
HIGHWAY_TYPES = {
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "service", "living_street",
    "pedestrian", "track", "road", "path", "footway", "cycleway",
    "steps", "bridleway", "corridor", "motorway_link", "trunk_link",
    "primary_link", "secondary_link", "tertiary_link"
}

# POI-relevant tags
POI_TAGS = {
    "amenity", "shop", "tourism", "leisure", "building", "office",
    "craft", "healthcare", "education", "public_transport", "historic",
    "emergency", "religion", "sport", "man_made", "natural", "landuse"
}

# OSM tags carrying alternate/colloquial street names (besides `name`).
ALT_NAME_TAGS = ("alt_name", "old_name", "loc_name", "short_name")
_PAREN_RE = re.compile(r"\(([^)]*)\)")


def _street_name_variants(tags: Dict[str, str]) -> List[str]:
    """All names a street can be searched by: `name`, plus the colloquial name
    Cuban OSM embeds in parentheses inside `name` ("Avenida de Italia (Galiano)"
    → also "Avenida de Italia" and "Galiano"), plus alt/old/loc/short names.

    Numeric aliases are dropped: "Neptuno (Avenida 22)" must not add Neptuno's
    geometry to the numbered street "22" and pollute the grid. `name` first."""
    name = tags.get("name")
    if not name:
        return []
    variants = [name]
    # the base name (parentheses stripped) is the real primary name — ALWAYS keep
    # it, even when numeric: "Calle 23 (La Rampa)" must still feed the "23" street,
    # or La Rampa becomes a disconnected object and 23 never reaches the Malecón.
    base = _PAREN_RE.sub("", name).strip()
    if base and base != name:
        variants.append(base)
    # colloquial aliases (parentheticals + alt/old/loc/short names) get the numeric
    # filter, so "Neptuno (Avenida 22)" does NOT dump its geometry onto street "22".
    aliases = [x.strip() for x in _PAREN_RE.findall(name) if x.strip()]
    for key in ALT_NAME_TAGS:
        if tags.get(key):
            aliases.extend(part.strip() for part in tags[key].split(";") if part.strip())
    for a in aliases:
        if a and not normalize_street_name(a).isdigit():
            variants.append(a)
    return variants


@dataclass
class EntityParserState:
    """Mutable state during parsing."""
    node_cache: Dict[int, Tuple[float, float]] = field(default_factory=dict)
    way_nodes: Dict[int, List[int]] = field(default_factory=dict)
    way_tags: Dict[int, Dict[str, str]] = field(default_factory=dict)
    streets: List[Lugar] = field(default_factory=list)
    portals: List[Portal] = field(default_factory=list)
    pois: List[Lugar] = field(default_factory=list)
    node_count: int = 0
    way_count: int = 0


class EntityHandler(osmium.SimpleHandler):
    """
    Osmium handler for streets, portals, and POIs.
    
    Uses node cache for way geometry reconstruction.
    """
    
    def __init__(self, bbox: Optional[Tuple] = None):
        super().__init__()
        self.bbox = bbox
        self.state = EntityParserState()
    
    def _in_bbox(self, lat: float, lon: float) -> bool:
        if not self.bbox:
            return True
        min_lon, min_lat, max_lon, max_lat = self.bbox
        return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat
    
    def node(self, n: osmium.osm.Node):
        self.state.node_count += 1
        if self._in_bbox(n.location.lat, n.location.lon):
            self.state.node_cache[n.id] = (n.location.lon, n.location.lat)
            
            tags = dict(n.tags)
            # Check for portal (addr:housenumber + addr:street)
            if "addr:housenumber" in tags and "addr:street" in tags:
                self._process_portal_node(n, tags)
            
            # Check for POI (named node with relevant tags)
            elif "name" in tags and self._is_poi(tags):
                self._process_poi_node(n, tags)
    
    def way(self, w: osmium.osm.Way):
        self.state.way_count += 1
        tags = dict(w.tags)
        
        if len(w.nodes) < 2:
            return
        
        # Store way data for later geometry building
        self.state.way_nodes[w.id] = [n.ref for n in w.nodes]
        self.state.way_tags[w.id] = tags
        
        # Street: highway + name
        if "highway" in tags and tags["highway"] in HIGHWAY_TYPES and "name" in tags:
            self._process_street_way(w, tags)
        
        # Portal on way (building with address)
        elif "addr:housenumber" in tags and "addr:street" in tags:
            self._process_portal_way(w, tags)
        
        # POI on way (building, amenity area, etc.)
        elif "name" in tags and self._is_poi(tags):
            self._process_poi_way(w, tags)
    
    def area(self, a: osmium.osm.Area):
        tags = dict(a.tags)
        if "name" in tags and self._is_poi(tags):
            self._process_poi_area(a, tags)
    
    def _is_poi(self, tags: Dict[str, str]) -> bool:
        """Check if tags indicate a POI."""
        return any(k in tags for k in POI_TAGS)
    
    def _process_street_way(self, w: osmium.osm.Way, tags: Dict[str, str]):
        """Extract street from way, emitting one entry per name variant.

        Cuban streets often carry a colloquial name in `alt_name`/`old_name`/
        `loc_name`/`short_name` (Galiano vs the official "Avenida de Italia";
        Prado vs "Paseo de Martí"). Each variant is emitted with the SAME
        geometry so the street resolves — and intersects — under any of its names.
        """
        coords = []
        for node_ref in w.nodes:
            if node_ref.ref in self.state.node_cache:
                coords.append(self.state.node_cache[node_ref.ref])
        if len(coords) < 2:
            return
        try:
            line = LineString(coords)
        except Exception:
            return
        wkt = line.wkt
        centroid = line.centroid
        seen = set()
        for variant in _street_name_variants(tags):
            street_norm = normalize_street_name(variant)
            if not street_norm or street_norm in seen:
                continue
            seen.add(street_norm)
            self.state.streets.append(Lugar(
                nombre=variant,
                norm_nombre=street_norm,
                tipo="calle",
                municipio=None,  # Filled later
                provincia=None,
                barrio=None,
                lat=centroid.y,
                lon=centroid.x,
                geom_wkt=wkt,
                street_norm=street_norm,
                portal_number=None,
            ))
    
    def _process_portal_node(self, n: osmium.osm.Node, tags: Dict[str, str]):
        """Extract portal from node."""
        street = tags["addr:street"]
        street_norm = normalize_street_name(street)
        number = tags["addr:housenumber"].strip()
        
        portal = Portal(
            street_norm=street_norm,
            numero=number,
            lat=n.location.lat,
            lon=n.location.lon,
            municipio=None,
            provincia=None,
            barrio=None
        )
        self.state.portals.append(portal)
    
    def _process_portal_way(self, w: osmium.osm.Way, tags: Dict[str, str]):
        """Extract portal from way (building with address)."""
        street = tags["addr:street"]
        street_norm = normalize_street_name(street)
        number = tags["addr:housenumber"].strip()
        
        # Calculate centroid from way nodes
        coords = []
        for node_ref in w.nodes:
            if node_ref.ref in self.state.node_cache:
                coords.append(self.state.node_cache[node_ref.ref])
        
        if len(coords) >= 3:
            try:
                poly = Polygon(coords)
                centroid = poly.centroid
            except Exception:
                centroid = Point(coords[0][0], coords[0][1])
        elif len(coords) >= 1:
            centroid = Point(coords[0][0], coords[0][1])
        else:
            return
        
        portal = Portal(
            street_norm=street_norm,
            numero=number,
            lat=centroid.y,
            lon=centroid.x,
            municipio=None,
            provincia=None,
            barrio=None
        )
        self.state.portals.append(portal)
    
    def _process_poi_node(self, n: osmium.osm.Node, tags: Dict[str, str]):
        """Extract POI from node."""
        name = tags["name"]
        poi = Lugar(
            nombre=name,
            norm_nombre=normalize_text(name),
            tipo="poi",
            municipio=None,
            provincia=None,
            barrio=None,
            lat=n.location.lat,
            lon=n.location.lon,
            geom_wkt=None,
            street_norm=None,
            portal_number=None
        )
        self.state.pois.append(poi)
    
    def _process_poi_way(self, w: osmium.osm.Way, tags: Dict[str, str]):
        """Extract POI from way."""
        name = tags["name"]
        
        coords = []
        for node_ref in w.nodes:
            if node_ref.ref in self.state.node_cache:
                coords.append(self.state.node_cache[node_ref.ref])
        
        if len(coords) >= 3:
            try:
                poly = Polygon(coords)
                centroid = poly.centroid
                geom_wkt = poly.wkt
            except Exception:
                centroid = Point(coords[0][0], coords[0][1])
                geom_wkt = None
        else:
            return
        
        poi = Lugar(
            nombre=name,
            norm_nombre=normalize_text(name),
            tipo="poi",
            municipio=None,
            provincia=None,
            barrio=None,
            lat=centroid.y,
            lon=centroid.x,
            geom_wkt=geom_wkt,
            street_norm=None,
            portal_number=None
        )
        self.state.pois.append(poi)
    
    def _process_poi_area(self, a: osmium.osm.Area, tags: Dict[str, str]):
        """Extract POI from area."""
        name = tags["name"]
        
        # Approximate centroid from area rings. osmium's inner_rings()
        # requires its parent outer ring.
        coords = []
        for outer in a.outer_rings():
            for nr in outer:
                coords.append((nr.lon, nr.lat))
            for inner in a.inner_rings(outer):
                for nr in inner:
                    coords.append((nr.lon, nr.lat))

        if not coords:
            return
        
        centroid = Point(sum(c[0] for c in coords)/len(coords), 
                        sum(c[1] for c in coords)/len(coords))
        
        poi = Lugar(
            nombre=name,
            norm_nombre=normalize_text(name),
            tipo="poi",
            municipio=None,
            provincia=None,
            barrio=None,
            lat=centroid.y,
            lon=centroid.x,
            geom_wkt=None,
            street_norm=None,
            portal_number=None
        )
        self.state.pois.append(poi)


def parse_entities(pbf_path: str, bbox: Optional[Tuple] = None) -> EntityParserState:
    """
    Parse streets, portals, and POIs from OSM PBF.
    
    Args:
        pbf_path: Path to .osm.pbf file.
        bbox: Optional bounding box filter.
        
    Returns:
        EntityParserState with collected entities.
    """
    handler = EntityHandler(bbox=bbox)
    handler.apply_file(pbf_path)
    print(f"Parsed: {handler.state.node_count} nodes, {handler.state.way_count} ways")
    print(f"  Streets: {len(handler.state.streets)}")
    print(f"  Portals: {len(handler.state.portals)}")
    print(f"  POIs: {len(handler.state.pois)}")
    return handler.state


if __name__ == "__main__":
    import sys
    pbf = sys.argv[1] if len(sys.argv) > 1 else str(SETTINGS.PBF_PATH)
    state = parse_entities(pbf, bbox=SETTINGS.HABANA_BBOX)