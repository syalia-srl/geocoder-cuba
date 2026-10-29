"""
Pydantic schemas for API request/response validation.
"""
from typing import Optional, List, Dict
from pydantic import BaseModel, Field, ConfigDict



class ReverseRequest(BaseModel):
    """Request parameters for /reverse endpoint."""
    lat: float = Field(..., ge=-90, le=90, description="Latitude")
    lon: float = Field(..., ge=-180, le=180, description="Longitude")
    radius: int = Field(default=100, ge=10, le=5000, description="Search radius in meters")
    limit: int = Field(default=5, ge=1, le=20)
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "lat": 23.1345,
                "lon": -82.3876,
                "radius": 100
            }
        }
    )


class Coordinates(BaseModel):
    """Coordinate pair."""
    lat: float
    lon: float


class Bbox(BaseModel):
    """Bounding box in lat/lon."""
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float


class RegionRequest(BaseModel):
    """Request body for POST /region — ≥3 street names bounding a region."""
    streets: List[str] = Field(..., min_length=3, max_length=20,
                               description="Street names enclosing the region (≥3)")
    municipio: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"streets": ["23", "Línea", "Paseo", "G"],
                        "municipio": "Plaza de la Revolución"}
        }
    )


class RegionResponse(BaseModel):
    """Response for POST /region — the convex polygon enclosed by the streets."""
    streets: List[str]
    unresolved: List[str]
    vertices: List[List[float]]
    polygon_wkt: str
    centroid: Coordinates
    area_m2: float
    area_ha: float
    bbox: Bbox
    municipio: str
    provincia: str
    method: str
    n_crossings: int
    degenerate: bool
    ambiguous: bool


class RouteRequest(BaseModel):
    """Request body for POST /route — two address/place queries."""
    from_: str = Field(..., alias="from", min_length=1)
    to: str = Field(..., min_length=1)

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={"example": {"from": "Hotel Nacional", "to": "El Capitolio, Habana Vieja"}},
    )


class RoutePoint(BaseModel):
    query: str
    nombre: str
    lat: float
    lon: float


class RouteResponse(BaseModel):
    """Response for POST /route — fastest path over the street graph."""
    distance_m: float
    duration_s: float
    duration_min: float
    from_: RoutePoint = Field(..., alias="from")
    to: RoutePoint
    geometry: Optional[dict] = None      # GeoJSON LineString

    model_config = ConfigDict(populate_by_name=True)


class TourStop(BaseModel):
    """One stop in a multi-point tour."""
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    nombre: Optional[str] = None


class TourRequest(BaseModel):
    """Request body for POST /tour — index 0 is the fixed origin."""
    points: List[TourStop] = Field(..., min_length=2, max_length=12)
    return_to_start: bool = False


class TourResponse(BaseModel):
    """Response for POST /tour — the optimal visiting order."""
    order: List[int]
    stops: List[TourStop]
    total_s: float
    distance_m: float
    duration_min: float
    geometry: Optional[dict] = None       # GeoJSON MultiLineString
    reason: str = "ok"


class ResolveResult(BaseModel):
    """A single resolved location with precision + ± error radius (meters)."""
    nombre: str
    tipo: str
    coordinates: Coordinates
    municipio: str
    provincia: str
    barrio: Optional[str] = None
    precision: str
    radius_m: float
    resolved: dict
    geometry: Optional[dict] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "nombre": "l e/ 23 y 25", "tipo": "calle",
                "coordinates": {"lat": 23.13918, "lon": -82.38302},
                "municipio": "Plaza de la Revolución", "provincia": "La Habana",
                "barrio": None, "precision": "segment_midpoint", "radius_m": 61.0,
                "resolved": {"kind": "address", "street": "l", "number": None,
                             "entre": ["23", "25"], "esquina": None,
                             "name": None, "barrio": "Vedado", "municipio": None},
            }
        }
    )


class ResolveResponse(BaseModel):
    """Response for /resolve — a list of results (empty if nothing resolves;
    one for an address/intersection/area; up to `limit` for a POI/name)."""
    query: str
    results: List[ResolveResult] = Field(default_factory=list)
    error: Optional[str] = Field(None, description="Set when this query failed (batch only)")


class BatchResolveRequest(BaseModel):
    """Request body for POST /resolve/batch."""
    queries: List[str] = Field(..., min_length=1, max_length=100,
                               description="Up to 100 address / place / area strings")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"queries": ["Hotel Nacional", "Calle L e/ 23 y 25, Vedado", "Vedado"]}
        }
    )


class BatchResolveResponse(BaseModel):
    """Response for POST /resolve/batch — one entry per input query, in order."""
    count: int
    results: List[ResolveResponse]


class ReverseResult(BaseModel):
    """Single reverse geocoding result."""
    nombre: str
    tipo: str
    coordinates: Coordinates
    municipio: str
    provincia: str
    barrio: Optional[str] = None
    distance_meters: float
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "nombre": "Calle 23",
                "tipo": "calle",
                "coordinates": {"lat": 23.1345, "lon": -82.3876},
                "municipio": "Plaza de la Revolución",
                "provincia": "La Habana",
                "barrio": "Vedado",
                "distance_meters": 15.2
            }
        }
    )


class AddressDescription(BaseModel):
    """A point described as a Cuban address (reverse geocoding).

    `method` says which rung answered — describe_segment (street + both
    bracketing crossings), describe_street (street only), describe_place
    (nearest POI) — so a caller can tell a precise answer from a coarse one.
    """
    texto: str
    method: str
    radius_m: float
    calle: Optional[str] = None
    entre: Optional[List[str]] = None
    municipio: str = ""
    barrio: Optional[str] = None
    provincia: Optional[str] = None


class ReverseResponse(BaseModel):
    """Response for /reverse endpoint."""
    coordinates: Coordinates
    # Added alongside `results`, which keeps its exact shape — existing
    # consumers are unaffected.
    address: Optional[AddressDescription] = None
    results: List[ReverseResult]
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "coordinates": {"lat": 23.1345, "lon": -82.3876},
                "results": [
                    {
                        "nombre": "Calle 23",
                        "tipo": "calle",
                        "coordinates": {"lat": 23.1345, "lon": -82.3876},
                        "municipio": "Plaza de la Revolución",
                        "provincia": "La Habana",
                        "barrio": "Vedado",
                        "distance_meters": 15.2
                    }
                ]
            }
        }
    )


class MunicipioInfo(BaseModel):
    """Municipality information."""
    id: int
    name: str
    name_es: Optional[str] = None
    province: str
    centroid: Coordinates
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 1,
                "name": "Plaza de la Revolución",
                "name_es": "Plaza de la Revolución",
                "province": "La Habana",
                "centroid": {"lat": 23.1314, "lon": -82.3943}
            }
        }
    )


class BarrioInfo(BaseModel):
    """Neighborhood information."""
    id: int
    name: str
    name_es: Optional[str] = None
    municipality: str
    centroid: Coordinates
    place_type: str
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 1,
                "name": "Vedado",
                "name_es": "Vedado",
                "municipality": "Plaza de la Revolución",
                "centroid": {"lat": 23.1350, "lon": -82.3880},
                "place_type": "neighbourhood"
            }
        }
    )


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    database: str
    version: str
    build_timestamp: Optional[str] = None
    stats: Optional[Dict[str, int]] = None
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "healthy",
                "database": "connected",
                "version": "2.0.0",
                "build_timestamp": "2026-01-15T14:30:00Z",
                "stats": {"municipios": 15, "lugares": 45000}
            }
        }
    )


class StatsResponse(BaseModel):
    """Database statistics."""
    municipios: int
    provincias: int
    barrios: int
    lugares: int
    por_tipo: Dict[str, int]
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "municipios": 15,
                "provincias": 1,
                "barrios": 120,
                "lugares": 45000,
                "por_tipo": {"calle": 25000, "portal": 15000, "poi": 5000}
            }
        }
    )


class ErrorResponse(BaseModel):
    """Error response."""
    error: str
    detail: Optional[str] = None
    code: int
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": "Not Found",
                "detail": "No results found for query",
                "code": 404
            }
        }
    )