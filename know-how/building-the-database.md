# Building the geo database

*Reach for this when building/rebuilding the geo DB, or debugging an ETL run that
produces empty, partial, or mislabeled data.*

## TL;DR

```bash
uv run geocoder download                    # fetch Cuba PBF from Geofabrik (~61 MB)
uv run geocoder build --bbox habana --force # build La Habana (~6-7 min)
uv run geocoder stats                        # sanity: counts per collection
```

Full Cuba: `uv run geocoder build --full-cuba` (same code path; heavier).

A good La Habana build produces ~**64.9k lugares** (16.5k streets, 6.4k portals,
42k POIs), **167 municipios**, **16 provincias**, ~96% barrio coverage.

## Pipeline (what `build` does)

1. **Admin boundaries** (`etl/admin_parser`) — relations with
   `boundary=administrative` at Cuba's levels **province=4, municipio=6**
   (`config.ADMIN_LEVEL_*`). Member ways are stitched into rings with
   `linemerge`+`polygonize` (they are segments, not individually-closed rings).
   Parsed **unclipped** (`bbox=None`) so boundaries whose member ways run outside
   the entity bbox still assemble.
2. **Barrios** (`etl/places_parser`) — `place=neighbourhood|suburb|quarter|city_block`.
   Many are OSM *nodes* (centroid only, no polygon).
3. **Entities** (`etl/entity_parser`) — streets (`highway`+`name` → LineString),
   portals (`addr:housenumber`+`addr:street`), POIs (`name`+amenity/shop/…),
   bbox-filtered. Single pass with a node cache.
4. **Enrichment** (`etl/enrich`) — STRtree point-in-polygon (`predicate="within"`)
   assigns `municipio`/`municipio_norm`/`barrio`. Municipio has a nearest-centroid
   fallback; barrio has a nearest-centroid fallback (2.2 km cap) for node-only barrios.
5. **Persist** (`store.GeoStore`) — one batched write per beaver collection (see
   `beaver-data-model.md`).

## Why Havana municipios are fiddly (hard-won)

Getting correct municipio/barrio labels took fixing a chain of real bugs — if
labels look wrong, suspect these:

- **STRtree predicate direction.** `query(pt, predicate=...)` applies
  `pt.<predicate>(polygon)`, so point-in-polygon is **`within`**, not `contains`.
- **Cuba admin levels are 4/6**, not 6/8 — Havana's 15 municipios are `admin_level=6`.
- **Multi-way boundaries** must be stitched (`linemerge`+`polygonize`); assembling
  each member way as its own ring produces garbage.
- **Coastal boundaries** reuse `natural=coastline` ways as member ways, so
  `admin_parser.way()` stores **every** way's geometry (not just boundary-tagged).
- **Admin parsed unclipped** so member ways outside the bbox are present.

## Gotchas

- **PBF integrity.** osmium "unexpected EOF" ⇒ truncated download. Re-fetch
  (`--force`), or `wget -c` `config.PBF_URL`.
- **Memory.** Unclipped admin caches all Cuba nodes + all way node-lists; keep a
  couple of GB free. Fine on a laptop; heavier for full-Cuba.
- **Bbox.** `config.HABANA_BBOX = (-82.55, 22.90, -82.20, 23.20)` filters *entity*
  parsing (admin is unclipped).
- **Empty or mislabeled DB?** Verify with `geocoder stats` and a spot search, not
  the exit code. Zero counts → check the enrich→store handoff. Wrong municipios →
  check the admin-level / assembly items above.
- **Speed.** Writes are batched (~7.5s for 64.9k rows). The time is osmium doing
  multiple full PBF passes (admin unclipped is the heaviest). Single-pass parsing
  is an open optimization.
- **Heavy download.** The 61 MB fetch is best on a fast uplink; La Habana build is
  fine anywhere.

## Where the DB lands

`config.DB_PATH` (default `data/cuba_geo.db`), overridable via `GEOCODER_DB_PATH`.
`data/` is gitignored (PBF + DB + wal/shm).
