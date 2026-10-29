"""Typo-tolerant resolution, run against a full-Cuba build.

Real users misspell ("plaza de la revolucon", "bedado", "hotel nacinal"). The
resolver must still land the right place: municipios/barrios via fuzzy name
matching, POIs via a typo-tolerant FTS fallback. Skipped when the DB isn't built.
"""
from pathlib import Path

import pytest

from geocoder.store import GeoStore
from geocoder.config import SETTINGS
from geocoder.search.resolver import Resolver

_ROOT = Path(__file__).parent.parent
_CANDIDATES = [_ROOT / "data" / "cuba_full.db", SETTINGS.DB_PATH]

# (query, expected precision, substring that must appear in the resolved name)
CASES = [
    ("Plaza de la Revolución", "municipio", "Plaza de la Revolución"),
    ("plaza de la revolucion", "municipio", "Plaza de la Revolución"),
    ("plaza de la revolucon", "municipio", "Plaza de la Revolución"),
    ("plaza de la revolusion", "municipio", "Plaza de la Revolución"),
    ("santiago de kuba", "municipio", "Santiago de Cuba"),
    ("camaguey", "municipio", "Camagüey"),
    ("Vedado", "barrio", "Vedado"),
    ("bedado", "barrio", "Vedado"),
    ("vedaod", "barrio", "Vedado"),
    ("Coppelia", "poi", "Coppelia"),
    ("coppellia", "poi", "Coppelia"),
    ("Hotel Nacional", "poi", "Hotel Nacional"),
    ("Hotel Nacinal", "poi", "Hotel Nacional"),
    ("hotel nacionl", "poi", "Hotel Nacional"),
]


@pytest.fixture(scope="module")
def resolver():
    db = next((p for p in _CANDIDATES if p.exists()), None)
    if db is None:
        pytest.skip("full-Cuba DB not built (geocoder build --full-cuba)")
    return Resolver(GeoStore(db))


@pytest.mark.parametrize("query,precision,needle", CASES, ids=[c[0] for c in CASES])
def test_fuzzy(resolver, query, precision, needle):
    r = resolver.resolve(query)
    assert r is not None, f"{query!r} did not resolve"
    assert r.method == precision, f"{query!r}: {r.method!r} != {precision!r}"
    assert needle.lower() in r.nombre.lower(), (
        f"{query!r} resolved to {r.nombre!r}, expected to contain {needle!r}"
    )


def _haversine(lat1, lon1, lat2, lon2):
    import math
    R = 6_371_000
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


@pytest.mark.parametrize("query,lat,lon", [
    ("belaskoain y reina", 23.1320, -82.3697),   # typo: belascoain
    ("galliano y san rafael", 23.1365, -82.3633),  # typo: galiano
], ids=["belaskoain y reina", "galliano y san rafael"])
def test_fuzzy_intersection(resolver, query, lat, lon):
    r = resolver.resolve(query)
    assert r is not None and r.method == "esquina", f"{query!r} → {r.method if r else None}"
    assert _haversine(r.lat, r.lon, lat, lon) <= 300
