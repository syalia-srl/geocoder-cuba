"""Beaver-backed persistence for the geocoder (single storage unit)."""
import math
import threading
from datetime import datetime, timezone
import re
from typing import Iterable, Optional
from beaver import BeaverDB, q
from rapidfuzz import process, fuzz
from shapely import wkt as _wkt
from shapely.geometry import Point
from geocoder.models import Lugar
from geocoder.utils.normalize import normalize_text

_LUGAR_FIELDS = ("nombre", "norm_nombre", "tipo", "municipio", "municipio_norm",
                 "provincia", "barrio", "lat", "lon", "street_norm", "portal_number")

# NOTE: each bulk write below uses ONE beaver `.batched()` context (a single
# executemany transaction). We do NOT chunk into multiple batches on the same
# collection: re-entering `col.batched()` on the same collection deadlocks in
# beaver 2.0rc5 (cross-collection batches are fine). A single batch handles
# La Habana comfortably; chunking for a full-Cuba build needs the beaver
# same-collection batch-reentry bug fixed first.


# One leading article/contraction a speaker may drop from a street name.
# Applied repeatedly, so "de las banderas" yields both "las banderas" and
# "banderas" — speakers drop any leading run of them, not just the first.
_ARTICLE_PREFIX = re.compile(r"^(?:del|de|las|los|la|el)\s+")


def _article_forms(name: str) -> list[str]:
    """Progressively article-stripped forms of a street name, shortest last."""
    out, cur = [], name
    while (m := _ARTICLE_PREFIX.match(cur)):
        cur = cur[m.end():]
        if cur:
            out.append(cur)
    return out


def _pkey(*parts) -> str:
    return "|".join("" if p is None else str(p) for p in parts)


class GeoStore:
    def __init__(self, db_path):
        self.db = BeaverDB(str(db_path))
        self.lugares = self.db.docs("lugares")
        self.portales = self.db.dict("portales")
        self.calles_geom = self.db.dict("calles_geom")
        self.municipios = self.db.dict("municipios")
        self.provincias = self.db.dict("provincias")
        self.barrios = self.db.dict("barrios")
        self.intersections = self.db.dict("intersections")
        self.graph_nodes = self.db.dict("graph_nodes")
        self.graph_edges = self.db.dict("graph_edges")
        self.meta = self.db.dict("meta")
        self._n = 0
        self._street_idx = None  # street_norm -> [municipio, ...], built once
        self._geoms = None  # calles_geom in memory: key -> wkt, loaded once
        self._portals = None  # portales in memory: key -> row, loaded once
        self._portal_any = None  # "street|numero" -> first row in any municipio
        self._muni_cache = None  # list(municipios.values()), built once
        self._barrio_cache = None  # list(barrios.values()), built once
        self._street_vocab = None  # list of distinct street_norm, for fuzzy match
        self._street_alias = None  # article-dropped street_norm → full name
        self._gazetteer = None  # Gazetteer of areas, built once
        self._routing_graph = None  # (nodes, adj), loaded once
        self._street_tree = None  # (STRtree, rows), built once
        self._street_tree_lock = threading.Lock()
        self._admin_geo = {}  # level -> list of GeoJSON features, built once

    # ---- writes ----
    def _portal_row(self, p) -> tuple[str, dict]:
        return _pkey(p.street_norm, p.numero, p.municipio), {
            "street_norm": p.street_norm, "numero": p.numero, "lat": p.lat, "lon": p.lon,
            "municipio": p.municipio, "provincia": p.provincia, "barrio": p.barrio,
        }

    def index_lugares(self, lugares: Iterable[Lugar]) -> int:
        """Bulk-index lugares in one batched transaction."""
        n = 0
        with self.lugares.batched() as batch:
            for lugar in lugares:
                self._n += 1
                body = {f: getattr(lugar, f) for f in _LUGAR_FIELDS}
                batch.index(id=str(self._n), body=body)
                n += 1
        return n

    def put_portal(self, p) -> None:
        key, row = self._portal_row(p)
        self.portales[key] = row
        self._portals = None

    def put_portals(self, portals: Iterable) -> int:
        """Bulk-write portals in one batched transaction."""
        n = 0
        with self.portales.batched() as batch:
            for p in portals:
                key, row = self._portal_row(p)
                batch[key] = row
                n += 1
        self._portals = None
        return n

    def put_calle_geom(self, street_norm, municipio, wkt) -> None:
        self.calles_geom[_pkey(street_norm, municipio)] = wkt
        self._geoms = self._street_idx = None

    def put_calle_geoms(self, items: Iterable) -> int:
        """Bulk-write street geometries. Each item is (street_norm, municipio, wkt)."""
        n = 0
        with self.calles_geom.batched() as batch:
            for street_norm, municipio, wkt in items:
                batch[_pkey(street_norm, municipio)] = wkt
                n += 1
        self._geoms = self._street_idx = None
        return n

    def put_municipio(self, m) -> None:
        self.municipios[m.name] = {
            "name": m.name, "name_es": getattr(m, "name_es", None), "province": m.province,
            "centroid_lat": m.centroid.y if m.centroid else None,
            "centroid_lon": m.centroid.x if m.centroid else None,
            "polygon_wkt": m.polygon.wkt if m.polygon else None,
            "bbox_minx": m.bbox_minx, "bbox_miny": m.bbox_miny,
            "bbox_maxx": m.bbox_maxx, "bbox_maxy": m.bbox_maxy,
            "municipio_norm": normalize_text(m.name),
        }

    def put_provincia(self, p) -> None:
        self.provincias[p.name] = {"name": p.name,
            "polygon_wkt": p.polygon.wkt if p.polygon else None,
            "centroid_lat": p.centroid.y if p.centroid else None,
            "centroid_lon": p.centroid.x if p.centroid else None}

    def put_barrio(self, b) -> None:
        key = _pkey(b.name, b.municipio_id)
        poly = b.polygon
        bbox = poly.bounds if poly else (None, None, None, None)
        self.barrios[key] = {
            "name": b.name, "barrio_norm": normalize_text(b.name),
            "municipio_id": b.municipio_id, "place_type": b.place_type,
            "polygon_wkt": poly.wkt if poly else None,
            "centroid_lat": b.centroid.y if b.centroid else None,
            "centroid_lon": b.centroid.x if b.centroid else None,
            "bbox_minx": bbox[0], "bbox_miny": bbox[1],
            "bbox_maxx": bbox[2], "bbox_maxy": bbox[3],
        }

    # ---- reads ----
    def _body(self, scored) -> dict:
        d = dict(scored.document.body)
        d["id"] = scored.document.id
        d["rank"] = scored.score
        return d

    def search_lugares(self, fts_q, municipio_norm=None, tipo=None, limit=100) -> list[dict]:
        where = []
        if municipio_norm:
            where.append(q("municipio_norm") == municipio_norm)
        if tipo:
            where.append(q("tipo") == tipo)
        res = self.lugares.search(query=fts_q, on=["nombre", "norm_nombre"],
                                  where=where or None, limit=limit)
        return [self._body(s) for s in res]

    def find_portal(self, street_norm, numero, municipio=None) -> Optional[dict]:
        # NB: beaver dict.get(k) RAISES KeyError on a missing key (not
        # Python-dict semantics), so gate every lookup with `k in dict`.
        if self._portals is None:
            # One `.items()` scan (~17.5k rows on full Cuba). The unscoped path
            # used to scan every key per call — ~0.8 s each through the beaver
            # bridge, which made batch geocoding crawl.
            # Built in locals and published `_portal_any` first: the startup
            # warm thread may be filling this while a request reads it.
            portals, any_muni = dict(self.portales.items()), {}
            for k, row in portals.items():
                any_muni.setdefault(k.rpartition("|")[0], row)
            self._portal_any, self._portals = any_muni, portals
        if municipio:
            return self._portals.get(_pkey(street_norm, numero, municipio))
        return self._portal_any.get(_pkey(street_norm, numero))

    def _geom_map(self) -> dict:
        """calles_geom in memory (~14 MB of WKT on full Cuba). Reading a
        fragment by key costs a SQLite round-trip through the beaver bridge,
        and a common name like "9" has dozens of fragments."""
        if self._geoms is None:
            self._geoms = dict(self.calles_geom.items())
        return self._geoms

    def _street_index(self) -> dict:
        """street_norm → [municipio, ...]. Built once from the in-memory
        geometry map, so callers can ask per query."""
        if self._street_idx is None:
            idx: dict = {}
            for k in self._geom_map():
                sn, _, mun = k.partition("|")
                idx.setdefault(sn, []).append(mun)
            self._street_idx = idx
        return self._street_idx

    def _street_alias_index(self) -> dict:
        """Article-dropped street_norm → the one full name it can mean.

        OSM stores what the sign says ("de las banderas"); people say the short
        form ("las banderas"). Built once from the street vocabulary, and only
        for names where the short form is unambiguous: an entry is dropped when
        the stripped form is itself a real street (it would shadow it) or when
        two articled streets share it ("canada" ← "de canada" / "la canada")."""
        if self._street_alias is None:
            names = self._street_index()
            grouped: dict = {}
            for n in names:
                for short in _article_forms(n):
                    grouped.setdefault(short, set()).add(n)
            self._street_alias = {
                short: next(iter(full))
                for short, full in grouped.items()
                if len(full) == 1 and short not in names
            }
        return self._street_alias

    def street_alias(self, street_norm) -> Optional[str]:
        """The full articled street name a short form stands for, or None."""
        return self._street_alias_index().get(normalize_text(street_norm))

    def get_calle_geom(self, street_norm, municipio=None) -> Optional[str]:
        """The merged geometry of a street, scoped to `municipio` when given.

        A named municipio is honoured or the call fails — it used to fall back to
        `muns[0]`, i.e. whichever municipio happened to be indexed first, so a
        street name that repeats across Cuba answered a Havana query with a
        geometry from Pinar del Río. Callers that want to widen the search have
        `street_geoms()` and can pick a fragment on their own terms.
        """
        muns = self._street_index().get(street_norm)
        if not muns:
            return None
        if municipio:
            return (self._geom_map()[_pkey(street_norm, municipio)]
                    if municipio in muns else None)
        return self._geom_map()[_pkey(street_norm, muns[0])]

    def street_geoms(self, street_norm) -> list[tuple]:
        """All merged street geometries for a street across municipios:
        list of (municipio, wkt). Used to pick the right fragment by proximity."""
        return [
            (mun, self._geom_map()[_pkey(street_norm, mun)])
            for mun in self._street_index().get(street_norm, [])
        ]

    def street_tree(self):
        """Cached STRtree over every street geometry, with a parallel row list.

        Returns (tree, rows) where rows[i] is (street_norm, municipio, geom) for
        tree index i. Reverse geocoding asks "which street is nearest this
        point", which no keyed lookup answers; building this per query would
        parse all ~25.6k geometries. Cached like the routing graph.
        """
        if self._street_tree is None:
            with self._street_tree_lock:
                if self._street_tree is None:   # another thread may have won
                    from shapely.strtree import STRtree
                    # `.items()` is one scan; reading the 25.6k values by key
                    # instead costs a SQLite round-trip each and dominated the
                    # build (~95 s vs a few seconds).
                    #
                    # Parse one at a time, NOT with a vectorised
                    # `shapely.from_wkt(all_wkts)`. Measured on the full-Cuba
                    # build (25 635 geometries, 14 MB of WKT):
                    #   whole list  -> 21 253 MB peak   (would OOM any small box)
                    #   chunks of 1k ->    955 MB peak, 5.8 s
                    #   one by one   ->    116 MB peak, 1.7 s
                    # The loop is both faster and ~180x lighter; the vectorised
                    # call allocates a pathological transient. Do not "optimise"
                    # this back without re-measuring peak RSS.
                    rows = []
                    for key, value in self.calles_geom.items():
                        try:
                            g = _wkt.loads(value)
                        except Exception:
                            continue
                        if g.is_empty:
                            continue
                        sn, _, mun = key.partition("|")
                        rows.append((sn, mun, g))
                    self._street_tree = (STRtree([r[2] for r in rows]), rows)
        return self._street_tree

    def put_intersections(self, records) -> int:
        """Bulk-write precomputed crossings, grouped by canonical pair key
        ('a|b' with a <= b) so 'A y B' and 'B y A' hit the same entry."""
        grouped: dict = {}
        for r in records:
            key = _pkey(*sorted((r["a"], r["b"])))
            grouped.setdefault(key, []).append({
                "lat": r["lat"], "lon": r["lon"], "muni": r["muni"],
                "prov": r["prov"], "a": r["a"], "b": r["b"],
            })
        n = 0
        with self.intersections.batched() as batch:
            for key, lst in grouped.items():
                batch[key] = lst
                n += 1
        return n

    def crossings(self, a_norm, b_norm) -> list[dict]:
        """Precomputed crossings for a street pair (order-independent), or []."""
        key = _pkey(*sorted((a_norm, b_norm)))
        return self.intersections[key] if key in self.intersections else []

    def put_graph(self, nodes, edges) -> tuple[int, int]:
        """Persist the routing graph. `nodes`: {node_id: (lat, lon)}. `edges`:
        {node_id: [{"to", "length_m", "highway", "polyline_wkt"}, ...]}. One
        batched write per collection (beaver #40)."""
        with self.graph_nodes.batched() as b:
            for nid, (lat, lon) in nodes.items():
                b[str(nid)] = {"lat": lat, "lon": lon}
        with self.graph_edges.batched() as b:
            for nid, elist in edges.items():
                b[str(nid)] = elist
        return len(nodes), len(edges)

    def load_graph(self) -> tuple[dict, dict]:
        """Load the whole routing graph into memory: (nodes {id→(lat,lon)},
        adj {id→[edge dict, ...]})."""
        nodes = {k: (v["lat"], v["lon"]) for k, v in self.graph_nodes.items()}
        adj = {k: list(v) for k, v in self.graph_edges.items()}
        return nodes, adj

    def routing_graph(self) -> tuple[dict, dict]:
        """Cached (nodes, adj) — a full beaver scan of the graph collections is
        ~7 s, so load it once and reuse across queries (Router reads this)."""
        if self._routing_graph is None:
            self._routing_graph = self.load_graph()
        return self._routing_graph

    def gazetteer(self):
        """Cached place index (provinces/municipios/barrios) — built once."""
        if self._gazetteer is None:
            from geocoder.search.gazetteer import Gazetteer
            self._gazetteer = Gazetteer.from_store(self)
        return self._gazetteer

    def fuzzy_street(self, word, min_score: float = 82.0):
        """Canonicalize a (possibly mistyped) street name to a real street_norm
        from the vocabulary, or None. 'belaskoain' → 'belascoain'."""
        w = normalize_text(word)
        if not w:
            return None
        if self._street_vocab is None:
            self._street_vocab = list(self._street_index().keys())
        res = process.extractOne(w, self._street_vocab, scorer=fuzz.ratio, score_cutoff=min_score)
        return res[0] if res else None

    def nearest_lugares(self, lat, lon, radius_m, limit) -> list[dict]:
        d = radius_m / 111_000.0  # deg per meter (lat); bbox pre-filter
        res = self.lugares.search(where=[q("lat") >= lat - d, q("lat") <= lat + d,
                                         q("lon") >= lon - d, q("lon") <= lon + d], limit=500)
        out = []
        for s in res:
            b = self._body(s)
            b["distance_meters"] = round(_haversine(lat, lon, b["lat"], b["lon"]), 1)
            if b["distance_meters"] <= radius_m:
                out.append(b)
        out.sort(key=lambda x: x["distance_meters"])
        return out[:limit]

    def _munis(self) -> list[dict]:
        """Cached list of municipio records (a beaver .values() scan per call is
        wasteful — resolve_municipio runs on every query)."""
        if self._muni_cache is None:
            self._muni_cache = list(self.municipios.values())
        return self._muni_cache

    def _barrios(self) -> list[dict]:
        if self._barrio_cache is None:
            self._barrio_cache = list(self.barrios.values())
        return self._barrio_cache

    def all_municipios(self) -> list[dict]:
        return self._munis()

    def all_barrios(self) -> list[dict]:
        return self._barrios()

    def admin_geometries(self, level: str) -> list[dict]:
        """GeoJSON features for every provincia / municipio / barrio, simplified
        (~50 m, coordinates to 1e-5°) so the whole country ships to a browser.

        Properties: name, municipio, provincia. A barrio without a polygon (most
        of them: OSM maps them as nodes) comes back as a Point at its centroid,
        and its municipio is the one its centroid falls in, since barrios carry
        no stored linkage. Built once per level; the data is read-only while
        serving.
        """
        if level not in self._admin_geo:
            self._admin_geo[level] = self._build_admin_geometries(level)
        return self._admin_geo[level]

    def _build_admin_geometries(self, level: str) -> list[dict]:
        import shapely
        from shapely.geometry import mapping
        from shapely.strtree import STRtree

        def shape(wkt_str):
            try:
                g = _wkt.loads(wkt_str)
                g = shapely.set_precision(g.simplify(0.0005, preserve_topology=True), 1e-5)
                return None if g.is_empty else mapping(g)
            except Exception:
                return None

        def feature(geom, name, municipio, provincia):
            return {"type": "Feature", "geometry": geom,
                    "properties": {"name": name, "municipio": municipio, "provincia": provincia}}

        out = []
        if level == "provincia":
            for p in self.provincias.values():
                g = p.get("polygon_wkt") and shape(p["polygon_wkt"])
                if g:
                    out.append(feature(g, p["name"], None, p["name"]))
        elif level == "municipio":
            for m in self._munis():
                g = m.get("polygon_wkt") and shape(m["polygon_wkt"])
                if g:
                    out.append(feature(g, m["name"], m["name"], m.get("province")))
        elif level == "barrio":
            polys, recs = [], []
            for m in self._munis():
                if m.get("polygon_wkt"):
                    try:
                        polys.append(_wkt.loads(m["polygon_wkt"]))
                        recs.append(m)
                    except Exception:
                        pass
            tree = STRtree(polys) if polys else None
            for b in self._barrios():
                if b.get("centroid_lat") is None:
                    continue
                parent = None
                if tree is not None:
                    hit = tree.query(Point(b["centroid_lon"], b["centroid_lat"]), predicate="within")
                    parent = recs[int(hit[0])] if len(hit) else None
                g = (b.get("polygon_wkt") and shape(b["polygon_wkt"])) or {
                    "type": "Point",
                    "coordinates": [round(b["centroid_lon"], 5), round(b["centroid_lat"], 5)]}
                out.append(feature(g, b["name"], parent and parent["name"],
                                   parent and parent.get("province")))
        else:
            raise ValueError(f"unknown level: {level}")
        return out

    def barrios_in_municipio(self, municipio_name) -> list[dict]:
        """Barrios whose centroid falls inside the municipio polygon. Barrios
        carry no stored municipio linkage, so resolve it spatially (point in
        polygon) at query time against the municipio's boundary."""
        if not municipio_name or municipio_name not in self.municipios:
            return []
        m = self.municipios[municipio_name]
        poly = None
        if m.get("polygon_wkt"):
            try:
                poly = _wkt.loads(m["polygon_wkt"])
            except Exception:
                poly = None
        out = []
        for b in self._barrios():
            if b.get("centroid_lat") is None:
                continue
            pt = Point(b["centroid_lon"], b["centroid_lat"])
            if poly is not None:
                if poly.contains(pt):
                    out.append(b)
            elif m.get("bbox_minx") is not None and (
                m["bbox_minx"] <= pt.x <= m["bbox_maxx"]
                and m["bbox_miny"] <= pt.y <= m["bbox_maxy"]
            ):
                out.append(b)
        return out

    def resolve_barrio(self, word, municipio=None) -> Optional[dict]:
        w = normalize_text(word)
        best = None
        for b in self._barrios():
            if b.get("centroid_lat") is None:
                continue
            bn = b.get("barrio_norm") or normalize_text(b["name"])
            if bn == w:
                return b
            if w and (bn.startswith(w) or w in bn):
                best = best or b
        return best

    def fuzzy_municipio(self, word, min_score: float = 82.0):
        """Best municipio by full-string similarity (typo-tolerant): (name, score)
        or None. Uses fuzz.ratio (edit distance) — not partial — so a substring
        like 'revolucion' doesn't hijack 'Plaza de la Revolución'."""
        w = normalize_text(word)
        if not w:
            return None
        munis = self._munis()
        norms = [m.get("municipio_norm") or normalize_text(m["name"]) for m in munis]
        res = process.extractOne(w, norms, scorer=fuzz.ratio, score_cutoff=min_score)
        if res is None:
            return None
        return munis[res[2]]["name"], res[1]

    def fuzzy_barrio(self, word, min_score: float = 82.0):
        """Best barrio (with a centroid) by full-string similarity: (dict, score)
        or None."""
        w = normalize_text(word)
        if not w:
            return None
        barrios = [b for b in self._barrios() if b.get("centroid_lat") is not None]
        if not barrios:
            return None
        norms = [b.get("barrio_norm") or normalize_text(b["name"]) for b in barrios]
        res = process.extractOne(w, norms, scorer=fuzz.ratio, score_cutoff=min_score)
        if res is None:
            return None
        return barrios[res[2]], res[1]

    def resolve_municipio(self, word) -> Optional[str]:
        w = normalize_text(word)
        best = None
        for m in self._munis():
            mn = m.get("municipio_norm") or normalize_text(m["name"])
            if mn == w:
                return m["name"]
            if w and (mn.startswith(w) or w in mn):
                best = best or m["name"]
        return best

    def stamp_build(self, commit: str, version: str) -> dict:
        """Record which code produced this data.

        The ETL and the query path share `normalize_street_name`, so a one-line
        change there silently invalidates every key already written. Shipping a
        baked store built by different code than reads it is a silent
        correctness bug; this stamp lets the packager refuse it loudly.

        Kept *inside* the database rather than in a sidecar file, because a
        sidecar can be copied away from the data it describes.
        """
        stamp = {
            "commit": commit,
            "version": version,
            "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self.meta["build"] = stamp
        return stamp

    def build_stamp(self) -> Optional[dict]:
        """The build stamp, or None on a store that predates stamping."""
        return self.meta["build"] if "build" in self.meta else None

    def stats(self) -> dict:
        s = {"municipios": len(list(self.municipios.keys())),
             "provincias": len(list(self.provincias.keys())),
             "barrios": len(list(self.barrios.keys())),
             "portales": len(list(self.portales.keys()))}
        if "counts" in self.meta:
            s.update(self.meta["counts"])
        return s

    def ping(self) -> bool:
        _ = list(self.meta.keys())
        return True

    def close(self):
        self.db.close()


def _haversine(lat1, lon1, lat2, lon2):
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))
