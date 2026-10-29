"""Hard Cuban-address golden corpus, run against the real La Habana DB.

Skipped when the DB isn't built (`geocoder build --bbox habana`). Each case
asserts the resolution method (the ladder contract) and, when a ground-truth
point is known, that the resolved point is within `max_radius_m` of it.
"""
import json
import math
from pathlib import Path

import pytest

from geocoder.store import GeoStore
from geocoder.config import SETTINGS
from geocoder.search.resolver import Resolver

CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "cuban_addresses.json").read_text()
)


@pytest.fixture(scope="module")
def resolver():
    if not SETTINGS.DB_PATH.exists():
        pytest.skip("real Havana DB not built (geocoder build --bbox habana)")
    return Resolver(GeoStore(SETTINGS.DB_PATH))


def _haversine(a, b, c, d):
    R = 6_371_000
    p1, p2 = math.radians(a), math.radians(c)
    dp = math.radians(c - a)
    dl = math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


@pytest.mark.parametrize("case", CORPUS, ids=[c["query"] for c in CORPUS])
def test_corpus_case(resolver, case):
    exp = case["expect"]
    r = resolver.resolve(case["query"])
    assert r is not None, f"no result for {case['query']!r}"
    assert r.method == exp["method"], (
        f"{case['query']!r}: method {r.method} != {exp['method']}"
    )
    if "municipio" in exp:
        assert r.municipio == exp["municipio"], (
            f"{case['query']!r}: municipio {r.municipio!r} != {exp['municipio']!r}"
        )
    if "barrio" in exp:
        assert r.barrio == exp["barrio"], (
            f"{case['query']!r}: barrio {r.barrio!r} != {exp['barrio']!r}"
        )
    if "near" in exp:
        d = _haversine(r.lat, r.lon, exp["near"][0], exp["near"][1])
        assert d <= exp["max_radius_m"], (
            f"{case['query']!r}: {d:.0f}m from ground truth > {exp['max_radius_m']}m"
        )
