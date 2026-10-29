"""Resolve a ParsedLocation to a point + method + ± radius (meters)."""
import math
from dataclasses import dataclass, field
from typing import Optional
from shapely import wkt
from shapely.geometry import Point, MultiPoint
from shapely.strtree import STRtree
from shapely.ops import nearest_points
from geocoder.models import Region
from geocoder.search.address import parse_location
from geocoder.search.fuzzy import FuzzyRanker
from geocoder.utils.normalize import (
    normalize_text, normalize_for_fts, normalize_for_fts_fuzzy, normalize_street_name,
)
from geocoder.config import SETTINGS

_M_PER_DEG = 111_000.0
_DEFAULT_MUNICIPIO_RADIUS_M = 2000.0  # municipio with no bbox
_DEFAULT_BARRIO_RADIUS_M = 800.0     # barrio with no bbox
# Max separation (m) between the two cross-street intersections of an `entre`
# stretch — beyond this the two crossings are on different parts of the country.
_ENTRE_MAX_SPAN_M = 3000.0

# region_hull rung
_REGION_AMBIGUOUS_DIAG_M = 6000.0     # hull bbox diagonal above this ⇒ likely missing municipio
_REGION_DEGENERATE_AREA_M2 = 1.0      # area below this ⇒ near-collinear crossings


@dataclass
class Resolved:
    nombre: str
    tipo: str
    lat: float
    lon: float
    method: str
    radius_m: float
    municipio: str = ""
    provincia: str = ""
    barrio: Optional[str] = None
    score: float = 0.0
    geometry_wkt: Optional[str] = None
    resolved: dict = field(default_factory=dict)


def _bbox_radius_m(minx, miny, maxx, maxy) -> float:
    dx = (maxx - minx) * _M_PER_DEG * math.cos(math.radians((miny + maxy) / 2))
    dy = (maxy - miny) * _M_PER_DEG
    return math.hypot(dx, dy) / 2


def _dist_m(p1, p2) -> float:
    """Approx distance in meters between two shapely points (Havana-lat)."""
    dx = (p2.x - p1.x) * _M_PER_DEG * math.cos(math.radians((p1.y + p2.y) / 2))
    dy = (p2.y - p1.y) * _M_PER_DEG
    return math.hypot(dx, dy)


def _rec_dist_m(r1, r2) -> float:
    """Approx distance in meters between two crossing records ({lat, lon})."""
    dx = (r2["lon"] - r1["lon"]) * _M_PER_DEG * math.cos(math.radians((r1["lat"] + r2["lat"]) / 2))
    dy = (r2["lat"] - r1["lat"]) * _M_PER_DEG
    return math.hypot(dx, dy)


def _polygon_area_m2(poly, lat0: float) -> float:
    """Area of a lon/lat polygon in m², via a local equirectangular projection
    at latitude `lat0` (fine for city-scale regions)."""
    kx = _M_PER_DEG * math.cos(math.radians(lat0))
    ky = _M_PER_DEG
    xs, ys = poly.exterior.coords.xy
    # shoelace on projected metric coords
    area = 0.0
    n = len(xs)
    for i in range(n - 1):
        x1, y1 = xs[i] * kx, ys[i] * ky
        x2, y2 = xs[i + 1] * kx, ys[i + 1] * ky
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


class Resolver:
    def __init__(self, store):
        self.store = store
        self.fuzzy = FuzzyRanker(
            fuzzy_threshold=SETTINGS.FUZZY_THRESHOLD,
            fuzzy_limit=SETTINGS.FUZZY_LIMIT,
        )
        # municipio name → its province, so results report the real province
        # (Cuba-wide) instead of the Havana-era default.
        self._muni_prov = {
            m["name"]: (m.get("province") or SETTINGS.TARGET_PROVINCE)
            for m in store.all_municipios()
        }
        self.gaz = store.gazetteer()
        self._canon_cache: dict = {}

    def _province_for(self, muni, hint=None):
        if hint:
            return hint
        if muni and muni in self._muni_prov:
            return self._muni_prov[muni]
        return SETTINGS.TARGET_PROVINCE

    def resolve(self, query: str) -> Optional[Resolved]:
        """Single best result (or None) — the head of resolve_many."""
        rs = self.resolve_many(query, limit=1)
        return rs[0] if rs else None

    def resolve_many(self, query: str, limit: int = 5) -> list:
        """Ranked results. Deterministic rungs (portal/esquina/entre/area) yield
        one; the POI rung yields up to `limit`. Always a list (possibly empty)."""
        p = parse_location(query, gazetteer=self.gaz)

        # Municipio scope: parser may put a municipio OR barrio name into p.barrio,
        # so try both hints to resolve the scope.
        muni = None
        for hint in (p.municipio, p.barrio):
            if hint:
                muni = self.store.resolve_municipio(hint)
                if muni:
                    break
        muni_norm = normalize_text(muni) if muni else None

        # Province scope — from the municipio when one resolved, else from a
        # province the query named outright ("El Capitolio, La Habana"). Without
        # this the province is parsed and then dropped, and a Cuba-wide index
        # answers from whichever homonym scores best: the query said La Habana
        # and got Pinar del Río, at POI precision.
        prov = self._province_scope(p, muni)

        # A barrio context that isn't a municipio still gives a reference point to
        # pick the right street fragment (streets like "L"/"23" repeat citywide).
        ref = None
        if not muni:
            for hint in (p.barrio, p.municipio):
                if hint:
                    b = self.store.resolve_barrio(hint)
                    if b and b.get("centroid_lat") is not None:
                        ref = (b["centroid_lat"], b["centroid_lon"])
                        break
            if ref is None and prov:
                ref = self._province_centroid(prov)

        if p.kind == "address":
            r = self._resolve_address(p, muni, muni_norm, ref, prov)
            if r:
                return [r]
        # area: exact municipio/barrio name (or address that fell through)
        r = self._resolve_area(p, muni)
        if r:
            return [r]
        if p.kind == "address":
            # A failed address is NOT retried as a place-name search — see
            # _resolve_context. Coarse-but-honest, or nothing.
            r = self._resolve_context(p, muni)
            return [r] if r else []
        # place / POI — the only multi-result rung
        return self._resolve_place(p, muni, muni_norm, ref, limit=limit, prov=prov)

    def _province_scope(self, p, muni) -> Optional[str]:
        """The canonical province this query is confined to, or None."""
        if muni:
            return self._muni_prov.get(muni)
        if not p.provincia:
            return None
        for rec in self.gaz.lookup(normalize_text(p.provincia)):
            if rec["level"] == "provincia":
                return rec["canonical"]
        return None

    def _province_centroid(self, prov) -> Optional[tuple]:
        for rec in self.gaz.lookup(normalize_text(prov)):
            if rec["level"] == "provincia" and rec.get("lat") is not None:
                return (rec["lat"], rec["lon"])
        return None

    def _in_province(self, muni, prov) -> bool:
        return self._muni_prov.get(muni) == prov

    # ---- rungs ----

    _STREET_PREFIXES = ("calle ", "avenida ", "ave ", "av ", "callejon ", "pasaje ")

    def _is_habana(self, muni) -> bool:
        return self._muni_prov.get(muni) == SETTINGS.TARGET_PROVINCE

    def _canon_street(self, name):
        """The street_norm the store actually holds for a parsed street name.

        Queries name streets the way people say them; OSM names them the way the
        sign reads. The difference is usually an article or qualifier the speaker
        drops — "ave las banderas" for OSM's "de las banderas". An unresolved
        name loses *every* address rung (portal, esquina, entre, street) and the
        query falls through to the POI rung, which answers with an unrelated
        place, so this miss is expensive.

        Only fires on a miss, and only accepts a candidate that actually has
        geometry — a name the store knows verbatim is never second-guessed. The
        article alias is tried before the fuzzy match because it is exact:
        `fuzz.ratio` is length-sensitive, so it recovers "de las banderas" from
        "las banderas" but not "de acosta" from "acosta".
        """
        if not name:
            return name
        if name not in self._canon_cache:
            out = name
            if not self.store.street_geoms(name):
                for c in (self.store.street_alias(name), self.store.fuzzy_street(name)):
                    if c and c != name and self.store.street_geoms(c):
                        out = c
                        break
            self._canon_cache[name] = out
        return self._canon_cache[name]

    def _street_frags(self, name):
        """All merged geometries of a street as (municipio, geom) — one per
        municipio the street name appears in."""
        out = []
        if not name:
            return out
        name = self._canon_street(name)
        for m, w in self.store.street_geoms(name):
            try:
                g = wkt.loads(w)
                if not g.is_empty:
                    out.append((m, g))
            except Exception:
                pass
        return out

    def _street_geom(self, name, muni, ref, prov=None):
        """One geometry for a street: the municipio-scoped fragment if a
        municipio is known, else the fragment nearest the reference point, else
        (no context) the longest fragment in La Habana — numbered/lettered
        streets repeat citywide, so this picks the Havana one by default."""
        if not name:
            return None
        name = self._canon_street(name)
        if muni:
            g = self.store.get_calle_geom(name, muni)
            if g:
                try:
                    return wkt.loads(g)
                except Exception:
                    return None
            # No fragment under that municipio. Widen to the rest of its province
            # below (a border street is often indexed under the neighbour) —
            # never past it.
        frags = self._street_frags(name)
        if prov:
            # same rule as _best_intersection: never leave a named province
            frags = [mg for mg in frags if self._in_province(mg[0], prov)]
        if not frags:
            return None
        if ref is not None:
            rp = Point(ref[1], ref[0])
            return min(frags, key=lambda mg: mg[1].distance(rp))[1]
        habana = [g for m, g in frags if self._is_habana(m)]
        pool = habana or [g for _, g in frags]
        return max(pool, key=lambda g: g.length)

    def _all_intersections_geom(self, name_a, name_b):
        """Every place street A crosses street B, as canonical records
        {lat, lon, muni}. Tests each fragment of A against a spatial index of B's
        fragments — O((|A|+|B|)·log) — so it finds the pair that actually meets."""
        A = self._street_frags(name_a)
        B = self._street_frags(name_b)
        if not A or not B:
            return []
        tol = SETTINGS.INTERSECTION_TOLERANCE
        b_buf = [g.buffer(tol) for _, g in B]
        tree = STRtree(b_buf)
        out = []
        for ma, ga in A:
            ga_buf = ga.buffer(tol)
            for j in tree.query(ga_buf, predicate="intersects"):
                j = int(j)
                inter = ga_buf.intersection(b_buf[j])
                if inter.is_empty:
                    continue
                c = inter.centroid
                if not c.is_empty:
                    out.append({"lat": c.y, "lon": c.x, "muni": ma})
        return out

    def _nearest_approach(self, name_a, name_b, max_m):
        """Fallback crossings for streets that don't quite meet in OSM (a small
        gap at the corner): the midpoint of the closest approach between a
        fragment of A and a fragment of B, when within `max_m`."""
        A = self._street_frags(name_a)
        B = self._street_frags(name_b)
        if not A or not B:
            return []
        tree = STRtree([g for _, g in B])
        max_deg = max_m / _M_PER_DEG
        out = []
        for ma, ga in A:
            idx = tree.query_nearest(ga, max_distance=max_deg, all_matches=False)
            for j in idx:
                j = int(j)
                pa, pb = nearest_points(ga, B[j][1])
                if _dist_m(pa, pb) <= max_m:
                    out.append({"lat": (pa.y + pb.y) / 2, "lon": (pa.x + pb.x) / 2, "muni": ma})
        return out

    def _crossings_geom(self, name_a, name_b):
        """Geometry-derived crossings — strict intersections if any, else bounded
        near-approaches (≤120 m) so a small OSM gap at the corner still resolves."""
        strict = self._all_intersections_geom(name_a, name_b)
        if strict:
            return strict
        return self._nearest_approach(name_a, name_b, 120.0)

    def _crossings(self, name_a, name_b):
        """All places A crosses B: the precomputed table first (O(1) lookup),
        geometry fallback for pairs not in the table. Both names are
        canonicalized first, so a mistyped or article-dropping name ('belaskoain'
        → 'belascoain', 'las banderas' → 'de las banderas') reaches the table
        *and* the geometry, not just the table."""
        name_a = self._canon_street(name_a)
        name_b = self._canon_street(name_b)
        recs = self.store.crossings(name_a, name_b)
        if recs:
            return recs
        return self._crossings_geom(name_a, name_b)

    def _best_intersection(self, cands, muni, ref, prov=None):
        """Pick the crossing that fits the context: the given municipio, or
        nearest the reference point, or (default) a La Habana crossing.

        A named area is binding at province level and only a preference at
        municipio level. OSM routinely files a corner on a municipio border
        under the neighbour, so insisting on the municipio would lose real
        corners; but a corner in another province is never the one that was
        asked for, and returning it reports a 700 km error as ±150 m."""
        if not cands:
            return None
        if prov:
            in_prov = [c for c in cands if self._in_province(c["muni"], prov)]
            if not in_prov:
                return None
            cands = in_prov
        if muni:
            scoped = [c for c in cands if c["muni"] == muni]
            if scoped:
                cands = scoped
        if ref is not None:
            return min(cands, key=lambda c: (c["lat"] - ref[0]) ** 2 + (c["lon"] - ref[1]) ** 2)
        habana = [c for c in cands if self._is_habana(c["muni"])]
        pool = habana or cands
        # deterministic pick among equivalent candidates
        return min(pool, key=lambda c: (c["lat"], c["lon"]))

    def _region_crossings(self, norm_names, muni):
        """Every pairwise crossing among the given streets, as {lat, lon, muni}
        records. Scoped to `muni` when given; otherwise prefer La Habana crossings
        so the corners stay in one coherent region."""
        pts = []
        for i in range(len(norm_names)):
            for j in range(i + 1, len(norm_names)):
                for c in self._crossings(norm_names[i], norm_names[j]):
                    if muni and c["muni"] != muni:
                        continue
                    pts.append(c)
        if not muni:
            hav = [c for c in pts if self._is_habana(c["muni"])]
            pts = hav or pts
        return pts

    def resolve_region(self, streets, municipio=None):
        """Convex polygon enclosed by ≥3 streets. Returns a Region or None."""
        raw = [s for s in (streets or []) if s and s.strip()]
        if len(raw) < 3:
            return None
        muni = self.store.resolve_municipio(municipio) if municipio else None

        # normalize + split resolved vs unresolved (a street resolves if it has
        # any geometry fragment in the store)
        norm_names, kept_raw, unresolved = [], [], []
        for s in raw:
            n = normalize_street_name(s)
            if n and self._street_frags(n):
                norm_names.append(n)
                kept_raw.append(s)
            else:
                unresolved.append(s)
        if len(norm_names) < 3:
            return None

        recs = self._region_crossings(norm_names, muni)
        # distinct corner points
        seen, pts = set(), []
        for c in recs:
            key = (round(c["lat"], 6), round(c["lon"], 6))
            if key not in seen:
                seen.add(key)
                pts.append(c)
        if len(pts) < 3:
            return None

        hull = MultiPoint([Point(c["lon"], c["lat"]) for c in pts]).convex_hull
        if hull.geom_type != "Polygon":
            return None      # collinear crossings → line/point, not a region

        ring = list(hull.exterior.coords)[:-1]      # drop the repeated closing point
        vertices = [[lat, lon] for lon, lat in ring]
        cen = hull.centroid
        minx, miny, maxx, maxy = hull.bounds
        area_m2 = _polygon_area_m2(hull, cen.y)
        diag_m = 2 * _bbox_radius_m(minx, miny, maxx, maxy)

        # municipio: majority vote of the corner crossings (they carry the real
        # municipio on a real build; "" on a bare-geometry test store)
        munis = [c["muni"] for c in pts if c.get("muni")]
        muni_out = muni or (max(set(munis), key=munis.count) if munis else "")

        return Region(
            streets=kept_raw,
            unresolved=unresolved,
            vertices=vertices,
            polygon_wkt=hull.wkt,
            centroid_lat=cen.y, centroid_lon=cen.x,
            area_m2=area_m2, area_ha=area_m2 / 10_000.0,
            bbox=(miny, minx, maxy, maxx),
            municipio=muni_out,
            provincia=self._province_for(muni_out),
            n_crossings=len(pts),
            degenerate=area_m2 < _REGION_DEGENERATE_AREA_M2,
            ambiguous=diag_m > _REGION_AMBIGUOUS_DIAG_M,
        )

    def describe(self, lat: float, lon: float):
        """Reverse: a point -> a Cuban-style address (see search/describe.py)."""
        from geocoder.search.describe import describe as _describe
        return _describe(self.store, lat, lon)

    @staticmethod
    def _cross_muni(cand, muni):
        """Report the crossing's real municipio — honour an explicit context muni
        only when the crossing actually lies in it (else the point and the label
        would disagree, e.g. a Havana crossing tagged with a distant context
        municipio that has no such corner)."""
        return muni if (muni and muni == cand["muni"]) else cand["muni"]

    def _resolve_address(self, p, muni, muni_norm, ref, prov=None):
        if p.street and p.number:
            row = self.store.find_portal(p.street, p.number, muni)
            if row:
                return self._mk(
                    row["lat"], row["lon"], "exact_portal", 15.0, p,
                    nombre=f"{p.street} {p.number}", tipo="portal", muni=muni,
                )

        # esquina → where the two streets actually cross (the right fragment
        # pair, scoped to context / Havana). Report the crossing's municipio.
        if p.esquina:
            best = self._best_intersection(
                self._crossings(p.esquina[0], p.esquina[1]), muni, ref, prov)
            if best is not None:
                return self._mk(best["lat"], best["lon"], "esquina", 20.0, p,
                                nombre=f"{p.esquina[0]} y {p.esquina[1]}",
                                tipo="interseccion", muni=self._cross_muni(best, muni))

        # entre → midpoint of street A's crossings with the two cross-streets.
        # Anchor to the same stretch of A: pick the A×B crossing by context, then
        # the A×C crossing nearest to it. If only one cross-street resolves (the
        # other is short/unmapped, e.g. "M" in Vedado), anchor at that single
        # crossing rather than falling through to a random A fragment.
        if p.entre and p.street:
            ab = self._crossings(p.street, p.entre[0])
            ac = self._crossings(p.street, p.entre[1])
            b = self._best_intersection(ab, muni, ref, prov)
            c = self._best_intersection(ac, muni, ref, prov)
            # keep the two bounds on the same stretch of A
            if b is not None and ac:
                c = min(ac, key=lambda x: _rec_dist_m(x, b))
            elif c is not None and ab:
                b = min(ab, key=lambda x: _rec_dist_m(x, c))
            nombre = (f"{p.street} {p.number}" if p.number
                      else f"{p.street} e/ {p.entre[0]} y {p.entre[1]}")
            if b is not None and c is not None and _rec_dist_m(b, c) <= _ENTRE_MAX_SPAN_M:
                span_m = _rec_dist_m(b, c)
                lat, lon = (b["lat"] + c["lat"]) / 2, (b["lon"] + c["lon"]) / 2
                cross_muni = self._cross_muni(b, muni)
                method, rad = (("interpolated_entre", max(span_m / 4, 20.0)) if p.number
                               else ("segment_midpoint", max(span_m / 2, 20.0)))
                return self._mk(lat, lon, method, rad, p, nombre=nombre,
                                tipo=("portal" if p.number else "calle"), muni=cross_muni)
            # single bound resolved → anchor at that crossing (block-scale radius)
            single = b or c
            if single is not None:
                return self._mk(single["lat"], single["lon"], "segment_midpoint", 150.0, p,
                                nombre=nombre, tipo=("portal" if p.number else "calle"),
                                muni=self._cross_muni(single, muni))

        # street fallback → centroid of the street line. Fires even when a house
        # number was given but no exact portal / entre resolved (better a coarse
        # street point than nothing; true number interpolation is a future rung).
        if p.street:
            g = self._street_geom(p.street, muni, ref, prov)
            if g is not None:
                c = g.centroid
                nombre = f"{p.street} {p.number}" if p.number else p.street
                return self._mk(c.y, c.x, "street", max(_bbox_radius_m(*g.bounds), 30.0), p,
                                nombre=nombre, tipo="calle", muni=muni)

        return None

    def _resolve_context(self, p, muni):
        """Coarsest honest point for an address whose whole ladder failed: the
        barrio or municipio the query itself named, carrying that area's radius.

        This rung exists so the POI rung does not get the query. An address that
        fails is not a place-name query, and handing its raw text to the POI
        index makes it fuzzy-match *something* — measured on real data, 404
        distinct addresses collapsed onto a single coordinate, each reported at
        a 25 m radius. A point that is wrong at 25 m is worse than a point that
        is right at 2 km, because only the second one tells the caller what it
        does not know. Consumers already drop on `precision`; they cannot drop
        what claims to be exact.
        """
        for hint in (p.barrio, p.municipio, p.provincia):
            if not hint:
                continue
            recs = self.gaz.lookup(normalize_text(hint))
            if not recs:
                continue
            rec = self._pick_area(recs, p)
            level = rec["level"]
            return self._mk(
                rec["lat"], rec["lon"], level, rec["radius_m"], p,
                nombre=rec["canonical"], tipo=level,
                muni=(rec["canonical"] if level == "municipio"
                      else (rec.get("parent_muni") or muni or "")),
                barrio=(rec["canonical"] if level == "barrio" else None),
                provincia=rec.get("parent_prov"),
                geometry_wkt=rec.get("polygon_wkt"),
            )
        return None

    def _resolve_area(self, p, muni):
        """Province / municipio / barrio via the gazetteer (exact, else fuzzy)."""
        name = p.name or p.raw
        if not name:
            return None
        nn = normalize_text(name)
        # strip an explicit level qualifier ("provincia de Cienfuegos" → look up
        # "cienfuegos"; _pick_area sees "provincia" in the raw text and prefers it)
        for pref in ("provincia de ", "provincia ", "municipio de ", "municipio "):
            if nn.startswith(pref):
                nn = nn[len(pref):].strip()
                break
        recs = self.gaz.lookup(nn) or self.gaz.fuzzy(nn, min_score=82.0)
        if not recs:
            return None
        rec = self._pick_area(recs, p)
        level = rec["level"]  # provincia | municipio | barrio
        prov = rec["canonical"] if level == "provincia" else rec.get("parent_prov")
        muni_out = rec["canonical"] if level == "municipio" else (rec.get("parent_muni") or muni or "")
        barrio_out = rec["canonical"] if level == "barrio" else None
        return self._mk(
            rec["lat"], rec["lon"], level, rec["radius_m"], p,
            nombre=rec["canonical"], tipo=level,
            muni=muni_out, barrio=barrio_out, provincia=prov,
            geometry_wkt=rec.get("polygon_wkt"),
        )

    def _pick_area(self, recs, p):
        """A name may be several levels (Cienfuegos = provincia+municipio) or
        repeat across cities. Default to municipio (a useful point); provincia
        only when qualified ('provincia de …'). Then disambiguate homonyms by
        context, else prefer La Habana."""
        wants_prov = "provincia" in normalize_text(p.raw)
        order = (["provincia", "municipio", "barrio"] if wants_prov
                 else ["municipio", "barrio", "provincia"])
        for level in order:
            group = [r for r in recs if r["level"] == level]
            if group:
                return self._disambiguate_area(group, p)
        return recs[0]

    def _disambiguate_area(self, group, p):
        if len(group) == 1:
            return group[0]
        ctx = {normalize_text(x) for x in (p.municipio, p.provincia, p.barrio) if x}
        if ctx:
            for r in group:
                if (normalize_text(r.get("parent_muni") or "") in ctx
                        or normalize_text(r.get("parent_prov") or "") in ctx):
                    return r
        hav = [r for r in group
               if r.get("parent_prov") == SETTINGS.TARGET_PROVINCE
               or (r["level"] == "provincia" and r["canonical"] == SETTINGS.TARGET_PROVINCE)]
        return (hav or group)[0]

    def _resolve_place(self, p, muni, muni_norm, ref=None, limit=1, prov=None):
        """POI resolution → up to `limit` ranked Resolved (the only multi-result
        rung; addresses/areas are single-answer). Returns a list."""
        name = p.name or p.raw
        nn = normalize_text(name)
        hits = self.store.search_lugares(
            normalize_for_fts(name), municipio_norm=muni_norm, limit=SETTINGS.FTS_LIMIT
        )
        # A province named in the query is binding: a homonym elsewhere is not a
        # worse answer, it is the wrong one. Dropping the out-of-province hits
        # here (rather than merely demoting them) also lets the typo fallback and
        # the street fallback fire, which is what the caller actually wants.
        def keep(hs):
            return [h for h in hs if h.get("provincia") == prov] if prov else hs

        ranked = keep(self.fuzzy.rerank(nn, hits)) if hits else []
        # Municipio-scope widening: the municipio is a preference, the province is
        # the binding scope. Several provinces are named after their capital
        # municipio ("Pinar del Río", "Cienfuegos"), so a query naming the
        # province scopes the FTS to that one municipio and finds nothing, while
        # the place sits in a neighbouring municipio of the same province.
        if not ranked and muni_norm and prov:
            ranked = keep(self.fuzzy.rerank(nn, self.store.search_lugares(
                normalize_for_fts(name), limit=SETTINGS.FTS_LIMIT)))
        # Typo fallback: strict FTS AND-matches every token, so one mistyped word
        # ("hotel nacinal", "coppellia") returns nothing. Retry with an OR of
        # short (3-char) prefixes — tolerant to typos after the prefix — and let
        # the fuzzy reranker pick the best of that wider pool.
        if not ranked:
            fq = normalize_for_fts_fuzzy(name, prefix_len=3)
            if fq:
                wide = self.store.search_lugares(
                    fq, municipio_norm=muni_norm, limit=SETTINGS.FTS_LIMIT
                )
                ranked = keep(self.fuzzy.rerank(nn, wide)) if wide else []
        if not ranked:
            # Street-name fallback: strip common prefix words and look up geometry
            r = self._resolve_street_by_name(name, muni, p, ref, prov)
            return [r] if r else []
        # Barrio context disambiguates duplicate names (Coppelia in La Lisa vs
        # Vedado): among near-tied fuzzy matches, prefer the one nearest the ref.
        if ref is not None and len(ranked) > 1:
            top = ranked[0].get("final_score", 0)
            ties = [h for h in ranked if top - h.get("final_score", 0) <= 10]
            if len(ties) > 1:
                ties.sort(key=lambda h: (h["lat"] - ref[0]) ** 2 + (h["lon"] - ref[1]) ** 2)
                ranked = ties + [h for h in ranked if h not in ties]
        return [self._poi_to_resolved(h, p) for h in ranked[:max(limit, 1)]]

    def _poi_to_resolved(self, h, p):
        """One FTS hit → a POI Resolved (radius from footprint, else ~25 m node)."""
        rad = 25.0
        if h.get("geom_wkt"):
            try:
                rad = _bbox_radius_m(*wkt.loads(h["geom_wkt"]).bounds)
            except Exception:
                pass
        return self._mk(
            h["lat"], h["lon"], "poi", rad, p,
            nombre=h["nombre"], tipo=h.get("tipo", "poi"),
            muni=h.get("municipio", ""), barrio=h.get("barrio"),
            score=h.get("final_score", 0.0), provincia=h.get("provincia"),
        )

    def _resolve_street_by_name(self, name, muni, p, ref=None, prov=None):
        """Fallback: strip address-type prefixes and look up street geometry."""
        norm = normalize_text(name)
        street_norm = norm
        for prefix in self._STREET_PREFIXES:
            if norm.startswith(prefix):
                street_norm = norm[len(prefix):].strip()
                break
        g = self._street_geom(street_norm, muni, ref, prov)
        if g is not None:
            c = g.centroid
            return self._mk(c.y, c.x, "street", max(_bbox_radius_m(*g.bounds), 30.0), p,
                            nombre=street_norm, tipo="calle", muni=muni)
        return None

    def _mk(
        self, lat, lon, method, radius_m, p, *, nombre, tipo,
        muni=None, barrio=None, score=0.0, provincia=None, geometry_wkt=None,
    ):
        return Resolved(
            nombre=nombre, tipo=tipo, lat=lat, lon=lon, method=method,
            radius_m=radius_m, municipio=muni or "",
            provincia=self._province_for(muni, provincia),
            barrio=barrio, score=score, geometry_wkt=geometry_wkt,
            resolved={
                "kind": p.kind, "street": p.street, "number": p.number,
                "entre": list(p.entre) if p.entre else None,
                "esquina": list(p.esquina) if p.esquina else None,
                "name": p.name, "barrio": p.barrio, "municipio": p.municipio,
            },
        )
