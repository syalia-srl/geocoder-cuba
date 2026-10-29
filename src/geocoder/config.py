"""Configuración centralizada del geocodificador."""
import os
from pathlib import Path
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # Rutas base
    BASE_DIR: Path = Path(__file__).parent.parent.parent
    # Deriving DATA_DIR from __file__ only works when running out of a checkout.
    # Installed (pip into an image, uv into a relocatable venv) it resolves to
    # something meaningless like /usr/local/lib/python3.12/data, and the /tiles
    # mount 404s with no error. GEOCODER_DATA_DIR relocates the payload.
    DATA_DIR: Path = Path(os.environ.get("GEOCODER_DATA_DIR") or str(BASE_DIR / "data"))
    DB_PATH: Path = Path(os.environ.get("GEOCODER_DB_PATH") or str(Path(__file__).parent.parent.parent / "data" / "cuba_geo.db"))
    PBF_PATH: Path = DATA_DIR / "cuba-latest.osm.pbf"
    PBF_URL: str = "https://download.geofabrik.de/central-america/cuba-latest.osm.pbf"

    # Bounding box La Habana (min_lon, min_lat, max_lon, max_lat)
    HABANA_BBOX = (-82.55, 22.90, -82.20, 23.20)

    # OSM admin levels for Cuba: país=2, provincia=4, municipio=6.
    # (Central Havana's 15 municipios are admin_level=6, not 8 — the earlier
    # 6/8 assumption stored them as provincias and missed them entirely.)
    ADMIN_LEVEL_PROVINCE = "4"
    ADMIN_LEVEL_MUNICIPIO = "6"

    # Provincia objetivo
    TARGET_PROVINCE = "La Habana"

    # Municipios La Habana (normalizados)
    HABANA_MUNICIPIOS = {
        "plaza de la revolucion", "plaza", "cerro", "centro habana", "habana vieja",
        "habana del este", "marianao", "regla", "guanabacoa", "san miguel del padron",
        "la lisa", "boyeros", "arroyo naranjo", "cotorro", "diez de octubre"
    }

    # Búsqueda
    FTS_LIMIT = 100
    FUZZY_LIMIT = 20
    FINAL_LIMIT = 5
    FUZZY_THRESHOLD = 60

    # Boosts ranking
    BOOST_PORTAL = 15
    BOOST_CALLE = 10
    BOOST_POI = 0
    BOOST_MUNICIPIO_EXACTO = 20
    BOOST_BARRIO_EXACTO = 10

    # Geometría
    INTERSECTION_TOLERANCE = 0.0001  # ~11m
    PIP_BBOX_PADDING = 0.001

    # Routing — highway subset that carries cars, and speeds (km/h) per type.
    DRIVABLE_HIGHWAYS = {
        "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
        "residential", "service", "living_street", "road", "track",
        "motorway_link", "trunk_link", "primary_link", "secondary_link",
        "tertiary_link",
    }
    SPEED_KMH = {
        "motorway": 90, "trunk": 70, "primary": 50, "secondary": 40,
        "tertiary": 40, "unclassified": 30, "residential": 30, "service": 20,
        "living_street": 15, "road": 30, "track": 15, "motorway_link": 50,
        "trunk_link": 40, "primary_link": 40, "secondary_link": 30,
        "tertiary_link": 30,
    }
    DEFAULT_SPEED_KMH = 25          # unknown/blank highway type
    MAX_SPEED_KMH = 90              # for the admissible A* heuristic
    SNAP_MAX_M = 300.0             # endpoint farther than this from any node ⇒ off_network

    # Reverse geocoding: a click farther than this from any street cannot be
    # described as an address and degrades to the nearest place. Hand-picked,
    # not measured — generous for dense Havana, possibly tight in the periphery
    # where blocks are long and OSM coverage is thin. Tune once clicking works.
    DESCRIBE_MAX_STREET_M = 150.0

    # Prefijos calles para normalización
    STREET_PREFIXES = (
        "calle ", "c ", "av ", "ave ", "avenida ", "carrer ", "camino ",
        "paseo ", "plaza ", "carretera ", "ctra ", "autopista ", "aut ",
        "bulevar ", "blvr ", "pasaje ", "psj ", "callejon ", "cln ",
        "ronda ", "rda ", "glorieta ", "gta ", "travesia ", "trav ",
        "urbanizacion ", "urb ", "reparto ", "rep ", "barrio "
    )

    def ensure_dirs(self):
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        (self.DATA_DIR / "versions").mkdir(exist_ok=True)


SETTINGS = Settings()
SETTINGS.ensure_dirs()