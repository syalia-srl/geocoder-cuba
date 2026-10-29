"""Gazetteer — a place index over provinces, municipios and barrios.

Gives the parser/resolver a real vocabulary of Cuban areas (so "Vedado",
"Cienfuegos", "diez de octubre" are recognized, not guessed) and makes provinces
resolvable. Built once from the store's admin data; hierarchy (barrio→municipio→
province) is attached by point-in-polygon.
"""
import json
import math
from pathlib import Path

from rapidfuzz import process, fuzz
from shapely import wkt as _wkt
from shapely.geometry import Point
from shapely.strtree import STRtree

from geocoder.utils.normalize import normalize_text

_M_PER_DEG = 111_000.0
_DEFAULT_PROV_RADIUS_M = 40_000.0
_DEFAULT_MUNI_RADIUS_M = 2_000.0
_DEFAULT_BARRIO_RADIUS_M = 800.0
_ALIASES_PATH = Path(__file__).parent / "aliases.json"


def _load_aliases() -> dict:
    """alias_norm → {canonical, level}. Curated colloquial/historical names."""
    try:
        raw = json.loads(_ALIASES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, dict) and "canonical" in v}


def _bbox_radius_m(minx, miny, maxx, maxy) -> float:
    dx = (maxx - minx) * _M_PER_DEG * math.cos(math.radians((miny + maxy) / 2))
    dy = (maxy - miny) * _M_PER_DEG
    return math.hypot(dx, dy) / 2


def _radius_of(rec: dict, wkt_str, default: float) -> float:
    if rec.get("bbox_minx") is not None:
        return _bbox_radius_m(rec["bbox_minx"], rec["bbox_miny"], rec["bbox_maxx"], rec["bbox_maxy"])
    if wkt_str:
        try:
            return _bbox_radius_m(*_wkt.loads(wkt_str).bounds)
        except Exception:
            pass
    return default


class Gazetteer:
    """norm_name → list of area records
    {level, canonical, lat, lon, radius_m, parent_muni, parent_prov}."""

    def __init__(self, index: dict):
        self._index = index
        self._keys = list(index.keys())

    @classmethod
    def from_store(cls, store) -> "Gazetteer":
        idx: dict = {}

        def add(norm, rec):
            if norm:
                idx.setdefault(norm, []).append(rec)

        # provinces
        for p in store.provincias.values():
            if p.get("centroid_lat") is None:
                continue
            add(normalize_text(p["name"]), {
                "level": "provincia", "canonical": p["name"],
                "lat": p["centroid_lat"], "lon": p["centroid_lon"],
                "radius_m": _radius_of(p, p.get("polygon_wkt"), _DEFAULT_PROV_RADIUS_M),
                "parent_muni": None, "parent_prov": None,
                "polygon_wkt": p.get("polygon_wkt"),
            })

        # municipios — parent province from the (already correct) province field
        munis = store.all_municipios()
        for m in munis:
            if m.get("centroid_lat") is None:
                continue
            add(m.get("municipio_norm") or normalize_text(m["name"]), {
                "level": "municipio", "canonical": m["name"],
                "lat": m["centroid_lat"], "lon": m["centroid_lon"],
                "radius_m": _radius_of(m, m.get("polygon_wkt"), _DEFAULT_MUNI_RADIUS_M),
                "parent_muni": None, "parent_prov": m.get("province"),
                "polygon_wkt": m.get("polygon_wkt"),
            })

        # barrios — parent municipio by PIP over municipio polygons
        muni_polys, muni_recs = [], []
        for m in munis:
            if m.get("polygon_wkt"):
                try:
                    g = _wkt.loads(m["polygon_wkt"])
                    if g.is_valid and not g.is_empty:
                        muni_polys.append(g)
                        muni_recs.append(m)
                except Exception:
                    pass
        muni_tree = STRtree(muni_polys) if muni_polys else None
        for b in store.all_barrios():
            if b.get("centroid_lat") is None:
                continue
            parent_muni = parent_prov = None
            if muni_tree is not None:
                pt = Point(b["centroid_lon"], b["centroid_lat"])
                hit = muni_tree.query(pt, predicate="within")
                if len(hit):
                    pm = muni_recs[int(hit[0])]
                    parent_muni, parent_prov = pm["name"], pm.get("province")
            add(b.get("barrio_norm") or normalize_text(b["name"]), {
                "level": "barrio", "canonical": b["name"],
                "lat": b["centroid_lat"], "lon": b["centroid_lon"],
                "radius_m": _radius_of(b, b.get("polygon_wkt"), _DEFAULT_BARRIO_RADIUS_M),
                "parent_muni": parent_muni, "parent_prov": parent_prov,
                "polygon_wkt": b.get("polygon_wkt"),
            })

        # curated aliases → point at the canonical record(s) already indexed
        for alias, spec in _load_aliases().items():
            canon = normalize_text(spec["canonical"])
            level = spec.get("level")
            recs = [r for r in idx.get(canon, []) if not level or r["level"] == level]
            if recs and alias not in idx:
                idx[alias] = recs

        return cls(idx)

    def lookup(self, norm: str) -> list:
        return self._index.get(norm, [])

    def match_longest(self, text: str):
        """Longest suffix of `text` that is a known place → (records, span).
        Cuban context trails the name ("Calle 23, Vedado"), so a suffix match
        catches both the trailing context and a whole-query area."""
        toks = normalize_text(text).split()
        for start in range(len(toks)):
            cand = " ".join(toks[start:])
            recs = self._index.get(cand)
            if recs:
                return recs, cand
        return None

    def fuzzy(self, norm: str, min_score: float = 85.0):
        """Best place by full-string similarity (typo-tolerant), or None."""
        if not norm:
            return None
        res = process.extractOne(norm, self._keys, scorer=fuzz.ratio, score_cutoff=min_score)
        return self._index[res[0]] if res else None
