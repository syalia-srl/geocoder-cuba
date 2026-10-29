# Changelog

All notable changes to the Cuba Geocoder are documented here.

## [Unreleased]

## [0.5.0] — 2026-09-28

### Added

- **Lote mode — geocode a spreadsheet.** Upload an `.xlsx` or `.csv`, pick the
  address (and optional municipio) column, and the rows stream through
  `/resolve/batch` onto the map, coloured by precision; clicking a dot shows its
  row. The result downloads as `.xlsx` with `lat`, `lon`, `precision`, `radio_m`,
  `resultado` and `municipio_resultado` appended. The spreadsheet is read and
  written in the browser (an `.xlsx` is a zip of XML; `DecompressionStream`
  inflates it), so the server gained no upload endpoint or spreadsheet library.
- **Lote views.** After a run the same rows draw as Puntos, Grupos (MapLibre
  clustering), Calor (a density heatmap) or Zonas: a choropleth of rows per
  provincia / municipio / barrio with a ranked table that downloads as `.xlsx`.
  New `GET /api/v1/admin/geometries?level=…` serves the simplified shapes.
- **Isochrones.** `POST /api/v1/isochrone` and "¿Hasta dónde llego?" in the
  Punto result: the area reachable by car in 5/10/15 min, drawn as the
  reachable street pieces widened and merged, so bands follow streets instead of
  swallowing the bay. `search/isochrone.reach` is a multi-source Dijkstra with a
  time budget.
- **Coverage.** `POST /api/v1/coverage` and the Lote "Cobertura" view: click up
  to 8 centres, and every row is assigned to the one it reaches fastest by car,
  with a per-centre table, a 10/15/30 min threshold and `centro` /
  `minutos_al_centro` columns in the download.
- **DBSCAN clusters.** `POST /api/v1/cluster` and Grupos → Análisis: density
  clusters in metres (numpy + shapely only, no scikit-learn), hulls labelled by
  number, a table per cluster and a `cluster` column in the download.
- **Air-gapped release kit.** A Docker bundle with the full-Cuba database, tiles
  and routing graph baked in, a no-engine tarball for targets without Docker, a
  provenance stamp and a smoke gate that proves nothing is fetched at runtime.
  `make airgap USB=<mount>` chains bundle, offline Docker installer (static
  binaries, for a machine with no Docker at all), smoke gate and USB copy, with
  a Spanish `README.md` for the recipient.

- **Reverse geocoding — the map is now an input.** Clicking the map describes
  that point as a Cuban-style address (`Calle 23 e/ L y M`) and lists the places
  near it; clicking a nearby place selects it. A new `describe` rung — the
  inverse of the address ladder — degrades `describe_segment` → `describe_street`
  → `describe_place` rather than inventing a corner where crossing data is thin.
  Surfaced as an additive `address` field on `GET /api/v1/reverse` (`coordinates`
  and `results` unchanged, so no consumer breaks).
- **Permalinks.** `?q=<query>` for a typed address and `?at=<lat>,<lon>` for a
  map point, read on load and kept current with `history.replaceState`.
- **Multi-point tours (travelling salesman).** `POST /api/v1/tour` returns the
  **exactly optimal** order to visit 2–12 stops — Held-Karp over a cost matrix
  built from pairwise A*, minimising time, with a toggle for closing the loop
  back to the origin. The new "Viaje" UI mode collects stops by clicking the map
  (each reverse-geocoded, so it arrives with its address), then renumbers the
  list to the optimal order and draws the route. The 12-point cap is what keeps
  the answer exact rather than approximate.

### Fixed

- **Double quotes in a query no longer 500.** `Tienda "La Concepción"` closed
  the quoted FTS5 term early ("unterminated string"); both FTS query builders now
  drop `"` from terms.
- **`/resolve/batch` isolates a failing query.** One bad row used to 500 the
  whole batch; it now comes back as an empty result with an `error` string.
- **A named province is binding.** A query naming a province no longer falls
  back to a homonym in another one.
- **A coordinate landing exactly on a graph node no longer crashes routing.**
  `substring(line, f, f)` returns a Point rather than a LineString, and a
  LineString cannot be built from a single coordinate — so `/route` returned 500
  for such a request. Found while building the tour tests.

### Performance

- **Batch geocoding: 5000 rows in ~40 s in the UI, down from ~15 min**
  (~175 → ~6 ms per address against the API).
  Portal and street-geometry lookups serve from memory after one scan (the
  unscoped portal lookup used to scan every key through the beaver bridge,
  ~0.8 s per call), and the strict FTS query skips function words and pins
  single letters (`"la"*` and `"c"*` expanded to a large share of the index).

### Changed

- The Ruta mode stacks origin and destination on two rows.
- The reverse path builds a cached STRtree over street geometry (~25 s on full
  Cuba). It warms on a background thread with the routing graph, and `/reverse`
  is a sync endpoint so the warm never freezes the event loop.

## [0.4.0] — 2026-07-23

### Added

- **Routing.** Fastest path A→B over the street network: the routing graph is
  built from raw OSM ways during the ETL (degree-2 chains contracted), stored in
  `graph_nodes`/`graph_edges`, and searched with an admissible-heuristic A* by
  time. Endpoints resolve through the normal resolver, then snap to the nearest
  edge. Surfaces: `geocoder route "<a>" "<b>"`, `POST /api/v1/route` (GeoJSON),
  and the "Ruta" UI mode. Graph load + STRtree edge index are cached (~700×
  faster routes after the first).
- **Region polygons.** `region_hull` rung: the convex polygon enclosed by ≥3
  streets, via the pairwise crossings. `resolver.resolve_region`,
  `POST /api/v1/region`, `geocoder region`, and the Región UI mode.
- **Area geometry in results.** Area hits carry `polygon_wkt` through the
  gazetteer and resolver, so `/resolve` returns the real boundary and the UI
  draws it instead of a circle.
- **Fully offline map.** The basemap is a local Cuba PMTiles archive built with
  tilemaker (`bin/build-tiles`, non-Java) and served from the app over HTTP Range
  at `/tiles`. Web fonts are vendored. No CDN, no external tiles.

### Changed — map engine is now MapLibre GL

- The offline basemap renders on **MapLibre GL (WebGL)** instead of
  protomaps-leaflet (Canvas2D), which measured ~2 500 ms per pan frame at z13
  over Havana; MapLibre pans at ~16 ms/frame. Result/route/region overlays are
  ported to a GeoJSON source plus styled layers and DOM markers.
- **Place labels render offline** from SDF glyph ranges generated from Inter
  Medium and vendored under `static/vendor/glyphs/` (`bin/build-glyphs`). The
  `text-font` name must equal the glyph directory name.
- Leaflet and protomaps-leaflet are no longer vendored or shipped.

### Changed — a failed address is no longer retried as a POI search (breaking)

- **`/resolve` no longer answers a failed address with an unrelated place.** When
  a query parses as an address and every rung fails (portal, esquina, entre,
  street), the POI rung used to receive the raw text and fuzzy-match *something*
  — measured on real data, 404 distinct addresses collapsed onto one coordinate,
  each reported at a 25 m radius. The address path now falls to the barrio or
  municipio the query itself named, carrying that area's own radius, and returns
  **no result** when the query named no context at all. Place/POI queries are
  untouched.
- **Callers that assumed a point always comes back must handle `municipio`- and
  `barrio`-precision results, and the empty list.** A wrong point at 25 m is
  worse than a right one at 2 km: only the second tells the caller what it does
  not know.
- Measured on a sample of periphery addresses: rows sitting on a shared
  coordinate that *claims* precision fell **87 → 5** (−94%); spurious `poi`
  results within the `tipo=calle` bucket fell **211 → 48**; precise results in
  that bucket went **0 → 18 of 300**. Part of the gain is second-order —
  consumers that try a second address field only reach it once the first fails
  honestly.

### Fixed

- **Street names are canonicalized before every geometry lookup.** A name that
  missed lost the whole address ladder, since all rungs share that lookup.
  `fuzzy_street` existed but was reachable only from a retry that re-queried the
  precomputed crossing table, so periphery pairs never benefited. Adds a
  deterministic article-alias index (10.7% of the 12 110-name vocabulary carries
  a leading article; aliases that would shadow a real street or fit two articled
  streets are dropped).
- **The placeholder `0` no longer poisons street names.** Legacy exports write a
  bare `0` where the house number is missing (`3ra. 0 e/ …`); glued into the name
  it defeated the match. Only a non-leading zero is dropped — `0 (186)`,
  `0 (cespedes)` and `0 (frente sur)` are real Havana streets.
- Parenthetical streets keep their numbered base name. `Calle 23 (La Rampa)` was
  dropping its base `Calle 23` to the numeric-alias filter, so the La Rampa
  segment (from L to the Malecón) never fed street `23` — and `23 y O` resolved
  in **Holguín** instead of Vedado. The base name is now always kept; the numeric
  filter applies only to parenthetical/alt-name aliases. Rebuild: intersections
  grew to ~122.9k as the numbered segments reconnect.

### UI

- Results show up to 5 candidates as a clickable list; the best is auto-selected
  and clicking another re-centers the map, so you can compare when a name matches
  more than one place.

## [0.3.0] — 2026-07-20

### Changed — single resolve engine (breaking API)

- One resolution engine and one endpoint. `GET /api/v1/resolve` now **always
  returns a list** — `{query, results: [...]}` (one for an address/intersection/
  area, up to `limit` for a POI/name; `geojson` → a `FeatureCollection`).
  `GET /api/v1/geocode` is **removed**; the legacy parallel search stack
  (`QueryParser`, `FTSSearcher`, `PortalLookup`, `IntersectionFinder`) is
  deleted. CLI `geocoder resolve` prints N results. `Resolver.resolve_many`
  underlies it; `Resolver.resolve` stays as the single best.

### Added — precomputed intersection table

- All street crossings are precomputed in the ETL (a nationwide STRtree, one
  pass) into an `intersections` collection keyed by canonical pair. Resolving an
  esquina/entre is now an O(1) table lookup; geometry is a fallback for pairs not
  in the table, plus a fuzzy street-name retry (`belaskoain y reina`). Full-Cuba
  build: ~116.7k crossings across ~95.3k pairs.

### Added — gazetteer of areas

- A level-aware place index (provinces / municipios / barrios, with hierarchy
  and curated aliases), built once from the admin data. **Provinces are now
  resolvable.** The parser recognizes whole-query areas and validates trailing
  context against it; the resolver disambiguates (`Cienfuegos` → municipio,
  `provincia de Cienfuegos` → provincia, homonyms → context then La Habana).
  Colloquial names resolve via `aliases.json` (`Habana` → La Habana province,
  `10 de octubre` → Diez de Octubre).

### Added (typo tolerance)

- Misspelled queries now resolve. Fuzzy matching had only *reranked* FTS
  candidates, so a typo that returned zero FTS rows resolved to nothing
  (`plaza de la revolucon`, `bedado`, `hotel nacinal` → NONE). Municipios and
  barrios now fall back to a fuzzy name match (`fuzz.ratio` over the cached
  lists); POIs retry with an OR of 3-char prefixes and rerank. The
  municipio/barrio lists are cached on the store (they were scanned per query).
  Covered by `tests/test_fuzzy.py`.

### UI

- Zoom is proportional to the result's size — `flyToBounds` fits ~2× the
  precision radius, so an esquina zooms right in and a municipio pulls back.

### Fixed (intersections)

- `calle y calle` (a corner) and `calle e/ calle y calle` (between two
  cross-streets) now resolve reliably across the country. The resolver had
  collapsed each street to one arbitrary fragment, so on the full-Cuba data two
  streets almost never met (`134 y 71` → nothing; `Calle L e/ 23 y 25` → Villa
  Clara). It now finds the fragment **pair** that actually crosses (STRtree),
  scoped to context/Havana, with a bounded near-approach fallback for small OSM
  gaps, and anchors `entre` to a single crossing when one cross-street is
  unmapped. Parses the `A/B` slash shorthand. Reports the crossing's real
  municipio. Grounded by `tests/test_intersections.py` (anchored on Coppelia's
  corner, 23 y L).

### Performance (queries)

- Resolving a street no longer scans all ~24k geometry keys per lookup — the
  store builds a street→municipios index once (warmed at API startup). Per-query
  resolve drops from ~6 s to ~0.1 s on the full-Cuba build.

### Added

- **Web UI** (served at `/`): a Leaflet dark-map resolver console — type a Cuban
  address and the point lands with an honest ± precision ring and the parsed
  address grammar decomposed into tokens. Static assets under
  `geocoder/api/static/`; FastAPI mounts them at `/` (API stays under `/api/v1`).
- **API parity**: `POST /api/v1/resolve/batch` (up to 100 queries, order
  preserved); `GET /api/v1/resolve?format=geojson` (GeoJSON Feature output);
  `GET /api/v1/barrios` and a now-functional `GET /api/v1/municipios/{id}/barrios`
  (barrios resolved spatially — centroid within the municipio polygon — replacing
  the empty stub).
- `GeoStore.barrios_in_municipio()` (point-in-polygon barrio lookup).
- Deployment: `deploy/geocoder.service` (a `/srv` + uv + uvicorn layout) and
  a hosted demo serving the full-Cuba
  build (183,399 lugares across all 15 provinces, 167 municipios, 1,814 barrios).

### Fixed

- Province is now assigned per municipio by spatial containment (PIP of the
  municipio centroid within the admin_level-4 province polygon). OSM rarely tags
  `is_in:province`, so every municipio outside Havana had been mislabelled
  "La Habana"; results now report the real province (Santiago de Cuba, Camagüey,
  Sancti Spíritus, …).

### Performance

- Full-Cuba enrichment is near-linear again (~8 min end to end). It had been
  O(N×M) + O(N×B) — a per-entity province rescan plus nearest-centroid fallbacks
  that iterated every municipio/barrio — and stalled for 30+ min on the full
  country. Province lookup is a dict; the fallbacks use `STRtree.query_nearest`.

## [0.2.0] — 2026-07-18

### Added

- **Unified location resolver** (`geocoder resolve`, `GET /api/v1/resolve`):
  parses Cuban addresses (`Calle X #N e/ Y y Z, Barrio`), POIs/named places, and
  administrative areas, and returns the single best point with a `precision`
  label and a geometry-derived `± radius_m`. Ladder: `exact_portal` ›
  `interpolated_entre`/`segment_midpoint` › `esquina` › `poi` › `street` ›
  `barrio` › `municipio`. Barrio/municipio context disambiguates repeated names.
- `GeoStore.street_geoms()` and merged street geometry in the ETL (streets are
  many OSM ways; fragments are now `linemerge`d per street/municipio so
  intersection/`entre` resolution works).
- Hard golden corpus (`tests/fixtures/cuban_addresses.json`, 24 cases) run
  against a real La Habana build.

### Notes

- A rebuild (`geocoder build --bbox habana --force`) is required to pick up
  merged street geometry and barrio centroids.
- True house-number interpolation along a street (a dedicated rung) is deferred;
  numbered addresses without an exact portal currently fall to `street`.

## [0.1.0] — 2026-07-18

First working release. The project was audited, found to have never run
end-to-end, and rebuilt on the **beaver** document store; it now geocodes real
La Habana data.

### Features

- Offline forward geocoding (streets, portals, POIs), exact portal lookup,
  geometric `A y B` intersections, and fuzzy (rapidfuzz) matching.
- Reverse geocoding (bbox pre-filter + haversine nearest-neighbor).
- Point-in-polygon **municipio + barrio** enrichment from real OSM boundaries.
- FastAPI REST API: `/geocode`, `/reverse`, `/municipios`, `/health`, `/stats`
  (health at both `/health` and `/api/v1/health`).
- Typer CLI: `download`, `build`, `search`, `serve`, `stats`, `bench`.
- beaver-backed storage (`GeoStore`); batched bulk writes.

### Notes

- A real `--bbox habana` build yields ~64.9k lugares, 167 municipios,
  16 provincias, ~96% barrio coverage.
- Requires Python ≥ 3.12 and `beaver-db >= 2.1.0`.
- Known open items: full-Cuba chunked build blocked on beaver #40;
  `/municipios/{id}/barrios` is a stub; single-pass ETL is a future optimization.
