"""Typed data models for the geocoder."""
from dataclasses import dataclass, field
from typing import Optional, List, Literal
from shapely.geometry import Point, Polygon


@dataclass(slots=True)
class Municipio:
    """Municipality with real polygon geometry."""
    id: Optional[int] = None
    name: str = ""
    name_es: Optional[str] = None
    province: Optional[str] = None
    polygon: Optional[Polygon] = None
    centroid: Optional[Point] = None
    admin_level: str = "8"
    source_osm_id: Optional[str] = None
    # Bounding box for fast pre-filtering
    bbox_minx: Optional[float] = None
    bbox_miny: Optional[float] = None
    bbox_maxx: Optional[float] = None
    bbox_maxy: Optional[float] = None

    @property
    def centroid_lat(self) -> Optional[float]:
        return self.centroid.y if self.centroid else None

    @property
    def centroid_lon(self) -> Optional[float]:
        return self.centroid.x if self.centroid else None

    @property
    def bbox(self) -> Optional[tuple]:
        """Return (minx, miny, maxx, maxy) if polygon exists."""
        if self.polygon:
            return self.polygon.bounds
        if all(v is not None for v in [self.bbox_minx, self.bbox_miny, self.bbox_maxx, self.bbox_maxy]):
            return (self.bbox_minx, self.bbox_miny, self.bbox_maxx, self.bbox_maxy)
        return None


@dataclass(slots=True)
class Provincia:
    """Province with polygon geometry."""
    id: Optional[int] = None
    name: str = ""
    polygon: Optional[Polygon] = None
    centroid: Optional[Point] = None
    admin_level: str = "6"
    source_osm_id: Optional[str] = None


@dataclass(slots=True)
class Barrio:
    """Neighborhood/quarter/suburb within a municipality."""
    id: Optional[int] = None
    name: str = ""
    name_es: Optional[str] = None
    municipio_id: Optional[int] = None
    polygon: Optional[Polygon] = None
    centroid: Optional[Point] = None
    place_type: Optional[str] = None
    source_osm_id: Optional[str] = None


@dataclass(slots=True)
class Lugar:
    """Unified entry en lugares_fts (calles, portales, POIs)."""
    id: Optional[int] = None
    nombre: str = ""
    norm_nombre: str = ""
    tipo: Literal["calle", "portal", "poi", "interseccion"] = "calle"
    municipio: Optional[str] = None
    municipio_norm: Optional[str] = None
    provincia: Optional[str] = None
    barrio: Optional[str] = None
    lat: float = 0.0
    lon: float = 0.0
    geom_wkt: Optional[str] = None
    street_norm: Optional[str] = None
    portal_number: Optional[str] = None
    tags_json: Optional[str] = None


@dataclass(slots=True)
class Portal:
    """Portal entry for exact O(1) lookup."""
    id: Optional[int] = None
    street_norm: str = ""
    numero: str = ""
    lat: float = 0.0
    lon: float = 0.0
    municipio: Optional[str] = None
    municipio_norm: Optional[str] = None
    provincia: Optional[str] = None
    barrio: Optional[str] = None
    tags_json: Optional[str] = None


@dataclass(slots=True)
class SearchQuery:
    """Parsed search query."""
    raw: str = ""
    terms: List[str] = field(default_factory=list)
    municipio: Optional[str] = None
    provincia: Optional[str] = None
    barrio: Optional[str] = None
    is_intersection: bool = False
    intersection_streets: tuple[str, str] = ("", "")
    is_portal: bool = False
    portal_street: str = ""
    portal_number: str = ""


@dataclass(slots=True)
class GeoResult:
    """Geocoding result with metadata."""
    nombre: str
    tipo: str
    lat: float
    lon: float
    municipio: str
    provincia: str
    barrio: Optional[str] = None
    score: float = 0.0
    match_type: str = "fuzzy"
    geometry_wkt: Optional[str] = None
    precision: Optional[str] = None
    radius_m: Optional[float] = None
    resolved: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "nombre": self.nombre,
            "tipo": self.tipo,
            "coordinates": {"lat": round(self.lat, 6), "lon": round(self.lon, 6)},
            "municipio": self.municipio,
            "provincia": self.provincia,
            "barrio": self.barrio,
            "score": round(self.score, 2),
            "match_type": self.match_type,
            "geometry": self.geometry_wkt,
            "precision": self.precision,
            "radius_m": round(self.radius_m, 1) if self.radius_m is not None else None,
            "resolved": self.resolved,
        }


@dataclass(slots=True)
class Region:
    """A polygon enclosed by a set of streets (region_hull rung)."""
    streets: List[str]
    unresolved: List[str]
    vertices: List[List[float]]          # ordered hull ring, each [lat, lon]
    polygon_wkt: str
    centroid_lat: float
    centroid_lon: float
    area_m2: float
    area_ha: float
    bbox: tuple                          # (min_lat, min_lon, max_lat, max_lon)
    municipio: str = ""
    provincia: str = ""
    method: str = "region_hull"
    n_crossings: int = 0
    degenerate: bool = False
    ambiguous: bool = False

    def to_dict(self) -> dict:
        return {
            "streets": self.streets,
            "unresolved": self.unresolved,
            "vertices": [[round(lat, 6), round(lon, 6)] for lat, lon in self.vertices],
            "polygon_wkt": self.polygon_wkt,
            "centroid": {"lat": round(self.centroid_lat, 6),
                         "lon": round(self.centroid_lon, 6)},
            "area_m2": round(self.area_m2, 1),
            "area_ha": round(self.area_ha, 2),
            "bbox": {"min_lat": round(self.bbox[0], 6), "min_lon": round(self.bbox[1], 6),
                     "max_lat": round(self.bbox[2], 6), "max_lon": round(self.bbox[3], 6)},
            "municipio": self.municipio,
            "provincia": self.provincia,
            "method": self.method,
            "n_crossings": self.n_crossings,
            "degenerate": self.degenerate,
            "ambiguous": self.ambiguous,
        }


@dataclass(slots=True)
class Route:
    """A fastest-path route between two points over the street graph."""
    distance_m: float
    duration_s: float
    polyline_wkt: str
    snapped_from: tuple            # (lat, lon) graph node the origin snapped to
    snapped_to: tuple              # (lat, lon) graph node the dest snapped to
    reason: str = "ok"             # ok | disconnected | off_network | empty_graph

    def to_dict(self) -> dict:
        return {
            "distance_m": round(self.distance_m, 1),
            "duration_s": round(self.duration_s, 1),
            "duration_min": round(self.duration_s / 60, 1),
            "polyline_wkt": self.polyline_wkt,
            "snapped_from": {"lat": round(self.snapped_from[0], 6),
                             "lon": round(self.snapped_from[1], 6)},
            "snapped_to": {"lat": round(self.snapped_to[0], 6),
                           "lon": round(self.snapped_to[1], 6)},
            "reason": self.reason,
        }


@dataclass
class Tour:
    """An optimal visiting order over several stops on the street graph."""
    order: list              # indices into the requested points, starting at 0
    stops: list              # the requested (lat, lon) points, in visiting order
    total_s: float
    total_m: float
    polyline_wkt: str        # all legs concatenated, in order
    reason: str = "ok"       # ok | too_many | too_few | unreachable | empty_graph
