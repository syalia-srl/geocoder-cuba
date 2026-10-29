"""Intersection / `entre` golden set, run against a full-Cuba build.

Cuban addresses are overwhelmingly "calle y calle" (a corner) or
"calle e/ calle y calle" (between two cross-streets). This asserts those
resolve to the right point across separator variations (`y`, `esq`, `esq. a`,
`/`, `e/`). Anchored on real crossings (23 y L is Coppelia's corner in Vedado).

Skipped when the full-Cuba DB isn't built. It looks for `data/cuba_full.db`
first (the deployed build), then falls back to `SETTINGS.DB_PATH`.
"""
import json
import math
from pathlib import Path

import pytest

from geocoder.store import GeoStore
from geocoder.config import SETTINGS
from geocoder.search.resolver import Resolver

FIX = json.loads((Path(__file__).parent / "fixtures" / "intersections.json").read_text())
_ROOT = Path(__file__).parent.parent
_CANDIDATES = [_ROOT / "data" / "cuba_full.db", SETTINGS.DB_PATH]


def _haversine(lat1, lon1, lat2, lon2):
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


@pytest.fixture(scope="module")
def resolver():
    db = next((p for p in _CANDIDATES if p.exists()), None)
    if db is None:
        pytest.skip("full-Cuba DB not built (geocoder build --full-cuba)")
    return Resolver(GeoStore(db))


@pytest.mark.parametrize("case", FIX["cases"], ids=[c["query"] for c in FIX["cases"]])
def test_intersection(resolver, case):
    tol = FIX["tolerance_m"]
    r = resolver.resolve(case["query"])
    assert r is not None, f"{case['query']!r} did not resolve"
    assert r.method in case["precision"], (
        f"{case['query']!r}: precision {r.method!r} not in {case['precision']}"
    )
    d = _haversine(r.lat, r.lon, case["lat"], case["lon"])
    assert d <= tol, f"{case['query']!r}: {d:.0f} m from expected (tol {tol} m)"
