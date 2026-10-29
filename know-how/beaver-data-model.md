# Beaver data model

*Reach for this when touching persistence or search: the collection/dict layout,
how queries map onto beaver, and the beaver API traps that bit us.*

All persistence goes through `GeoStore` (`src/geocoder/store.py`), which wraps a
`BeaverDB`. **No raw SQLite in app code.** If beaver can't express a query we need,
extend beaver — don't hand-roll SQL around it.

## Collections & dicts

| beaver object | kind | key / shape | used by |
|---|---|---|---|
| `lugares` | `db.docs` | `Document(id, body)` per street/portal/POI; FTS-indexed | forward search, reverse geo |
| `portales` | `db.dict` | `f"{street_norm}\|{numero}\|{municipio}"` → payload | exact portal lookup (O(1)) |
| `calles_geom` | `db.dict` | `f"{street_norm}\|{municipio}"` → LineString WKT | intersection geometry |
| `municipios` | `db.dict` | name → `{polygon_wkt, centroid_*, bbox, province, municipio_norm}` | enrichment, `/municipios` |
| `provincias` / `barrios` | `db.dict` | name → payload | enrichment, admin routes |
| `graph_nodes` | `db.dict` | `node_id` (OSM) → `{lat, lon}` | routing graph (junctions/endpoints) |
| `graph_edges` | `db.dict` | `node_id` → `[{to, length_m, highway, polyline_wkt}]` | routing adjacency list |
| `meta` | `db.dict` | build counts | `/stats` |

### `lugares` document body

`nombre, norm_nombre, tipo, municipio, municipio_norm, provincia, barrio, lat,
lon, street_norm, portal_number`. FTS auto-indexes text fields; **searches are
scoped** with `on=["nombre","norm_nombre"]` so `tipo`/`municipio` aren't matched
as text. Metadata filters use `json_extract` on the stored body (not the FTS
index), so non-searched fields don't need FTS.

**Street geometry is deliberately NOT in this document.** A LineString WKT would
bloat the FTS index with garbage tokens; it lives in the `calles_geom` dict, read
by the intersection finder.

## Real beaver API (verified — the README examples are stale for 2.0)

- `db = BeaverDB(str(path))`; `db.docs(name)` (**not** `db.collection`), `db.dict(name)`.
- Index: `col.index(id="<str>", body={...})`. Bulk: `with col.batched() as b: b.index(...)`.
- Filtered search: `col.search(query=<str|None>, on=[...], where=[q("path")==v, ...], limit=N)`
  → `list[ScoredDocument]`, each with `.document.id`, `.document.body` (dict), `.score`.
  `from beaver import q`; filters are `q("path") == v` / `>=` / `<=` (numeric ranges work).
- Dict: `d[k]=v`, `d[k]`, `k in d`, `.values()`, `.keys()`, `.items()`.
- FTS5 operator syntax like `'"calle"* AND "23"'` passes straight to `MATCH`.

## Query mapping

- **Forward broad search** (`search_lugares`): `col.search(query=fts_q,
  on=["nombre","norm_nombre"], where=[q("municipio_norm")==m, q("tipo")==t],
  limit=100)` → rapidfuzz WRatio rerank in Python.
- **Exact portal** (`find_portal`): keyed dict lookup.
- **Intersection** (`get_calle_geom`): keyed dict → shapely `LineString.intersection`.
- **Reverse geo** (`nearest_lugares`): `col.search(where=[q("lat")>=…, q("lat")<=…,
  q("lon")>=…, q("lon")<=…], limit=500)` bbox pre-filter, then haversine rank,
  filter by radius.
- **Routing** (`Router`): `store.load_graph()` reads `graph_nodes`+`graph_edges`
  into memory once; A* by time over the adjacency list. The graph is built by
  `etl/graph_builder.build_graph` from the raw OSM way topology (node id =
  identity) with degree-2 chains contracted into edges that keep the full
  polyline. Endpoints resolve via the normal resolver, then snap onto the
  nearest edge (projected). See `2026-07-22-routing-graph-design.md`.
- **Municipio filter resolution** (`resolve_municipio`): the filter word is
  normalized and matched (prefix/substring) against the `municipios` catalog →
  canonical name → its `municipio_norm`, so the `lugares` filter compares
  normalized-to-normalized.

## beaver traps that bit us (do not re-learn the hard way)

- **`dict.get(k)` RAISES `KeyError` on a miss** — it is *not* Python-dict
  semantics. Gate every keyed lookup with `k in dict` (see `find_portal`,
  `get_calle_geom`, `stats`). Also, `.get(k, default)` (two args) does not exist.
- **`col.search(...)` needs beaver `main`.** The `where=`/`limit=` keywords were
  added there (commit `be12f47`); published `beaver-db 2.0.0` lacks them. Dev uses
  the editable `../beaver`.
- **`col.batched()` re-entry deadlocks** on a second *large* write under the sync
  portal (beaver issue **#40**). Use **one batch per collection** — never chunk a
  single collection into multiple sequential batches. A single batch handles tens
  of thousands of rows fine (~5.6k docs/s vs ~80/s per-item).
- FTS auto-indexes every text field in the body; keep large strings (geometry) out
  of the document.
